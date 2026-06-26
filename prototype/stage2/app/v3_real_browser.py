from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from prototype.stage2.app.v3_orchestrator import V3RunConfig


V3_SCHEMA_VERSION = "stage2_v3_run.v1"
LOW_RISK_ONLY_POLICY = "low_risk_only"
TEST_ENV_FULL_ACCESS_POLICY = "test_env_full_access"
SIDE_EFFECT_ACTION_TYPES = {"submit", "save", "approve", "delete", "create", "edit"}
DEFAULT_SIDE_EFFECT_TOTAL_LIMIT = 4
DEFAULT_SIDE_EFFECT_PER_TYPE_LIMIT = 1


def build_menu_discovery_artifacts(
    *,
    start_url: str,
    menu_candidates: list[dict[str, Any]],
    traversal_events: list[dict[str, Any]],
    screenshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the stable first-round menu discovery artifact contract."""

    event_by_menu_id = {
        _text(event.get("menu_id")): event
        for event in traversal_events
        if isinstance(event, dict) and _text(event.get("menu_id"))
    }
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    raw_entries: list[dict[str, Any]] = []
    for index, candidate in enumerate(menu_candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        menu_id = _text(candidate.get("discovery_id")) or _text(candidate.get("menu_id")) or f"menu_{index:03d}"
        parent_id = _text(candidate.get("parent_id"))
        raw_entry = {
            **candidate,
            "menu_id": menu_id,
            "parent_id": parent_id or None,
        }
        raw_entries.append(raw_entry)
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(raw_entry)

    entries: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(raw_entries, start=1):
        menu_id = raw_entry["menu_id"]
        event = event_by_menu_id.get(menu_id, {})
        status = _menu_entry_status(raw_entry, event, children_by_parent.get(menu_id, []))
        failure_reason = _text(event.get("failure_reason")) or _text(raw_entry.get("failure_reason"))
        screenshot_refs = _menu_screenshot_refs(raw_entry, event)
        href = _text(raw_entry.get("href") or raw_entry.get("route_hint") or raw_entry.get("url"))
        menu_path = _menu_path(raw_entry, raw_entries)
        expandable = bool(raw_entry.get("expandable"))
        entry = {
            "menu_id": menu_id,
            "text": _text(raw_entry.get("text") or raw_entry.get("name")) or f"菜单项 {index}",
            "level": _int_or_default(raw_entry.get("level"), len(menu_path) or 1),
            "parent_id": raw_entry.get("parent_id"),
            "menu_path": menu_path,
            "is_leaf": bool(raw_entry.get("is_leaf"))
            or (not expandable and status not in {"permission_blocked", "expansion_failed"}),
            "expandable": expandable,
            "route_hint": href,
            "locator_candidates": _locator_candidates(raw_entry),
            "source": _text(raw_entry.get("source")) or "playwright.menu_discovery",
            "screenshot_refs": screenshot_refs,
            "status": status,
            "failure_reason": failure_reason or None,
        }
        entries.append(entry)

    entries = _dedupe_menu_entries(entries)
    entry_by_id = {entry["menu_id"]: entry for entry in entries}
    children_by_parent = _children_by_parent_from_entries(entries)
    root_entries = [
        entry
        for entry in entries
        if not entry.get("parent_id") or entry.get("parent_id") not in entry_by_id
    ]
    tree_nodes = [_tree_node(entry, entry_by_id, children_by_parent) for entry in root_entries]
    has_failure = any(
        entry["status"] in {"permission_blocked", "expansion_failed"}
        or bool(entry.get("failure_reason"))
        for entry in entries
    )
    screenshots_index = _menu_screenshots_index(screenshots)
    return {
        "menu_tree": {
            "schema_version": "stage2_menu_tree.v1",
            "status": "incomplete" if has_failure else "completed",
            "start_url": start_url,
            "root_count": len(root_entries),
            "entry_count": len(entries),
            "leaf_count": sum(1 for entry in entries if entry["is_leaf"]),
            "nodes": tree_nodes,
        },
        "menu_entries": entries,
        "menu_traversal_log": [event for event in traversal_events if isinstance(event, dict)],
        "screenshots_index": screenshots_index,
    }


async def collect_real_browser_artifacts(config: V3RunConfig, run_dir: Path) -> dict[str, Any]:
    """Collect first-round menu evidence from a connected real browser."""

    if not config.cdp_url:
        return _blocked("cdp_required", "真实浏览器模式需要 CDP 地址，例如 http://localhost:9222。")
    try:
        return await _collect_with_playwright_menu_discovery(config, run_dir)
    except ImportError as exc:
        raw_payload = await _collect_with_raw_cdp(config, run_dir)
        raw_payload["executor_stack"] = {
            "playwright": {
                "status": "unavailable",
                "failure_reason": f"{type(exc).__name__}: {exc}",
            },
            "browser_use": {
                "status": "not_invoked",
                "selected_model": config.model_name,
                "reason": "first-round deterministic menu traversal can run with Playwright; Browser-use is reserved for semantic recovery.",
            },
            "raw_cdp": {"status": "diagnostic_fallback"},
        }
        raw_payload.setdefault(
            "menu_tree",
            {
                "schema_version": "stage2_menu_tree.v1",
                "status": "not_available",
                "root_count": 0,
                "nodes": [],
                "notes": ["Playwright 不可用，raw CDP 仅作为诊断兜底，不能计入菜单覆盖。"],
            },
        )
        raw_payload.setdefault("menu_entries", [])
        raw_payload.setdefault("menu_traversal_log", [])
        return raw_payload
    except Exception as exc:
        return _blocked(
            "playwright_menu_discovery_failed",
            f"Playwright 菜单遍历失败：{type(exc).__name__}: {exc}",
            cdp_url=config.cdp_url,
        )


async def _collect_with_playwright_menu_discovery(
    config: V3RunConfig,
    run_dir: Path,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    screenshots_dir = run_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(config.cdp_url)
        try:
            page = await _resolve_playwright_target_page(browser, config.start_url)
            browser_targets = _list_cdp_targets(config.cdp_url)
            menu_bundle = await _discover_menu_with_playwright(page, screenshots_dir, config)
            if _should_block_for_login_text(
                await _safe_playwright_body_text(page),
                menu_bundle,
            ):
                return {
                    "schema_version": V3_SCHEMA_VERSION,
                    "status": "blocked",
                    "failure_reason": "login_required",
                    "message": "目标页面看起来仍在登录或接管页，请先在浏览器中完成人工登录后重试。",
                    "preflight_result": _preflight(False, "login_required", config.cdp_url),
                    "browser_targets": browser_targets,
                    "menu_tree": menu_bundle["menu_tree"],
                    "menu_entries": menu_bundle["menu_entries"],
                    "menu_traversal_log": menu_bundle["menu_traversal_log"],
                    "pages": [],
                    "features": [],
                    "screenshots_index": menu_bundle["screenshots_index"],
                }
            preflight = _preflight(True, "ok", config.cdp_url)
            preflight["checks"]["playwright"] = {"ok": True}
            preflight["browser_target_count"] = len(browser_targets)
            page_bundle = await _explore_menu_leaf_pages_with_playwright(
                page,
                menu_bundle["menu_entries"],
                screenshots_dir,
                config,
            )
            screenshots_index = _merge_screenshot_indexes(
                menu_bundle["screenshots_index"],
                page_bundle["screenshots_index"],
            )
            return {
                "schema_version": V3_SCHEMA_VERSION,
                "status": "completed",
                "failure_reason": None,
                "message": "已通过 Playwright 连接真实浏览器完成第一轮菜单入口遍历。",
                "source": "playwright.menu_discovery",
                "preflight_result": preflight,
                "executor_stack": {
                    "playwright": {"status": "used", "cdp_url": config.cdp_url},
                    "browser_use": {
                        "status": "available_for_semantic_recovery",
                        "selected_model": config.model_name,
                        "reason": "第一轮先由 Playwright 确定性展开菜单；语义歧义和目标追踪由 Browser-use/模型恢复层接管。",
                    },
                    "raw_cdp": {"status": "diagnostic_only", "browser_target_count": len(browser_targets)},
                },
                "browser_targets": browser_targets,
                "menu_tree": menu_bundle["menu_tree"],
                "menu_entries": menu_bundle["menu_entries"],
                "menu_traversal_log": menu_bundle["menu_traversal_log"],
                "page_exploration_log": page_bundle["page_exploration_log"],
                "pages": page_bundle["pages"],
                "features": page_bundle["features"],
                "screenshots_index": screenshots_index,
            }
        finally:
            await browser.close()


async def _resolve_playwright_target_page(browser: Any, start_url: str) -> Any:
    pages: list[Any] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    if not pages:
        raise RuntimeError("未发现可用页面，无法执行 Playwright 菜单遍历")
    page = next((item for item in pages if start_url and start_url in item.url), pages[0])
    await page.bring_to_front()
    await page.wait_for_load_state("domcontentloaded")
    if start_url and start_url not in page.url:
        await page.goto(start_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)
    return page


def _list_cdp_targets(cdp_url: str) -> list[dict[str, Any]]:
    base = cdp_url.rstrip("/") + "/"
    try:
        targets = _http_json(urljoin(base, "json/list"))
    except Exception:
        return []
    if not isinstance(targets, list):
        return []
    return [
        {
            "id": _text(item.get("id")),
            "type": _text(item.get("type")),
            "title": _text(item.get("title")),
            "url": _text(item.get("url")),
        }
        for item in targets
        if isinstance(item, dict)
    ]


async def _discover_menu_with_playwright(
    page: Any,
    screenshots_dir: Path,
    config: V3RunConfig,
) -> dict[str, Any]:
    screenshots: list[dict[str, Any]] = [
        await _capture_playwright_screenshot(
            page,
            screenshots_dir,
            "menu_initial",
            "第一轮菜单初始可见状态",
        )
    ]
    candidates = await _scan_menu_candidates_playwright(page)
    for candidate in candidates:
        candidate.setdefault("screenshot_id", "menu_initial")

    traversal_events: list[dict[str, Any]] = []
    seen_keys = {_menu_candidate_key(item) for item in candidates}
    seen_ids = {
        _text(item.get("discovery_id"))
        for item in candidates
        if _text(item.get("discovery_id"))
    }
    expandable_roots = [
        item
        for item in candidates
        if item.get("expandable") and not item.get("parent_id")
    ][: max(1, int(config.max_pages or 1))]
    expandable_queue = list(expandable_roots)
    expanded_ids: set[str] = set()
    max_expand_attempts = max(1, int(config.max_pages or 1)) * 4
    while expandable_queue and len(expanded_ids) < max_expand_attempts:
        candidate = expandable_queue.pop(0)
        menu_id = _text(candidate.get("discovery_id"))
        if not menu_id or menu_id in expanded_ids:
            continue
        expanded_ids.add(menu_id)
        if candidate.get("disabled"):
            traversal_events.append(
                {
                    "event": "expand",
                    "menu_id": menu_id,
                    "status": "permission_blocked",
                    "failure_reason": "permission_denied",
                }
            )
            continue
        try:
            locator = page.locator(f"[data-stage2-menu-id='{menu_id}']").first
            if callable(locator):
                locator = locator()
            await locator.scroll_into_view_if_needed(timeout=1000)
            await locator.click(timeout=2500)
            await page.wait_for_timeout(600)
            screenshot_id = f"{menu_id}_after_expand"
            screenshots.append(
                await _capture_playwright_screenshot(
                    page,
                    screenshots_dir,
                    screenshot_id,
                    f"展开菜单：{candidate.get('text')}",
                )
            )
            after_candidates = await _scan_menu_candidates_playwright(
                page,
                parent_candidate=candidate,
            )
            new_children: list[dict[str, Any]] = []
            for item in after_candidates:
                item_id = _text(item.get("discovery_id"))
                if item_id == menu_id:
                    continue
                key = _menu_candidate_key(item)
                if key in seen_keys:
                    continue
                if item_id in seen_ids:
                    item["discovery_id"] = _next_menu_discovery_id(seen_ids)
                    item_id = _text(item.get("discovery_id"))
                item["parent_id"] = menu_id
                item["level"] = max(
                    _int_or_default(candidate.get("level"), 1) + 1,
                    _int_or_default(item.get("level"), 2),
                )
                item["screenshot_id"] = screenshot_id
                seen_keys.add(key)
                if item_id:
                    seen_ids.add(item_id)
                new_children.append(item)
                if item.get("expandable"):
                    expandable_queue.append(item)
            candidates.extend(new_children)
            traversal_events.append(
                {
                    "event": "expand",
                    "menu_id": menu_id,
                    "status": "success" if new_children else "failed",
                    "failure_reason": None if new_children else "no_child_menu_appeared",
                    "screenshot_ref": screenshot_id,
                    "new_child_count": len(new_children),
                }
            )
        except Exception as exc:
            traversal_events.append(
                {
                    "event": "expand",
                    "menu_id": menu_id,
                    "status": "failed",
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                }
            )

    return build_menu_discovery_artifacts(
        start_url=page.url,
        menu_candidates=candidates,
        traversal_events=traversal_events,
        screenshots=screenshots,
    )


async def _explore_menu_leaf_pages_with_playwright(
    page: Any,
    menu_entries: list[dict[str, Any]],
    screenshots_dir: Path,
    config: V3RunConfig,
) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    screenshots: list[dict[str, Any]] = []
    leaf_entries = [
        entry
        for entry in menu_entries
        if entry.get("is_leaf") and _text(entry.get("status")) not in {"permission_blocked", "failed"}
    ][: max(1, int(config.max_pages or 1))]
    for index, entry in enumerate(leaf_entries, start=1):
        menu_id = _text(entry.get("menu_id")) or f"menu_leaf_{index:03d}"
        page_id = f"menu_page_{index:03d}"
        try:
            locator = page.locator(f"[data-stage2-menu-id='{menu_id}']").first
            if callable(locator):
                locator = locator()
            await locator.scroll_into_view_if_needed(timeout=1000)
            await locator.click(timeout=2500)
            await page.wait_for_load_state("domcontentloaded", timeout=4000)
            await page.wait_for_timeout(500)
            snapshot = await page.evaluate(
                _dom_collection_expression(),
                {"maxPages": 1, "maxFeatures": int(config.max_features_per_page or 1)},
            )
            screenshot_id = f"{page_id}_visible"
            screenshots.append(
                await _capture_playwright_screenshot(
                    page,
                    screenshots_dir,
                    screenshot_id,
                    f"菜单页面：{entry.get('text')}",
                )
            )
            current_url = _text(snapshot.get("url")) or page.url or config.start_url
            title = _text(snapshot.get("title")) or _text(entry.get("text")) or f"页面入口 {index}"
            page_type = _infer_page_type(title, current_url)
            pages.append(
                {
                    "page_id": page_id,
                    "page_entry_id": page_id,
                    "menu_id": menu_id,
                    "name": _text(entry.get("text")) or title,
                    "url": current_url,
                    "menu_path": entry.get("menu_path", []),
                    "page_type": page_type,
                    "semantic_page_type": page_type,
                    "discovery_depth": 1,
                    "status": "reachable",
                    "source": "playwright.menu_page_exploration",
                    "confidence": "observed",
                    "screenshot_refs": [screenshot_id],
                    "failure_reason": None,
                }
            )
            logs.append(
                {
                    "event": "enter_menu_leaf",
                    "menu_id": menu_id,
                    "menu_path": entry.get("menu_path", []),
                    "status": "reachable",
                    "page_entry_id": page_id,
                    "screenshot_ref": screenshot_id,
                    "url": current_url,
                }
            )
            features.extend(
                _build_page_features_from_snapshot(
                    page_id,
                    snapshot,
                    int(config.max_features_per_page or 1),
                    screenshot_id,
                    start_index=len(features) + 1,
                )
            )
        except Exception as exc:
            route_hint = _text(entry.get("route_hint"))
            url = urljoin(config.start_url, route_hint) if route_hint else config.start_url
            pages.append(
                {
                    "page_id": page_id,
                    "page_entry_id": page_id,
                    "menu_id": menu_id,
                    "name": _text(entry.get("text")) or f"页面入口 {index}",
                    "url": url,
                    "menu_path": entry.get("menu_path", []),
                    "page_type": _infer_page_type(_text(entry.get("text")), url),
                    "semantic_page_type": _infer_page_type(_text(entry.get("text")), url),
                    "discovery_depth": 1,
                    "status": "unreachable",
                    "source": "playwright.menu_page_exploration",
                    "confidence": "failed",
                    "screenshot_refs": [],
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                }
            )
            logs.append(
                {
                    "event": "enter_menu_leaf",
                    "menu_id": menu_id,
                    "menu_path": entry.get("menu_path", []),
                    "status": "failed",
                    "page_entry_id": page_id,
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "pages": pages,
        "features": features,
        "page_exploration_log": logs,
        "screenshots_index": _menu_screenshots_index(
            [{**item, "stage": "page_exploration", "source": "playwright.menu_page_exploration"} for item in screenshots]
        ),
    }


def _build_page_features_from_snapshot(
    page_id: str,
    snapshot: dict[str, Any],
    max_features_per_page: int,
    screenshot_id: str,
    *,
    start_index: int,
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for offset, control in enumerate(snapshot.get("controls", [])[: max(1, max_features_per_page)], start=0):
        text = _text(control.get("text")) or f"可见控件 {offset + 1}"
        feature_type = _infer_feature_type(text, _text(control.get("tag")), _text(control.get("type")))
        feature_id = f"feature_{start_index + offset:03d}"
        risk_level = _risk_level(feature_type)
        features.append(
            {
                "feature_id": feature_id,
                "feature_point_id": feature_id,
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": text,
                "feature_type": feature_type,
                "risk_level": risk_level,
                "auto_verifiable": True,
                "verification_strategy": "side_effect_policy_gate" if risk_level == "high" else "playwright_visible_control",
                "source": "playwright.light_interaction",
                "confidence": "observed",
                "review_status": "pending" if risk_level == "high" else "auto_included",
                "evidence": {
                    "screenshot_id": screenshot_id,
                    "tag": control.get("tag"),
                    "type": control.get("type"),
                    "candidate_index": control.get("candidate_index"),
                },
            }
        )
    if not features:
        feature_id = f"feature_{start_index:03d}"
        features.append(
            {
                "feature_id": feature_id,
                "feature_point_id": feature_id,
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": "页面可见性验证",
                "feature_type": "view",
                "risk_level": "low",
                "auto_verifiable": True,
                "verification_strategy": "playwright_page_visible",
                "source": "playwright.menu_page_exploration",
                "confidence": "observed",
                "review_status": "auto_included",
                "evidence": {"screenshot_id": screenshot_id},
            }
        )
    return features


def _merge_screenshot_indexes(*indexes: dict[str, Any]) -> dict[str, Any]:
    screenshots: list[dict[str, Any]] = []
    for index in indexes:
        for item in index.get("screenshots", []) or index.get("items", []) or []:
            if isinstance(item, dict):
                screenshots.append(item)
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "screenshots": screenshots,
        "items": screenshots,
        "notes": ["菜单发现与页面探索截图证据。"] if screenshots else [],
    }


async def _scan_menu_candidates_playwright(
    page: Any,
    *,
    parent_candidate: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates = await page.evaluate(_menu_candidate_scan_script())
    if not isinstance(candidates, list):
        return []
    parent_level = _int_or_default((parent_candidate or {}).get("level"), 0)
    normalized = [item for item in candidates if isinstance(item, dict) and _text(item.get("text"))]
    if parent_candidate:
        parent_text = _text(parent_candidate.get("text"))
        return [
            item
            for item in normalized
            if _text(item.get("text")) != parent_text
            and _int_or_default(item.get("level"), parent_level + 1) >= parent_level
        ]
    return normalized


async def _capture_playwright_screenshot(
    page: Any,
    screenshots_dir: Path,
    screenshot_id: str,
    label: str,
) -> dict[str, Any]:
    path = screenshots_dir / f"{screenshot_id}.png"
    await page.screenshot(path=str(path), full_page=True)
    return {
        "screenshot_id": screenshot_id,
        "label": label,
        "path": str(path),
        "relative_path": str(path.relative_to(screenshots_dir.parent)),
        "stage": "menu_discovery",
        "source": "playwright.menu_discovery",
    }


def _menu_candidate_key(item: dict[str, Any]) -> str:
    return "|".join(
        [
            _text(item.get("text")),
            _text(item.get("href") or item.get("route_hint")),
            _text(item.get("locator")),
        ]
    )


def _next_menu_discovery_id(seen_ids: set[str]) -> str:
    index = len(seen_ids) + 1
    while True:
        candidate_id = f"menu_{index}"
        if candidate_id not in seen_ids:
            return candidate_id
        index += 1


async def _safe_playwright_body_text(page: Any) -> str:
    with suppress(Exception):
        return await page.locator("body").inner_text(timeout=1200)
    return ""


def _looks_like_login_text(text: str) -> bool:
    lowered = _text(text).lower()
    return any(word in lowered for word in ("login", "sign in", "登录", "登陆", "验证码", "密码"))


def _should_block_for_login_text(text: str, menu_bundle: dict[str, Any]) -> bool:
    if not _looks_like_login_text(text):
        return False
    menu_entries = menu_bundle.get("menu_entries") if isinstance(menu_bundle, dict) else []
    if not isinstance(menu_entries, list):
        return True
    return not any(_is_business_menu_evidence(entry) for entry in menu_entries if isinstance(entry, dict))


def _is_business_menu_evidence(entry: dict[str, Any]) -> bool:
    if _text(entry.get("status")) in {"permission_blocked", "expansion_failed", "failed"}:
        return False
    text = _text(entry.get("text"))
    if not text or text in {"0", "首页", "大写锁定已打开"}:
        return False
    return bool(entry.get("is_leaf") or _text(entry.get("route_hint")) or _text(entry.get("parent_id")))


def _menu_candidate_scan_script() -> str:
    return """
    () => {
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const compact = (value, max = 100) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, max);
      const text = (el) => compact(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('data-title'));
      const cssPath = (el) => {
        if (!el || !el.tagName) return '';
        if (el.id) return `#${CSS.escape(el.id)}`;
        const parts = [];
        let current = el;
        while (current && current.nodeType === 1 && parts.length < 5) {
          let selector = current.tagName.toLowerCase();
          const classes = Array.from(current.classList || [])
            .filter((name) => name && name.length < 40 && !/^is-/.test(name))
            .slice(0, 2);
          if (classes.length) selector += '.' + classes.map((name) => CSS.escape(name)).join('.');
          const parent = current.parentElement;
          if (parent) {
            const siblings = Array.from(parent.children).filter((node) => node.tagName === current.tagName);
            if (siblings.length > 1) selector += `:nth-of-type(${siblings.indexOf(current) + 1})`;
          }
          parts.unshift(selector);
          current = parent;
        }
        return parts.join(' > ');
      };
      const levelOf = (el) => {
        const aria = Number(el.getAttribute('aria-level') || 0);
        if (aria) return aria;
        const nested = el.closest('.el-sub-menu .el-menu, .ant-menu-sub, ul ul, [role="menu"] [role="menu"]');
        if (nested) return 2;
        return 1;
      };
      const isExpandable = (el) => {
        const cls = String(el.className || '').toLowerCase();
        return el.getAttribute('aria-expanded') !== null
          || cls.includes('submenu')
          || cls.includes('sub-menu')
          || Boolean(el.querySelector('ul, [role="menu"], .el-menu, .ant-menu'));
      };
      const isDisabled = (el) => {
        const cls = String(el.className || '').toLowerCase();
        return Boolean(el.disabled)
          || el.getAttribute('aria-disabled') === 'true'
          || cls.includes('disabled');
      };
      const selectors = [
        'nav a, aside a, [role="navigation"] a',
        '[role="menuitem"]',
        '.el-menu-item, .el-sub-menu__title, .ant-menu-item, .ant-menu-submenu-title',
        '[class*="menu-item"], [class*="menu__item"], [class*="submenu"], [class*="nav-item"]',
        'a[href]'
      ].join(',');
      const seen = new Set();
      return Array.from(document.querySelectorAll(selectors))
        .filter(visible)
        .map((el, index) => {
          const label = text(el);
          if (!label) return null;
          const href = el.href || el.getAttribute('href') || '';
          const key = `${label}|${href}|${cssPath(el)}`;
          if (seen.has(key)) return null;
          seen.add(key);
          window.__stage2MenuSeq = Number(window.__stage2MenuSeq || 0);
          const existing = el.getAttribute('data-stage2-menu-id');
          if (!existing) window.__stage2MenuSeq += 1;
          const id = existing || `menu_${window.__stage2MenuSeq}`;
          el.setAttribute('data-stage2-menu-id', id);
          return {
            discovery_id: id,
            text: label,
            level: levelOf(el),
            href,
            route_hint: href,
            locator: cssPath(el),
            expandable: isExpandable(el),
            disabled: isDisabled(el),
            aria_disabled: el.getAttribute('aria-disabled') || '',
            source: 'playwright.menu_discovery',
          };
        })
        .filter(Boolean)
        .slice(0, 240);
    }
    """


async def _collect_with_raw_cdp(config: V3RunConfig, run_dir: Path) -> dict[str, Any]:
    screenshots_dir = run_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ws_url = _resolve_page_websocket(config.cdp_url, config.start_url)
    session = await RawCdpSession.connect(ws_url)
    try:
        await session.call("Page.enable")
        await session.call("Runtime.enable")
        if config.start_url:
            await session.call("Page.navigate", {"url": config.start_url})
            await asyncio.sleep(1.2)
        discovered = await _collect_dom_cdp(session, config.max_pages, config.max_features_per_page)
        screenshots = [
            await _capture_cdp(session, screenshots_dir, "home_visible", "首页可见状态")
        ]
        if _looks_like_login_payload(discovered):
            return {
                "schema_version": V3_SCHEMA_VERSION,
                "status": "blocked",
                "failure_reason": "login_required",
                "message": "目标页面看起来仍在登录或接管页，请先在浏览器中完成人工登录后重试。",
                "preflight_result": _preflight(False, "login_required", config.cdp_url),
                "pages": [],
                "features": [],
                "screenshots_index": _screenshots_index(screenshots),
            }
        pages = _build_pages(
            config,
            discovered.get("url") or config.start_url,
            discovered.get("title") or config.target_name,
            discovered,
            screenshots[0]["screenshot_id"],
        )
        features = _build_features(
            pages,
            discovered,
            config.max_features_per_page,
            screenshots[0]["screenshot_id"],
        )
        side_effect_bundle = await _execute_side_effect_actions_cdp(
            session,
            screenshots_dir,
            discovered,
            config,
        )
        screenshots.extend(side_effect_bundle["screenshots"])
        return {
            "schema_version": V3_SCHEMA_VERSION,
            "status": "completed",
            "failure_reason": None,
            "message": _real_browser_completion_message(side_effect_bundle),
            "preflight_result": _preflight(True, "ok", config.cdp_url),
            "pages": pages,
            "features": features,
            "screenshots_index": _screenshots_index(screenshots),
            "side_effect_policy": side_effect_bundle["policy"],
            "side_effect_results": side_effect_bundle["results"],
            "side_effect_skipped": side_effect_bundle["skipped"],
        }
    finally:
        await session.close()


class RawCdpSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.next_id = 1

    @classmethod
    async def connect(cls, websocket_url: str) -> "RawCdpSession":
        parsed = urlparse(websocket_url)
        if parsed.scheme != "ws":
            raise ValueError(f"unsupported websocket scheme: {parsed.scheme}")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, parsed.port or 80),
            timeout=8,
        )
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=8)
        status_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise ConnectionError(status_line.decode("latin1", errors="replace"))
        return cls(reader, writer)

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        await self._send_json({"id": message_id, "method": method, "params": params or {}})
        while True:
            payload = await self._recv_json()
            if payload.get("id") == message_id:
                if "error" in payload:
                    raise RuntimeError(payload["error"])
                return payload.get("result", {})

    async def close(self) -> None:
        with suppress(Exception):
            self.writer.close()
            await self.writer.wait_closed()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126, *struct.pack("!H", length)])
        else:
            header.extend([0x80 | 127, *struct.pack("!Q", length)])
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.writer.write(bytes(header) + mask + masked)
        await self.writer.drain()

    async def _recv_json(self) -> dict[str, Any]:
        while True:
            first = await self.reader.readexactly(2)
            opcode = first[0] & 0x0F
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if first[1] & 0x80:
                mask = await self.reader.readexactly(4)
                data = await self.reader.readexactly(length)
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            else:
                data = await self.reader.readexactly(length)
            if opcode == 0x8:
                raise ConnectionError("CDP websocket closed")
            if opcode == 0x9:
                continue
            if opcode == 0x1:
                return json.loads(data.decode("utf-8"))


def _resolve_page_websocket(cdp_url: str, start_url: str) -> str:
    base = cdp_url.rstrip("/") + "/"
    try:
        targets = _http_json(urljoin(base, "json/list"))
    except URLError as exc:
        raise ConnectionError(f"CDP 地址不可访问：{cdp_url}；{exc}") from exc
    pages = [item for item in targets if item.get("type") == "page" and item.get("webSocketDebuggerUrl")]
    if pages:
        return pages[0]["webSocketDebuggerUrl"]
    if start_url:
        request = Request(urljoin(base, f"json/new?{quote(start_url, safe=':/?&=%')}"), method="PUT")
        target = _http_json(request)
        if target.get("webSocketDebuggerUrl"):
            return target["webSocketDebuggerUrl"]
    raise ConnectionError("CDP 未返回可连接的 page target。")


def _http_json(url_or_request: str | Request) -> Any:
    with urlopen(url_or_request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


async def _capture_cdp(
    session: RawCdpSession,
    screenshots_dir: Path,
    screenshot_id: str,
    label: str,
) -> dict[str, Any]:
    result = await session.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    path = screenshots_dir / f"{screenshot_id}.png"
    path.write_bytes(base64.b64decode(result["data"]))
    return {
        "screenshot_id": screenshot_id,
        "label": label,
        "path": str(path),
        "relative_path": str(path.relative_to(screenshots_dir.parent)),
    }


async def _collect_dom_cdp(
    session: RawCdpSession,
    max_pages: int,
    max_features_per_page: int,
) -> dict[str, Any]:
    result = await session.call(
        "Runtime.evaluate",
        {
            "expression": f"({_dom_collection_expression()})({json.dumps({'maxPages': max_pages, 'maxFeatures': max_features_per_page})})",
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    return result.get("result", {}).get("value") or {}


async def _execute_side_effect_actions_cdp(
    session: RawCdpSession,
    screenshots_dir: Path,
    discovered: dict[str, Any],
    config: V3RunConfig,
) -> dict[str, Any]:
    plan = _plan_side_effect_actions(discovered, config)
    screenshots: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for action in plan["selected"]:
        started_at = _now()
        before_state = await _page_state_cdp(session)
        before_ref = None
        after_ref = None
        try:
            before = await _capture_cdp(
                session,
                screenshots_dir,
                f"side_effect_{action['execution_order']:03d}_before",
                f"副作用动作前：{action['control_label']}",
            )
            before_ref = before["screenshot_id"]
            screenshots.append(before)
            click_result = await _click_side_effect_control_cdp(session, action)
            await asyncio.sleep(1.0)
            after_state = await _page_state_cdp(session)
            after = await _capture_cdp(
                session,
                screenshots_dir,
                f"side_effect_{action['execution_order']:03d}_after",
                f"副作用动作后：{action['control_label']}",
            )
            after_ref = after["screenshot_id"]
            screenshots.append(after)
            clicked = bool(click_result.get("clicked"))
            results.append(
                _side_effect_execution_result(
                    action,
                    status="side_effect_executed" if clicked else "side_effect_failed",
                    started_at=started_at,
                    before_ref=before_ref,
                    after_ref=after_ref,
                    before_state=before_state,
                    after_state=after_state,
                    click_result=click_result,
                    failure_reason=None if clicked else _text(click_result.get("reason")) or "click_failed",
                )
            )
        except Exception as exc:
            after_state = await _safe_page_state_cdp(session)
            results.append(
                _side_effect_execution_result(
                    action,
                    status="side_effect_failed",
                    started_at=started_at,
                    before_ref=before_ref,
                    after_ref=after_ref,
                    before_state=before_state,
                    after_state=after_state,
                    click_result={},
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            )
    return {
        "policy": plan["policy"],
        "selected": plan["selected"],
        "skipped": plan["skipped"],
        "results": results,
        "screenshots": screenshots,
    }


async def _page_state_cdp(session: RawCdpSession) -> dict[str, Any]:
    result = await session.call(
        "Runtime.evaluate",
        {
            "expression": """(() => {
              const text = document.body ? document.body.innerText : '';
              const dialogLog = Array.isArray(window.__stage2DialogLog) ? window.__stage2DialogLog : [];
              return {
                url: location.href,
                title: document.title,
                visible_text_sample: text.replace(/\\s+/g, ' ').slice(0, 600),
                dialog_events: dialogLog.slice(-10)
              };
            })()""",
            "returnByValue": True,
        },
    )
    return result.get("result", {}).get("value") or {}


async def _safe_page_state_cdp(session: RawCdpSession) -> dict[str, Any]:
    with suppress(Exception):
        return await _page_state_cdp(session)
    return {}


async def _click_side_effect_control_cdp(
    session: RawCdpSession,
    action: dict[str, Any],
) -> dict[str, Any]:
    result = await session.call(
        "Runtime.evaluate",
        {
            "expression": f"({_side_effect_click_expression()})({json.dumps(action)})",
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    return result.get("result", {}).get("value") or {}


def _plan_side_effect_actions(
    discovered: dict[str, Any],
    config: V3RunConfig,
) -> dict[str, Any]:
    policy = _side_effect_policy(config)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    per_type_counts: dict[str, int] = {}
    for fallback_index, control in enumerate(discovered.get("controls", []) or []):
        if not isinstance(control, dict):
            continue
        action_type = _normalize_action_type(
            _infer_feature_type(
                str(control.get("text") or ""),
                str(control.get("tag") or ""),
                str(control.get("type") or ""),
            )
        )
        if action_type not in SIDE_EFFECT_ACTION_TYPES:
            continue
        control_label = str(control.get("text") or f"可见控件 {fallback_index + 1}")
        base = {
            "action_id": f"side_effect_{fallback_index + 1:03d}_{action_type}",
            "action_type": action_type,
            "control_label": control_label,
            "risk_level": "high",
            "candidate_index": int(control.get("candidate_index", fallback_index) or 0),
            "control": {
                "text": control_label,
                "tag": str(control.get("tag") or ""),
                "type": str(control.get("type") or ""),
                "role": str(control.get("role") or ""),
                "href": str(control.get("href") or ""),
                "name": str(control.get("name") or ""),
                "id": str(control.get("id") or ""),
            },
        }
        decision = _side_effect_policy_decision(action_type, policy)
        if decision["decision"] == "allowed":
            if len(selected) >= policy["max_total_actions"]:
                decision = {
                    **decision,
                    "decision": "skipped",
                    "reason_code": "side_effect_total_limit_reached",
                }
            elif per_type_counts.get(action_type, 0) >= policy["max_per_action_type"]:
                decision = {
                    **decision,
                    "decision": "skipped",
                    "reason_code": "side_effect_per_type_limit_reached",
                }
        item = {**base, "policy_decision": decision}
        if decision["decision"] == "allowed":
            per_type_counts[action_type] = per_type_counts.get(action_type, 0) + 1
            item["execution_order"] = len(selected) + 1
            selected.append(item)
        else:
            skipped.append(item)
    return {"policy": policy, "selected": selected, "skipped": skipped}


def _side_effect_policy(config: V3RunConfig) -> dict[str, Any]:
    metadata = config.metadata if isinstance(config.metadata, dict) else {}
    safety_policy = _text(
        metadata.get("safety_policy")
        or metadata.get("stage2_safety_policy")
        or config.safety_policy
        or LOW_RISK_ONLY_POLICY
    )
    allowed = _normalize_allowed_side_effect_actions(
        metadata.get("allowed_side_effect_actions")
        or metadata.get("side_effect_allowlist")
        or metadata.get("allowed_side_effects")
        or config.allowed_side_effect_actions
        or config.risk_whitelist
    )
    max_total = _bounded_int(
        metadata.get("max_side_effect_actions"),
        default=DEFAULT_SIDE_EFFECT_TOTAL_LIMIT,
        minimum=0,
        maximum=5,
    )
    max_per_type = _bounded_int(
        metadata.get("max_side_effect_actions_per_type"),
        default=DEFAULT_SIDE_EFFECT_PER_TYPE_LIMIT,
        minimum=0,
        maximum=2,
    )
    return {
        "safety_policy": safety_policy,
        "allowed_side_effect_actions": sorted(allowed),
        "max_total_actions": max_total,
        "max_per_action_type": max_per_type,
        "execution_boundary": (
            "Only visible current-page controls are eligible. Actions are capped and audited with before/after screenshots."
        ),
    }


def _side_effect_policy_decision(action_type: str, policy: dict[str, Any]) -> dict[str, Any]:
    allowed_actions = set(policy.get("allowed_side_effect_actions") or [])
    if policy.get("safety_policy") != TEST_ENV_FULL_ACCESS_POLICY:
        return {
            "decision": "blocked",
            "reason_code": "requires_test_env_full_access",
            "safety_policy": policy.get("safety_policy"),
            "allowed": False,
        }
    if "*" not in allowed_actions and action_type not in allowed_actions:
        return {
            "decision": "blocked",
            "reason_code": "action_not_allowlisted",
            "safety_policy": policy.get("safety_policy"),
            "allowed": False,
        }
    return {
        "decision": "allowed",
        "reason_code": "test_env_full_access_allowlisted",
        "safety_policy": policy.get("safety_policy"),
        "allowed": True,
    }


def _normalize_allowed_side_effect_actions(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = [str(value).strip()]
    normalized = {_normalize_action_type(item) for item in raw_items if item}
    if "all" in normalized or "*" in raw_items:
        return {"*"}
    return {item for item in normalized if item in SIDE_EFFECT_ACTION_TYPES}


def _normalize_action_type(value: str) -> str:
    lowered = _text(value).lower()
    aliases = {
        "approval": "approve",
        "audit": "approve",
        "confirm": "submit",
        "remove": "delete",
        "new": "create",
        "add": "create",
        "update": "edit",
    }
    return aliases.get(lowered, lowered)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _side_effect_execution_result(
    action: dict[str, Any],
    *,
    status: str,
    started_at: str,
    before_ref: str | None,
    after_ref: str | None,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    click_result: dict[str, Any],
    failure_reason: str | None,
) -> dict[str, Any]:
    return {
        "result_id": f"{action['action_id']}_result",
        "action_id": action["action_id"],
        "action_type": action["action_type"],
        "control_label": action["control_label"],
        "risk_level": action["risk_level"],
        "policy_decision": action["policy_decision"],
        "before_screenshot_ref": before_ref,
        "after_screenshot_ref": after_ref,
        "status": status,
        "failure_reason": failure_reason,
        "started_at": started_at,
        "finished_at": _now(),
        "url_before": before_state.get("url") or click_result.get("url_before"),
        "url_after": after_state.get("url") or click_result.get("url_after"),
        "visible_feedback": after_state.get("visible_text_sample") or "",
        "dialog_events": (click_result.get("dialog_events") or []) + (after_state.get("dialog_events") or []),
        "evidence": [item for item in (before_ref, after_ref) if item],
    }


def _real_browser_completion_message(side_effect_bundle: dict[str, Any]) -> str:
    executed = len(side_effect_bundle.get("results") or [])
    policy = side_effect_bundle.get("policy") or {}
    if executed:
        return f"已通过 Chrome DevTools Protocol 完成页面扫描，并在 {policy.get('safety_policy')} 策略下执行 {executed} 个白名单副作用动作。"
    if policy.get("safety_policy") == TEST_ENV_FULL_ACCESS_POLICY:
        return "已通过 Chrome DevTools Protocol 完成页面扫描；未发现符合白名单和执行上限的可见副作用动作。"
    return "已通过 Chrome DevTools Protocol 完成低风险页面可见元素扫描，默认未执行提交、删除、审批等副作用动作。"


def _looks_like_login_payload(payload: dict[str, Any]) -> bool:
    return bool(payload.get("password") or payload.get("hasLoginWord"))


def _dom_collection_expression() -> str:
    return """(opts) => {
      const maxPages = Math.max(1, Number(opts.maxPages || 1));
      const maxFeatures = Math.max(1, Number(opts.maxFeatures || 1));
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const label = (el) => (
        el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') ||
        el.getAttribute('placeholder') || el.value || el.name || el.id || el.tagName
      ).trim().replace(/\\s+/g, ' ').slice(0, 80);
      const bodyText = document.body ? document.body.innerText.toLowerCase() : '';
      const loginWords = ['login', 'sign in', '登录', '登陆', '验证码'];
      const links = Array.from(document.querySelectorAll('a[href]'))
        .filter(visible)
        .slice(0, maxPages)
        .map((el) => ({ text: label(el), href: el.href }));
      const visibleControls = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,[role="button"],[onclick]'))
        .filter(visible);
      const controls = visibleControls
        .slice(0, maxFeatures)
        .map((el, index) => ({
          text: label(el),
          candidate_index: index,
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute('role') || '',
          type: el.getAttribute('type') || '',
          href: el.href || '',
          name: el.name || '',
          id: el.id || ''
        }));
      return {
        links,
        controls,
        title: document.title,
        url: location.href,
        password: Boolean(document.querySelector('input[type="password"]')),
        hasLoginWord: loginWords.some((word) => bodyText.includes(word))
      };
    }"""


def _side_effect_click_expression() -> str:
    return """async (candidate) => {
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const label = (el) => (
        el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') ||
        el.getAttribute('placeholder') || el.value || el.name || el.id || el.tagName
      ).trim().replace(/\\s+/g, ' ').slice(0, 80);
      window.__stage2DialogLog = Array.isArray(window.__stage2DialogLog) ? window.__stage2DialogLog : [];
      if (!window.__stage2DialogPatched) {
        const originalAlert = window.alert.bind(window);
        const originalConfirm = window.confirm.bind(window);
        const originalPrompt = window.prompt.bind(window);
        window.alert = (message) => {
          window.__stage2DialogLog.push({type: 'alert', message: String(message), handled: 'recorded'});
          return undefined;
        };
        window.confirm = (message) => {
          window.__stage2DialogLog.push({type: 'confirm', message: String(message), handled: 'accepted'});
          return true;
        };
        window.prompt = (message, defaultValue = '') => {
          window.__stage2DialogLog.push({type: 'prompt', message: String(message), handled: 'default_value'});
          return String(defaultValue || '');
        };
        window.__stage2OriginalDialogFns = {originalAlert, originalConfirm, originalPrompt};
        window.__stage2DialogPatched = true;
      }
      const controls = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,[role="button"],[onclick]'))
        .filter(visible);
      const targetText = String(candidate.control_label || candidate.control?.text || '').trim();
      const index = Number(candidate.candidate_index || 0);
      const exactMatches = controls.filter((el) => label(el) === targetText);
      const indexed = controls[index];
      const target = exactMatches[0] || (indexed && label(indexed) === targetText ? indexed : null);
      if (!target) {
        return {
          clicked: false,
          reason: 'control_not_found',
          url_before: location.href,
          url_after: location.href,
          visible_labels: controls.slice(0, 12).map(label),
          dialog_events: window.__stage2DialogLog.slice(-10)
        };
      }
      if (target.disabled || target.getAttribute('aria-disabled') === 'true') {
        return {
          clicked: false,
          reason: 'control_disabled',
          url_before: location.href,
          url_after: location.href,
          matched_label: label(target),
          dialog_events: window.__stage2DialogLog.slice(-10)
        };
      }
      const urlBefore = location.href;
      target.scrollIntoView({block: 'center', inline: 'center'});
      await new Promise((resolve) => window.setTimeout(resolve, 120));
      target.click();
      await new Promise((resolve) => window.setTimeout(resolve, 120));
      return {
        clicked: true,
        reason: null,
        url_before: urlBefore,
        url_after: location.href,
        matched_label: label(target),
        dialog_events: window.__stage2DialogLog.slice(-10)
      };
    }"""


def _build_pages(
    config: V3RunConfig,
    current_url: str,
    title: str,
    discovered: dict[str, Any],
    screenshot_id: str,
) -> list[dict[str, Any]]:
    pages = [
        {
            "page_id": "page_001",
            "page_entry_id": "page_001",
            "name": title or config.target_name or "首页",
            "url": current_url or config.start_url,
            "source": "real_browser_cdp",
            "confidence": "observed",
            "semantic_page_type": _infer_page_type(title, current_url),
            "priority": "high",
            "requires_human_review": False,
            "evidence": {"screenshot_id": screenshot_id},
        }
    ]
    for index, link in enumerate(discovered.get("links", []), start=2):
        href = str(link.get("href") or "")
        if not href:
            continue
        pages.append(
            {
                "page_id": f"page_{index:03d}",
                "page_entry_id": f"page_{index:03d}",
                "name": str(link.get("text") or f"页面入口 {index}"),
                "url": href,
                "source": "real_browser_visible_link",
                "confidence": "candidate",
                "semantic_page_type": _infer_page_type(str(link.get("text") or ""), href),
                "priority": "normal",
                "requires_human_review": True,
                "evidence": {"screenshot_id": screenshot_id},
            }
        )
    return pages[: max(1, int(config.max_pages or 1))]


def _build_features(
    pages: list[dict[str, Any]],
    discovered: dict[str, Any],
    max_features_per_page: int,
    screenshot_id: str,
) -> list[dict[str, Any]]:
    page_id = pages[0]["page_id"] if pages else "page_001"
    features = []
    for index, control in enumerate(discovered.get("controls", []), start=1):
        text = str(control.get("text") or f"可见控件 {index}")
        feature_type = _infer_feature_type(text, str(control.get("tag") or ""), str(control.get("type") or ""))
        features.append(
            {
                "feature_id": f"feature_{index:03d}",
                "feature_point_id": f"feature_{index:03d}",
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": text,
                "feature_type": feature_type,
                "source": "real_browser_visible_control",
                "confidence": "observed",
                "risk_level": _risk_level(feature_type),
                "requires_test_data": feature_type in SIDE_EFFECT_ACTION_TYPES,
                "evidence": {
                    "screenshot_id": screenshot_id,
                    "tag": control.get("tag"),
                    "type": control.get("type"),
                    "candidate_index": control.get("candidate_index"),
                },
            }
        )
    if not features:
        features.append(
            {
                "feature_id": "feature_001",
                "feature_point_id": "feature_001",
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": "页面可见性验证",
                "feature_type": "view",
                "source": "real_browser_page_visible",
                "confidence": "observed",
                "risk_level": "low",
                "requires_test_data": False,
                "evidence": {"screenshot_id": screenshot_id},
            }
        )
    return features[: max(1, int(max_features_per_page or 1))]


def _preflight(ok: bool, reason: str, cdp_url: str) -> dict[str, Any]:
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "execution_mode": "real_browser",
        "ok": ok,
        "status": "completed" if ok else "blocked",
        "failure_reason": None if ok else reason,
        "checks": {
            "cdp_url": {"ok": bool(cdp_url), "url": cdp_url},
            "browser_session": {"ok": ok},
            "raw_cdp": {"ok": ok or reason != "cdp_connect_failed"},
        },
    }


def _screenshots_index(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "screenshots": items,
        "items": items,
        "notes": ["截图由 Chrome DevTools Protocol 执行器采集。"] if items else [],
    }


def _blocked(reason: str, message: str, *, cdp_url: str = "") -> dict[str, Any]:
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "status": "blocked",
        "failure_reason": reason,
        "message": message,
        "preflight_result": _preflight(False, reason, cdp_url),
        "pages": [],
        "features": [],
        "screenshots_index": _screenshots_index([]),
    }


def _dedupe_menu_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    order: list[tuple[str, ...]] = []
    for entry in entries:
        key = _menu_entry_dedupe_key(entry)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = entry
            order.append(key)
            continue
        deduped[key] = _merge_menu_entry(existing, entry)
    return [deduped[key] for key in order]


def _menu_entry_dedupe_key(entry: dict[str, Any]) -> tuple[str, ...]:
    path = [_text(part) for part in entry.get("menu_path", []) if _text(part)]
    if path:
        return tuple(path)
    return (_text(entry.get("parent_id")), _text(entry.get("text")))


def _merge_menu_entry(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    winner, loser = (left, right)
    if _menu_entry_rank(right) > _menu_entry_rank(left):
        winner, loser = right, left
    merged = {**loser, **winner}
    merged["screenshot_refs"] = _merge_unique_text_lists(
        loser.get("screenshot_refs"),
        winner.get("screenshot_refs"),
    )
    locators = []
    for source in (loser.get("locator_candidates"), winner.get("locator_candidates")):
        if isinstance(source, list):
            for item in source:
                if isinstance(item, dict) and item not in locators:
                    locators.append(item)
    merged["locator_candidates"] = locators
    return merged


def _menu_entry_rank(entry: dict[str, Any]) -> tuple[int, int, int]:
    return (
        1 if _text(entry.get("route_hint")) else 0,
        1 if entry.get("is_leaf") else 0,
        0 if _text(entry.get("status")) in {"permission_blocked", "expansion_failed", "failed"} else 1,
    )


def _merge_unique_text_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            text = _text(item)
            if text and text not in merged:
                merged.append(text)
    return merged


def _children_by_parent_from_entries(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        parent_id = _text(entry.get("parent_id"))
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(entry)
    return children_by_parent


def _menu_entry_status(
    candidate: dict[str, Any],
    event: dict[str, Any],
    children: list[dict[str, Any]],
) -> str:
    event_status = _text(event.get("status"))
    if event_status == "success":
        return "expanded" if candidate.get("expandable") else "discovered"
    if event_status == "permission_blocked":
        return "permission_blocked"
    if event_status in {"failed", "error"}:
        return "expansion_failed"
    if candidate.get("disabled") or _text(candidate.get("aria_disabled")) == "true":
        return "permission_blocked"
    explicit_status = _text(candidate.get("status"))
    if explicit_status:
        return explicit_status
    if candidate.get("expandable") and children:
        return "expanded"
    return "discovered"


def _menu_screenshot_refs(candidate: dict[str, Any], event: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for value in (
        candidate.get("screenshot_id"),
        candidate.get("screenshot_ref"),
        event.get("screenshot_ref"),
    ):
        text = _text(value)
        if text and text not in refs:
            refs.append(text)
    raw_refs = candidate.get("screenshot_refs")
    if isinstance(raw_refs, list):
        for value in raw_refs:
            text = _text(value)
            if text and text not in refs:
                refs.append(text)
    return refs


def _menu_path(entry: dict[str, Any], all_entries: list[dict[str, Any]]) -> list[str]:
    explicit = entry.get("menu_path")
    if isinstance(explicit, list) and explicit:
        return [str(part) for part in explicit if _text(part)]
    by_id = {_text(item.get("menu_id")): item for item in all_entries}
    parts: list[str] = []
    current: dict[str, Any] | None = entry
    seen: set[str] = set()
    while current:
        current_id = _text(current.get("menu_id"))
        if current_id in seen:
            break
        seen.add(current_id)
        label = _text(current.get("text") or current.get("name"))
        if label:
            parts.append(label)
        parent_id = _text(current.get("parent_id"))
        current = by_id.get(parent_id) if parent_id else None
    return list(reversed(parts))


def _locator_candidates(entry: dict[str, Any]) -> list[dict[str, str]]:
    raw = entry.get("locator_candidates")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    candidates: list[dict[str, str]] = []
    locator = _text(entry.get("locator"))
    if locator:
        candidates.append({"kind": "css", "value": locator})
    text = _text(entry.get("text") or entry.get("name"))
    if text:
        candidates.append({"kind": "text", "value": text})
    return candidates


def _tree_node(
    entry: dict[str, Any],
    entry_by_id: dict[str, dict[str, Any]],
    children_by_parent: dict[str, list[dict[str, Any]]],
    seen: set[str] | None = None,
) -> dict[str, Any]:
    menu_id = _text(entry.get("menu_id"))
    seen = set(seen or set())
    if menu_id in seen:
        return {
            key: value
            for key, value in {**entry, "cycle_detected": True}.items()
            if value not in (None, [], "")
        }
    next_seen = {*seen, menu_id} if menu_id else seen
    children = [
        _tree_node(entry_by_id[child["menu_id"]], entry_by_id, children_by_parent, next_seen)
        for child in children_by_parent.get(entry["menu_id"], [])
        if child.get("menu_id") in entry_by_id
        and _text(child.get("menu_id")) not in next_seen
    ]
    return {
        key: value
        for key, value in {**entry, "children": children}.items()
        if value not in (None, [], "")
    }


def _menu_screenshots_index(items: list[dict[str, Any]]) -> dict[str, Any]:
    screenshots = []
    for item in items:
        if not isinstance(item, dict):
            continue
        screenshot_id = _text(item.get("screenshot_id"))
        if not screenshot_id:
            continue
        screenshots.append(
            {
                **item,
                "screenshot_id": screenshot_id,
                "stage": _text(item.get("stage")) or "menu_discovery",
                "source": _text(item.get("source")) or "playwright.menu_discovery",
            }
        )
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "screenshots": screenshots,
        "items": screenshots,
        "notes": ["第一轮菜单遍历截图证据。"] if screenshots else [],
    }


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _infer_page_type(name: str, url: str) -> str:
    text = f"{name} {url}".lower()
    if any(keyword in text for keyword in ("query", "search", "list", "查询", "列表")):
        return "query_list"
    if any(keyword in text for keyword in ("detail", "详情")):
        return "detail"
    return "page"


def _infer_feature_type(text: str, tag: str, input_type: str) -> str:
    lowered = f"{text} {tag} {input_type}".lower()
    if any(word in lowered for word in ("delete", "删除", "作废")):
        return "delete"
    if any(word in lowered for word in ("approve", "审批", "审核")):
        return "approve"
    if any(word in lowered for word in ("save", "保存")):
        return "save"
    if any(word in lowered for word in ("submit", "提交")):
        return "submit"
    if any(word in lowered for word in ("add", "new", "create", "新增", "新建")):
        return "create"
    if any(word in lowered for word in ("edit", "修改", "编辑")):
        return "edit"
    if any(word in lowered for word in ("search", "query", "查询", "检索")) or input_type in {"search", "text"}:
        return "query"
    if tag == "a":
        return "navigation"
    return "view"


def _risk_level(feature_type: str) -> str:
    if feature_type in SIDE_EFFECT_ACTION_TYPES:
        return "high"
    return "low"
