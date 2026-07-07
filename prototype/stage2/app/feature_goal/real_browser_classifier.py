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

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .feature_adapter import FeatureAdapter

EXECUTION_MODE_REAL_BROWSER = "real_browser"


_STRUCTURAL_TAG_NAMES = frozenset({
    "A", "BUTTON", "DIV", "INPUT", "SELECT", "SPAN",
    "LI", "UL", "TD", "TR", "FORM", "LABEL", "NAV",
    "HEADER", "FOOTER", "MAIN", "SECTION", "ARTICLE",
})

_TAGS_WITHOUT_TEXT_CONTENT = frozenset({"input", "textarea", "select"})


def _build_stable_locator(
    *,
    tag: str = "",
    text: str = "",
    el_id: str = "",
    role: str = "",
    css_path: str = "",
    classes: list[str] | None = None,
) -> str:
    """Build a stable Playwright locator for a DOM element from its collected properties.

    Priority (descending):
    1. ``#<id>`` — permanently stable, survives any page mutation
    2. ``<tag>:has-text("<escaped text>")`` — text-based, survives re-render
    3. ``<tag>[role="<role>"]`` — attribute-based, but ambiguous with multiple buttons
    4. CSS path — structural, least stable but always available as fallback
    """
    classes = classes or []

    # 1. ID selector — gold standard, never degrades
    if el_id and _is_sane_css_id(el_id):
        return f"#{el_id}"

    # 2. Text-based Playwright selector — survives page reload; more specific than role
    if text and tag:
        if text.upper() == tag.upper() or text.strip().upper() in _STRUCTURAL_TAG_NAMES:
            pass  # fall through to role / CSS path
        else:
            escaped = _escape_playwright_text(text)
            if escaped and len(escaped) < 60:
                if tag in _TAGS_WITHOUT_TEXT_CONTENT:
                    return f'{tag}[placeholder*="{escaped}"], {tag}[aria-label*="{escaped}"]'
                return f'{tag}:has-text("{escaped}")'

    # 3. Tag + role attribute — stable across DOM re-renders, but ambiguous with multiple same-tag elements
    if role and tag:
        return f'{tag}[role="{role}"]'

    # 4. CSS path — available for every element, but fragile against DOM restructuring
    if css_path:
        return css_path

    # Last resort: class-based
    if classes and tag:
        safe_classes = [c for c in classes if _is_sane_css_class(c)]
        if safe_classes:
            return f"{tag}.{'.'.join(safe_classes)}"

    return f'{tag}:has-text("未知控件")'


