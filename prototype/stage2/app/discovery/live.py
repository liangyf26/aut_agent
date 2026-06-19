from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, async_playwright

from .identity import (
    build_feature_point_identity,
    build_page_entry_identity,
    canonicalize_url,
    locator_anchor,
    normalize_text,
    slug,
)
from .models import DiscoveryResult, FeaturePointRecord, PageEntryRecord, ScreenshotRecord, utc_now_iso


@dataclass(frozen=True)
class LiveDiscoveryConfig:
    max_nav_targets: int = 5
    max_feature_points: int = 8
    max_feature_screenshots: int = 3
    max_discovery_depth: int = 1
    include_url_keywords: tuple[str, ...] = ()
    exclude_url_keywords: tuple[str, ...] = ("logout", "delete", "remove", "download", "export")
    skip_text_keywords: tuple[str, ...] = (
        "退出",
        "注销",
        "删除",
        "移除",
        "下载",
        "导出",
        "打印",
        "logout",
        "delete",
        "remove",
        "download",
        "export",
        "print",
    )
    include_text_keywords: tuple[str, ...] = ()
    exclude_locator_keywords: tuple[str, ...] = ("logout", "delete", "remove", "download", "export")
    include_locator_keywords: tuple[str, ...] = ()
    skip_path_keywords: tuple[str, ...] = ("logout", "delete", "remove", "download", "export", "print")
    max_nav_candidates_per_page: int = 80
    max_action_candidates_per_page: int = 160
    max_feature_points_per_page: int = 6
    revisit_page_feature_points: bool = True
    screenshot_root: str = "screenshots/discovery"


async def plan_live_discovery(
    page: Page,
    *,
    template_name: str,
    template: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    screenshots_dir: Path,
    config: LiveDiscoveryConfig | None = None,
) -> DiscoveryResult:
    config = config or LiveDiscoveryConfig()
    await page.wait_for_load_state("domcontentloaded")

    entry_name = template.get("page_entry", {}).get("name") or (await page.title()) or template_name
    entry_url = page.url or template.get("page_entry", {}).get("url", "")
    execution_path = template.get("execution_path")
    entry_identity = build_page_entry_identity(
        template_name,
        name=entry_name,
        url=entry_url,
    )
    page_entry_id = entry_identity["record_id"]
    page_entry_key = entry_identity["stable_key"]
    landing_dir = Path(config.screenshot_root) / slug(page_entry_id, fallback="page_entry", max_length=96)
    landing_path = landing_dir / "landing.png"
    await page.screenshot(path=str(screenshots_dir / landing_path), full_page=True)

    scan = await _scan_live_page(page, config=config)

    root_page_entry = PageEntryRecord(
        page_entry_id=page_entry_id,
        name=entry_name,
        url=entry_url,
        template_name=template_name,
        source="playwright.live_page",
        confidence="live_page_loaded",
        page_type="page_entry",
        stable_key=entry_identity["stable_key"],
        dedupe_key=entry_identity["dedupe_key"],
        dedupe_basis=entry_identity["dedupe_basis"],
        discovery_depth=0,
        execution_path=execution_path,
        evidence={
            "title": await page.title(),
            "canonical_url": canonicalize_url(entry_url) or entry_url,
            "baseline_reference_record_id": (baseline or {}).get("reference_success_record_id"),
            "raw_nav_candidate_count": len(scan["nav_candidates"]),
            "raw_action_candidate_count": len(scan["action_candidates"]),
        },
    )
    screenshot_records: list[ScreenshotRecord] = [
        ScreenshotRecord(
            screenshot_id=f"{page_entry_id}__landing",
            page_entry_id=page_entry_id,
            feature_point_id=None,
            stage="page_entry_landing",
            purpose="capture actual landing page in controlled discovery",
            status="captured",
            relative_path=str(landing_path).replace("\\", "/"),
            source="playwright.live_page",
            notes=[f"url={entry_url}", f"canonical_url={canonicalize_url(entry_url) or entry_url}"],
        )
    ]

    discovered_page_entries, traversal_artifacts = await _discover_same_origin_targets(
        page,
        page_entry_id=page_entry_id,
        page_entry_key=page_entry_key,
        template_name=template_name,
        base_url=entry_url,
        nav_candidates=scan["nav_candidates"],
        screenshots_dir=screenshots_dir,
        screenshot_root=landing_dir,
        config=config,
        screenshot_records=screenshot_records,
    )

    feature_points, feature_stats = await _build_feature_points(
        page,
        page_entry_id=page_entry_id,
        page_entry_key=page_entry_key,
        template_name=template_name,
        template=template,
        action_candidates=scan["action_candidates"],
        screenshots_dir=screenshots_dir,
        landing_dir=landing_dir,
        config=config,
        screenshot_records=screenshot_records,
    )

    page_entries = [root_page_entry, *discovered_page_entries]
    linked_feature_points = traversal_artifacts["feature_points"] if config.revisit_page_feature_points else []
    feature_points = [*feature_points, *linked_feature_points]
    feature_scope_breakdown = Counter(item.feature_scope for item in feature_points)
    action_type_breakdown = Counter(item.action_type for item in feature_points)
    page_type_breakdown = Counter(item.page_type for item in page_entries)
    review_queue = _build_review_queue(page_entries=page_entries, feature_points=feature_points)

    return DiscoveryResult(
        template_name=template_name,
        generated_at=utc_now_iso(),
        strategy="playwright_controlled_live",
        page_entries=page_entries,
        feature_points=feature_points,
        screenshot_records=screenshot_records,
        review_queue=review_queue,
        review_hints={
            "status": "pending_manual_review",
            "entry": {
                "kind": "manual_review_placeholder",
                "suggested_outputs": ["page_entries.json", "feature_points.json", "discovery_result.json"],
                "review_queue_file": "discovery_review_queue.json",
            },
            "recommended_checks": [
                "确认去重后的页面入口是否仍然保持业务上唯一。",
                "确认 page_action / row_action / modal_action 的分类是否符合页面语义。",
                "优先人工确认 discovery_depth 较深、来源为 same_origin_link 的入口。",
                "优先人工确认 modal_action 和 row_action 的候选项。",
                "优先人工确认 page_type=linked_page_entry、feature_scope=row_action 且 occurrence_count 较高的候选项。",
            ],
            "target_fields": [
                "stable_key",
                "dedupe_basis",
                "discovery_depth",
                "source_page_entry_id",
                "page_type",
                "feature_scope",
                "action_type",
            ],
        },
        stats={
            "page_entry_count": len(page_entries),
            "feature_point_count": len(feature_points),
            "screenshot_record_count": len(screenshot_records),
            "review_queue_count": len(review_queue),
            "nav_candidate_count": len(scan["nav_candidates"]),
            "action_candidate_count": len(scan["action_candidates"]),
            "page_type_breakdown": dict(page_type_breakdown),
            "feature_scope_breakdown": dict(feature_scope_breakdown),
            "action_type_breakdown": dict(action_type_breakdown),
            "discovery_depth_limit": config.max_discovery_depth,
            **traversal_artifacts["stats"],
            **feature_stats,
        },
        notes=[
            "发现结果来自真实页面受控遍历。",
            "页面入口优先使用 canonical_url 生成稳定 ID，并保留 dedupe_basis 作为后续人工审核依据。",
            "同源链接使用独立页面探测，避免扰动当前已登录主页面。",
            "功能点已区分 page_action、row_action、modal_action，并补充 action_type 与 discovery_depth。",
            "已遍历的同源页面会追加独立 page_entry，并可按配置补采该页的 feature_points。",
            "当前同源遍历默认只深入 1 层，优先产出稳定入口清单，不扩成通用爬虫。",
            "跳过规则默认规避退出、删除、下载、导出等高风险或低价值动作。",
        ],
    )


