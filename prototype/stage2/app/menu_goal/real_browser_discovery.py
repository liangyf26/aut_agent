"""
Real-browser menu discovery for Stage B (verification spike, 2026-07-04).

Wraps ``v3_real_browser._discover_menu_with_playwright`` (already proven
against real business systems — see docs/第二阶段新系统接入测试手册.md) and
feeds its ``menu_entries`` output through ``DiscoveryAdapter``'s existing
goal/attempt/step/evidence recording API — the SAME calls
``test_integration_menu_discovery_flow.py`` makes by hand, just driven by a
real scan instead of literal test data.

This module does not reimplement menu scanning/expansion: that logic
already exists, is used in production verification flows, and is left
untouched. It only exists to translate its output shape into goal-loop
primitives, mirroring ``execution_goal.real_browser_runner``'s role for
Stage E.

Fixture-based ``load_menu_goals_from_fixture``/``MenuGoalOrchestrator.export_fixture``
remain the default, unmodified path (实施计划 §2.6) — this is an additive,
parallel entrypoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .discovery_adapter import DiscoveryAdapter

EXECUTION_MODE_REAL_BROWSER = "real_browser"

# Failure classes recognized by goal_loop.playbook's fixed table (Stage A),
# reused here rather than inventing a parallel vocabulary.
_STATUS_TO_FAILURE_CLASS = {
    "permission_blocked": "permission_blocked",
    "expansion_failed": "menu_expand_failed",
}


async def discover_menus_with_playwright(
    page: "Page",
    adapter: "DiscoveryAdapter",
    *,
    screenshots_dir: Path,
    parent_goal_id: str | None = None,
    max_pages: int = 5,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Discover real menus via Playwright and register one goal per entry.

    Args:
        page: a live Playwright page, already connected/navigated by the
            caller (mirrors ``main.run_connected_template_validation``'s
            page-resolution pattern).
        adapter: the DiscoveryAdapter goals should be registered into.
        screenshots_dir: directory real menu screenshots are written under.
        parent_goal_id: parent goal for every discovered menu (typically the
            Stage B root goal).
        max_pages: forwarded to the underlying scanner's expansion budget
            (same meaning as ``V3RunConfig.max_pages``).

    Returns:
        ``(goal_ids, raw_entries)`` — ``raw_entries`` is the underlying
        scanner's full ``menu_entries`` list (``is_leaf``/``expandable``/
        ``locator_candidates`` included), NOT the reduced schema
        ``fixture_writer.write_menu_fixture`` exports. Downstream real-browser
        callers (e.g. ``page_goal.real_browser_discovery``, which filters on
        ``is_leaf``) need this superset; only the fixture path's frozen
        ``menu_entries.json`` intentionally uses the reduced schema.
    """

    from ..v3_orchestrator import V3RunConfig
    from ..v3_real_browser import _discover_menu_with_playwright

    config = V3RunConfig(max_pages=max_pages)
    artifacts = await _discover_menu_with_playwright(page, screenshots_dir, config)
    entries: list[dict[str, Any]] = artifacts.get("menu_entries") or []

    # Register goals in a parent-before-child order: an entry whose parent_id
    # hasn't been registered yet would otherwise have no goal_id to attach to.
    registered_goal_ids: dict[str, str] = {}
    goal_ids: list[str] = []

    def _register(entry: dict[str, Any]) -> None:
        menu_id = entry["menu_id"]
        if menu_id in registered_goal_ids:
            return
        parent_id = entry.get("parent_id")
        parent_goal = parent_goal_id
        if parent_id:
            if parent_id not in registered_goal_ids:
                parent_entry = next((e for e in entries if e["menu_id"] == parent_id), None)
                if parent_entry is not None:
                    _register(parent_entry)
            parent_goal = registered_goal_ids.get(parent_id, parent_goal_id)

        goal_id = adapter.register_menu_goal(
            menu_id=menu_id,
            menu_path=entry.get("menu_path") or [entry.get("text", menu_id)],
            parent_goal_id=parent_goal,
        )
        registered_goal_ids[menu_id] = goal_id
        goal_ids.append(goal_id)

        attempt_id = adapter.record_discovery_attempt(
            goal_id=goal_id, route_hint=entry.get("route_hint")
        )
        step_id = adapter.record_navigation_step(
            attempt_id=attempt_id,
            action="playwright_menu_scan",
            target=entry.get("route_hint"),
        )
        evidence_refs = []
        for ref in entry.get("screenshot_refs") or []:
            path = ref.get("path") if isinstance(ref, dict) else ref
            if path:
                evidence_refs.append(
                    adapter.attach_screenshot_evidence(step_id=step_id, screenshot_path=path)
                )
        evidence_refs.append(
            adapter.attach_menu_metadata_evidence(step_id=step_id, menu_text=entry.get("text", menu_id))
        )

        status = entry.get("status")
        if status in _STATUS_TO_FAILURE_CLASS:
            adapter.record_discovery_failure(
                attempt_id=attempt_id,
                failure_class=_STATUS_TO_FAILURE_CLASS[status],
                confidence="high",
                evidence_refs=evidence_refs,
                note=entry.get("failure_reason"),
            )
        else:
            adapter.record_discovery_success(attempt_id=attempt_id, evidence_refs=evidence_refs)

    for entry in entries:
        _register(entry)

    return goal_ids, entries


__all__ = ["EXECUTION_MODE_REAL_BROWSER", "discover_menus_with_playwright"]
