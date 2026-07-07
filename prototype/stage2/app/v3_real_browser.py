from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import struct
import zipfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from prototype.stage2.app.v3_orchestrator import V3RunConfig
from prototype.stage2.app.verification.native_control_skills import (
    native_button_is_disabled,
    native_control_selected_value,
    native_upload_existing_file_name,
)


V3_SCHEMA_VERSION = "stage2_v3_run.v1"
LOW_RISK_ONLY_POLICY = "low_risk_only"
TEST_ENV_FULL_ACCESS_POLICY = "test_env_full_access"
SIDE_EFFECT_ACTION_TYPES = {"submit", "save", "approve", "delete", "create", "edit"}
DEFAULT_SIDE_EFFECT_TOTAL_LIMIT = 4
DEFAULT_SIDE_EFFECT_PER_TYPE_LIMIT = 1
BROWSER_USE_DETERMINISTIC_TOOL_FIRST_MAX_STEPS = 30
BROWSER_USE_TOOL_RESULT_PREVIEW_LIMIT = 4000


def _final_dialog_unit_options_from_visible_text(text: str) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()
    for line in re.split(r"[\r\n]+", _text(text)):
        candidate = _text(line)
        if not candidate:
            continue
        if re.search(r"请选择|备案登记|监管单位|申请表|提交备案|上传|文件", candidate):
            continue
        if not re.search(r"林业局|监管", candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        options.append(candidate)
    return options


def _choose_final_dialog_unit(candidates: list[str]) -> str:
    clean = [_text(item) for item in candidates if _text(item) and not re.search(r"请选择|全部", _text(item))]
    for pattern in [r"广西壮族自治区林业局", r"自治区林业局", r"林业局"]:
        for candidate in clean:
            if re.search(pattern, candidate):
                return candidate
    return clean[0] if clean else ""


def _online_apply_repair_plan(validation_errors: list[str]) -> dict[str, Any]:
    text = "\n".join(_text(item) for item in validation_errors if _text(item))
    if not text:
        return {
            "dates": True,
            "dropdowns": True,
            "uploads": True,
            "reason": "no_validation_errors_available",
        }
    return {
        "dates": bool(re.search(r"日期|育苗开始日期|验收日期", text)),
        "dropdowns": bool(
            re.search(
                r"验收监管单位|备案品种|备案类型|育苗方式|育苗目的|育苗地点|育苗地址|地点不能为空|请选择",
                text,
            )
        ),
        "uploads": bool(re.search(r"验收文件|上传|附件|图片|人员信息表|文件", text)),
        "reason": "validation_error_labels",
    }


def _ensure_online_apply_upload_samples(run_dir: Path) -> dict[str, Path]:
    samples_dir = run_dir / "upload_samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "personnel": samples_dir / "人员信息表1.xls",
        "image": samples_dir / "备案图片01.jpg",
        "attachment": samples_dir / "附件11.doc",
        "acceptance": samples_dir / "验收文件00.pdf",
        "application": samples_dir / "备案申请表.pdf",
    }
    if not files["personnel"].exists():
        with zipfile.ZipFile(files["personnel"], "w", compression=zipfile.ZIP_DEFLATED) as workbook:
            workbook.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
            )
            workbook.writestr(
                "_rels/.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
            )
            workbook.writestr(
                "xl/workbook.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="人员信息" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
            )
            workbook.writestr(
                "xl/_rels/workbook.xml.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
            )
            workbook.writestr(
                "xl/worksheets/sheet1.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>姓名</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>测试人员</t></is></c></row>
  </sheetData>
</worksheet>""",
            )
    if not files["image"].exists():
        files["image"].write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00" + bytes([8] * 64) + b"\xff\xd9"
        )
    if not files["attachment"].exists():
        with zipfile.ZipFile(files["attachment"], "w", compression=zipfile.ZIP_DEFLATED) as document:
            document.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
            )
            document.writestr(
                "_rels/.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
            )
            document.writestr(
                "word/document.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>附件11 测试文档</w:t></w:r></w:p>
  </w:body>
</w:document>""",
            )
    if not files["acceptance"].exists():
        files["acceptance"].write_bytes(
            b"%PDF-1.4\n"
            b"1 0 obj<<>>endobj\n"
            b"2 0 obj<< /Length 0 >>stream\nendstream\nendobj\n"
            b"trailer<<>>\n%%EOF\n"
        )
    if not files["application"].exists():
        files["application"].write_bytes(
            b"%PDF-1.4\n"
            b"1 0 obj<<>>endobj\n"
            b"2 0 obj<< /Length 0 >>stream\nendstream\nendobj\n"
            b"trailer<<>>\n%%EOF\n"
        )
    return files


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


async def collect_real_browser_artifacts(
    config: V3RunConfig,
    run_dir: Path,
    *,
    browser_use_handover_provider: Any | None = None,
) -> dict[str, Any]:
    """Collect first-round menu evidence from a connected real browser."""

    if not config.cdp_url:
        return _blocked("cdp_required", "真实浏览器模式需要 CDP 地址，例如 http://localhost:9222。")
    try:
        return await _collect_with_playwright_menu_discovery(
            config,
            run_dir,
            browser_use_handover_provider=browser_use_handover_provider,
        )
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
    *,
    browser_use_handover_provider: Any | None = None,
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
            if _is_menu_discovery_only_round(config):
                return {
                    "schema_version": V3_SCHEMA_VERSION,
                    "status": "completed",
                    "failure_reason": None,
                    "message": "已通过 Playwright 连接真实浏览器完成第一轮菜单入口遍历；页面详细测试将在第二轮开始。",
                    "source": "playwright.menu_discovery",
                    "preflight_result": preflight,
                    "executor_stack": {
                        "playwright": {
                            "status": "used",
                            "cdp_url": config.cdp_url,
                            "current_page": _text(await page.title()) or page.url,
                            "current_skill": "Playwright",
                            "current_step": "菜单入口遍历",
                        },
                        "browser_use": {
                            "status": "not_invoked",
                            "selected_model": config.model_name,
                            "reason": "第 1 轮只做菜单入口树遍历，Browser Use 从第 2 轮页面/功能点详细测试开始按需接管。",
                        },
                        "raw_cdp": {"status": "diagnostic_only", "browser_target_count": len(browser_targets)},
                    },
                    "browser_targets": browser_targets,
                    "menu_tree": menu_bundle["menu_tree"],
                    "menu_entries": menu_bundle["menu_entries"],
                    "menu_traversal_log": menu_bundle["menu_traversal_log"],
                    "page_exploration_log": [],
                    "pages": [],
                    "features": [],
                    "case_execution_results": [],
                    "screenshots_index": menu_bundle["screenshots_index"],
                }
            page_bundle = await _explore_menu_leaf_pages_with_playwright(
                page,
                menu_bundle["menu_entries"],
                screenshots_dir,
                config,
            )
            handover_reasons = _target_handover_reasons(
                config,
                menu_entries=menu_bundle["menu_entries"],
                pages=page_bundle["pages"],
                features=page_bundle["features"],
            )
            browser_use_handover = None
            if handover_reasons:
                provider = browser_use_handover_provider or _run_browser_use_target_handover
                browser_use_handover = await provider(
                    page=page,
                    config=config,
                    run_dir=run_dir,
                    screenshots_dir=screenshots_dir,
                    handover_reasons=handover_reasons,
                    menu_bundle=menu_bundle,
                    page_bundle=page_bundle,
                )
                page_bundle = _merge_browser_use_handover(page_bundle, browser_use_handover)
            screenshots_index = _merge_screenshot_indexes(
                menu_bundle["screenshots_index"],
                page_bundle["screenshots_index"],
            )
            browser_use_status = "not_needed"
            browser_use_reason = "Playwright 已覆盖用户优先目标或本轮未提供优先目标。"
            if browser_use_handover is not None:
                browser_use_status = (
                    "used"
                    if _text(browser_use_handover.get("status")) == "completed"
                    else "failed"
                )
                browser_use_reason = _text(browser_use_handover.get("message")) or _text(
                    browser_use_handover.get("failure_reason")
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
                        "status": browser_use_status,
                        "selected_model": config.model_name,
                        "reason": browser_use_reason,
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
                "case_execution_results": page_bundle.get("case_execution_results", []),
                "browser_use_handover": browser_use_handover,
                "screenshots_index": screenshots_index,
            }
        finally:
            await browser.close()


def _is_menu_discovery_only_round(config: V3RunConfig) -> bool:
    return _text(getattr(config, "round_stage", "")) == "menu_discovery"


async def _resolve_playwright_target_page(browser: Any, start_url: str) -> Any:
    pages: list[Any] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    if not pages:
        raise RuntimeError("未发现可用页面，无法执行 Playwright 菜单遍历")
    page = next((item for item in pages if start_url and start_url in item.url), pages[0])
    await page.bring_to_front()
    await page.wait_for_load_state("domcontentloaded")
    body_text = await _safe_playwright_body_text(page)
    if start_url and (start_url not in page.url or not _text(body_text)):
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
    normalization = await _normalize_playwright_menu_shell(page)
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
    if normalization.get("attempted"):
        traversal_events.append(
            {
                "event": "normalize_menu_shell",
                "status": "success" if normalization.get("expanded") else "skipped",
                "reason": normalization.get("reason"),
                "details": normalization,
            }
        )
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
            click_result = await _click_playwright_menu_candidate(page, menu_id)
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
                    "click_method": click_result.get("method"),
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


async def _normalize_playwright_menu_shell(page: Any) -> dict[str, Any]:
    """Open a collapsed app sidebar before scanning or clicking menu candidates."""

    try:
        result = await page.evaluate(_normalize_menu_shell_script())
        if not isinstance(result, dict):
            return {"attempted": False, "expanded": False, "reason": "unexpected_result"}
        if result.get("attempted"):
            with suppress(Exception):
                await page.wait_for_timeout(350)
        return result
    except Exception as exc:
        return {
            "attempted": False,
            "expanded": False,
            "reason": "normalization_failed",
            "failure_reason": f"{type(exc).__name__}: {exc}",
        }


def _normalize_menu_shell_script() -> str:
    return """
    () => {
      const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const hasCollapsedMenu = () => Boolean(document.querySelector('.el-menu--collapse'));
      const sidebar = document.querySelector('.sidebar-container, aside, nav, .el-menu');
      const collapsedBefore = hasCollapsedMenu()
        || (sidebar && sidebar.getBoundingClientRect().width > 0 && sidebar.getBoundingClientRect().width < 96);
      if (!collapsedBefore) {
        return {
          attempted: false,
          expanded: false,
          reason: 'menu_shell_already_open',
          collapsedBefore: false,
          collapsedAfter: false,
        };
      }
      const candidates = Array.from(document.querySelectorAll(
        '.hamburger, #hamburger-container, .hamburger-container, .sidebar-toggle, [class*="hamburger"], [class*="collapse"], .navbar svg, .navbar button, button'
      )).filter(visible);
      const toggle = candidates.find((el) => {
        const rect = el.getBoundingClientRect();
        const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
        return rect.left <= 96 && rect.top <= 96 && !/退出|个人中心|通知|消息/.test(text);
      }) || candidates[0];
      if (!toggle) {
        return {
          attempted: true,
          expanded: false,
          reason: 'sidebar_toggle_not_found',
          collapsedBefore,
          collapsedAfter: hasCollapsedMenu(),
        };
      }
      toggle.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
      return {
        attempted: true,
        expanded: !hasCollapsedMenu(),
        reason: 'clicked_sidebar_toggle',
        collapsedBefore,
        collapsedAfter: hasCollapsedMenu(),
      };
    }
    """


async def _click_playwright_menu_candidate(page: Any, menu_id: str) -> dict[str, Any]:
    locator = page.locator(f"[data-stage2-menu-id='{menu_id}']").first
    if callable(locator):
        locator = locator()
    try:
        await locator.scroll_into_view_if_needed(timeout=1000)
        await locator.click(timeout=2500)
        return {"method": "playwright_locator_click"}
    except Exception as exc:
        fallback_result = await _click_playwright_menu_candidate_by_label(page, menu_id)
        if fallback_result.get("ok"):
            return {
                "method": fallback_result.get("method") or "dom_label_click_fallback",
                "fallback_after": f"{type(exc).__name__}: {exc}",
            }
        raise exc


