"""
Real-browser page discovery for Stage C (verification spike, 2026-07-04).

Wraps ``v3_real_browser._explore_menu_leaf_pages_with_playwright`` (already
proven against real business systems — see
docs/第二阶段新系统接入测试手册.md) and feeds its ``pages`` output through
``PageAdapter``'s existing goal/attempt/step/evidence recording API — the
SAME calls ``test_page_goal_integration.py`` makes by hand, just driven by a
real navigation instead of literal test data.

This module does not reimplement page navigation/snapshotting: that logic
already exists, is used in production verification flows, and is left
untouched. It only translates its output shape into goal-loop primitives,
mirroring ``menu_goal.real_browser_discovery``'s role for Stage B.

Fixture-based ``load_page_goals_from_menu_fixture``/``PageGoalOrchestrator.export_fixture``
remain the default, unmodified path (实施计划 §2.6) — this is an additive,
parallel entrypoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .page_adapter import PageAdapter

EXECUTION_MODE_REAL_BROWSER = "real_browser"

# is_blank thresholds mirror PageAdapter.record_page_success's own docstring
# (visible_text_len < 20 OR dom_nodes < 5 OR blank_screenshot_ratio >= 0.98):
# a real page that reached _snapshot_is_blank()==False is reported with
# comfortably-above-threshold values rather than fabricated exact pixel
# ratios (this codebase has no real pixel-blankness analyzer yet — see
# _snapshot_is_blank's own DOM-based heuristic, which this mirrors).
# has_main_content=True is included here (not just passed separately to
# record_page_success) because page_fixture_writer.write_page_fixture reads
# it back OUT of this same dom_snapshot dict via attach_page_metadata_evidence
# — omitting it here silently exported has_main_content=False for every real
# reachable page, contradicting status="reachable"/is_blank=False on the same
# entry (found during 2026-07-04 real suyuan-system verification).
_NOT_BLANK_SIGNALS = {"visible_text_len": 200, "dom_nodes": 50, "blank_screenshot_ratio": 0.0}
_PAGE_METADATA_DOM_SNAPSHOT = {**_NOT_BLANK_SIGNALS, "has_main_content": True}
_BLANK_FAILURE_CLASS = "page_blank"
_UNREACHABLE_FAILURE_CLASS = "page_load_timeout"


async def discover_pages_with_playwright(
    page: "Page",
    adapter: "PageAdapter",
    menu_entries: list[dict[str, Any]],
    *,
    screenshots_dir: Path,
    parent_goal_id: str | None = None,
    max_pages: int = 5,
    max_features_per_page: int = 6,
) -> list[str]:
    """Navigate to real menu leaf entries and register one page goal each.

    Args:
        page: a live Playwright page, already connected/navigated by the
            caller.
        adapter: the PageAdapter goals should be registered into.
        menu_entries: real ``menu_entries`` list, e.g. from
            ``menu_goal.real_browser_discovery.discover_menus_with_playwright``'s
            underlying scan (the SAME shape ``build_menu_discovery_artifacts``
            produces — this function filters to ``is_leaf`` entries itself).
        screenshots_dir: directory real page screenshots are written under.
        parent_goal_id: parent goal for every discovered page (typically the
            Stage C root goal).
        max_pages: forwarded to the underlying explorer's leaf-page budget.
        max_features_per_page: forwarded to the underlying explorer's
            per-page DOM-snapshot control budget.

    Returns:
        List of registered page goal IDs, in exploration order.
    """

    from ..v3_orchestrator import V3RunConfig
    from ..v3_real_browser import _explore_menu_leaf_pages_with_playwright

    config = V3RunConfig(
        start_url=page.url,
        max_pages=max_pages,
        max_features_per_page=max_features_per_page,
    )
    exploration = await _explore_menu_leaf_pages_with_playwright(
        page, menu_entries, screenshots_dir, config
    )
    real_pages: list[dict[str, Any]] = exploration.get("pages") or []
    screenshots_by_id = {
        item["screenshot_id"]: item
        for item in (exploration.get("screenshots_index") or {}).get("items", [])
        if isinstance(item, dict) and item.get("screenshot_id")
    }

    goal_ids: list[str] = []
    for real_page in real_pages:
        page_id = real_page["page_id"]
        menu_path = real_page.get("menu_path") or []
        route_hint = real_page.get("url")
        parent_menu_id = real_page.get("menu_id")

        goal_id = adapter.register_page_goal(
            page_id=page_id,
            menu_path=menu_path,
            route_hint=route_hint,
            parent_goal_id=parent_goal_id,
            parent_menu_id=parent_menu_id,
        )
        goal_ids.append(goal_id)

        # Matches the SAME activation convention page_goal's own fixture flow
        # uses (see test_page_goal_integration.py) rather than activate_next():
        # PageAdapter.record_page_attempt's docstring documents this directly
        # (engine.start_attempt requires STATUS_RUNNING, and this driver visits
        # every discovered page in one pass rather than yielding control back
        # to a frontier scheduler between pages).
        adapter.engine.goals[goal_id].status = "running"

        attempt_id = adapter.record_page_attempt(goal_id=goal_id, route_hint=route_hint)
        nav_step_id = adapter.record_navigation_step(
            attempt_id=attempt_id, action="navigate_to_page", target=route_hint, observed=False
        )
        capture_step_id = adapter.record_navigation_step(
            attempt_id=attempt_id, action="capture_state", target=route_hint, observed=True
        )

        evidence_refs = []
        for screenshot_id in real_page.get("screenshot_refs") or []:
            ref = screenshots_by_id.get(screenshot_id)
            screenshot_path = ref.get("path") if ref else None
            if screenshot_path:
                evidence_refs.append(
                    adapter.attach_screenshot_evidence(
                        step_id=capture_step_id, screenshot_path=screenshot_path
                    )
                )

        status = real_page.get("status")
        page_title = real_page.get("name") or page_id
        page_url = real_page.get("url") or route_hint or ""

        if status == "reachable":
            evidence_refs.append(
                adapter.attach_page_metadata_evidence(
                    step_id=capture_step_id,
                    page_title=page_title,
                    page_url=page_url,
                    http_status=200,
                    dom_snapshot=_PAGE_METADATA_DOM_SNAPSHOT,
                )
            )
            adapter.record_page_success(
                attempt_id=attempt_id,
                page_url=page_url,
                page_title=page_title,
                evidence_refs=evidence_refs,
                **_NOT_BLANK_SIGNALS,
            )
        else:
            failure_class = (
                _BLANK_FAILURE_CLASS
                if real_page.get("failure_reason") == "blank_page_after_navigation"
                else _UNREACHABLE_FAILURE_CLASS
            )
            adapter.record_page_failure(
                attempt_id=attempt_id,
                failure_class=failure_class,
                confidence="high",
                evidence_refs=evidence_refs,
                note=real_page.get("failure_reason"),
            )

    return goal_ids


__all__ = ["EXECUTION_MODE_REAL_BROWSER", "discover_pages_with_playwright"]
