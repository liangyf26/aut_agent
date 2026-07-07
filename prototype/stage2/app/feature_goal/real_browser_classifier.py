"""
Real-browser feature classification for Stage D (verification spike, 2026-07-04).

Wraps ``v3_real_browser._build_page_features_from_snapshot`` (already proven
against real business systems — see docs/第二阶段新系统接入测试手册.md) and
feeds its output through ``FeatureAdapter``'s existing goal/attempt/step/
evidence recording API — the SAME calls
``FeatureGoalOrchestrator.scan_page_features`` makes, just classified from a
real DOM snapshot instead of a title/URL keyword guess
(``feature_classifier.classify_feature_from_page_context``).

Feature-type/risk-level vocabulary note: this driver passes through
``_build_page_features_from_snapshot``'s OWN vocabulary
(delete/approve/save/submit/navigation/create/edit/query/view,
risk_level in {low, high}) rather than translating it into
``feature_classifier.FEATURE_TYPE_DEFINITIONS``'s vocabulary
(query/reset/detail/export/tab/dialog/row_action_edit/row_action_delete/
submit/view, risk_level in {low, medium, high, none}). Neither
``FeatureAdapter.register_feature_goal`` nor
``test_case_generator.generate_test_case`` enforces a fixed enum on these
fields — an unrecognized feature_type degrades to generic test steps rather
than erroring — so passing the real classification through untranslated is
more honest than inventing a mapping between two vocabularies that were
never designed to align.

IMPORTANT — must use an INDEPENDENT GoalLoopEngine, never the one an
execution_goal orchestrator is driving: see
``FeatureGoalOrchestrator``'s own module docstring (orchestrator.py) — Stage
D's ``scan_page_features`` sets ``goal.status = "running"`` directly rather
than through ``activate_next()``, which this driver also does (mirroring
the same convention), and that is incompatible with a caller that expects
proper single-active-goal semantics.

Fixture-based ``classify_feature_from_page_context``/``FeatureGoalOrchestrator.scan_page_features``
remain the default, unmodified path (实施计划 §2.6) — this is an additive,
parallel entrypoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .feature_adapter import FeatureAdapter

EXECUTION_MODE_REAL_BROWSER = "real_browser"


async def classify_features_with_playwright(
    page: "Page",
    adapter: "FeatureAdapter",
    page_goal_id: str,
    *,
    page_id: str,
    screenshots_dir: Path,
    max_features_per_page: int = 6,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Scan the CURRENT real page's DOM and register one feature goal per
    detected control.

    Args:
        page: a live Playwright page, already navigated to the target page
            by the caller (this driver does not navigate anywhere itself —
            page_goal.real_browser_discovery already did that for the same
            page_goal_id and left the page in that state).
        adapter: the FeatureAdapter goals should be registered into.
        page_goal_id: the page-scan goal these features are children of
            (mirrors ``scan_page_features(page_goal_id)``'s contract).
        page_id: the page identifier features should be attributed to.
        screenshots_dir: directory the real DOM-snapshot screenshot is
            written under.
        max_features_per_page: forwarded to the underlying snapshot's
            control-collection budget.

    Returns:
        ``(feature_goal_ids, test_cases)`` — test_cases has the SAME shape
        ``test_case_generator.generate_test_case`` produces for the fixture
        path (mirrors ``FeatureGoalOrchestrator._test_cases``, which this
        driver has no orchestrator instance to append into directly).
    """

    from ..v3_real_browser import (
        _build_page_features_from_snapshot,
        _capture_playwright_screenshot,
        _dom_collection_expression,
    )
    from .test_case_generator import generate_test_case

    snapshot = await page.evaluate(
        _dom_collection_expression(),
        {"maxPages": 1, "maxFeatures": max_features_per_page},
    )
    screenshot_id = f"{page_id}_features"
    screenshot_ref = await _capture_playwright_screenshot(
        page, screenshots_dir, screenshot_id, f"页面特征扫描：{page_id}"
    )
    real_features: list[dict[str, Any]] = _build_page_features_from_snapshot(
        page_id, snapshot, max_features_per_page, screenshot_id, start_index=1
    )

    # Mirrors scan_page_features's own activation convention (see this
    # module's docstring): this driver visits one page in a single pass, not
    # via activate_next()'s frontier scheduler.
    adapter.engine.goals[page_goal_id].status = "running"
    scan_attempt_id = adapter.record_feature_attempt(goal_id=page_goal_id)
    scan_step_id = adapter.record_scan_step(
        attempt_id=scan_attempt_id, action="scan_page", target=page.url, observed=True
    )
    scan_dom_evidence = adapter.attach_dom_evidence(
        step_id=scan_step_id,
        dom_snapshot={
            "page_url": page.url,
            "scan_method": "playwright_real_dom_snapshot",
            "features_found": len(real_features),
            "screenshot_path": screenshot_ref.get("path"),
        },
    )

    # confidence="high": a real DOM observation of an actual control is
    # stronger evidence than the fixture path's title/URL keyword guess
    # (which only ever reaches "medium" — see feature_classifier.py), so it
    # is deliberately NOT downgraded to match that ceiling.
    confidence = "high"

    feature_goal_ids: list[str] = []
    test_cases: list[dict[str, Any]] = []
    for feature in real_features:
        feature_type = feature.get("feature_type", "view")
        risk_level = feature.get("risk_level", "low")
        element_text = feature.get("name")
        element_locator = f"[data-real-feature-index='{feature.get('evidence', {}).get('candidate_index')}']"

        feature_goal_id = adapter.register_feature_goal(
            feature_id=feature["feature_id"],
            page_id=page_id,
            feature_type=feature_type,
            risk_level=risk_level,
            parent_goal_id=page_goal_id,
            element_text=element_text,
            element_locator=element_locator,
        )
        adapter.engine.goals[feature_goal_id].status = "running"

        feature_attempt_id = adapter.record_feature_attempt(goal_id=feature_goal_id)
        feature_step_id = adapter.record_scan_step(
            attempt_id=feature_attempt_id, action="classify_feature", target=feature_type, observed=True
        )
        feature_metadata_evidence = adapter.attach_feature_metadata_evidence(
            step_id=feature_step_id,
            feature_type=feature_type,
            risk_level=risk_level,
            confidence=confidence,
            element_text=element_text,
            element_locator=element_locator,
        )
        adapter.record_feature_identified(
            attempt_id=feature_attempt_id,
            feature_type=feature_type,
            risk_level=risk_level,
            confidence=confidence,
            evidence_refs=[feature_metadata_evidence],
        )
        feature_goal_ids.append(feature_goal_id)

        test_cases.append(
            generate_test_case(
                feature_id=feature["feature_id"],
                page_id=page_id,
                feature_type=feature_type,
                risk_level=risk_level,
                confidence=confidence,
                element_text=element_text,
                element_locator=element_locator,
                page_url=page.url,
            )
        )

    adapter.record_feature_identified(
        attempt_id=scan_attempt_id,
        feature_type="page_scan",
        risk_level="none",
        confidence=confidence,
        evidence_refs=[scan_dom_evidence],
    )

    return feature_goal_ids, test_cases


__all__ = ["EXECUTION_MODE_REAL_BROWSER", "classify_features_with_playwright"]