async def _click_playwright_menu_candidate_by_label(page: Any, menu_id: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        (menuId) => {
          const escapeAttr = (value) => String(value).replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");
          const el = document.querySelector(`[data-stage2-menu-id='${escapeAttr(menuId)}']`);
          if (!el) return { ok: false, method: 'dom_label_click_fallback', reason: 'menu_not_found' };
          const target = el.matches('.el-submenu__title, .el-menu-item')
            ? el
            : (el.querySelector('.el-submenu__title, .el-menu-item, [role="menuitem"]') || el);
          const rect = target.getBoundingClientRect();
          if (!rect.width || !rect.height) {
            return { ok: false, method: 'dom_label_click_fallback', reason: 'empty_rect' };
          }
          if (rect.right < 0 || rect.left > window.innerWidth || rect.bottom < 0 || rect.top > window.innerHeight) {
            return { ok: false, method: 'dom_label_click_fallback', reason: 'still_outside_viewport' };
          }
          const safeX = Math.min(Math.max(rect.left + 36, rect.left + rect.width * 0.35), rect.right - 8);
          const safeY = rect.top + rect.height / 2;
          const clickTarget = document.elementFromPoint(safeX, safeY) || target;
          clickTarget.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window, clientX: safeX, clientY: safeY }));
          clickTarget.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window, clientX: safeX, clientY: safeY }));
          clickTarget.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window, clientX: safeX, clientY: safeY }));
          return { ok: true, method: 'dom_label_safe_point_click', x: safeX, y: safeY };
        }
        """,
        menu_id,
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
            open_result = await _open_menu_entry_page(page, entry, config)
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
            if _snapshot_is_blank(snapshot):
                pages.append(
                    {
                        "page_id": page_id,
                        "page_entry_id": page_id,
                        "menu_id": menu_id,
                        "name": _text(entry.get("text")) or title,
                        "url": current_url,
                        "menu_path": entry.get("menu_path", []),
                        "page_type": _infer_page_type(title, current_url),
                        "semantic_page_type": _infer_page_type(title, current_url),
                        "discovery_depth": 1,
                        "status": "unreachable",
                        "source": "playwright.menu_page_exploration",
                        "confidence": "blank",
                        "screenshot_refs": [screenshot_id],
                        "failure_reason": "blank_page_after_navigation",
                    }
                )
                logs.append(
                    {
                        "event": "enter_menu_leaf",
                        "menu_id": menu_id,
                        "menu_path": entry.get("menu_path", []),
                        "status": "failed",
                        "page_entry_id": page_id,
                        "screenshot_ref": screenshot_id,
                        "url": current_url,
                        "failure_reason": "blank_page_after_navigation",
                    }
                )
                continue
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
                    "navigation_method": open_result.get("method"),
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
                    "navigation_method": open_result.get("method"),
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
        finally:
            logs.append(
                await _return_to_start_url_after_menu_leaf(
                    page,
                    config,
                    menu_id=menu_id,
                    page_id=page_id,
                )
            )
    deduped_pages, deduped_features = _dedupe_page_exploration(pages, features)
    return {
        "pages": deduped_pages,
        "features": deduped_features,
        "page_exploration_log": logs,
        "screenshots_index": _menu_screenshots_index(
            [{**item, "stage": "page_exploration", "source": "playwright.menu_page_exploration"} for item in screenshots]
        ),
    }


def _target_handover_reasons(
    config: V3RunConfig,
    *,
    menu_entries: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    targets = [
        _text(item)
        for item in (config.metadata or {}).get("prioritized_targets", [])
        if _text(item)
    ]
    reasons: list[dict[str, Any]] = []
    for target in targets:
        matched_menu_ids = [
            _text(entry.get("menu_id"))
            for entry in menu_entries
            if _record_matches_target(entry, target, ("text", "name", "route_hint", "url"))
            or _path_matches_target(entry.get("menu_path"), target)
        ]
        matched_page_ids = [
            _text(page.get("page_id") or page.get("page_entry_id"))
            for page in pages
            if _record_matches_target(page, target, ("name", "title", "url", "failure_reason"))
            or _path_matches_target(page.get("menu_path"), target)
        ]
        matched_feature_ids = [
            _text(feature.get("feature_id") or feature.get("feature_point_id"))
            for feature in features
            if _record_matches_target(feature, target, ("name", "title", "feature_type"))
        ]
        deep_matched_feature_ids = [
            _text(feature.get("feature_id") or feature.get("feature_point_id"))
            for feature in features
            if _record_matches_target(feature, target, ("name", "title", "feature_type"))
            and not _is_shallow_playwright_target_feature(feature)
        ]
        target_pages = [
            page
            for page in pages
            if _text(page.get("page_id") or page.get("page_entry_id")) in set(matched_page_ids)
        ]
        has_reachable_page = any(_text(page.get("status")) == "reachable" for page in target_pages)
        if has_reachable_page and deep_matched_feature_ids:
            continue
        reasons.append(
            {
                "target": target,
                "reason": "target_page_uncovered" if matched_menu_ids or matched_page_ids else "target_not_found",
                "matched_menu_entry_ids": [item for item in matched_menu_ids if item],
                "matched_page_ids": [item for item in matched_page_ids if item],
                "matched_feature_ids": [item for item in matched_feature_ids if item],
            }
        )
    return reasons


def _is_shallow_playwright_target_feature(feature: dict[str, Any]) -> bool:
    feature_type = _text(feature.get("feature_type") or feature.get("type"))
    strategy = _text(feature.get("verification_strategy"))
    source = _text(feature.get("source"))
    if strategy == "side_effect_policy_gate" and source.startswith("playwright"):
        return True
    return strategy in {"playwright_page_visible", "playwright_visible_control"} and feature_type in {
        "view",
        "navigation",
    }


def _target_page_restore_plan(
    config: V3RunConfig,
    handover_reasons: list[dict[str, Any]],
    menu_bundle: dict[str, Any],
    page_bundle: dict[str, Any],
) -> dict[str, Any] | None:
    menu_entries = menu_bundle.get("menu_entries") if isinstance(menu_bundle, dict) else []
    if not isinstance(menu_entries, list):
        menu_entries = []
    pages = page_bundle.get("pages") if isinstance(page_bundle, dict) else []
    if not isinstance(pages, list):
        pages = []

    for reason in handover_reasons:
        target = _text(reason.get("target"))
        matched_menu_ids = {
            _text(item)
            for item in reason.get("matched_menu_entry_ids", [])
            if _text(item)
        }
        for entry in menu_entries:
            if not isinstance(entry, dict):
                continue
            menu_id = _text(entry.get("menu_id") or entry.get("menu_entry_id"))
            if matched_menu_ids and menu_id not in matched_menu_ids:
                continue
            if not matched_menu_ids and not (
                _record_matches_target(entry, target, ("text", "name", "route_hint", "url"))
                or _path_matches_target(entry.get("menu_path"), target)
            ):
                continue
            route_hint = _text(entry.get("route_hint") or entry.get("href") or entry.get("url"))
            if not route_hint:
                continue
            return {
                "target": target,
                "source": "menu_entry_route_hint",
                "menu_id": menu_id,
                "url": urljoin(config.start_url, route_hint),
                "route_hint": route_hint,
                "menu_path": entry.get("menu_path", []),
            }

        matched_page_ids = {
            _text(item)
            for item in reason.get("matched_page_ids", [])
            if _text(item)
        }
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_id = _text(page.get("page_id") or page.get("page_entry_id"))
            if matched_page_ids and page_id not in matched_page_ids:
                continue
            if not matched_page_ids and not (
                _record_matches_target(page, target, ("name", "title", "url", "failure_reason"))
                or _path_matches_target(page.get("menu_path"), target)
            ):
                continue
            url = _text(page.get("url"))
            if not url:
                continue
            return {
                "target": target,
                "source": "page_entry_url",
                "page_id": page_id,
                "url": urljoin(config.start_url, url),
                "menu_path": page.get("menu_path", []),
            }
    return None


async def _restore_target_page_start(
    page: Any,
    config: V3RunConfig,
    handover_reasons: list[dict[str, Any]],
    menu_bundle: dict[str, Any],
    page_bundle: dict[str, Any],
) -> dict[str, Any]:
    plan = _target_page_restore_plan(config, handover_reasons, menu_bundle, page_bundle)
    if not plan:
        return {"ok": False, "reason": "no_restore_plan"}
    url = _text(plan.get("url"))
    if not url:
        return {"ok": False, "reason": "restore_plan_missing_url", "plan": plan}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        with suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=2500)
        with suppress(Exception):
            await page.wait_for_timeout(300)
        return {
            "ok": True,
            "method": "goto",
            "url": url,
            "plan": plan,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": "target_page_restore_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "plan": plan,
        }


async def _return_to_start_url_after_menu_leaf(
    page: Any,
    config: V3RunConfig,
    *,
    menu_id: str,
    page_id: str,
) -> dict[str, Any]:
    target_url = _text(config.start_url)
    if not target_url:
        return {
            "event": "return_to_home_after_menu_leaf",
            "status": "skipped",
            "menu_id": menu_id,
            "page_entry_id": page_id,
            "failure_reason": "missing_start_url",
        }
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=8000)
        with suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=2500)
        with suppress(Exception):
            await page.wait_for_timeout(300)
        return {
            "event": "return_to_home_after_menu_leaf",
            "status": "completed",
            "menu_id": menu_id,
            "page_entry_id": page_id,
            "url": target_url,
            "method": "goto_start_url",
        }
    except Exception as exc:
        return {
            "event": "return_to_home_after_menu_leaf",
            "status": "failed",
            "menu_id": menu_id,
            "page_entry_id": page_id,
            "url": target_url,
            "failure_reason": f"{type(exc).__name__}: {exc}",
        }


async def _return_to_start_url_after_browser_use_handover(
    page: Any,
    config: V3RunConfig,
    *,
    targets: list[str],
) -> dict[str, Any]:
    target_url = _text(config.start_url)
    if not target_url:
        return {
            "event": "return_to_home_after_browser_use_handover",
            "status": "skipped",
            "targets": targets,
            "failure_reason": "missing_start_url",
        }
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=8000)
        with suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=2500)
        with suppress(Exception):
            await page.wait_for_timeout(300)
        return {
            "event": "return_to_home_after_browser_use_handover",
            "status": "completed",
            "targets": targets,
            "url": target_url,
            "method": "goto_start_url",
        }
    except Exception as exc:
        return {
            "event": "return_to_home_after_browser_use_handover",
            "status": "failed",
            "targets": targets,
            "url": target_url,
            "failure_reason": f"{type(exc).__name__}: {exc}",
        }


def _merge_browser_use_handover(
    page_bundle: dict[str, Any],
    handover: dict[str, Any] | None,
) -> dict[str, Any]:
    if not handover:
        return page_bundle
    return {
        **page_bundle,
        "pages": [
            *(page_bundle.get("pages") or []),
            *[item for item in handover.get("pages", []) if isinstance(item, dict)],
        ],
        "features": [
            *(page_bundle.get("features") or []),
            *[item for item in handover.get("features", []) if isinstance(item, dict)],
        ],
        "case_execution_results": [
            *(page_bundle.get("case_execution_results") or []),
            *[item for item in handover.get("case_execution_results", []) if isinstance(item, dict)],
        ],
        "page_exploration_log": [
            *(page_bundle.get("page_exploration_log") or []),
            *[item for item in handover.get("page_exploration_log", []) if isinstance(item, dict)],
        ],
        "screenshots_index": _merge_screenshot_indexes(
            page_bundle.get("screenshots_index", {}),
            handover.get("screenshots_index", {}),
        ),
    }


def _record_matches_target(record: dict[str, Any], target: str, fields: tuple[str, ...]) -> bool:
    normalized_target = _text(target)
    if not normalized_target:
        return False
    return any(normalized_target in _text(record.get(field)) for field in fields)


def _path_matches_target(path_value: Any, target: str) -> bool:
    if isinstance(path_value, list):
        return any(_text(target) in _text(item) for item in path_value)
    return _text(target) in _text(path_value)


async def _run_browser_use_target_handover(
    *,
    page: Any,
    config: V3RunConfig,
    run_dir: Path,
    screenshots_dir: Path,
    handover_reasons: list[dict[str, Any]],
    menu_bundle: dict[str, Any],
    page_bundle: dict[str, Any],
) -> dict[str, Any]:
    targets = [_text(item.get("target")) for item in handover_reasons if _text(item.get("target"))]
    screenshots: list[dict[str, Any]] = []
    with suppress(Exception):
        screenshots.append(
            await _capture_playwright_screenshot(
                page,
                screenshots_dir,
                "browser_use_handover_initial",
                f"Browser Use 接管前状态：{'、'.join(targets)}",
            )
        )
    try:
        from browser_use import Agent, Browser, ChatOpenAI, Tools  # type: ignore
    except Exception as exc:
        return _browser_use_handover_failure(
            targets,
            handover_reasons,
            "browser_use_unavailable",
            f"Browser Use 依赖不可用：{type(exc).__name__}: {exc}",
            screenshots,
        )

    profile = _load_browser_use_model_profile(config.model_name)
    if not profile:
        return _browser_use_handover_failure(
            targets,
            handover_reasons,
            "model_profile_unavailable",
            f"未找到可用于 Browser Use 接管的模型 profile：{config.model_name or '<empty>'}",
            screenshots,
        )

    try:
        browser = Browser(cdp_url=config.cdp_url)
        tools = Tools()
        uploaded_kinds_in_session: set[str] = set()
        network_events: list[dict[str, Any]] = []

        def capture_response(response: Any) -> None:
            if len(network_events) >= 100:
                return
            with suppress(Exception):
                request = response.request
                network_events.append(
                    {
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "url": _text(response.url),
                        "method": _text(request.method),
                        "status": response.status,
                    }
                )

        def write_network_events(capture_status: str = "enabled") -> None:
            with suppress(Exception):
                (run_dir / "network_events.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "stage2_network_events.v1",
                            "capture_status": capture_status,
                            "items": network_events,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )

        with suppress(Exception):
            page.on("response", capture_response)
        write_network_events("enabled")

        def append_tool_timing(action: str, status: str, started: float, detail: dict[str, Any] | None = None) -> None:
            record = {
                "at": datetime.now().isoformat(timespec="seconds"),
                "action": action,
                "status": status,
                "duration_ms": round((perf_counter() - started) * 1000),
                "detail": detail or {},
            }
            with suppress(Exception):
                with (run_dir / "browser_use_tool_timings.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            action_record = {
                "schema_version": "stage2_action_log.v1",
                "at": record["at"],
                "executor": "browser_use_playwright_tool",
                "action": action,
                "status": status,
                "duration_ms": record["duration_ms"],
                "detail": detail or {},
            }
            with suppress(Exception):
                with (run_dir / "action_log.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(action_record, ensure_ascii=False) + "\n")

        async def timed_tool(action: str, factory: Any) -> Any:
            started = perf_counter()
            try:
                result = await factory()
                append_tool_timing(
                    action,
                    "completed",
                    started,
                    {"result_preview": _text(result)[:BROWSER_USE_TOOL_RESULT_PREVIEW_LIMIT]},
                )
                return result
            except Exception as exc:
                append_tool_timing(
                    action,
                    "failed",
                    started,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
                raise

        restore_result = await timed_tool(
            "script_restore_target_page",
            lambda: _restore_target_page_start(
                page,
                config,
                handover_reasons,
                menu_bundle,
                page_bundle,
            ),
        )

        async def current_browser_page() -> Any:
            with suppress(Exception):
                current_page = await browser.get_current_page()
                if current_page and hasattr(current_page, "locator"):
                    return current_page
            return page

        async def clear_blocking_overlays(target_page: Any, *, preserve_business_dialogs: bool = False) -> str:
            return await target_page.evaluate(
                r"""(preserveBusinessDialogs) => {
                    let count = 0;
                    const selector = preserveBusinessDialogs
                      ? '.el-picker-panel,.el-select-dropdown,.el-calendar,.el-popover,.el-loading-mask,.el-message,.el-notification'
                      : '.v-modal,.el-picker-panel,.el-select-dropdown,.el-calendar,.el-popover,.el-message-box,.el-message-box__wrapper,.el-loading-mask,.el-message,.el-notification';
                    document.querySelectorAll(selector).forEach((el) => {
                      if (el.offsetHeight > 0 || el.offsetWidth > 0) {
                        el.style.display = 'none';
                        count += 1;
                      }
                    });
                    return `已关闭 ${count} 个弹窗`;
                }""",
                preserve_business_dialogs,
            )

        @tools.action(description="获取当前页面标题、URL 和可见文本摘要。")
        async def get_page_feedback() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                body_text = await _safe_playwright_body_text(target_page)
                title = ""
                with suppress(Exception):
                    title = await target_page.title()
                return json.dumps(
                    {"title": title, "url": target_page.url, "visible_text": body_text[:2000]},
                    ensure_ascii=False,
                )

            return await timed_tool("get_page_feedback", run)

        @tools.action(description="[脚本内部工具] 关闭所有弹窗、遮罩、日期选择器和提示层。Agent 不需要传参数。")
        async def script_close_popups() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                return await clear_blocking_overlays(target_page)

            return await timed_tool("script_close_popups", run)

        async def select_required_online_apply_dropdowns(target_page: Any) -> dict[str, Any]:
            async def visible_text(locator: Any) -> str:
                with suppress(Exception):
                    return _text(await locator.inner_text(timeout=800))
                return ""

            async def is_visible(locator: Any) -> bool:
                with suppress(Exception):
                    return bool(await locator.is_visible(timeout=800))
                return False

            async def click_first_visible(container: Any, selectors: list[str]) -> bool:
                for selector in selectors:
                    locator = container.locator(selector).first
                    if await locator.count() and await is_visible(locator):
                        await locator.scroll_into_view_if_needed(timeout=1500)
                        await locator.click(timeout=2500)
                        return True
                return False

            async def active_form_root() -> Any:
                roots = target_page.locator(
                    ".el-dialog:not([style*='display: none']), .el-drawer__wrapper:not([style*='display: none'])"
                )
                count = await roots.count()
                for index in range(count - 1, -1, -1):
                    root = roots.nth(index)
                    if await is_visible(root) and await root.locator(".el-form-item").count():
                        return root
                return target_page

            dropdown_trigger_selectors = [
                "[role=combobox]",
                "input[placeholder*='请选择']",
                ".el-select .el-input__wrapper",
                ".el-select__wrapper",
                ".el-select__placeholder",
                ".el-select__selected-item",
                ".el-select",
                ".vue-treeselect__control",
                ".vue-treeselect__placeholder",
                ".vue-treeselect__single-value",
                ".vue-treeselect__input",
                ".vue-treeselect",
                "input[readonly]",
                ".el-input__wrapper",
                ".el-input",
            ]

            diagnostic_selectors = [
                ".el-select",
                ".el-select__wrapper",
                ".el-select__placeholder",
                ".el-select__selected-item",
                ".vue-treeselect",
                ".vue-treeselect__control",
                ".vue-treeselect__placeholder",
                ".vue-treeselect__single-value",
                ".vue-treeselect__input",
                ".el-input__wrapper",
                "input",
                "[role=combobox]",
                "[aria-haspopup]",
                "[tabindex]",
                "button",
            ]

            async def find_enabled_trigger(container: Any) -> Any | None:
                for selector in dropdown_trigger_selectors:
                    triggers = container.locator(selector)
                    trigger_count = await triggers.count()
                    for trigger_index in range(trigger_count):
                        trigger = triggers.nth(trigger_index)
                        with suppress(Exception):
                            if await is_visible(trigger) and not await widget_is_disabled(trigger):
                                return trigger
                return None

            async def item_has_enabled_widget(item: Any) -> bool:
                return await find_enabled_trigger(item) is not None

            async def widget_is_disabled(widget: Any) -> bool:
                with suppress(Exception):
                    return bool(
                        await widget.evaluate(
                            r"""(el) => Boolean(
                              el.disabled ||
                              el.getAttribute('aria-disabled') === 'true' ||
                              el.classList.contains('is-disabled') ||
                              el.closest('.is-disabled')
                            )"""
                        )
                    )
                return False

            async def item_label_text(item: Any) -> str:
                for selector in [".el-form-item__label", ".el-form-item__label-wrap", ".title", "label"]:
                    locator = item.locator(selector).first
                    if await locator.count():
                        text = await visible_text(locator)
                        if text:
                            return text
                return await visible_text(item)

            async def already_has_selected_value(item: Any) -> str | None:
                inputs = item.locator(".el-select input,.el-cascader input,.vue-treeselect input,input[readonly]")
                count = await inputs.count()
                for index in range(count):
                    input_locator = inputs.nth(index)
                    with suppress(Exception):
                        if not await is_visible(input_locator):
                            continue
                        selected_value = await native_control_selected_value(input_locator)
                        if selected_value:
                            return selected_value
                selected_items = item.locator(
                    ".el-select__selected-item,.el-select__placeholder,"
                    ".vue-treeselect__single-value,.vue-treeselect__placeholder"
                )
                selected_count = await selected_items.count()
                for index in range(selected_count):
                    selected_item = selected_items.nth(index)
                    with suppress(Exception):
                        if not await is_visible(selected_item):
                            continue
                        selected_text = await visible_text(selected_item)
                        if selected_text and not re.search(r"请选择|全部", selected_text):
                            return selected_text
                return None

            async def collect_form_item_diagnostics(
                label_patterns: list[re.Pattern[str]],
                label_texts: list[str] | None = None,
                placeholder_texts: list[str] | None = None,
            ) -> dict[str, Any]:
                form_items = (await active_form_root()).locator(".el-form-item")
                count = await form_items.count()
                expected_labels = [text.strip() for text in (label_texts or []) if text and text.strip()]
                expected_placeholders = [text.strip() for text in (placeholder_texts or []) if text and text.strip()]
                candidate_labels: list[dict[str, Any]] = []
                widget_labels: list[dict[str, Any]] = []
                matched_label_diagnostics: list[dict[str, Any]] = []

                async def selector_counts(item: Any) -> dict[str, int]:
                    counts: dict[str, int] = {}
                    for selector in diagnostic_selectors:
                        with suppress(Exception):
                            counts[selector] = await item.locator(selector).count()
                    return counts

                async def trigger_candidates(item: Any) -> list[dict[str, Any]]:
                    candidates: list[dict[str, Any]] = []
                    for selector in dropdown_trigger_selectors:
                        triggers = item.locator(selector)
                        trigger_count = await triggers.count()
                        for trigger_index in range(min(trigger_count, 4)):
                            trigger = triggers.nth(trigger_index)
                            with suppress(Exception):
                                candidates.append(
                                    {
                                        "selector": selector,
                                        "index": trigger_index,
                                        "visible": await is_visible(trigger),
                                        "disabled": await widget_is_disabled(trigger),
                                        "text": (await visible_text(trigger))[:120],
                                    }
                                )
                            if len(candidates) >= 12:
                                return candidates
                    return candidates

                async def item_diagnostic(index: int, item: Any, text: str) -> dict[str, Any]:
                    class_name = ""
                    outer_html = ""
                    with suppress(Exception):
                        class_name = _text(await item.evaluate("(el) => el.className || ''"))[:180]
                    with suppress(Exception):
                        outer_html = _text(await item.evaluate("(el) => el.outerHTML || ''"))[:900]
                    return {
                        "index": index,
                        "label_text": text[:180],
                        "class_name": class_name,
                        "selector_counts": await selector_counts(item),
                        "trigger_candidates": await trigger_candidates(item),
                        "outer_html_preview": outer_html,
                    }

                for index in range(count):
                    item = form_items.nth(index)
                    label_only = await item_label_text(item)
                    text = label_only or await visible_text(item)
                    if not text:
                        continue
                    label_match = bool(
                        any(expected_label in text for expected_label in expected_labels)
                        or any(pattern.search(text) for pattern in label_patterns)
                    )
                    entry = {"index": index, "label_text": text[:180]}
                    if len(candidate_labels) < 40:
                        candidate_labels.append(entry)
                    if await item_has_enabled_widget(item) and len(widget_labels) < 40:
                        widget_labels.append(entry)
                    if label_match and len(matched_label_diagnostics) < 5:
                        matched_label_diagnostics.append(await item_diagnostic(index, item, text))

                placeholder_candidates: list[dict[str, Any]] = []
                for placeholder in expected_placeholders:
                    visible_placeholder_triggers = target_page.locator(
                        f"xpath=//*[contains(@class, 'el-select__placeholder') and contains(normalize-space(.), '{placeholder}')] | "
                        f"//*[contains(@class, 'el-select__selected-item') and contains(normalize-space(.), '{placeholder}')] | "
                        f"//*[contains(@class, 'vue-treeselect__placeholder') and contains(normalize-space(.), '{placeholder}')] | "
                        f"//*[contains(@class, 'vue-treeselect__single-value') and contains(normalize-space(.), '{placeholder}')] | "
                        f"//input[contains(@placeholder, '{placeholder}')]"
                    )
                    candidate_count = await visible_placeholder_triggers.count()
                    for candidate_index in range(min(candidate_count, 8)):
                        candidate = visible_placeholder_triggers.nth(candidate_index)
                        with suppress(Exception):
                            placeholder_candidates.append(
                                {
                                    "placeholder": placeholder,
                                    "index": candidate_index,
                                    "visible": await is_visible(candidate),
                                    "disabled": await widget_is_disabled(candidate),
                                    "text": (await visible_text(candidate))[:120],
                                }
                            )
                return {
                    "expected_labels": expected_labels,
                    "expected_placeholders": expected_placeholders,
                    "label_patterns": [pattern.pattern for pattern in label_patterns],
                    "form_item_count": count,
                    "candidate_labels": candidate_labels,
                    "widget_labels": widget_labels,
                    "matched_label_diagnostics": matched_label_diagnostics,
                    "placeholder_candidates": placeholder_candidates,
                }

            async def find_labeled_form_item(
                form_items: Any,
                count: int,
                *,
                label_patterns: list[re.Pattern[str]],
                expected_labels: list[str],
            ) -> tuple[Any | None, str]:
                for index in range(count):
                    item = form_items.nth(index)
                    label_only = await item_label_text(item)
                    text = label_only or await visible_text(item)
                    label_match = bool(
                        text and (
                            any(expected_label in text for expected_label in expected_labels)
                            or any(pattern.search(text) for pattern in label_patterns)
                        )
                    )
                    if not label_match:
                        continue
                    if await item_has_enabled_widget(item):
                        return item, text
                    neighbor_form_items = [
                        form_items.nth(neighbor_index)
                        for neighbor_index in range(index + 1, min(count, index + 3))
                    ]
                    for neighbor in neighbor_form_items:
                        if await item_has_enabled_widget(neighbor):
                            return neighbor, text
                return None, ""

            async def choose_dropdown(
                label_patterns: list[re.Pattern[str]],
                *,
                label_texts: list[str] | None = None,
                placeholder_texts: list[str] | None = None,
                preferred: list[re.Pattern[str]] | None = None,
                avoid: list[re.Pattern[str]] | None = None,
            ) -> dict[str, Any]:
                form_items = (await active_form_root()).locator(".el-form-item")
                count = await form_items.count()
                expected_labels = [text.strip() for text in (label_texts or []) if text and text.strip()]
                expected_placeholders = [text.strip() for text in (placeholder_texts or []) if text and text.strip()]
                matched_item, matched_label = await find_labeled_form_item(
                    form_items,
                    count,
                    label_patterns=label_patterns,
                    expected_labels=expected_labels,
                )
                matched_trigger = None
                matched_trigger_is_value_reader = False

                async def find_field_by_placeholder() -> tuple[Any | None, str, Any | None, bool]:
                    async def trigger_container(trigger: Any) -> Any:
                        container = trigger.locator(
                            "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' el-form-item ')][1]"
                        )
                        if await container.count():
                            return container
                        select_container = trigger.locator(
                            "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' el-select ')][1]"
                        )
                        if await select_container.count():
                            return select_container
                        return trigger.locator("xpath=..")

                    for placeholder in expected_placeholders:
                        triggers = target_page.locator(
                            f"input[placeholder*='{placeholder}'],textarea[placeholder*='{placeholder}'],"
                            f"[role=combobox][placeholder*='{placeholder}']"
                        )
                        trigger_count = await triggers.count()
                        for trigger_index in range(trigger_count):
                            trigger = triggers.nth(trigger_index)
                            if not await is_visible(trigger) or await widget_is_disabled(trigger):
                                continue
                            return await trigger_container(trigger), placeholder, trigger, True
                        visible_placeholder_triggers = target_page.locator(
                            f"xpath=//*[contains(@class, 'el-select__placeholder') and contains(normalize-space(.), '{placeholder}')] | "
                            f"//*[contains(@class, 'el-select__selected-item') and contains(normalize-space(.), '{placeholder}')] | "
                            f"//*[contains(@class, 'vue-treeselect__placeholder') and contains(normalize-space(.), '{placeholder}')] | "
                            f"//*[contains(@class, 'vue-treeselect__single-value') and contains(normalize-space(.), '{placeholder}')]"
                        )
                        visible_placeholder_count = await visible_placeholder_triggers.count()
                        for trigger_index in range(visible_placeholder_count):
                            trigger = visible_placeholder_triggers.nth(trigger_index)
                            if not await is_visible(trigger) or await widget_is_disabled(trigger):
                                continue
                            return await trigger_container(trigger), placeholder, trigger, False
                    return None, "", None, False

                if matched_item is None:
                    matched_item, matched_label, matched_trigger, matched_trigger_is_value_reader = await find_field_by_placeholder()
                    if matched_item is None:
                        return {
                            "ok": False,
                            "reason": "form_item_not_found",
                            "diagnostics": await collect_form_item_diagnostics(
                                label_patterns,
                                label_texts,
                                placeholder_texts,
                            ),
                        }

                existing_value = (
                    await native_control_selected_value(matched_trigger)
                    if matched_trigger is not None and matched_trigger_is_value_reader
                    else await already_has_selected_value(matched_item)
                )
                if existing_value:
                    return {
                        "ok": True,
                        "selected": existing_value,
                        "skipped": "already_selected",
                        "label_text": matched_label[:120],
                    }

                clicked = False
                if matched_trigger is not None:
                    await matched_trigger.scroll_into_view_if_needed(timeout=1500)
                    await matched_trigger.click(timeout=2500)
                    clicked = True
                else:
                    clicked = await click_first_visible(
                        matched_item,
                        dropdown_trigger_selectors,
                    )
                if not clicked:
                    return {"ok": False, "reason": "trigger_not_found", "label_text": matched_label}

                await target_page.wait_for_timeout(350)
                options = target_page.locator(
                    ".el-select-dropdown .el-select-dropdown__item:not(.is-disabled), "
                    "[role=option]:not(.is-disabled), "
                    ".el-cascader-node:not(.is-disabled), "
                    ".vue-treeselect__option:not(.vue-treeselect__option--disabled), "
                    ".vue-treeselect__label-container"
                )
                option_count = await options.count()
                candidates: list[tuple[Any, str]] = []
                for index in range(option_count):
                    option = options.nth(index)
                    text = await visible_text(option)
                    if await is_visible(option) and text and not re.search(r"请选择|全部", text):
                        candidates.append((option, text))
                if not candidates:
                    return {
                        "ok": False,
                        "reason": "option_not_found",
                        "label_text": matched_label,
                        "diagnostics": await collect_form_item_diagnostics(
                            label_patterns,
                            label_texts,
                            placeholder_texts,
                        ),
                    }

                preferred = preferred or []
                avoid = avoid or []
                selected = None
                for option, text in candidates:
                    if any(pattern.search(text) for pattern in preferred):
                        selected = (option, text)
                        break
                if selected is None:
                    for option, text in candidates:
                        if not any(pattern.search(text) for pattern in avoid):
                            selected = (option, text)
                            break
                selected = selected or candidates[0]
                await selected[0].scroll_into_view_if_needed(timeout=1500)
                await selected[0].click(timeout=2500)
                await target_page.wait_for_timeout(350)
                committed_value = (
                    await native_control_selected_value(matched_trigger)
                    if matched_trigger is not None and matched_trigger_is_value_reader
                    else await already_has_selected_value(matched_item)
                )
                return {
                    "ok": bool(committed_value),
                    "selected": selected[1],
                    "selected_value": committed_value,
                    "label_text": matched_label[:120],
                    "reason": None if committed_value else "value_not_committed",
                }

            async def choose_cascader(
                label_patterns: list[re.Pattern[str]],
                *,
                label_texts: list[str] | None = None,
                max_depth: int = 4,
            ) -> dict[str, Any]:
                async def visible_cascader_menus() -> list[Any]:
                    menus = target_page.locator(".el-cascader-panel .el-cascader-menu")
                    menu_count = await menus.count()
                    return [menus.nth(index) for index in range(menu_count) if await is_visible(menus.nth(index))]

                async def wait_for_visible_cascader_menu_count(
                    min_count: int,
                    *,
                    timeout_ms: int = 2500,
                ) -> tuple[list[Any], bool]:
                    deadline = perf_counter() + (timeout_ms / 1000)
                    visible_menus = await visible_cascader_menus()
                    while len(visible_menus) < min_count and perf_counter() < deadline:
                        await target_page.wait_for_timeout(150)
                        visible_menus = await visible_cascader_menus()
                    return visible_menus, len(visible_menus) >= min_count

                form_items = (await active_form_root()).locator(".el-form-item")
                count = await form_items.count()
                matched_item = None
                matched_label = ""
                expected_labels = [text.strip() for text in (label_texts or []) if text and text.strip()]
                for index in range(count):
                    item = form_items.nth(index)
                    label_only = await item_label_text(item)
                    text = label_only or await visible_text(item)
                    label_match = bool(
                        text and (
                            any(expected_label in text for expected_label in expected_labels)
                            or any(pattern.search(text) for pattern in label_patterns)
                        )
                    )
                    if label_match and await item_has_enabled_widget(item):
                        matched_item = item
                        matched_label = text
                        break
                if matched_item is None:
                    return {
                        "ok": False,
                        "reason": "form_item_not_found",
                        "diagnostics": await collect_form_item_diagnostics(label_patterns, label_texts),
                    }
                existing_value = await already_has_selected_value(matched_item)
                if existing_value:
                    return {
                        "ok": True,
                        "selected_path": [existing_value],
                        "skipped": "already_selected",
                        "label_text": matched_label[:120],
                    }
                clicked = await click_first_visible(
                    matched_item,
                    [
                        ".el-cascader",
                        ".el-cascader__label",
                        ".el-cascader .el-input__wrapper",
                        "input[readonly]",
                        ".el-input__wrapper",
                        ".el-input",
                    ],
                )
                if not clicked:
                    return {"ok": False, "reason": "trigger_not_found", "label_text": matched_label}
                await target_page.wait_for_timeout(400)
                selected_path: list[str] = []
                trace: list[dict[str, Any]] = []
                for depth in range(max_depth):
                    visible_menus, _ = await wait_for_visible_cascader_menu_count(
                        depth + 1,
                        timeout_ms=2500,
                    )
                    if depth >= len(visible_menus):
                        return {
                            "ok": False,
                            "selected_path": selected_path,
                            "label_text": matched_label[:120],
                            "reason": "cascader_menu_not_visible",
                            "depth": depth,
                            "visible_menu_count": len(visible_menus),
                            "trace": trace,
                        }
                    nodes = visible_menus[depth].locator(".el-cascader-node:not(.is-disabled)")
                    node_count = await nodes.count()
                    chosen = None
                    chosen_text = ""
                    node_candidates: list[str] = []
                    for node_index in range(node_count):
                        node = nodes.nth(node_index)
                        text = await visible_text(node)
                        if text and len(node_candidates) < 8:
                            node_candidates.append(text)
                        if await is_visible(node) and text and not re.search(r"请选择|全部", text):
                            chosen = node
                            chosen_text = text
                            break
                    if chosen is None:
                        return {
                            "ok": False,
                            "selected_path": selected_path,
                            "label_text": matched_label[:120],
                            "reason": "cascader_option_not_found",
                            "depth": depth,
                            "visible_menu_count": len(visible_menus),
                            "node_candidates": node_candidates,
                            "trace": trace,
                        }
                    await chosen.scroll_into_view_if_needed(timeout=1500)
                    await chosen.click(timeout=2500)
                    selected_path.append(chosen_text)
                    await target_page.wait_for_timeout(400)
                    next_visible_menus, next_menu_visible = (
                        await wait_for_visible_cascader_menu_count(depth + 2, timeout_ms=2500)
                        if depth < max_depth - 1
                        else (await visible_cascader_menus(), False)
                    )
                    committed_after_click = await already_has_selected_value(matched_item)
                    panel_visible = await is_visible(target_page.locator(".el-cascader-panel").first)
                    trace.append(
                        {
                            "depth": depth,
                            "selected": chosen_text,
                            "visible_menu_count": len(next_visible_menus),
                            "next_menu_visible": next_menu_visible,
                            "panel_visible": panel_visible,
                            "committed_value": committed_after_click,
                        }
                    )
                    if next_menu_visible:
                        continue
                    if committed_after_click and not panel_visible:
                        break
                    if depth < max_depth - 1:
                        return {
                            "ok": False,
                            "selected_path": selected_path,
                            "label_text": matched_label[:120],
                            "reason": "terminal_level_not_reached",
                            "visible_menu_count": len(next_visible_menus),
                            "committed_value": committed_after_click,
                            "trace": trace,
                        }
                    if committed_after_click:
                        break
                committed_value = await already_has_selected_value(matched_item)
                return {
                    "ok": bool(committed_value),
                    "selected_path": selected_path,
                    "selected_value": committed_value,
                    "label_text": matched_label[:120],
                    "reason": None if committed_value else "option_not_found",
                    "trace": trace,
                }

            with suppress(Exception):
                await target_page.keyboard.press("Escape")
            with suppress(Exception):
                await clear_blocking_overlays(target_page)
            control_timings: list[dict[str, Any]] = []

            async def timed_control(control_id: str, label: str, factory: Any) -> dict[str, Any]:
                started = perf_counter()
                try:
                    result = await factory()
                    control_timings.append(
                        {
                            "control_id": control_id,
                            "label": label,
                            "status": "completed" if result.get("ok") else "failed",
                            "duration_ms": round((perf_counter() - started) * 1000),
                            "reason": result.get("reason"),
                        }
                    )
                    return result
                except Exception as exc:
                    control_timings.append(
                        {
                            "control_id": control_id,
                            "label": label,
                            "status": "failed",
                            "duration_ms": round((perf_counter() - started) * 1000),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    raise

            plant_result = await timed_control(
                "plantId",
                "备案品种",
                lambda: choose_dropdown([re.compile(r"备案品种|品种|plantId", re.I)]),
            )
            register_type_result = await timed_control(
                "registerType",
                "备案类型",
                lambda: choose_dropdown([re.compile(r"备案类型|类型|registerType", re.I)]),
            )
            seedling_method_result = await timed_control(
                "seedlingMethod",
                "育苗方式",
                lambda: choose_dropdown(
                    [re.compile(r"育苗方式|繁殖方式|seedling|breeding", re.I)],
                    label_texts=["育苗方式"],
                    preferred=[
                        re.compile(r"分蘗繁殖|分蘖繁殖|炼苗|其他|扦插|分株|组培|组织培养", re.I),
                    ],
                    avoid=[re.compile(r"种子繁殖|种子", re.I)],
                ),
            )
            seedling_purpose_result = await timed_control(
                "seedlingPurpose",
                "育苗目的",
                lambda: choose_dropdown([re.compile(r"育苗目的|目的|purpose", re.I)]),
            )
            acceptance_unit_result = await timed_control(
                "acceptanceUnit",
                "验收监管单位",
                lambda: choose_dropdown(
                    [],
                    label_texts=["验收监管单位"],
                    placeholder_texts=["请选择验收监管单位"],
                    preferred=[re.compile(r"广西壮族自治区林业局|林业局|监管", re.I)],
                ),
            )
            seedling_location_result = await timed_control(
                "seedlingLocation",
                "育苗地址/育苗地点",
                lambda: choose_cascader(
                    [],
                    label_texts=["育苗地址", "育苗地点"],
                ),
            )
            return {
                "plantId": plant_result,
                "registerType": register_type_result,
                "seedlingMethod": seedling_method_result,
                "seedlingPurpose": seedling_purpose_result,
                "acceptanceUnit": acceptance_unit_result,
                "seedlingLocation": seedling_location_result,
                "control_timings": control_timings,
            }

        async def select_required_online_apply_dates(target_page: Any) -> list[dict[str, Any]]:
            async def visible_text(locator: Any) -> str:
                with suppress(Exception):
                    return _text(await locator.inner_text(timeout=800))
                return ""

            desired_dates = [
                "育苗开始日期",
                "验收日期",
            ]
            selected: list[dict[str, Any]] = []
            active_form = target_page.locator(
                ".el-dialog:not([style*='display: none']) .el-form-item, "
                ".el-drawer__wrapper:not([style*='display: none']) .el-form-item"
            )
            form_items = active_form if await active_form.count() else target_page.locator(".el-form-item")
            item_count = await form_items.count()
            for label in desired_dates:
                matched_item = None
                for index in range(item_count):
                    item = form_items.nth(index)
                    text = await visible_text(item)
                    if text and label in text:
                        matched_item = item
                        break
                if matched_item is None:
                    selected.append({"label": label, "ok": False, "reason": "form_item_not_found"})
                    continue
                input_locator = matched_item.locator(
                    ".el-date-editor input:not([type=hidden]), input[type=date], input[placeholder*='日期'], input[placeholder*='date']"
                ).first
                if not await input_locator.count():
                    selected.append({"label": label, "ok": False, "reason": "date_input_not_found"})
                    continue
                try:
                    existing_value = _text(await input_locator.input_value(timeout=800))
                    if existing_value:
                        selected.append(
                            {
                                "label": label,
                                "ok": True,
                                "value": existing_value,
                                "skipped": "already_selected",
                            }
                        )
                        continue
                    await input_locator.scroll_into_view_if_needed(timeout=1500)
                    await input_locator.click(timeout=2500)
                    await target_page.wait_for_timeout(300)
                    picker = target_page.locator(".el-picker-panel:visible,.el-date-picker:visible").last
                    if not await picker.count():
                        selected.append({"label": label, "ok": False, "reason": "date_picker_not_opened"})
                        continue
                    day_cells = picker.locator(
                        "td.available:not(.disabled):not(.today) .cell, "
                        "td.available:not(.disabled):not(.today)"
                    )
                    if not await day_cells.count():
                        selected.append({"label": label, "ok": False, "reason": "date_option_not_found"})
                        continue
                    day_cell = day_cells.first
                    await day_cell.scroll_into_view_if_needed(timeout=1500)
                    await day_cell.click(timeout=2500)
                    await target_page.wait_for_timeout(300)
                    with suppress(Exception):
                        await input_locator.dispatch_event("change")
                        await input_locator.dispatch_event("blur")
                    selected_value = _text(await input_locator.input_value(timeout=800))
                    selected.append({"label": label, "ok": bool(selected_value), "value": selected_value or None})
                except Exception as exc:
                    selected.append(
                        {"label": label, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
                    )
            return selected

        async def repair_online_apply_required_fields(target_page: Any) -> dict[str, Any]:
            with suppress(Exception):
                await clear_blocking_overlays(target_page)

            async def visible_text(locator: Any) -> str:
                with suppress(Exception):
                    return _text(await locator.inner_text(timeout=800))
                return ""

            async def repair_required_dates() -> list[dict[str, Any]]:
                return await select_required_online_apply_dates(target_page)

            async def collect_validation_errors() -> list[str]:
                errors = await target_page.evaluate(
                    r"""() => Array.from(document.querySelectorAll('.el-form-item__error,.el-message,.el-notification'))
                      .map((el) => (el.innerText || el.textContent || '').trim())
                      .filter(Boolean)"""
                )
                return [str(item) for item in errors] if isinstance(errors, list) else []

            fill_summary = {"dates": [], "text_fields": [], "errors": [], "skipped": "date_text_prefill_disabled"}
            validation_errors_before = await collect_validation_errors()
            repair_plan = _online_apply_repair_plan(validation_errors_before)
            date_summary = (
                await repair_required_dates()
                if repair_plan["dates"]
                else [{"skipped": "not_requested_by_validation_errors"}]
            )
            dropdown_summary = (
                await select_required_online_apply_dropdowns(target_page)
                if repair_plan["dropdowns"]
                else {"ok": True, "skipped": "not_requested_by_validation_errors"}
            )
            upload_summary = (
                await upload_online_apply_sample_files(target_page, trust_session_uploads=False)
                if repair_plan["uploads"]
                else {"ok": True, "skipped": "not_requested_by_validation_errors"}
            )
            validation_errors = await collect_validation_errors()
            return {
                "ok": True,
                "filled": fill_summary,
                "repair_plan": repair_plan,
                "validation_errors_before": validation_errors_before,
                "date_repairs": date_summary,
                "dropdowns": dropdown_summary,
                "uploads": upload_summary,
                "validation_errors": validation_errors,
            }

        async def prefill_visible_online_apply_fields(target_page: Any) -> dict[str, Any]:
            fill_result = await target_page.evaluate(
                r"""() => {
                    const results = { filled: 0, checked: 0, skipped: 0, errors: [] };
                    const fire = (el, type) => el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
                    const setNativeValue = (el, value) => {
                      const prototype = Object.getPrototypeOf(el);
                      const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value')
                        || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                        || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
                      if (descriptor && descriptor.set) {
                        descriptor.set.call(el, value);
                      } else {
                        el.value = value;
                      }
                    };
                    const labelText = (el) => {
                      const item = el.closest('.el-form-item');
                      return (item && (item.innerText || item.textContent) || '').trim();
                    };
                    const looksLikeChoiceField = (el) => {
                      const label = labelText(el);
                      const placeholder = (el.getAttribute('placeholder') || '').trim();
                      const classText = [
                        el.className || '',
                        el.parentElement && el.parentElement.className || '',
                        el.closest('.el-form-item') && el.closest('.el-form-item').className || ''
                      ].join(' ');
                      return /验收监管单位|监管单位|备案品种|备案类型|育苗方式|育苗目的|育苗地址|育苗地点/.test(label)
                        || /请选择|选择|下拉/.test(placeholder)
                        || /\bel-select\b|\bel-cascader\b|select|cascader/i.test(classText)
                        || el.getAttribute('role') === 'combobox'
                        || el.getAttribute('aria-haspopup') === 'listbox';
                    };
                    const valueFor = (el) => {
                      const label = labelText(el);
                      const type = (el.type || 'text').toLowerCase();
                      if (/面积|数量|株数|重量|亩|number|amount|count|area/i.test(label) || type === 'number') return '100';
                      if (/电话|手机|联系方式|tel|phone/i.test(label) || type === 'tel') return '13800138000';
                      if (/邮箱|email/i.test(label) || type === 'email') return 'test@test.com';
                      if (/联系人|人员|姓名/i.test(label)) return '测试人员';
                      return '测试数据';
                    };
                    const fields = document.querySelectorAll(
                      'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=checkbox]):not([type=radio]), textarea'
                    );
                    fields.forEach((el) => {
                      const isWidgetInput = Boolean(el.closest('.el-select,.el-cascader'));
                      const isDateInput = Boolean(el.closest('.el-date-editor'));
                      const isChoiceField = looksLikeChoiceField(el);
                      if (el.disabled || !el.offsetParent || isWidgetInput || isDateInput || isChoiceField || el.readOnly) {
                        results.skipped += 1;
                        return;
                      }
                      try {
                        setNativeValue(el, valueFor(el));
                        fire(el, 'input');
                        fire(el, 'change');
                        fire(el, 'blur');
                        el.dispatchEvent(new CustomEvent('el-input-change', { bubbles: true }));
                        el.dispatchEvent(new CustomEvent('el-input-input', { bubbles: true }));
                        results.filled += 1;
                      } catch (error) {
                        results.errors.push(error && error.message ? error.message : String(error));
                      }
                    });
                    document.querySelectorAll('input[type=checkbox]').forEach((el) => {
                      if (!el.checked && !el.disabled && el.offsetParent) {
                        el.click();
                        fire(el, 'change');
                        fire(el, 'input');
                        results.checked += 1;
                      }
                    });
                    window.scrollTo(0, document.body.scrollHeight);
                    return JSON.stringify(results);
                }"""
            )
            return json.loads(fill_result) if isinstance(fill_result, str) else fill_result

        @tools.action(description="[脚本内部工具] 预填写表单、勾选协议，并用 Playwright 直接点击必填下拉列表项。Agent 不需要传参数。")
        async def script_prefill_form() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                phases: list[dict[str, Any]] = []

                async def run_prefill_phase(phase: str, factory: Any) -> Any:
                    started = perf_counter()
                    try:
                        result = await factory()
                        phases.append(
                            {
                                "phase": phase,
                                "status": "completed",
                                "duration_ms": round((perf_counter() - started) * 1000),
                                "result": result,
                            }
                        )
                        return result
                    except Exception as exc:
                        phases.append(
                            {
                                "phase": phase,
                                "status": "failed",
                                "duration_ms": round((perf_counter() - started) * 1000),
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        raise

                with suppress(Exception):
                    await clear_blocking_overlays(target_page)
                first_fill_result = await run_prefill_phase(
                    "plain_prefill_initial",
                    lambda: prefill_visible_online_apply_fields(target_page),
                )
                dropdown_result = await run_prefill_phase(
                    "native_controls",
                    lambda: select_required_online_apply_dropdowns(target_page),
                )
                date_result = await run_prefill_phase(
                    "native_date_controls",
                    lambda: select_required_online_apply_dates(target_page),
                )
                with suppress(Exception):
                    await clear_blocking_overlays(target_page)
                second_fill_result = await run_prefill_phase(
                    "plain_prefill_after_native_controls",
                    lambda: prefill_visible_online_apply_fields(target_page),
                )
                return json.dumps(
                    {
                        "ok": True,
                        "phases": phases,
                        "prefill": first_fill_result,
                        "dropdowns": dropdown_result,
                        "dates": date_result,
                        "expanded_prefill": second_fill_result,
                    },
                    ensure_ascii=False,
                )

            return await timed_tool("script_prefill_form", run)

        @tools.action(description="[脚本内部工具] 用 Playwright 直接点击备案申请表必填下拉项：备案品种、备案类型、育苗方式。Agent 不需要传参数。")
        async def script_select_required_dropdowns() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                with suppress(Exception):
                    await clear_blocking_overlays(target_page)
                return json.dumps(await select_required_online_apply_dropdowns(target_page), ensure_ascii=False)

            return await timed_tool("script_select_required_dropdowns", run)

        async def upload_online_apply_sample_files(
            target_page: Any,
            *,
            trust_session_uploads: bool = True,
        ) -> dict[str, Any]:
            samples = _ensure_online_apply_upload_samples(run_dir)
            with suppress(Exception):
                await clear_blocking_overlays(target_page)
            inputs = target_page.locator("input[type=file]")
            input_count = await inputs.count()
            if input_count <= 0:
                return {"ok": False, "reason": "file_inputs_not_found", "uploaded": [], "input_count": 0}

            async def label_text_for_file_input(file_input: Any) -> str:
                with suppress(Exception):
                    label = await file_input.evaluate(
                        r"""(input) => {
                            const upload = input.closest('.el-upload,.el-upload-dragger,[class*=upload]');
                            const item = input.closest('.el-form-item') || (upload && upload.closest('.el-form-item'));
                            const labelNode = item && (
                              item.querySelector('.el-form-item__label')
                              || item.querySelector('.el-form-item__label-wrap')
                              || item.querySelector('.title')
                              || item.querySelector('label')
                            );
                            const labelText = ((labelNode && (labelNode.innerText || labelNode.textContent)) || '').trim();
                            if (labelText) return labelText;
                            const parent = item || upload || input.parentElement;
                            const rawText = ((parent && (parent.innerText || parent.textContent)) || '').trim();
                            const lines = rawText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
                            return lines[0] || rawText;
                        }"""
                    )
                    return _text(label)
                return ""

            def sample_for(label: str, accept: str, index: int) -> tuple[str, Path]:
                hint = f"{label} {accept}"
                if re.search(r"验收文件|验收文件00|验收", hint, re.I):
                    return "acceptance", samples["acceptance"]
                if re.search(r"附件|doc|docx|word", hint, re.I):
                    return "attachment", samples["attachment"]
                if re.search(r"育苗人员信息表|人员信息表|人员|xls|xlsx|excel|表格", hint, re.I):
                    return "personnel", samples["personnel"]
                if re.search(r"备案图片|图片|照片|图像|image|jpg|jpeg|png", hint, re.I):
                    return "image", samples["image"]
                fallback = [
                    ("personnel", samples["personnel"]),
                    ("image", samples["image"]),
                    ("attachment", samples["attachment"]),
                    ("acceptance", samples["acceptance"]),
                ]
                return fallback[index] if index < len(fallback) else ("acceptance", samples["acceptance"])

            uploaded: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            seen_kinds: set[str] = set()
            for index in range(input_count):
                file_input = inputs.nth(index)
                label = await label_text_for_file_input(file_input)
                accept = ""
                with suppress(Exception):
                    accept = _text(await file_input.get_attribute("accept") or "")
                kind, sample_path = sample_for(label, accept, index)
                if kind in uploaded_kinds_in_session:
                    if trust_session_uploads:
                        seen_kinds.add(kind)
                        skipped.append({
                            "kind": kind,
                            "input_index": index,
                            "label_text": label[:120],
                            "accept": accept,
                            "skipped": "already_uploaded_in_session",
                        })
                        continue
                existing_name = await native_upload_existing_file_name(file_input)
                if existing_name == "__stage2_existing_upload_image__" and kind != "image":
                    skipped.append({
                        "kind": kind,
                        "input_index": index,
                        "label_text": label[:120],
                        "accept": accept,
                        "skipped": "ignored_thumbnail_sentinel_for_non_image_slot",
                        "existing_file_name": existing_name,
                    })
                    existing_name = ""
                if existing_name:
                    seen_kinds.add(kind)
                    uploaded_kinds_in_session.add(kind)
                    skipped.append({
                        "kind": kind,
                        "input_index": index,
                        "label_text": label[:120],
                        "accept": accept,
                        "skipped": "already_uploaded",
                        "existing_file_name": existing_name,
                    })
                    continue
                if kind in seen_kinds:
                    skipped.append({
                        "kind": kind,
                        "input_index": index,
                        "label_text": label[:120],
                        "accept": accept,
                        "skipped": "duplicate_upload_slot",
                    })
                    continue
                try:
                    await file_input.set_input_files(str(sample_path), timeout=8000)
                    seen_kinds.add(kind)
                    uploaded_kinds_in_session.add(kind)
                    uploaded.append({
                        "kind": kind,
                        "file_name": sample_path.name,
                        "input_index": index,
                        "label_text": label[:120],
                        "accept": accept,
                    })
                    await target_page.wait_for_timeout(250)
                except Exception as exc:
                    skipped.append({
                        "kind": kind,
                        "input_index": index,
                        "label_text": label[:120],
                        "accept": accept,
                        "error": f"{type(exc).__name__}: {exc}",
                    })

            uploaded_kinds = {item["kind"] for item in uploaded}
            preserved_kinds = {
                item["kind"]
                for item in skipped
                if item.get("skipped") in {"already_uploaded", "already_uploaded_in_session"}
            }
            return {
                "ok": {"personnel", "image", "attachment", "acceptance"}.issubset(
                    uploaded_kinds | preserved_kinds
                ),
                "uploaded": uploaded,
                "skipped": skipped,
                "input_count": input_count,
            }

        async def complete_online_apply_final_record_dialog(target_page: Any) -> dict[str, Any]:
            samples = _ensure_online_apply_upload_samples(run_dir)
            dialog = target_page.locator(
                ".el-message-box__wrapper:visible, .el-dialog:visible, "
                ".el-overlay:visible .el-dialog"
            ).last
            if not await dialog.count():
                return {"ok": False, "reason": "final_dialog_not_found"}

            async def dialog_text() -> str:
                with suppress(Exception):
                    return _text(await dialog.inner_text(timeout=800))
                return ""

            text = await dialog_text()
            if text and not re.search(r"备案登记|监管单位|申请表|提交备案", text):
                return {"ok": False, "reason": "not_final_record_dialog", "dialog_text": text[:120]}

            unit_result: dict[str, Any] = {"ok": False, "reason": "unit_field_not_found"}

            async def visible_text(locator: Any) -> str:
                with suppress(Exception):
                    return _text(await locator.inner_text(timeout=800))
                return ""

            async def is_visible(locator: Any) -> bool:
                with suppress(Exception):
                    return bool(await locator.is_visible(timeout=800))
                return False

            dialog_select_trigger_selectors = [
                ".vue-treeselect__control",
                ".vue-treeselect__placeholder",
                ".vue-treeselect",
                "[role=combobox]",
                "input[placeholder*='请选择']",
                ".el-select .el-input__wrapper",
                ".el-select__wrapper",
                ".el-select__placeholder",
                ".el-select__selected-item",
                ".el-select",
                "input[readonly]",
                ".el-input__wrapper",
                ".el-input",
                "[class*='select']",
            ]
            unit_option_selectors = [
                ".vue-treeselect__menu .vue-treeselect__option",
                ".vue-treeselect__menu .vue-treeselect__label",
                ".vue-treeselect__option",
                ".vue-treeselect__label",
                ".el-select-dropdown .el-select-dropdown__item:not(.is-disabled)",
                "[role=option]:not(.is-disabled)",
            ]

            async def dialog_trigger_container(trigger: Any) -> Any:
                for class_name in ["vue-treeselect", "el-select", "el-form-item", "el-input"]:
                    container = trigger.locator(
                        f"xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' {class_name} ')][1]"
                    )
                    if await container.count():
                        return container
                return trigger

            async def click_dialog_select_trigger() -> bool:
                for selector in dialog_select_trigger_selectors:
                    triggers = dialog.locator(selector)
                    trigger_count = await triggers.count()
                    for index in range(trigger_count):
                        trigger = triggers.nth(index)
                        if not await is_visible(trigger):
                            continue
                        await trigger.scroll_into_view_if_needed(timeout=1500)
                        await trigger.click(timeout=2500)
                        return True
                visible_placeholder_triggers = dialog.locator(
                    "xpath=.//*[normalize-space(.)='请选择备案登记/监管单位'] | "
                    ".//*[contains(normalize-space(.), '请选择备案登记/监管单位') "
                    "and string-length(normalize-space(.)) <= 40]"
                )
                visible_placeholder_count = await visible_placeholder_triggers.count()
                for index in range(visible_placeholder_count):
                    trigger = visible_placeholder_triggers.nth(index)
                    if not await is_visible(trigger):
                        continue
                    container = await dialog_trigger_container(trigger)
                    await container.scroll_into_view_if_needed(timeout=1500)
                    await container.click(timeout=2500)
                    return True
                return False

            async def collect_dialog_select_diagnostics() -> dict[str, Any]:
                trigger_candidates: list[dict[str, Any]] = []
                visible_text_option_candidates = _final_dialog_unit_options_from_visible_text(text)
                for selector in dialog_select_trigger_selectors:
                    triggers = dialog.locator(selector)
                    trigger_count = await triggers.count()
                    for index in range(min(trigger_count, 4)):
                        trigger = triggers.nth(index)
                        trigger_text = await visible_text(trigger)
                        visible_text_option_candidates.extend(
                            _final_dialog_unit_options_from_visible_text(trigger_text)
                        )
                        trigger_candidates.append(
                            {
                                "selector": selector,
                                "index": index,
                                "text": trigger_text[:120],
                                "visible": await is_visible(trigger),
                            }
                        )
                        if len(trigger_candidates) >= 20:
                            break
                    if len(trigger_candidates) >= 20:
                        break
                visible_placeholder_triggers = dialog.locator(
                    "xpath=.//*[normalize-space(.)='请选择备案登记/监管单位'] | "
                    ".//*[contains(normalize-space(.), '请选择备案登记/监管单位') "
                    "and string-length(normalize-space(.)) <= 40]"
                )
                visible_placeholder_count = await visible_placeholder_triggers.count()
                for index in range(min(visible_placeholder_count, 8)):
                    trigger = visible_placeholder_triggers.nth(index)
                    trigger_candidates.append(
                        {
                            "selector": "visible_placeholder_triggers",
                            "index": index,
                            "text": (await visible_text(trigger))[:120],
                            "visible": await is_visible(trigger),
                        }
                    )
                option_candidates: list[dict[str, Any]] = []
                options = target_page.locator(", ".join(unit_option_selectors))
                option_count = await options.count()
                for index in range(min(option_count, 30)):
                    option = options.nth(index)
                    option_candidates.append(
                        {
                            "index": index,
                            "text": (await visible_text(option))[:120],
                            "visible": await is_visible(option),
                        }
                    )
                return {
                    "dialog_text": text[:300],
                    "trigger_candidates": trigger_candidates,
                    "option_candidates": option_candidates,
                    "visible_text_option_candidates": list(dict.fromkeys(visible_text_option_candidates)),
                }

            async def option_click_target(option: Any) -> Any:
                container = option.locator(
                    "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' vue-treeselect__option ') "
                    "or contains(concat(' ', normalize-space(@class), ' '), ' el-select-dropdown__item ') "
                    "or @role='option'][1]"
                )
                if await container.count():
                    return container.first
                return option

            async def click_unit_option_safely(option: Any) -> dict[str, Any]:
                label_selectors = [
                    ".vue-treeselect__label",
                    ".vue-treeselect__label-container",
                    "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' vue-treeselect__label ')][1]",
                    "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' vue-treeselect__label-container ')][1]",
                ]
                for selector in label_selectors:
                    labels = option.locator(selector)
                    label_count = await labels.count()
                    for index in range(min(label_count, 4)):
                        label = labels.nth(index)
                        if not await is_visible(label):
                            continue
                        label_text = await visible_text(label)
                        if not label_text or re.search(r"请选择|全部", label_text):
                            continue
                        await label.scroll_into_view_if_needed(timeout=1500)
                        await label.click(timeout=2500)
                        return {
                            "click_method": "label_center",
                            "click_selector": selector,
                            "clicked_text": label_text[:120],
                        }

                click_target = await option_click_target(option)
                await click_target.scroll_into_view_if_needed(timeout=1500)
                arrow_count = 0
                with suppress(Exception):
                    arrow_count = await click_target.locator(".vue-treeselect__option-arrow-container").count()
                with suppress(Exception):
                    box = await click_target.bounding_box(timeout=800)
                    if box and box.get("width") and box.get("height"):
                        safe_x = min(max(48, box["width"] * 0.35), max(1, box["width"] - 8))
                        safe_y = min(max(4, box["height"] / 2), max(1, box["height"] - 4))
                        await click_target.click(position={"x": safe_x, "y": safe_y}, timeout=2500)
                        return {
                            "click_method": "safe_offset_avoiding_arrow",
                            "click_position": {"x": round(safe_x, 1), "y": round(safe_y, 1)},
                            "arrow_container_count": arrow_count,
                        }
                await click_target.click(timeout=2500)
                return {
                    "click_method": "fallback_center",
                    "arrow_container_count": arrow_count,
                }

            async def click_visible_unit_option() -> dict[str, Any]:
                candidates = _final_dialog_unit_options_from_visible_text(await dialog_text())
                option_records: list[dict[str, Any]] = []
                for selector in unit_option_selectors:
                    options = target_page.locator(selector)
                    option_count = await options.count()
                    for index in range(min(option_count, 40)):
                        option = options.nth(index)
                        if not await is_visible(option):
                            continue
                        option_text = await visible_text(option)
                        if not option_text or re.search(r"请选择|全部", option_text):
                            continue
                        candidates.extend(_final_dialog_unit_options_from_visible_text(option_text))
                        option_records.append(
                            {
                                "selector": selector,
                                "index": index,
                                "text": option_text,
                                "locator": option,
                            }
                        )
                selected = _choose_final_dialog_unit([item["text"] for item in option_records]) or _choose_final_dialog_unit(candidates)
                if not selected:
                    return {
                        "ok": False,
                        "reason": "visible_dropdown_option_not_found",
                        "candidates": list(dict.fromkeys(candidates)),
                    }
                for record in option_records:
                    option_text = _text(record.get("text"))
                    if selected not in option_text and option_text not in selected:
                        continue
                    with suppress(Exception):
                        click_info = await click_unit_option_safely(record["locator"])
                        await target_page.wait_for_timeout(500)
                        with suppress(Exception):
                            await target_page.keyboard.press("Tab")
                        return {
                            "ok": True,
                            "selected": selected,
                            "method": "visible_dropdown_option",
                            "selector": record["selector"],
                            "candidate_index": record["index"],
                            "candidates": list(dict.fromkeys(candidates)),
                            "click": click_info,
                        }
                return {
                    "ok": False,
                    "reason": "visible_dropdown_option_click_failed",
                    "selected": selected,
                    "candidates": list(dict.fromkeys(candidates)),
                }

            async def current_dialog_unit_value() -> str:
                value_selectors = [
                    ".vue-treeselect__single-value",
                    ".el-select__selected-item",
                    ".el-select .el-input__inner",
                    "input[placeholder*='备案登记']",
                    "input[placeholder*='监管单位']",
                    ".el-input__inner",
                ]
                for selector in value_selectors:
                    locators = dialog.locator(selector)
                    locator_count = await locators.count()
                    for index in range(min(locator_count, 8)):
                        locator = locators.nth(index)
                        if not await is_visible(locator):
                            continue
                        value = await native_control_selected_value(locator)
                        if not value:
                            value = await visible_text(locator)
                        value = _text(value)
                        if value and not re.search(r"请选择|全部", value):
                            return value
                return ""

            async def select_dialog_unit_once(reason: str) -> dict[str, Any]:
                result: dict[str, Any]
                try:
                    if await click_dialog_select_trigger():
                        await target_page.wait_for_timeout(350)
                        options = target_page.locator(", ".join(unit_option_selectors))
                        option_count = await options.count()
                        option_candidates: list[tuple[int, str]] = []
                        for index in range(option_count):
                            option = options.nth(index)
                            option_text = _text(await option.inner_text(timeout=800))
                            if option_text and not re.search(r"请选择|全部", option_text):
                                option_candidates.append((index, option_text))
                        selected_option_text = _choose_final_dialog_unit([item[1] for item in option_candidates])
                        result = {"ok": False, "reason": "unit_option_not_found"}
                        if selected_option_text:
                            for index, option_text in option_candidates:
                                if option_text != selected_option_text:
                                    continue
                                option = options.nth(index)
                                click_info = await click_unit_option_safely(option)
                                result = {
                                    "ok": True,
                                    "selected": option_text,
                                    "method": "standard_option",
                                    "click": click_info,
                                }
                                await target_page.wait_for_timeout(500)
                                with suppress(Exception):
                                    await target_page.keyboard.press("Tab")
                                break
                        if not result.get("ok"):
                            visible_unit_result = await click_visible_unit_option()
                            result = visible_unit_result if visible_unit_result.get("ok") else {
                                "ok": False,
                                "reason": "unit_option_not_found",
                                "visible_text_result": visible_unit_result,
                                "diagnostics": await collect_dialog_select_diagnostics(),
                            }
                    else:
                        result = {
                            "ok": False,
                            "reason": "unit_field_not_found",
                            "diagnostics": await collect_dialog_select_diagnostics(),
                        }
                except Exception as exc:
                    result = {
                        "ok": False,
                        "reason": "unit_select_exception",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                committed_value = await current_dialog_unit_value()
                if committed_value:
                    result["committed_value"] = committed_value
                selected_value = _text(result.get("selected"))
                result["commit_confirmed"] = bool(
                    committed_value and (not selected_value or selected_value in committed_value or committed_value in selected_value)
                )
                if result.get("ok") and not result.get("commit_confirmed"):
                    result["pre_commit_reason"] = result.get("reason")
                    result["reason"] = "unit_not_committed"
                    result["ok"] = bool(result.get("ok") and result.get("commit_confirmed"))
                result["attempt_reason"] = reason
                return result

            unit_result = await select_dialog_unit_once("initial")

            file_inputs = dialog.locator("input[type=file]")
            file_input_count = await file_inputs.count()
            upload_result: dict[str, Any] = {"ok": False, "reason": "file_input_not_found", "input_count": file_input_count}
            if file_input_count:
                file_input = file_inputs.first
                existing_application_name = await native_upload_existing_file_name(file_input)
                if existing_application_name:
                    upload_result = {
                        "ok": True,
                        "file_name": samples["application"].name,
                        "input_count": file_input_count,
                        "skipped": "already_uploaded",
                        "existing_file_name": existing_application_name,
                    }
                else:
                    await file_input.set_input_files(str(samples["application"]), timeout=8000)
                    upload_result = {
                        "ok": True,
                        "file_name": samples["application"].name,
                        "input_count": file_input_count,
                    }
                    await target_page.wait_for_timeout(800)

            async def submit_button_states() -> list[dict[str, Any]]:
                states: list[dict[str, Any]] = []
                buttons = dialog.locator("button,[role=button],input[type=submit]")
                button_count = await buttons.count()
                for index in range(button_count):
                    button = buttons.nth(index)
                    button_text = _text(await button.inner_text(timeout=800))
                    if not (button_text and re.search(r"提交备案|提交|确定", button_text)):
                        continue
                    states.append(
                        {
                            "index": index,
                            "text": button_text,
                            "disabled": await native_button_is_disabled(button),
                        }
                    )
                return states

            async def wait_for_submit_enabled() -> dict[str, Any]:
                checks: list[dict[str, Any]] = []
                for delay_ms in [0, 300, 700, 1200]:
                    if delay_ms:
                        await target_page.wait_for_timeout(delay_ms)
                    states = await submit_button_states()
                    checks.append({"after_ms": delay_ms, "buttons": states})
                    if any(not item.get("disabled") for item in states):
                        return {"ok": True, "checks": checks}
                return {"ok": False, "checks": checks}

            async def click_submit_button() -> dict[str, Any]:
                submit_skipped: list[dict[str, Any]] = []
                buttons = dialog.locator("button,[role=button],input[type=submit]")
                button_count = await buttons.count()
                for index in range(button_count):
                    button = buttons.nth(index)
                    button_text = _text(await button.inner_text(timeout=800))
                    if not (button_text and re.search(r"提交备案|提交|确定", button_text)):
                        continue
                    if await native_button_is_disabled(button):
                        submit_skipped.append({"index": index, "text": button_text, "skipped": "disabled"})
                        continue
                    await button.scroll_into_view_if_needed(timeout=1500)
                    await button.click(timeout=2500)
                    await target_page.wait_for_timeout(500)
                    return {"ok": True, "clicked": button_text}
                if submit_skipped:
                    return {"ok": False, "reason": "submit_button_disabled", "skipped": submit_skipped}
                return {"ok": False, "reason": "submit_button_not_found"}

            submit_enabled_after_wait = await wait_for_submit_enabled()
            unit_result["submit_enabled_after_wait"] = submit_enabled_after_wait
            submit_result = await click_submit_button()
            if (
                not submit_result.get("ok")
                and submit_result.get("reason") == "submit_button_disabled"
                and (unit_result.get("ok") or unit_result.get("reason") == "unit_not_committed")
            ):
                retry_result = await select_dialog_unit_once("submit_disabled_retry")
                unit_result.setdefault("reselect_attempts", []).append(retry_result)
                submit_enabled_after_retry = await wait_for_submit_enabled()
                unit_result["submit_enabled_after_retry"] = submit_enabled_after_retry
                submit_result = await click_submit_button()
            if not submit_result.get("ok") and submit_result.get("reason") == "submit_button_disabled":
                upload_result = {
                    **upload_result,
                    "submit_button_blocked": submit_result.get("skipped", []),
                }

            return {
                "ok": bool(unit_result.get("ok") and upload_result.get("ok") and submit_result.get("ok")),
                "unit": unit_result,
                "upload": upload_result,
                "submit": submit_result,
            }

        @tools.action(description="[脚本内部工具] 上传线上备案申请 4 处所需样本文件：人员信息表 xls、备案图片 jpg、附件 doc、验收文件 pdf。Agent 不需要传参数。")
        async def script_upload_sample_files() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                return json.dumps(await upload_online_apply_sample_files(target_page), ensure_ascii=False)

            return await timed_tool("script_upload_sample_files", run)

        @tools.action(description="[脚本内部工具] 修复线上备案申请提交后仍红字提示的必填项：育苗开始日期、验收日期、验收监管单位、验收文件。Agent 不需要传参数。")
        async def script_repair_required_fields() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                return json.dumps(await repair_online_apply_required_fields(target_page), ensure_ascii=False)

            return await timed_tool("script_repair_required_fields", run)

        @tools.action(description="[脚本内部工具] 完成最终备案提交弹窗：选择备案登记/监管单位、用 set_input_files 上传备案申请表.pdf 并点击提交备案。Agent 不需要传参数。")
        async def script_complete_final_record_dialog() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                return json.dumps(await complete_online_apply_final_record_dialog(target_page), ensure_ascii=False)

            return await timed_tool("script_complete_final_record_dialog", run)

        @tools.action(description="[脚本内部工具] 提交当前表单，优先点击底部“信息纳入溯源系统”，再考虑其他提交/确定按钮。Agent 不需要传参数。")
        async def script_submit_form() -> str:
            async def run() -> str:
                target_page = await current_browser_page()
                with suppress(Exception):
                    await clear_blocking_overlays(target_page, preserve_business_dialogs=True)
                return await target_page.evaluate(
                    r"""() => {
                        document.querySelectorAll('.el-picker-panel,.el-select-dropdown,.el-calendar,.el-popover')
                          .forEach((el) => el.remove());
                        window.scrollTo(0, document.body.scrollHeight);
                        const buttons = Array.from(document.querySelectorAll('button,[role=button],input[type=submit]'));
                        const isVisible = (button) => {
                          const rect = button.getBoundingClientRect();
                          const style = window.getComputedStyle(button);
                          return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                        };
                        const isDisabled = (button) => Boolean(
                          button.disabled
                          || button.getAttribute('aria-disabled') === 'true'
                          || button.classList.contains('is-disabled')
                        );
                        const buttonRank = (text) => {
                          if (text.includes('信息纳入溯源系统')) return 0;
                          if (text.includes('纳入溯源系统')) return 1;
                          if (text.includes('纳入')) return 2;
                          if (text.includes('提交备案')) return 3;
                          if (text.includes('提交')) return 4;
                          if (text.includes('确定')) return 5;
                          return 99;
                        };
                        const candidates = [];
                        for (const button of buttons) {
                          const text = (button.textContent || button.value || '').trim();
                          const rank = buttonRank(text);
                          if (text && rank < 99 && isVisible(button) && !isDisabled(button)) {
                            const rect = button.getBoundingClientRect();
                            candidates.push({button, text, rank, y: rect.top + window.scrollY});
                          }
                        }
                        candidates.sort((left, right) => left.rank - right.rank || right.y - left.y);
                        if (candidates.length) {
                          candidates[0].button.click();
                          return `已点击提交按钮: ${candidates[0].text}`;
                        }
                        for (const form of Array.from(document.querySelectorAll('form'))) {
                          const submit = form.querySelector('button[type=submit],input[type=submit]');
                          if (submit) {
                            submit.click();
                            return '已点击表单提交';
                          }
                        }
                        return '未找到提交按钮';
                    }"""
                )

            return await timed_tool("script_submit_form", run)

        llm = ChatOpenAI(
            model=profile["model"],
            api_key=profile.get("apiKey") or profile.get("api_key") or "EMPTY",
            base_url=profile.get("baseUrl") or profile.get("base_url"),
        )
        agent = Agent(
            task=_browser_use_handover_task(config, targets, handover_reasons),
            llm=llm,
            browser=browser,
            tools=tools,
            use_vision=True,
            max_actions_per_step=1,
        )
        history = await agent.run(max_steps=BROWSER_USE_DETERMINISTIC_TOOL_FIRST_MAX_STEPS)
        screenshots.append(
            await _capture_playwright_screenshot(
                page,
                screenshots_dir,
                "browser_use_handover_final",
                f"Browser Use 接管后状态：{'、'.join(targets)}",
            )
        )
        write_network_events("enabled")
        feedback = _text(history)
        history_result = _browser_use_history_result(feedback)
        current_url = page.url or config.start_url
        handover_return_home = await _return_to_start_url_after_browser_use_handover(
            page,
            config,
            targets=targets,
        )
        page_id = "browser_use_target_001"
        feature_id = "browser_use_target_001_flow"
        screenshot_refs = [item["screenshot_id"] for item in screenshots if item.get("screenshot_id")]
        handover_succeeded = _browser_use_history_is_success(history_result)
        handover_status = "completed" if handover_succeeded else "failed"
        return {
            "schema_version": V3_SCHEMA_VERSION,
            "status": handover_status,
            "targets": targets,
            "handover_reasons": handover_reasons,
            "message": (
                "Browser Use 已接管优先目标并返回执行历史。"
                if handover_succeeded
                else "Browser Use 接管已结束，但最终裁判为失败。"
            ),
            "failure_reason": history_result["failure_reason"],
            "pages": [{
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": targets[0] if targets else "Browser Use 接管目标",
                "url": current_url,
                "menu_path": targets,
                "page_type": _infer_page_type(targets[0] if targets else "", current_url),
                "semantic_page_type": _infer_page_type(targets[0] if targets else "", current_url),
                "discovery_depth": 2,
                "status": "reachable",
                "source": "browser_use.semantic_recovery",
                "confidence": "agent_observed",
                "screenshot_refs": screenshot_refs,
            }],
            "features": [{
                "feature_id": feature_id,
                "feature_point_id": feature_id,
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": f"{targets[0] if targets else '优先目标'}目标接管流程",
                "feature_type": "create" if config.safety_policy == TEST_ENV_FULL_ACCESS_POLICY else "navigation",
                "risk_level": "high" if config.safety_policy == TEST_ENV_FULL_ACCESS_POLICY else "low",
                "auto_verifiable": True,
                "verification_strategy": "browser_use_target_handover",
                "source": "browser_use.semantic_recovery",
                "confidence": "agent_observed",
                "review_status": "auto_included",
                "evidence": {"screenshot_refs": screenshot_refs},
            }],
            "case_execution_results": [{
                "case_id": "browser_use_target_001_flow_case",
                "test_case_id": "browser_use_target_001_flow_case",
                "feature_id": feature_id,
                "feature_point_id": feature_id,
                "status": history_result["status"],
                "verdict": history_result["verdict"],
                "execution_mode": "browser_use_takeover",
                "started_at": _now(),
                "finished_at": _now(),
                "actions": [{
                    "source": "browser_use",
                    "action": "agent_run",
                    "target": "、".join(targets),
                    "status": handover_status,
                }],
                "page_feedback": [feedback[:4000]] if feedback else [],
                "screenshot_refs": screenshot_refs,
                "failure_reason": history_result["failure_reason"],
                "manual_confirmation_required": history_result["manual_confirmation_required"],
                "message": (
                    "Browser Use 接管已完成，执行历史和截图已落盘。"
                    if handover_succeeded
                    else "Browser Use 接管失败，执行历史和截图已落盘。"
                ),
            }],
            "page_exploration_log": [{
                "event": "target_page_restore",
                "status": "completed" if restore_result.get("ok") else "failed",
                "targets": targets,
                "url": restore_result.get("url"),
                "method": restore_result.get("method"),
                "detail": restore_result,
            }, {
                "event": "browser_use_handover",
                "status": handover_status,
                "targets": targets,
                "reasons": handover_reasons,
                "url": current_url,
                "failure_reason": history_result["failure_reason"],
            }, handover_return_home],
            "screenshots_index": _menu_screenshots_index(
                [{**item, "stage": "browser_use_handover", "source": "browser_use.semantic_recovery"} for item in screenshots]
            ),
        }
    except Exception as exc:
        with suppress(Exception):
            (run_dir / "network_events.json").write_text(
                json.dumps(
                    {
                        "schema_version": "stage2_network_events.v1",
                        "capture_status": "failed",
                        "items": [],
                        "failure_reason": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return _browser_use_handover_failure(
            targets,
            handover_reasons,
            "browser_use_handover_failed",
            f"Browser Use 接管失败：{type(exc).__name__}: {exc}",
            screenshots,
        )


def _browser_use_handover_failure(
    targets: list[str],
    handover_reasons: list[dict[str, Any]],
    failure_reason: str,
    message: str,
    screenshots: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "status": "failed",
        "targets": targets,
        "handover_reasons": handover_reasons,
        "failure_reason": failure_reason,
        "message": message,
        "pages": [],
        "features": [],
        "case_execution_results": [],
        "page_feedback": [message],
        "page_exploration_log": [{
            "event": "browser_use_handover",
            "status": "failed",
            "targets": targets,
            "reasons": handover_reasons,
            "failure_reason": failure_reason,
            "message": message,
        }],
        "screenshots_index": _menu_screenshots_index(
            [{**item, "stage": "browser_use_handover", "source": "browser_use.semantic_recovery"} for item in screenshots]
        ),
    }


def _browser_use_handover_task(
    config: V3RunConfig,
    targets: list[str],
    handover_reasons: list[dict[str, Any]],
) -> str:
    allowed_actions = "、".join(config.allowed_side_effect_actions) or "无"
    can_submit = (
        config.safety_policy == TEST_ENV_FULL_ACCESS_POLICY
        and {"create", "submit", "save"}.issubset(set(config.allowed_side_effect_actions))
    )
    execution_goal = (
        "如果安全策略是 test_env_full_access 且允许 create/submit/save，必须按顺序调用 "
        "script_prefill_form、script_upload_sample_files、script_submit_form；下拉项和文件上传完成后必须调用 "
        "script_submit_form，最终提交一次备案申请；不要停在只看见按钮或只打开表单。"
        if can_submit
        else "当前安全策略未同时允许 create/submit/save，只验证进入目标页、打开申请表单、读取页面反馈和前端校验状态；"
        "不要调用 script_submit_form、script_complete_final_record_dialog 或任何最终提交动作。"
    )
    repair_goal = (
        "如果提交后出现“育苗开始日期、验收监管单位、验收日期、验收文件”等红字必填错误，调用 "
        "script_repair_required_fields 修复一次，再调用 script_submit_form 重试一次；如果仍有红字必填错误，记录失败原因并结束本轮，"
        "如果验收监管单位仍未提交成功，立即结束并报告失败，不要再尝试 evaluate，不要再自行编写 evaluate/JS 或 write_file 脚本，不要进入重复兜底循环。"
        if can_submit
        else "如果表单出现红字必填错误，只调用 get_page_feedback 记录校验反馈并结束本轮；不要为了修复校验而提交或重试提交。"
    )
    return f"""你是第二阶段 Browser Use 目标接管 Agent，执行「线上备案申请」全流程测试。
