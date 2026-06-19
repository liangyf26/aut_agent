from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, async_playwright

from .models import DiscoveryResult, FeaturePointRecord, PageEntryRecord, ScreenshotRecord, utc_now_iso


def _slug(value: str) -> str:
    normalized = []
    for ch in value.strip().lower():
        if ch.isalnum():
            normalized.append(ch)
        else:
            normalized.append("_")
    result = "".join(normalized).strip("_")
    return result or "item"


@dataclass(frozen=True)
class LiveDiscoveryConfig:
    max_nav_targets: int = 5
    max_feature_points: int = 8
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
    page_entry_id = f"{template_name}__page_entry__{_slug(entry_name)}"
    landing_dir = Path(config.screenshot_root) / _slug(entry_name)
    landing_path = landing_dir / "landing.png"
    await page.screenshot(path=str(screenshots_dir / landing_path), full_page=True)

    scan = await _scan_live_page(page)

    page_entries: list[PageEntryRecord] = [
        PageEntryRecord(
            page_entry_id=page_entry_id,
            name=entry_name,
            url=entry_url,
            template_name=template_name,
            source="playwright.live_page",
            confidence="live_page_loaded",
            execution_path=execution_path,
            evidence={
                "title": await page.title(),
                "baseline_reference_record_id": (baseline or {}).get("reference_success_record_id"),
            },
        )
    ]
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
            notes=[f"url={entry_url}"],
        )
    ]

    page_entries.extend(
        await _discover_same_origin_targets(
            page,
            page_entry_id=page_entry_id,
            template_name=template_name,
            base_url=entry_url,
            nav_candidates=scan["nav_candidates"],
            screenshots_dir=screenshots_dir,
            screenshot_root=landing_dir,
            max_targets=config.max_nav_targets,
            screenshot_records=screenshot_records,
        )
    )

    feature_points = await _build_feature_points(
        page,
        page_entry_id=page_entry_id,
        template_name=template_name,
        template=template,
        action_candidates=scan["action_candidates"],
        screenshots_dir=screenshots_dir,
        landing_dir=landing_dir,
        max_feature_points=config.max_feature_points,
        screenshot_records=screenshot_records,
    )

    return DiscoveryResult(
        template_name=template_name,
        generated_at=utc_now_iso(),
        strategy="playwright_controlled_live",
        page_entries=page_entries,
        feature_points=feature_points,
        screenshot_records=screenshot_records,
        stats={
            "page_entry_count": len(page_entries),
            "feature_point_count": len(feature_points),
            "screenshot_record_count": len(screenshot_records),
            "nav_candidate_count": len(scan["nav_candidates"]),
            "action_candidate_count": len(scan["action_candidates"]),
        },
        notes=[
            "发现结果来自真实页面受控遍历。",
            "同源链接使用独立页面探测，避免扰动当前已登录主页面。",
            "无 href 的菜单、标签和按钮先作为可见功能点记录，不强制点击。",
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


async def _scan_live_page(page: Page) -> dict[str, list[dict[str, Any]]]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
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
              if (current.classList && current.classList.length) {
                selector += '.' + Array.from(current.classList).slice(0, 2).join('.');
              }
              const parent = current.parentElement;
              if (parent) {
                const siblings = Array.from(parent.children).filter(x => x.tagName === current.tagName);
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
          function unique(items, keyFn) {
            const seen = new Set();
            return items.filter(item => {
              const key = keyFn(item);
              if (!key || seen.has(key)) return false;
              seen.add(key);
              return true;
            });
          }

          const navRaw = Array.from(document.querySelectorAll('a[href], [role="menuitem"], [role="tab"], .el-menu-item, .el-tabs__item'))
            .filter(visible)
            .map((el, index) => ({
              discovery_id: discoveryId('nav', el, index),
              text: text(el),
              href: el.getAttribute('href') || '',
              role: el.getAttribute('role') || '',
              tag: el.tagName.toLowerCase(),
              locator: cssPath(el),
            }));

          const actionRaw = Array.from(document.querySelectorAll('button, [role="button"], a[href], [role="tab"], input, select, textarea, .el-button'))
            .filter(visible)
            .map((el, index) => ({
              discovery_id: discoveryId('action', el, index),
              text: text(el),
              href: el.getAttribute('href') || '',
              role: el.getAttribute('role') || '',
              tag: el.tagName.toLowerCase(),
              type: el.getAttribute('type') || '',
              locator: cssPath(el),
            }));

          return {
            nav_candidates: unique(navRaw, item => `${item.text}|${item.href}|${item.locator}`).filter(item => item.text).slice(0, 24),
            action_candidates: unique(actionRaw, item => `${item.text}|${item.href}|${item.locator}|${item.type}`).filter(item => item.text).slice(0, 40),
          };
        }
        """
    )


async def _discover_same_origin_targets(
    page: Page,
    *,
    page_entry_id: str,
    template_name: str,
    base_url: str,
    nav_candidates: list[dict[str, Any]],
    screenshots_dir: Path,
    screenshot_root: Path,
    max_targets: int,
    screenshot_records: list[ScreenshotRecord],
) -> list[PageEntryRecord]:
    base_origin = urlparse(base_url).netloc
    context = page.context
    discovered_urls = {base_url}
    results: list[PageEntryRecord] = []
    traversed = 0

    for candidate in nav_candidates:
        href = (candidate.get("href") or "").strip()
        text = (candidate.get("text") or "").strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        absolute_url = urljoin(base_url, href)
        if not _is_navigation_target(absolute_url, base_origin) or absolute_url in discovered_urls:
            continue
        traversed += 1
        if traversed > max_targets:
            break

        probe_page = await context.new_page()
        try:
            await probe_page.goto(absolute_url, wait_until="domcontentloaded", timeout=15000)
            await probe_page.wait_for_timeout(1200)
            page_name = text or (await probe_page.title()) or absolute_url
            page_id = f"{template_name}__page_entry__{_slug(page_name)}"
            screenshot_path = screenshot_root / f"{_slug(page_name)}.png"
            await probe_page.screenshot(path=str(screenshots_dir / screenshot_path), full_page=True)
            results.append(
                PageEntryRecord(
                    page_entry_id=page_id,
                    name=page_name,
                    url=probe_page.url,
                    template_name=template_name,
                    source="playwright.same_origin_link",
                    confidence="live_traversed",
                    execution_path="controlled_navigation",
                    evidence={
                        "from_page_entry_id": page_entry_id,
                        "locator": candidate.get("locator"),
                        "role": candidate.get("role"),
                    },
                )
            )
            screenshot_records.append(
                ScreenshotRecord(
                    screenshot_id=f"{page_id}__landing",
                    page_entry_id=page_id,
                    feature_point_id=None,
                    stage="linked_page_landing",
                    purpose="capture linked page discovered via controlled traversal",
                    status="captured",
                    relative_path=str(screenshot_path).replace("\\", "/"),
                    source="playwright.same_origin_link",
                    notes=[f"from={page_entry_id}", f"href={absolute_url}"],
                )
            )
            discovered_urls.add(absolute_url)
        except Exception as exc:
            screenshot_records.append(
                ScreenshotRecord(
                    screenshot_id=f"{page_entry_id}__failed_probe__{traversed}",
                    page_entry_id=page_entry_id,
                    feature_point_id=None,
                    stage="linked_page_probe",
                    purpose="record failed controlled traversal target",
                    status="failed",
                    relative_path=None,
                    source="playwright.same_origin_link",
                    notes=[f"href={absolute_url}", f"error={type(exc).__name__}: {exc}"],
                )
            )
        finally:
            await probe_page.close()

    return results


async def _build_feature_points(
    page: Page,
    *,
    page_entry_id: str,
    template_name: str,
    template: dict[str, Any],
    action_candidates: list[dict[str, Any]],
    screenshots_dir: Path,
    landing_dir: Path,
    max_feature_points: int,
    screenshot_records: list[ScreenshotRecord],
) -> list[FeaturePointRecord]:
    results: list[FeaturePointRecord] = []
    template_feature_name = template.get("feature_point", {}).get("name", "")
    ranked_candidates = _rank_action_candidates(action_candidates, template_feature_name)
    grouped_candidates = _group_action_candidates(ranked_candidates)

    for index, group in enumerate(grouped_candidates[:max_feature_points], start=1):
        candidate = group["primary"]
        feature_name = candidate.get("text") or f"action_{index}"
        feature_id = f"{template_name}__feature_point__{_slug(feature_name)}"
        feature_type = _infer_feature_type(feature_name)
        results.append(
            FeaturePointRecord(
                feature_point_id=feature_id,
                page_entry_id=page_entry_id,
                name=feature_name,
                feature_type=feature_type,
                template_name=template_name,
                source="playwright.visible_action",
                confidence="live_visible",
                execution_path="controlled_live_scan",
                evidence={
                    "locator": candidate.get("locator"),
                    "tag": candidate.get("tag"),
                    "role": candidate.get("role"),
                    "occurrence_count": group["occurrence_count"],
                    "locator_samples": group["locator_samples"],
                },
            )
        )

        if index > 3:
            continue
        discovery_id = candidate.get("discovery_id")
        if not discovery_id:
            continue
        locator = page.locator(f"[data-stage2-discovery-id='{discovery_id}']").first
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
            screenshot_path = landing_dir / f"{_slug(feature_name)}.png"
            await locator.screenshot(path=str(screenshots_dir / screenshot_path))
            screenshot_records.append(
                ScreenshotRecord(
                    screenshot_id=f"{feature_id}__feature",
                    page_entry_id=page_entry_id,
                    feature_point_id=feature_id,
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
                    screenshot_id=f"{feature_id}__feature",
                    page_entry_id=page_entry_id,
                    feature_point_id=feature_id,
                    stage="feature_point_focus",
                    purpose="record feature point capture failure",
                    status="failed",
                    relative_path=None,
                    source="playwright.visible_action",
                    notes=[f"error={type(exc).__name__}: {exc}"],
                )
            )

    return results


def _group_action_candidates(action_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    for candidate in action_candidates:
        name = (candidate.get("text") or "").strip()
        if not name:
            continue
        feature_type = _infer_feature_type(name)
        key = f"{name}|{feature_type}"
        if key not in grouped:
            grouped[key] = {
                "primary": candidate,
                "occurrence_count": 0,
                "locator_samples": [],
            }
            ordered_keys.append(key)
        grouped[key]["occurrence_count"] += 1
        locator = candidate.get("locator")
        if locator and locator not in grouped[key]["locator_samples"]:
            grouped[key]["locator_samples"].append(locator)
    return [grouped[key] for key in ordered_keys]


def _rank_action_candidates(action_candidates: list[dict[str, Any]], template_feature_name: str) -> list[dict[str, Any]]:
    def score(item: dict[str, Any]) -> tuple[int, int, str]:
        text = (item.get("text") or "").strip()
        role = (item.get("role") or "").strip()
        tag = (item.get("tag") or "").strip()
        primary = 0
        if template_feature_name and (template_feature_name in text or text in template_feature_name):
            primary -= 5
        if any(keyword in text for keyword in ("申请", "新增", "查询", "筛选", "搜索", "提交", "详情")):
            primary -= 3
        if role in {"button", "tab"} or tag in {"button", "a"}:
            primary -= 1
        return (primary, len(text), text)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in action_candidates:
        text = (candidate.get("text") or "").strip()
        if not text or len(text) > 32:
            continue
        key = f"{text}|{candidate.get('locator', '')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return sorted(deduped, key=score)


def _infer_feature_type(text: str) -> str:
    if any(keyword in text for keyword in ("新增", "申请", "创建")):
        return "新增"
    if any(keyword in text for keyword in ("查询", "搜索", "筛选")):
        return "查询"
    if any(keyword in text for keyword in ("详情", "查看")):
        return "查看"
    if any(keyword in text for keyword in ("提交", "保存")):
        return "提交"
    return "操作"


def _is_navigation_target(url: str, base_origin: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return False
    if parsed.netloc != base_origin:
        return False
    path = parsed.path.lower()
    if "/profile/upload/" in path:
        return False
    if any(path.endswith(ext) for ext in (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".doc", ".docx", ".xls", ".xlsx", ".zip")):
        return False
    return True