async def run_live_discovery_session(
    *,
    cdp_url: str,
    template_name: str,
    template: dict[str, Any],
    baseline: dict[str, Any] | None,
    output_dir: Path,
    config: LiveDiscoveryConfig | None = None,
) -> DiscoveryResult:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            page = await _resolve_target_page(browser, template.get("page_entry", {}).get("url", ""))
            return await plan_live_discovery(
                page,
                template_name=template_name,
                template=template,
                baseline=baseline,
                screenshots_dir=output_dir,
                config=config,
            )
        finally:
            await browser.close()


async def _resolve_target_page(browser: Browser, target_url: str) -> Page:
    pages: list[Page] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    if not pages:
        raise RuntimeError("未发现可用页面，无法执行 live discovery")

    page = next((item for item in pages if target_url and target_url in item.url), pages[0])
    await page.bring_to_front()
    await page.wait_for_load_state("domcontentloaded")
    if target_url and target_url not in page.url:
        await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
    return page


async def _scan_live_page(page: Page, *, config: LiveDiscoveryConfig) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ maxNavCandidates, maxActionCandidates }) => {
          function compactText(value, maxLength = 120) {
            const normalized = (value || '').replace(/\\s+/g, ' ').trim();
            if (!normalized) return '';
            return normalized.length > maxLength ? normalized.slice(0, maxLength) : normalized;
          }
          function text(node) {
            return compactText(node?.innerText || node?.textContent || '');
          }
          function attr(el, name) {
            return compactText(el?.getAttribute?.(name) || '');
          }
          function boolAttr(el, name) {
            const value = el?.getAttribute?.(name);
            return value === '' || value === 'true' || value === '1';
          }
          function visible(el) {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          }
          function cssPath(el) {
            if (!el || !el.tagName) return '';
            if (el.id) return `#${el.id}`;
            const parts = [];
            let current = el;
            while (current && current.nodeType === 1 && parts.length < 5) {
              let selector = current.tagName.toLowerCase();
              const classNames = Array.from(current.classList || [])
                .filter(name => name && name.length < 40 && !/^is-/.test(name))
                .slice(0, 2);
              if (classNames.length) {
                selector += '.' + classNames.join('.');
              }
              const parent = current.parentElement;
              if (parent) {
                const siblings = Array.from(parent.children).filter(node => node.tagName === current.tagName);
                if (siblings.length > 1) {
                  selector += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                }
              }
              parts.unshift(selector);
              current = parent;
            }
            return parts.join(' > ');
          }
          function discoveryId(prefix, el, index) {
            const existing = el.getAttribute('data-stage2-discovery-id');
            if (existing) return existing;
            const value = `${prefix}-${index + 1}`;
            el.setAttribute('data-stage2-discovery-id', value);
            return value;
          }
          function titleText(root) {
            if (!root) return '';
            const titleNode = root.querySelector('[role="heading"], .el-dialog__title, .ant-modal-title, .el-drawer__header, .drawer-title, h1, h2, h3, h4');
            return text(titleNode) || attr(root, 'aria-label') || attr(root, 'title');
          }
          function rowSummary(root) {
            if (!root) return '';
            const cells = Array.from(root.querySelectorAll('td, [role="gridcell"], .cell'))
              .map(node => text(node))
              .filter(Boolean)
              .slice(0, 3);
            return compactText(cells.join(' | '), 160);
          }
          function label(el) {
            const tag = el?.tagName?.toLowerCase() || '';
            if (tag === 'input' || tag === 'textarea' || tag === 'select') {
              return (
                attr(el, 'aria-label') ||
                attr(el, 'placeholder') ||
                attr(el, 'title') ||
                attr(el, 'value') ||
                text(el)
              );
            }
            return text(el) || attr(el, 'aria-label') || attr(el, 'title') || attr(el, 'value');
          }
          function contextInfo(el) {
            const modalRoot = el.closest('[role="dialog"], .el-dialog, .ant-modal, .el-drawer, .drawer, .modal');
            if (modalRoot) {
              return {
                container_type: 'modal',
                container_label: titleText(modalRoot),
                context_text: compactText(text(modalRoot), 160),
              };
            }
            const rowRoot = el.closest('tr, [role="row"], .el-table__row, .ant-table-row');
            if (rowRoot) {
              return {
                container_type: 'table_row',
                container_label: '',
                context_text: rowSummary(rowRoot),
              };
            }
            const navRoot = el.closest('nav, [role="navigation"], .el-menu, .ant-menu, .tabs, .el-tabs');
            if (navRoot) {
              return {
                container_type: 'navigation',
                container_label: titleText(navRoot),
                context_text: compactText(text(navRoot), 160),
              };
            }
            const sectionRoot = el.closest('section, .panel, .pane, .card, .el-card, .ant-card, .el-form, main');
            return {
              container_type: 'page',
              container_label: titleText(sectionRoot),
              context_text: '',
            };
          }
          function stableAttrs(el) {
            return {
              id: attr(el, 'id'),
              name: attr(el, 'name'),
              aria_label: attr(el, 'aria-label'),
              placeholder: attr(el, 'placeholder'),
              title: attr(el, 'title'),
              data_testid: attr(el, 'data-testid') || attr(el, 'data-testid'),
              data_test: attr(el, 'data-test'),
            };
          }
          function isPrimaryAction(el) {
            const classNames = Array.from(el.classList || []).join(' ').toLowerCase();
            return (
              classNames.includes('primary') ||
              boolAttr(el, 'data-primary') ||
              /(^|\\s)(submit|save|confirm|create|search)(\\s|$)/i.test(label(el))
            );
          }

          function collect(selector, prefix, limit) {
            return Array.from(document.querySelectorAll(selector))
              .filter(visible)
              .slice(0, limit)
              .map((el, index) => ({
                discovery_id: discoveryId(prefix, el, index),
                text: label(el),
                href: attr(el, 'href'),
                role: attr(el, 'role'),
                tag: el.tagName.toLowerCase(),
                type: attr(el, 'type'),
                locator: cssPath(el),
                disabled: !!el.disabled || attr(el, 'aria-disabled') === 'true',
                is_primary_action: isPrimaryAction(el),
                page_title: compactText(document.title, 120),
                page_url: location.href,
                stable_attrs: stableAttrs(el),
                ...contextInfo(el),
              }));
          }

          return {
            page_context: {
              title: compactText(document.title, 160),
              url: location.href,
            },
            nav_candidates: collect('a[href], [role="menuitem"], [role="tab"], .el-menu-item, .el-tabs__item', 'nav', maxNavCandidates),
            action_candidates: collect('button, [role="button"], a[href], [role="tab"], input, select, textarea, .el-button, .el-link', 'action', maxActionCandidates),
          };
        }
        """,
        {
            "maxNavCandidates": config.max_nav_candidates_per_page,
            "maxActionCandidates": config.max_action_candidates_per_page,
        },
    )


async def _discover_same_origin_targets(
    page: Page,
    *,
    page_entry_id: str,
    page_entry_key: str,
    template_name: str,
    base_url: str,
    nav_candidates: list[dict[str, Any]],
    screenshots_dir: Path,
    screenshot_root: Path,
    config: LiveDiscoveryConfig,
    screenshot_records: list[ScreenshotRecord],
) -> tuple[list[PageEntryRecord], dict[str, Any]]:
    base_origin = urlparse(base_url).netloc
    context = page.context
    discovered_urls = {canonicalize_url(base_url) or base_url}
    discovered_keys = {page_entry_key}
    results: list[PageEntryRecord] = []
    discovered_feature_points: list[FeaturePointRecord] = []
    queue: list[dict[str, Any]] = [
        {
            "page_entry_id": page_entry_id,
            "page_entry_key": page_entry_key,
            "depth": 0,
            "url": base_url,
            "nav_candidates": _ordered_nav_candidates(nav_candidates),
        }
    ]
    stats: Counter[str] = Counter(
        {
            "nav_targets_considered": 0,
            "nav_targets_traversed": 0,
            "nav_targets_failed": 0,
            "nav_targets_duplicate": 0,
            "nav_targets_blocked_by_rules": 0,
            "nav_targets_skipped_without_href": 0,
            "linked_feature_point_count": 0,
        }
    )

    while queue and stats["nav_targets_traversed"] < config.max_nav_targets:
        current = queue.pop(0)
        current_depth = int(current["depth"])
        if current_depth >= config.max_discovery_depth:
            continue

        for candidate in current["nav_candidates"]:
            if stats["nav_targets_traversed"] >= config.max_nav_targets:
                break
            stats["nav_targets_considered"] += 1

            href = normalize_text(candidate.get("href"))
            text = normalize_text(candidate.get("text"))
            if not href or href.startswith("javascript:") or href.startswith("#"):
                stats["nav_targets_skipped_without_href"] += 1
                continue

            absolute_url = urljoin(current["url"], href)
            allowed, block_reason = _is_navigation_target(
                absolute_url,
                base_origin=base_origin,
                candidate_text=text,
                locator=candidate.get("locator"),
                config=config,
            )
            if not allowed:
                stats["nav_targets_blocked_by_rules"] += 1
                continue

            candidate_canonical_url = canonicalize_url(absolute_url) or absolute_url
            if candidate_canonical_url in discovered_urls:
                stats["nav_targets_duplicate"] += 1
                continue

            identity = build_page_entry_identity(
                template_name,
                name=text or absolute_url,
                url=absolute_url,
            )
            if identity["dedupe_key"] in discovered_keys:
                stats["nav_targets_duplicate"] += 1
                continue

            stats["nav_targets_traversed"] += 1
            probe_page = await context.new_page()
            try:
                await probe_page.goto(absolute_url, wait_until="domcontentloaded", timeout=15000)
                await probe_page.wait_for_timeout(1200)
                page_name = text or (await probe_page.title()) or absolute_url
                resolved_identity = build_page_entry_identity(
                    template_name,
                    name=page_name,
                    url=probe_page.url,
                )
                resolved_canonical_url = resolved_identity["dedupe_basis"].get("canonical_url") or candidate_canonical_url
                if resolved_canonical_url in discovered_urls or resolved_identity["dedupe_key"] in discovered_keys:
                    stats["nav_targets_duplicate"] += 1
                    continue

                screenshot_path = screenshot_root / (
                    f"depth_{current_depth + 1}_{slug(resolved_identity['record_id'], fallback='page_entry', max_length=96)}.png"
                )
                await probe_page.screenshot(path=str(screenshots_dir / screenshot_path), full_page=True)
                results.append(
                    PageEntryRecord(
                        page_entry_id=resolved_identity["record_id"],
                        name=page_name,
                        url=probe_page.url,
                        template_name=template_name,
                        source="playwright.same_origin_link",
                        confidence="live_traversed",
                        page_type="linked_page_entry",
                        stable_key=resolved_identity["stable_key"],
                        dedupe_key=resolved_identity["dedupe_key"],
                        dedupe_basis=resolved_identity["dedupe_basis"],
                        discovery_depth=current_depth + 1,
                        parent_page_entry_id=current["page_entry_id"],
                        source_page_entry_id=current["page_entry_id"],
                        source_action_type=_infer_navigation_action_type(candidate),
                        execution_path="controlled_navigation",
                        evidence={
                            "from_page_entry_id": current["page_entry_id"],
                            "locator": candidate.get("locator"),
                            "locator_anchor": locator_anchor(candidate.get("locator")) or None,
                            "role": candidate.get("role"),
                            "container_type": candidate.get("container_type"),
                            "container_label": candidate.get("container_label"),
                            "source_href": href,
                            "navigation_depth": current_depth + 1,
                        },
                    )
                )
                screenshot_records.append(
                    ScreenshotRecord(
                        screenshot_id=f"{resolved_identity['record_id']}__landing",
                        page_entry_id=resolved_identity["record_id"],
                        feature_point_id=None,
                        stage="linked_page_landing",
                        purpose="capture linked page discovered via controlled traversal",
                        status="captured",
                        relative_path=str(screenshot_path).replace("\\", "/"),
                        source="playwright.same_origin_link",
                        notes=[f"from={current['page_entry_id']}", f"href={absolute_url}"],
                    )
                )
                discovered_urls.add(resolved_canonical_url)
                discovered_keys.add(resolved_identity["dedupe_key"])

                scan = await _scan_live_page(probe_page, config=config)
                if config.revisit_page_feature_points:
                    linked_landing_dir = Path(config.screenshot_root) / slug(
                        resolved_identity["record_id"], fallback="page_entry", max_length=96
                    )
                    linked_feature_points, linked_feature_stats = await _build_feature_points(
                        probe_page,
                        page_entry_id=resolved_identity["record_id"],
                        page_entry_key=resolved_identity["stable_key"],
                        template_name=template_name,
                        template={},
                        action_candidates=scan["action_candidates"],
                        screenshots_dir=screenshots_dir,
                        landing_dir=linked_landing_dir,
                        config=config,
                        screenshot_records=screenshot_records,
                        discovery_depth=current_depth + 1,
                        source_page_entry_id=current["page_entry_id"],
                        execution_path="controlled_navigation",
                    )
                    discovered_feature_points.extend(linked_feature_points)
                    stats["linked_feature_point_count"] += len(linked_feature_points)
                    stats["linked_grouped_feature_candidate_count"] += linked_feature_stats.get(
                        "grouped_feature_candidate_count", 0
                    )
                    stats["linked_skipped_action_candidate_count"] += linked_feature_stats.get(
                        "skipped_action_candidate_count", 0
                    )
                    stats["linked_retained_feature_point_count"] += linked_feature_stats.get(
                        "retained_feature_point_count", 0
                    )

                if current_depth + 1 < config.max_discovery_depth:
                    queue.append(
                        {
                            "page_entry_id": resolved_identity["record_id"],
                            "page_entry_key": resolved_identity["stable_key"],
                            "depth": current_depth + 1,
                            "url": probe_page.url,
                            "nav_candidates": _ordered_nav_candidates(scan["nav_candidates"]),
                        }
                    )
            except Exception as exc:
                stats["nav_targets_failed"] += 1
                screenshot_records.append(
                    ScreenshotRecord(
                        screenshot_id=f"{current['page_entry_id']}__failed_probe__{stats['nav_targets_traversed']}",
                        page_entry_id=current["page_entry_id"],
                        feature_point_id=None,
                        stage="linked_page_probe",
                        purpose="record failed controlled traversal target",
                        status="failed",
                        relative_path=None,
                        source="playwright.same_origin_link",
                        notes=[
                            f"href={absolute_url}",
                            f"block_reason={block_reason or 'none'}",
                            f"error={type(exc).__name__}: {exc}",
                        ],
                    )
                )
            finally:
                await probe_page.close()

    ordered_results = sorted(
        results,
        key=lambda item: (item.discovery_depth, item.stable_key or item.page_entry_id, item.name),
    )
    return ordered_results, {"stats": dict(stats), "feature_points": discovered_feature_points}


async def _build_feature_points(
    page: Page,
    *,
    page_entry_id: str,
    page_entry_key: str,
    template_name: str,
    template: dict[str, Any],
    action_candidates: list[dict[str, Any]],
    screenshots_dir: Path,
    landing_dir: Path,
    config: LiveDiscoveryConfig,
    screenshot_records: list[ScreenshotRecord],
    discovery_depth: int = 0,
    source_page_entry_id: str | None = None,
    execution_path: str = "controlled_live_scan",
) -> tuple[list[FeaturePointRecord], dict[str, Any]]:
    template_feature_name = normalize_text(template.get("feature_point", {}).get("name", ""))
    grouped_candidates, skipped_count = _group_action_candidates(
        action_candidates,
        template_name=template_name,
        page_entry_key=page_entry_key,
        config=config,
    )
    ordered_groups = sorted(
        grouped_candidates,
        key=lambda item: _feature_group_sort_key(item, template_feature_name),
    )

    results: list[FeaturePointRecord] = []
    page_title = await page.title()
    limit = config.max_feature_points if discovery_depth == 0 else config.max_feature_points_per_page
    for index, group in enumerate(ordered_groups[:limit], start=1):
        candidate = group["primary"]
        feature_identity = group["identity"]
        feature_name = candidate.get("text") or f"action_{index}"
        feature_scope = group["feature_scope"]
        action_type = group["action_type"]
        feature_type = group["feature_type"]
        results.append(
            FeaturePointRecord(
                feature_point_id=feature_identity["record_id"],
                page_entry_id=page_entry_id,
                name=feature_name,
                feature_type=feature_type,
                template_name=template_name,
                source="playwright.visible_action",
                confidence="live_visible",
                feature_scope=feature_scope,
                action_type=action_type,
                stable_key=feature_identity["stable_key"],
                dedupe_key=feature_identity["dedupe_key"],
                dedupe_basis=feature_identity["dedupe_basis"],
                discovery_depth=discovery_depth,
                source_page_entry_id=source_page_entry_id or page_entry_id,
                execution_path=execution_path,
                evidence={
                    "locator": candidate.get("locator"),
                    "locator_anchor": locator_anchor(candidate.get("locator")) or None,
                    "tag": candidate.get("tag"),
                    "role": candidate.get("role"),
                    "href": candidate.get("href"),
                    "type": candidate.get("type"),
                    "container_type": candidate.get("container_type"),
                    "container_label": candidate.get("container_label"),
                    "context_text": candidate.get("context_text"),
                    "stable_attrs": candidate.get("stable_attrs"),
                    "page_title": page_title,
                    "page_url": candidate.get("page_url") or page.url,
                    "is_primary_action": candidate.get("is_primary_action"),
                    "occurrence_count": group["occurrence_count"],
                    "locator_samples": group["locator_samples"],
                    "href_samples": group["href_samples"],
                    "review_priority": group["review_priority"],
                },
            )
        )

        if index > config.max_feature_screenshots:
            continue
        discovery_id = candidate.get("discovery_id")
        if not discovery_id:
            continue
        locator = page.locator(f"[data-stage2-discovery-id='{discovery_id}']").first
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
            screenshot_path = landing_dir / (
                f"{index:02d}_{slug(feature_identity['record_id'], fallback='feature_point', max_length=96)}.png"
            )
            await locator.screenshot(path=str(screenshots_dir / screenshot_path))
            screenshot_records.append(
                ScreenshotRecord(
                    screenshot_id=f"{feature_identity['record_id']}__feature",
                    page_entry_id=page_entry_id,
                    feature_point_id=feature_identity["record_id"],
                    stage="feature_point_focus",
                    purpose="capture visible feature point during live discovery",
                    status="captured",
                    relative_path=str(screenshot_path).replace("\\", "/"),
                    source="playwright.visible_action",
                    notes=[f"locator={candidate.get('locator', '')}"],
                )
            )
        except Exception as exc:
            screenshot_records.append(
                ScreenshotRecord(
                    screenshot_id=f"{feature_identity['record_id']}__feature",
                    page_entry_id=page_entry_id,
                    feature_point_id=feature_identity["record_id"],
                    stage="feature_point_focus",
                    purpose="record feature point capture failure",
                    status="failed",
                    relative_path=None,
                    source="playwright.visible_action",
                    notes=[f"error={type(exc).__name__}: {exc}"],
                )
            )

    return (
        results,
        {
            "grouped_feature_candidate_count": len(grouped_candidates),
            "skipped_action_candidate_count": skipped_count,
            "retained_feature_point_count": len(results),
        },
    )


def _ordered_nav_candidates(nav_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in nav_candidates:
        href = normalize_text(candidate.get("href"))
        text = normalize_text(candidate.get("text"))
        key = canonicalize_url(href) or f"{text}|{candidate.get('role') or ''}|{locator_anchor(candidate.get('locator'))}"
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(candidate)
    return sorted(
        results,
        key=lambda item: (
            0 if normalize_text(item.get("href")) else 1,
            normalize_text(item.get("text")).lower(),
            normalize_text(item.get("href")).lower(),
            locator_anchor(item.get("locator")).lower(),
        ),
    )


def _group_action_candidates(
    action_candidates: list[dict[str, Any]],
    *,
    template_name: str,
    page_entry_key: str,
    config: LiveDiscoveryConfig,
) -> tuple[list[dict[str, Any]], int]:
    grouped: dict[str, dict[str, Any]] = {}
    skipped_count = 0

    for candidate in action_candidates:
        text = normalize_text(candidate.get("text"))
        if not text or candidate.get("disabled"):
            continue
        if len(text) > 48:
            continue
        skip_candidate, _skip_reason = _should_skip_action_candidate(candidate, config=config)
        if skip_candidate:
            skipped_count += 1
            continue

        feature_scope = _infer_feature_scope(candidate)
        action_type = _infer_action_type(candidate, text, feature_scope=feature_scope)
        feature_type = _infer_feature_type(text, action_type=action_type)
        identity = build_feature_point_identity(
            template_name,
            page_entry_key=page_entry_key,
            name=text,
            feature_scope=feature_scope,
            action_type=action_type,
            container_label=candidate.get("container_label") or candidate.get("context_text"),
            href=candidate.get("href"),
            locator=candidate.get("locator"),
        )
        key = identity["stable_key"]
        if key not in grouped:
            grouped[key] = {
                "identity": identity,
                "primary": candidate,
                "feature_scope": feature_scope,
                "action_type": action_type,
                "feature_type": feature_type,
                "occurrence_count": 0,
                "locator_samples": [],
                "href_samples": [],
                "review_priority": "normal",
            }
        grouped[key]["occurrence_count"] += 1

        locator = normalize_text(candidate.get("locator"))
        href = normalize_text(candidate.get("href"))
        if locator and locator not in grouped[key]["locator_samples"]:
            grouped[key]["locator_samples"].append(locator)
        if href and href not in grouped[key]["href_samples"]:
            grouped[key]["href_samples"].append(href)

        grouped[key]["review_priority"] = _merge_review_priority(
            grouped[key]["review_priority"],
            _candidate_review_priority(candidate, feature_scope=feature_scope, action_type=action_type),
        )
        if _candidate_priority(candidate) < _candidate_priority(grouped[key]["primary"]):
            grouped[key]["primary"] = candidate

    return list(grouped.values()), skipped_count


def _feature_group_sort_key(group: dict[str, Any], template_feature_name: str) -> tuple[Any, ...]:
    primary = group["primary"]
    text = normalize_text(primary.get("text"))
    feature_scope = group["feature_scope"]
    action_type = group["action_type"]
    feature_type = group["feature_type"]
    scope_order = {"page_action": 0, "row_action": 1, "modal_action": 2}.get(feature_scope, 9)
    feature_type_order = {"新增": 0, "查询": 1, "查看": 2, "编辑": 3, "提交": 4, "操作": 5, "删除": 6}.get(
        feature_type,
        9,
    )
    action_order = {
        "create": 0,
        "filter": 1,
        "navigate": 2,
        "switch_tab": 3,
        "inspect": 4,
        "edit": 5,
        "submit": 6,
        "confirm_modal": 7,
        "input": 8,
        "select": 9,
        "trigger": 10,
        "delete": 11,
    }.get(action_type, 20)
    template_bias = 0
    if template_feature_name and (template_feature_name in text or text in template_feature_name):
        template_bias = -1
    return (
        template_bias,
        scope_order,
        feature_type_order,
        action_order,
        -group["occurrence_count"],
        text.lower(),
        normalize_text(primary.get("container_label")).lower(),
        normalize_text(primary.get("context_text")).lower(),
        locator_anchor(primary.get("locator")).lower(),
    )


def _candidate_priority(candidate: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        0 if candidate.get("is_primary_action") else 1,
        0 if normalize_text(candidate.get("href")) else 1,
        0 if normalize_text(candidate.get("container_label")) else 1,
        len(locator_anchor(candidate.get("locator"))),
        normalize_text(candidate.get("locator")).lower(),
    )


def _infer_feature_type(text: str, *, action_type: str) -> str:
    if action_type == "create" or any(keyword in text for keyword in ("新增", "申请", "创建")):
        return "新增"
    if action_type in {"filter", "input", "select"} or any(keyword in text for keyword in ("查询", "搜索", "筛选")):
        return "查询"
    if action_type in {"inspect", "navigate"} or any(keyword in text for keyword in ("详情", "查看")):
        return "查看"
    if action_type == "edit" or any(keyword in text for keyword in ("编辑", "修改")):
        return "编辑"
    if action_type in {"submit", "confirm_modal"} or any(keyword in text for keyword in ("提交", "保存", "确认", "确定")):
        return "提交"
    if action_type == "delete" or any(keyword in text for keyword in ("删除", "移除", "作废")):
        return "删除"
    return "操作"


def _infer_feature_scope(candidate: dict[str, Any]) -> str:
    container_type = normalize_text(candidate.get("container_type")).lower()
    if container_type == "modal":
        return "modal_action"
    if container_type == "table_row":
        return "row_action"
    if container_type == "navigation":
        return "page_action"
    return "page_action"


def _infer_action_type(candidate: dict[str, Any], text: str, *, feature_scope: str) -> str:
    href = normalize_text(candidate.get("href"))
    tag = normalize_text(candidate.get("tag")).lower()
    role = normalize_text(candidate.get("role")).lower()
    if href and not href.startswith("#") and not href.startswith("javascript:"):
        return "navigate"
    if role == "tab":
        return "switch_tab"
    if tag in {"input", "textarea"}:
        return "input"
    if tag == "select":
        return "select"
    if any(keyword in text for keyword in ("新增", "添加", "申请", "创建", "新建")):
        return "create"
    if any(keyword in text for keyword in ("查询", "搜索", "筛选", "重置")):
        return "filter"
    if any(keyword in text for keyword in ("详情", "查看")):
        return "inspect"
    if any(keyword in text for keyword in ("编辑", "修改")):
        return "edit"
    if any(keyword in text for keyword in ("删除", "移除", "作废")):
        return "delete"
    if any(keyword in text for keyword in ("提交", "保存", "完成")):
        return "submit"
    if any(keyword in text for keyword in ("确定", "确认")) and feature_scope == "modal_action":
        return "confirm_modal"
    return "trigger"


def _infer_navigation_action_type(candidate: dict[str, Any]) -> str:
    role = normalize_text(candidate.get("role")).lower()
    if role == "tab":
        return "switch_tab"
    return "navigate"


def _is_navigation_target(
    url: str,
    *,
    base_origin: str,
    candidate_text: str,
    locator: str | None,
    config: LiveDiscoveryConfig,
) -> tuple[bool, str]:
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return False, "non_http_scheme"
    if parsed.netloc != base_origin:
        return False, "cross_origin"

    text_lower = candidate_text.lower()
    if any(keyword.lower() in text_lower for keyword in config.skip_text_keywords):
        return False, "skip_text_keyword"
    if config.include_text_keywords and not any(keyword.lower() in text_lower for keyword in config.include_text_keywords):
        return False, "include_text_keyword_miss"

    canonical = canonicalize_url(url).lower()
    path = parsed.path.lower()
    if "/profile/upload/" in path:
        return False, "blocked_upload_path"
    if any(keyword.lower() in path for keyword in config.skip_path_keywords):
        return False, "skip_path_keyword"
    if any(
        path.endswith(ext)
        for ext in (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".doc", ".docx", ".xls", ".xlsx", ".zip")
    ):
        return False, "binary_asset"
    if config.include_url_keywords:
        include_hit = any(keyword.lower() in canonical for keyword in config.include_url_keywords)
        if not include_hit:
            return False, "include_url_keyword_miss"
    if any(keyword.lower() in canonical for keyword in config.exclude_url_keywords):
        return False, "exclude_url_keyword"
    locator_text = normalize_text(locator).lower()
    if config.include_locator_keywords and not any(keyword.lower() in locator_text for keyword in config.include_locator_keywords):
        return False, "include_locator_keyword_miss"
    if any(keyword.lower() in locator_text for keyword in config.exclude_locator_keywords):
        return False, "exclude_locator_keyword"
    return True, "allowed"


def _should_skip_action_candidate(candidate: dict[str, Any], *, config: LiveDiscoveryConfig) -> tuple[bool, str]:
    text = normalize_text(candidate.get("text"))
    normalized = text.lower()
    if any(keyword.lower() in normalized for keyword in config.skip_text_keywords):
        return True, "skip_text_keyword"
    if config.include_text_keywords and not any(keyword.lower() in normalized for keyword in config.include_text_keywords):
        return True, "include_text_keyword_miss"
    locator_text = normalize_text(candidate.get("locator")).lower()
    if any(keyword.lower() in locator_text for keyword in config.exclude_locator_keywords):
        return True, "exclude_locator_keyword"
    if config.include_locator_keywords and not any(keyword.lower() in locator_text for keyword in config.include_locator_keywords):
        return True, "include_locator_keyword_miss"
    return False, "allowed"


def _candidate_review_priority(candidate: dict[str, Any], *, feature_scope: str, action_type: str) -> str:
    if feature_scope in {"modal_action", "row_action"}:
        return "high"
    if action_type in {"delete", "submit", "confirm_modal"}:
        return "high"
    if candidate.get("is_primary_action"):
        return "high"
    if action_type in {"create", "edit", "filter"}:
        return "medium"
    return "normal"


def _merge_review_priority(current: str, incoming: str) -> str:
    order = {"high": 0, "medium": 1, "normal": 2}
    return incoming if order.get(incoming, 9) < order.get(current, 9) else current


def _build_review_queue(
    *,
    page_entries: list[PageEntryRecord],
    feature_points: list[FeaturePointRecord],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for page_entry in page_entries:
        if page_entry.discovery_depth <= 0 and page_entry.page_type == "page_entry":
            continue
        queue.append(
            {
                "record_type": "page_entry",
                "record_id": page_entry.page_entry_id,
                "priority": "high" if page_entry.discovery_depth > 0 else "medium",
                "reason": "deep_linked_entry" if page_entry.discovery_depth > 0 else "entry_review",
                "fields": {
                    "name": page_entry.name,
                    "url": page_entry.url,
                    "page_type": page_entry.page_type,
                    "discovery_depth": page_entry.discovery_depth,
                    "stable_key": page_entry.stable_key,
                },
            }
        )
    for feature_point in feature_points:
        priority = str(feature_point.evidence.get("review_priority") or "normal")
        if priority == "normal" and feature_point.discovery_depth == 0:
            continue
        queue.append(
            {
                "record_type": "feature_point",
                "record_id": feature_point.feature_point_id,
                "priority": priority,
                "reason": feature_point.feature_scope,
                "fields": {
                    "name": feature_point.name,
                    "page_entry_id": feature_point.page_entry_id,
                    "feature_scope": feature_point.feature_scope,
                    "action_type": feature_point.action_type,
                    "discovery_depth": feature_point.discovery_depth,
                    "stable_key": feature_point.stable_key,
                },
            }
        )
    return sorted(
        queue,
        key=lambda item: (
            {"high": 0, "medium": 1, "normal": 2}.get(str(item.get("priority")), 9),
            str(item.get("record_type")),
            str(item.get("record_id")),
        ),
    )