目标：优先进入并验证这些页面/流程：{'、'.join(targets)}。
入口 URL：{config.start_url}
安全策略：{config.safety_policy}
允许副作用动作：{allowed_actions}
接管原因：{json.dumps(handover_reasons, ensure_ascii=False)}

要求：
1. 确定性 script_* 工具优先；先调用 script_close_popups，关闭明显弹窗或遮罩。
2. 导航或点击进入“线上备案申请”菜单/页面。
3. 找到“我要申请备案”“申请备案”或“新增”按钮并点击，进入申请表单。
4. {execution_goal}
5. 每个 script_* 工具最多调用一次，只有 script_repair_required_fields 后允许再调用一次 script_submit_form 作为修复重试。
6. script_prefill_form 会预填文本字段、勾选协议，并用 Playwright 直接点击备案品种 plantId、备案类型 registerType、育苗目的、可见的育苗方式、验收监管单位和“育苗地点”多级 cascader（最多四级）；其中“验收监管单位”可能是 vue-treeselect 控件，必须依赖 script_prefill_form / script_select_required_dropdowns 内置的原生控件交互。如果提交后仍提示下拉必填，再调用 script_select_required_dropdowns 重试。不要用 evaluate/JS 直接给下拉输入框赋值。
7. 如果页面出现“育苗方式”下拉框，测试数据默认走低前置数据分支：不要选择“种子繁殖”，因为它会要求填写“种子采集许可证号”；优先选择“分蘗繁殖”“分蘖繁殖”“炼苗”或“其他”等不需要许可证的选项。遇到“育苗地点”这类城市/城区/社区多级控件时，不要手工逐项尝试，不要反复手工点击 cascader，直接依赖 script_prefill_form 内置的 Playwright cascader 选择。
8. 文件上传必须调用 script_upload_sample_files，它会按控件附近的标签完成 4 处上传，分别使用真实样本文件：人员信息表 xls、备案图片 jpg、附件 doc、验收文件 pdf。不要用 evaluate/JS 构造 File 对象，不要反复尝试手工文件上传；同一上传位已有文件时不要重复上传。
9. 如果 script_submit_form 后弹出要求选择“备案登记/监管单位”并上传申请表的最终提交弹窗，必须调用 script_complete_final_record_dialog；不要点击弹窗里的“上传文件”按钮，不要打开原生文件选择窗口，不要用 evaluate/JS 构造 File 对象。
10. {repair_goal}
11. get_page_feedback 只在失败、最终确认或本轮结束时调用，避免每个确定性工具后都等待页面摘要。
12. 最后用可读文本总结：是否进入目标页面、是否点击“我要申请备案”、填写字段数量、上传文件数量、提交次数、最终页面反馈和失败原因。
"""


def _load_browser_use_model_profile(model_name: str | None) -> dict[str, Any] | None:
    if not model_name:
        return None
    default_path = Path(__file__).resolve().parents[3] / "config" / "stage2-model-profiles.json"
    config_path = Path(os.environ.get("STAGE2_MODEL_PROFILES_PATH", str(default_path)))
    if not config_path.exists():
        return None
    with suppress(Exception):
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        for profile in payload.get("profiles", []):
            if _text(profile.get("id")) == model_name or _text(profile.get("label")) == model_name:
                return profile
    return None


async def _open_menu_entry_page(page: Any, entry: dict[str, Any], config: V3RunConfig) -> dict[str, Any]:
    menu_id = _text(entry.get("menu_id"))
    locator_candidates = [{"kind": "css", "value": f"[data-stage2-menu-id='{menu_id}']"}]
    locator_candidates.extend(_locator_candidates(entry))
    last_error: Exception | None = None
    for candidate in locator_candidates:
        kind = _text(candidate.get("kind"))
        value = _text(candidate.get("value"))
        if not value:
            continue
        try:
            if kind == "text":
                locator = page.get_by_text(value, exact=True).first
            else:
                locator = page.locator(value).first
            if callable(locator):
                locator = locator()
            await locator.scroll_into_view_if_needed(timeout=1000)
            await locator.click(timeout=2500)
            return {"method": kind or "locator", "value": value}
        except Exception as exc:
            last_error = exc

    route_hint = _text(entry.get("route_hint"))
    if route_hint:
        target_url = urljoin(config.start_url, route_hint)
        await page.goto(target_url, wait_until="domcontentloaded", timeout=6000)
        return {"method": "route_hint", "url": target_url}

    if last_error is not None:
        raise last_error
    raise RuntimeError("menu_entry_has_no_locator_or_route")


def _snapshot_is_blank(snapshot: dict[str, Any]) -> bool:
    if not isinstance(snapshot, dict):
        return True
    if _text(snapshot.get("title")):
        return False
    if snapshot.get("links") or snapshot.get("controls"):
        return False
    return not _text(snapshot.get("visibleTextSample"))


def _dedupe_page_exploration(
    pages: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    page_key_by_id: dict[str, str] = {}
    for page in pages:
        key = _normalize_page_url(_text(page.get("url")))
        if not key:
            key = _text(page.get("page_id")) or _text(page.get("name"))
        page_id = _text(page.get("page_id") or page.get("page_entry_id"))
        if page_id:
            page_key_by_id[page_id] = key
        existing = selected.get(key)
        if existing is None:
            selected[key] = page
            order.append(key)
            continue
        if _page_entry_rank(page) > _page_entry_rank(existing):
            selected[key] = page

    deduped_pages = [selected[key] for key in order]
    kept_page_id_by_key = {
        key: _text(page.get("page_id") or page.get("page_entry_id"))
        for key, page in selected.items()
    }
    kept_page_ids = set(kept_page_id_by_key.values())
    deduped_features: list[dict[str, Any]] = []
    for feature in features:
        feature_page_id = _text(feature.get("page_id") or feature.get("page_entry_id"))
        page_key = page_key_by_id.get(feature_page_id)
        target_page_id = kept_page_id_by_key.get(page_key or "", feature_page_id)
        if target_page_id not in kept_page_ids:
            continue
        if target_page_id != feature_page_id:
            feature = {
                **feature,
                "page_id": target_page_id,
                "page_entry_id": target_page_id,
            }
        deduped_features.append(feature)
    return deduped_pages, deduped_features


def _normalize_page_url(url: str) -> str:
    text = _text(url)
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return text.rstrip("/")
    path = parsed.path.rstrip("/") or "/"
    if path in {"/", "/index"}:
        path = "/index"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _page_entry_rank(page: dict[str, Any]) -> tuple[int, int, int, int]:
    name = _text(page.get("name"))
    status_rank = 2 if _text(page.get("status")) == "reachable" else 1
    canonical_home_rank = 1 if name == "首页" else 0
    noise_rank = 0 if _is_noise_menu_label(name) else 1
    evidence_rank = len(page.get("screenshot_refs") or [])
    return (status_rank, canonical_home_rank, noise_rank, evidence_rank)


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
        if feature_type == "view":
            record_unrecognized_control({
                "tag": _text(control.get("tag")),
                "type": _text(control.get("type")),
                "text": text[:80],
                "classes": str(control.get("classes", ""))[:120],
                "page_id": page_id,
            })
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
                    "raw_text": _text(control.get("text")),
                    "css_path": control.get("css_path") or "",
                    "classes": control.get("classes") or [],
                    "id": control.get("id") or "",
                    "role": control.get("role") or "",
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
    if _looks_like_logged_in_app_shell(text):
        return False
    if any(_is_business_menu_label_evidence(entry) for entry in menu_entries if isinstance(entry, dict)):
        return False
    return not any(_is_business_menu_evidence(entry) for entry in menu_entries if isinstance(entry, dict))


def _looks_like_logged_in_app_shell(text: str) -> bool:
    value = _text(text)
    return (
        ("欢迎您" in value and "管理平台" in value)
        or "当前品种" in value
        or "通知公告" in value
        or "录入提醒" in value
    )


def _is_business_menu_label_evidence(entry: dict[str, Any]) -> bool:
    text = _text(entry.get("text"))
    if not text or text == "首页" or _is_noise_menu_label(text) or _looks_like_login_text(text):
        return False
    business_words = ("管理", "备案", "申请", "溯源", "通知", "列表", "收购", "生产", "销售", "查询")
    return any(word in text for word in business_words)


def _is_business_menu_evidence(entry: dict[str, Any]) -> bool:
    if _text(entry.get("status")) in {"permission_blocked", "expansion_failed", "failed"}:
        return False
    text = _text(entry.get("text"))
    if not text or text == "首页" or _is_noise_menu_label(text):
        return False
    return bool(entry.get("is_leaf") or _text(entry.get("route_hint")) or _text(entry.get("parent_id")))


def _is_noise_menu_label(text: str) -> bool:
    return _text(text) in {
        "0",
        "大写锁定已打开",
        "Default Medium Small Mini",
        "Default",
        "Medium",
        "Small",
        "Mini",
        "个人中心布局设置退出登录",
        "个人中心",
        "布局设置",
        "退出登录",
    }


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
      const noiseLabels = new Set([
        '0',
        '大写锁定已打开',
        'Default Medium Small Mini',
        'Default',
        'Medium',
        'Small',
        'Mini',
        '个人中心布局设置退出登录',
        '个人中心',
        '布局设置',
        '退出登录'
      ]);
      const inMenuShell = (el) => Boolean(el.closest(
        '.sidebar-container, aside, nav, [role="navigation"], .el-menu, .ant-menu'
      ));
      const inChromeShell = (el) => Boolean(el.closest(
        '.navbar, .right-menu, .avatar-container, .sidebar-logo-container, .el-dropdown-menu, #header-search, #size-select'
      ));
      const allowedMenuCandidate = (el, label) => {
        if (noiseLabels.has(label)) return false;
        if (inChromeShell(el)) return false;
        return inMenuShell(el);
      };
      const seen = new Set();
      return Array.from(document.querySelectorAll(selectors))
        .filter(visible)
        .map((el, index) => {
          const label = text(el);
          if (!label) return null;
          if (!allowedMenuCandidate(el, label)) return null;
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
        el.getAttribute('placeholder') || el.value || el.name || el.id || ''
      ).trim().replace(/\\s+/g, ' ').slice(0, 80);
      const cssPath = (el) => {
        if (!el || !el.tagName) return '';
        if (el.id) return '#' + CSS.escape(el.id);
        const parts = [];
        let current = el;
        while (current && current.nodeType === 1 && parts.length < 5) {
          let selector = current.tagName.toLowerCase();
          const classes = Array.from(current.classList || [])
            .filter(function (name) { return name && name.length < 40 && name.indexOf('is-') !== 0; })
            .slice(0, 2);
          if (classes.length) selector += '.' + classes.map(function (name) { return CSS.escape(name); }).join('.');
          const parent = current.parentElement;
          if (parent) {
            const siblings = Array.from(parent.children).filter(function (node) { return node.tagName === current.tagName; });
            if (siblings.length > 1) selector += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
          }
          parts.unshift(selector);
          current = parent;
        }
        return parts.join(' > ');
      };
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
          id: el.id || '',
          classes: Array.from(el.classList).filter(function (c) { return c && c.length < 40; }).slice(0, 4),
          css_path: cssPath(el)
        }));
      return {
        links,
        controls,
        title: document.title,
        url: location.href,
        visibleTextSample: bodyText.replace(/\\s+/g, ' ').trim().slice(0, 200),
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
        el.getAttribute('placeholder') || el.value || el.name || el.id || ''
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
                    "raw_text": str(control.get("text") or ""),
                    "css_path": control.get("css_path") or "",
                    "classes": control.get("classes") or [],
                    "id": control.get("id") or "",
                    "role": control.get("role") or "",
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


def _browser_use_history_result(feedback: str) -> dict[str, Any]:
    text = _text(feedback)
    failure_patterns = [
        r"Judge Verdict:\s*(?:❌\s*)?FAIL",
        r"success\s*[:=]\s*False",
        r'"ok"\s*:\s*false',
        r"'ok'\s*:\s*False",
        r"submit_button_disabled",
        r"测试未能成功提交",
        r"未能成功提交",
        r"仍为空",
        r"红框错误",
        r"验证错误",
        r"validation errors?",
        r"Failure Reason:",
        r"form_item_not_found",
        r"required field",
        r"不能为空",
    ]
    success_patterns = [
        r"Judge Verdict:\s*(?:✅\s*)?PASS",
        r"success\s*[:=]\s*True",
        r"is_done\s*=\s*True[^)]*success\s*=\s*True",
        r"成功提交",
        r"提交成功",
        r"提交完成",
    ]
    has_failure_signal = any(re.search(pattern, text, re.I) for pattern in failure_patterns)
    has_success_signal = any(re.search(pattern, text, re.I) for pattern in success_patterns)
    has_final_success_evidence = bool(
        re.search(r"(提交成功|成功提交|提交完成)", text, re.I)
        and (
            re.search(r"(success\s*[:=]\s*True|is_done\s*=\s*True[^)]*success\s*=\s*True)", text, re.I)
            or re.search(r"/registration/apply/dept[^\\n\\r]*(?:status\s*=?\s*200|POST)", text, re.I)
            or re.search(r"待备案登记/监管单位登记备案", text, re.I)
        )
    )
    if has_failure_signal and has_final_success_evidence:
        return {
            "status": "recovered_passed",
            "verdict": "passed",
            "failure_reason": None,
            "manual_confirmation_required": False,
            "recovered_from": "browser_use_intermediate_failure",
        }
    if has_failure_signal:
        return {
            "status": "failed",
            "verdict": "failed",
            "failure_reason": "browser_use_handover_failed",
            "manual_confirmation_required": True,
        }
    if has_success_signal:
        return {
            "status": "passed",
            "verdict": "passed",
            "failure_reason": None,
            "manual_confirmation_required": False,
        }
    return {
        "status": "needs_review",
        "verdict": "needs_review",
        "failure_reason": "browser_use_handover_unverified",
        "manual_confirmation_required": True,
    }


def _browser_use_history_is_success(history_result: dict[str, Any]) -> bool:
    return _text(history_result.get("verdict")) == "passed" and not _text(history_result.get("failure_reason"))


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


_DYNAMIC_TYPE_VOCABULARY: dict[str, list[str]] = {}


def register_feature_type(feature_type: str, keywords: list[str]) -> None:
    """Register a new feature type with keyword triggers for future classification.

    After Browser Use or AI discovers a new control type (e.g. ``file_upload``),
    call this to add it to the vocabulary.  The next time ``_infer_feature_type``
    encounters a control whose text/tag matches one of the *keywords*, it will
    classify it as *feature_type* without needing Browser Use again.
    """
    if feature_type not in _DYNAMIC_TYPE_VOCABULARY:
        _DYNAMIC_TYPE_VOCABULARY[feature_type] = []
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if kw_lower and kw_lower not in _DYNAMIC_TYPE_VOCABULARY[feature_type]:
            _DYNAMIC_TYPE_VOCABULARY[feature_type].append(kw_lower)


def _get_registered_feature_types() -> list[str]:
    return sorted(_DYNAMIC_TYPE_VOCABULARY.keys())


# Seed with input-type-based detection
register_feature_type("file_upload", ["upload", "上传", "file", "选择文件", "附件", "浏览"])
register_feature_type("date_picker", ["date", "日期", "picker", "时间", "年月日"])
register_feature_type("cascader", ["cascader", "级联", "省市区", "地区选择"])
register_feature_type("number_input", ["number", "数字", "数量", "金额"])


def _infer_feature_type(text: str, tag: str, input_type: str) -> str:
    lowered = f"{text} {tag} {input_type}".lower()

    # 1. Check dynamic vocabulary (self-evolving, registered by Browser Use / AI)
    for feature_type, keywords in sorted(_DYNAMIC_TYPE_VOCABULARY.items()):
        if any(kw in lowered for kw in keywords):
            return feature_type

    # 2. Check input_type-based rules (DOM-type awareness, before text matching)
    if input_type == "file":
        return "file_upload"
    if input_type in {"date", "datetime-local", "month", "week", "time"}:
        return "date_picker"
    if input_type == "number":
        return "number_input"

    # 3. Static text-based rules (original vocabulary)
    if any(word in lowered for word in ("delete", "删除", "作废")):
        return "delete"
    if any(word in lowered for word in ("approve", "审批", "审核")):
        return "approve"
    if any(word in lowered for word in ("save", "保存")):
        return "save"
    if any(word in lowered for word in ("submit", "提交")):
        return "submit"
    if tag == "a" and not any(word in lowered for word in ("我要", "add", "new", "create", "新增", "新建")):
        return "navigation"
    if any(word in lowered for word in ("add", "new", "create", "新增", "新建", "申请", "申报")):
        return "create"
    if any(word in lowered for word in ("edit", "修改", "编辑")):
        return "edit"
    if any(word in lowered for word in ("search", "query", "查询", "检索")) or input_type in {"search", "text"}:
        return "query"
    return "view"

_UNRECOGNIZED_CONTROLS: list[dict] = []


def record_unrecognized_control(control: dict) -> None:
    _UNRECOGNIZED_CONTROLS.append(control)


def get_unrecognized_controls() -> list[dict]:
    return list(_UNRECOGNIZED_CONTROLS)


def clear_unrecognized_controls() -> None:
    _UNRECOGNIZED_CONTROLS.clear()


def _risk_level(feature_type: str) -> str:
    if feature_type in SIDE_EFFECT_ACTION_TYPES:
        return "high"
    return "low"