def _build_locator_candidates(
    *,
    tag: str = "",
    text: str = "",
    el_id: str = "",
    role: str = "",
    css_path: str = "",
    classes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a ranked list of locator candidates for a DOM element.

    Each candidate::

        {"selector": "<playwright locator>", "confidence": float, "strategy": "<id|text|role|css_path|class|fallback>"}

    Candidates are ordered by descending confidence. The confidence values
    follow the same priority hierarchy as ``_build_stable_locator()``:
    #id (0.95) > :has-text (0.85) > [role] (0.60) > CSS path (0.40) >
    class-based (0.30) > fallback (0.10).

    P0-2's locator_trier iterates these candidates and picks the first one
    that matches the live page at execution time.
    """
    classes = list(classes or [])
    candidates: list[dict[str, Any]] = []

    # 1. ID-based (0.95)
    if el_id and _is_sane_css_id(el_id):
        candidates.append({"selector": f"#{el_id}", "confidence": 0.95, "strategy": "id"})

    # 2. Text-based (0.85) — when text is meaningful and distinct from the tag name
    _text_is_meaningful = bool(
        text
        and tag
        and text.upper() != tag.upper()
        and text.strip().upper() not in _STRUCTURAL_TAG_NAMES
    )
    if _text_is_meaningful:
        escaped = _escape_playwright_text(text)
        if escaped and len(escaped) < 60:
            if tag in _TAGS_WITHOUT_TEXT_CONTENT:
                selector = f'{tag}[placeholder*="{escaped}"], {tag}[aria-label*="{escaped}"]'
            else:
                selector = f'{tag}:has-text("{escaped}")'
            candidates.append({"selector": selector, "confidence": 0.85, "strategy": "text"})

    # 3. Role-based (0.60)
    if role and tag:
        candidates.append({"selector": f'{tag}[role="{role}"]', "confidence": 0.60, "strategy": "role"})

    # 4. CSS path (0.40)
    if css_path:
        candidates.append({"selector": css_path, "confidence": 0.40, "strategy": "css_path"})

    # 5. Class-based (0.30)
    if classes and tag:
        safe_classes = [c for c in classes if _is_sane_css_class(c)]
        if safe_classes:
            candidates.append({"selector": f"{tag}.{'.'.join(safe_classes)}", "confidence": 0.30, "strategy": "class"})

    # 6. Fallback (0.10) — always included as last resort
    candidates.append({"selector": f'{tag}:has-text("未知控件")', "confidence": 0.10, "strategy": "fallback"})

    return candidates


def _escape_playwright_text(raw: str) -> str:
    """Escape text for a Playwright text selector (``has-text`` / ``text=``).

    Playwright treats single quotes, double quotes, and backslashes as
    special inside a ``has-text`` argument.  We strip newlines (body text
    often contains them) and backslash-escape the dangerous characters.
    """
    compacted = re.sub(r"\s+", " ", str(raw or "")).strip()[:80]
    escaped = compacted.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
    return escaped


def _is_sane_css_id(value: str) -> bool:
    """True when *value* is safe to use as a bare CSS ``#`` selector.

    Auto-generated ids (``:r1:``, random hex strings, very-long strings)
    can change across page loads, making them no more stable than a CSS path,
    so we skip them.
    """
    v = str(value).strip()
    if not v or len(v) < 2 or len(v) > 48:
        return False
    # React-style auto-ids start with colon or digit
    if re.match(r"^[:\d]", v):
        return False
    # Pure hex strings (8+ chars) are likely random
    if re.fullmatch(r"[0-9a-fA-F]{8,}", v):
        return False
    # Space, slash, backslash are never safe in bare CSS #id
    if "/" in v or "\\" in v or " " in v:
        return False
    # Otherwise: if it contains only alphanumeric, hyphens, underscores,
    # and colons (for legacy frameworks), it's safe
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_:.\-]*", v):
        return True
    return False


def _is_sane_css_class(value: str) -> bool:
    """True when *value* is safe to use as a bare CSS ``.class`` selector."""
    v = str(value).strip()
    if not v or len(v) > 80:
        return False
    if re.search(r"[^a-zA-Z0-9_-]", v):
        return False
    return True


async def classify_features_with_playwright(
    page: "Page",
    adapter: "FeatureAdapter",
    page_goal_id: str,
    *,
    page_id: str,
    screenshots_dir: Path,
    max_features_per_page: int = 6,
    safety_policy: str = "low_risk_only",
    cdp_url: str = "",
    model_name: str | None = None,
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
    _browser_use_used = False
    for feature in real_features:
        feature_type = feature.get("feature_type", "view")
        risk_level = feature.get("risk_level", "low")
        element_text = feature.get("name")
        evidence = feature.get("evidence", {})
        raw_text = str(evidence.get("raw_text") or "")
        tag = evidence.get("tag") or ""
        input_type = evidence.get("type") or ""

        # Browser Use reclassification for unrecognized input/select controls
        if feature_type == "view" and cdp_url and model_name and tag in {"input", "select", "textarea"}:
            try:
                from ..execution_goal.browser_use_executor import (
                    BrowserUseSafety,
                    execute_with_browser_use,
                )
                instruction = (
                    f"页面上有一个 <{tag}> 控件" +
                    (f"，type={input_type}" if input_type else "") +
                    (f"，标签文字是 \"{element_text}\"" if element_text else "") +
                    "。请判断这个控件的功能类型，从以下选项中选择："
                    "file_upload(文件上传)、date_picker(日期选择)、text_input(文本输入)、"
                    "cascader(级联选择下拉)、dropdown(普通下拉)、submit(提交按钮)、other(其他)。"
                    "只返回 JSON: {\"feature_type\": \"...\", \"risk_level\": \"low|high\"}"
                )
                result = await execute_with_browser_use(
                    page, instruction,
                    context={"stage": "feature_discovery", "cdp_url": cdp_url},
                    safety=BrowserUseSafety(write_allowed=False, max_steps=3),
                    model_name=model_name,
                )
                if result.ok:
                    import json as _json
                    try:
                        content = str(result.actions[0].get("content", "")) if result.actions else ""
                        m = __import__("re").search(r'\{[^{}]*"feature_type"[^{}]*\}', content or str(result.actions))
                        if m:
                            data = _json.loads(m.group(0))
                            new_type = data.get("feature_type")
                        else:
                            new_type = None
                    except Exception:
                        new_type = None
                    if new_type and new_type != "other":
                        feature_type = new_type
                        risk_level = data.get("risk_level", "high") if new_type in {"file_upload", "submit"} else "low"
                        _browser_use_used = True
                        # Register new keywords so future rounds can classify this type directly
                        from ..v3_real_browser import register_feature_type
                        register_feature_type(new_type, [raw_text, element_text or "", tag, input_type])
            except Exception:
                pass
            finally:
                if feature_type == "view":
                    continue  # still unrecognized — skip this control

        element_locator = _build_stable_locator(
            tag=evidence.get("tag") or "",
            text=raw_text,
            el_id=evidence.get("id") or "",
            role=evidence.get("role") or "",
            css_path=evidence.get("css_path") or "",
            classes=evidence.get("classes") or [],
        )
        locator_candidates = _build_locator_candidates(
            tag=evidence.get("tag") or "",
            text=raw_text,
            el_id=evidence.get("id") or "",
            role=evidence.get("role") or "",
            css_path=evidence.get("css_path") or "",
            classes=evidence.get("classes") or [],
        )

        feature_goal_id = adapter.register_feature_goal(
            feature_id=feature["feature_id"],
            page_id=page_id,
            feature_type=feature_type,
            risk_level=risk_level,
            parent_goal_id=page_goal_id,
            element_text=element_text,
            element_locator=element_locator,
            locator_candidates=locator_candidates,
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
            locator_candidates=locator_candidates,
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
                locator_candidates=locator_candidates,
                page_url=page.url,
                safety_policy=safety_policy,
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


__all__ = [
    "EXECUTION_MODE_REAL_BROWSER",
    "_build_locator_candidates",
    "_build_stable_locator",
    "classify_features_with_playwright",
]
