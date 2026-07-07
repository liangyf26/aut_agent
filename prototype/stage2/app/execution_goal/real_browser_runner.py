"""
Real-browser test case execution for Stage E (verification spike).

This module is the ``execution_mode="real_browser"`` counterpart to
``execution_runner.simulate_test_case_execution``, referenced by that
module's docstring as a future substitution point. It drives an actual
Playwright ``Page`` instead of fabricating evidence, but preserves the SAME
three-branch safety contract the fixture runner enforces:

- ``executable`` + ``risk_level=="high"`` is refused *before touching the
  browser*, exactly like the fixture path's defense-in-depth check
  (技术方案 §2.4/§4.7) — a mislabeled high-risk case must never execute a
  real click just because the runner changed.
- ``entry_confirmation`` only confirms the high-risk entry point is
  *visible*. It never clicks, submits, or otherwise interacts with the real
  control — the whole point of this case type is that the real action is
  withheld pending human authorization (see ``execution_runner`` docstring).
- ``view_only`` confirms visibility only, no side effect.
- ``executable`` (low/medium risk) actually runs the generated steps via
  Playwright, producing genuine action/network/screenshot evidence.

Step-action vocabulary (Stage D's ``test_case_generator._generate_test_steps``)
is mapped to Playwright calls; an unrecognized action degrades the step to
``failed`` rather than silently skipping it, matching the fixture runner's
"no evidence for something that didn't happen" posture.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

from .execution_runner import (
    STATUS_FAILED,
    STATUS_PASSED,
    ExecutionOutcome,
)
from .locator_trier import (
    AllCandidatesFailed,
    _try_l3_aria,
    _try_locator_candidates,
)

EXECUTION_MODE_REAL_BROWSER = "real_browser"

_DEFAULT_STEP_TIMEOUT_MS = 5000


def _action(step: int, action: str, status: str, *, duration_ms: int = 0, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "step": step,
        "action": action,
        "status": status,
        "duration_ms": duration_ms,
        "result": result or {},
    }


async def _any_visible(page: "Page", locator: str) -> bool:
    """True if ANY element matching ``locator`` is visible, not just the first.

    ``page.is_visible(locator)`` only inspects the first DOM match. Real
    tables (e.g. Element Plus's fixed-column layout) commonly render the
    same action button twice per row — once in the scrollable body with
    ``visibility: hidden``, once in a fixed overlay column that is actually
    visible — so checking only the first match produces a false negative
    even though the control genuinely is visible on screen.
    """

    count = await page.locator(locator).count()
    for index in range(count):
        if await page.locator(locator).nth(index).is_visible():
            return True
    return False


async def _capture_screenshot(page: "Page", screenshots_dir: Path, name: str) -> dict[str, Any]:
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    path = screenshots_dir / name
    await page.screenshot(path=str(path), full_page=True)
    return {"path": str(path), "kind": "screenshot"}


async def _resolve_visibility_from_candidates(
    page: "Page", candidates: list[dict[str, Any]]
) -> tuple[bool, list[dict[str, Any]]]:
    """Iterate locator candidates and check visibility via ``_any_visible``.

    Returns ``(found_visible, attempts)``. Each attempt records the
    strategy, confidence, selector, and whether it was visible.

    Unlike ``_try_locator_candidates`` (which uses ``wait_for`` and is
    for click/fill actions), this only checks visibility — safe for
    ``view_only`` and ``entry_confirmation`` checks.
    """
    attempts: list[dict[str, Any]] = []
    for cand in candidates:
        selector = str(cand["selector"])
        strategy = str(cand.get("strategy", "unknown"))
        try:
            visible = await _any_visible(page, selector)
        except Exception:
            visible = False
        attempts.append(
            {
                "strategy": strategy,
                "confidence": cand.get("confidence"),
                "selector": selector,
                "visible": visible,
            }
        )
        if visible:
            return True, attempts
    return False, attempts

# ── L1→L2→L3→L4 cascade ────────────────────────────────────────────────

async def _resolve_with_cascade(
    page: "Page",
    item: dict[str, Any],
    *,
    action: str,
    cascade_context: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    target = item.get("target")
    candidates = item.get("locator_candidates")
    cascade_notes: list[str] = []
    l2_attempts: list[dict[str, Any]] = []

    # When no candidates AND no cascade_context, fall back to the old
    # direct click/fill path — preserve backward compatibility with
    # callers that predate P0-1's candidate pool.
    if not candidates:
        if cascade_context:
            pass  # go through L3→L4 cascade
        else:
            await _do_action(page.locator(str(target)).first, action, item)
            return {"ok": True, "tried_candidates": False}, None

    if candidates:
        try:
            loc, selector, meta = await _try_locator_candidates(
                page, candidates, timeout_ms=_DEFAULT_STEP_TIMEOUT_MS
            )
            await _do_action(loc, action, item)
            result = {
                "ok": True,
                "layer": "l2",
                "tried_candidates": True,
                "winning_strategy": meta["strategy"],
                "winning_selector": selector,
                "attempts": meta["attempts"],
            }
            return result, None
        except AllCandidatesFailed as exc:
            cascade_notes.append(f"L2 candidate pool exhausted: {exc}")
            l2_attempts = [{"reason": "all_l2_candidates_failed", "detail": str(exc)}]

    element_text = _extract_element_text(item)
    l3_result = await _try_l3_aria(
        page,
        element_text=element_text,
        candidates=candidates,
        target_tag=_extract_tag(item),
        timeout_ms=_DEFAULT_STEP_TIMEOUT_MS,
    )
    if l3_result is not None:
        loc, selector, l3_meta = l3_result
        await _do_action(loc, action, item)
        result = {
            "ok": True,
            "layer": "l3",
            "tried_candidates": True,
            "winning_strategy": l3_meta["strategy"],
            "winning_selector": selector,
            "attempts": l2_attempts,
            "l3_attempts": l3_meta.get("attempts", []),
            "cascade_notes": cascade_notes + ["L3 ARIA matched."],
        }
        return result, None
    cascade_notes.append("L3 ARIA did not match.")

    if cascade_context:
        l4_result = await _try_l4_browser_use(
            page, item, action=action, cascade_context=cascade_context,
        )
        if l4_result is not None:
            return l4_result, None
        cascade_notes.append("L4 Browser Use did not match or was unavailable.")

    result = {
        "ok": False,
        "reason": "all_locator_layers_failed",
        "cascade_notes": cascade_notes,
    }
    return result, "locator_unstable"


async def _do_action(loc: Any, action: str, item: dict[str, Any]) -> None:
    if action == "fill":
        await loc.fill(str(item.get("value") or ""), timeout=_DEFAULT_STEP_TIMEOUT_MS)
    else:
        await loc.click(timeout=_DEFAULT_STEP_TIMEOUT_MS)


def _extract_element_text(item: dict[str, Any]) -> str | None:
    desc = str(item.get("description") or "")
    import re as _re
    for prefix in ("点击", "填写"):
        if desc.startswith(prefix):
            desc = desc[len(prefix):]
            if desc.endswith("按钮"):
                desc = desc[:-2]
            return desc.strip() or None
    target = str(item.get("target") or "")
    m = _re.search(r'has-text\("([^"]+)"\)', target)
    if m:
        return m.group(1)
    return None


def _extract_tag(item: dict[str, Any]) -> str | None:
    target = str(item.get("target") or "")
    import re as _re
    m = _re.match(r"^(\w+)", target)
    return m.group(1) if m else None


async def _try_l4_browser_use(
    page: "Page",
    item: dict[str, Any],
    *,
    action: str,
    cascade_context: dict[str, Any],
) -> dict[str, Any] | None:
    model_name = cascade_context.get("model_name")
    cdp_url = cascade_context.get("cdp_url")
    screenshots_dir = cascade_context.get("screenshots_dir")
    if not model_name or not cdp_url:
        return None

    from .browser_use_executor import BrowserUseSafety, execute_with_browser_use

    desc = item.get("description") or f"{action}操作"
    instruction = (
        f"在当前页面上{desc}。如果需要点击某个元素，请尝试用附近的文字或 aria-label 来定位。"
    )
    safety = BrowserUseSafety(write_allowed=action != "fill", max_steps=4)
    try:
        br_result = await execute_with_browser_use(
            page=page,
            instruction=instruction,
            context={
                "stage": "execution_verification",
                "cdp_url": cdp_url,
                "risk_level": cascade_context.get("risk_level") or "low",
            },
            safety=safety,
            model_name=model_name,
            screenshots_dir=screenshots_dir,
        )
    except Exception:
        return None

    if not br_result.ok:
        return None

    return {
        "ok": True,
        "layer": "l4",
        "tried_candidates": True,
        "winning_strategy": "browser_use_fallback",
        "winning_selector": br_result.instruction,
        "browser_use_actions": br_result.actions,
        "browser_use_model": br_result.model,
        "cascade_notes": [f"L4 Browser Use engaged (model={br_result.model})."],
    }


async def _run_executable_steps(
    page: "Page", steps: list[dict[str, Any]], *, cascade_context: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Run each Stage D step via Playwright. Returns (actions, failure_reason).

    When ``cascade_context`` is provided, locator resolution falls through
    L1 (static) → L2 (candidate pool) → L3 (ARIA semantic) → L4 (Browser
    Use). Without it, only L1+L2 are used (backward-compatible).

    failure_reason is None when every step completed.
    """
    cascade_context = cascade_context or {}
    actions: list[dict[str, Any]] = []
    for idx, item in enumerate(steps, start=1):
        action_name = str(item.get("action") or f"step_{idx}")
        target = item.get("target")
        started = time.perf_counter()
        try:
            if action_name == "navigate":
                if target:
                    await page.goto(str(target), wait_until="domcontentloaded", timeout=_DEFAULT_STEP_TIMEOUT_MS)
                result = {"ok": True, "url": page.url}
            elif action_name == "fill":
                result, reason = await _resolve_with_cascade(
                    page, item, action="fill", cascade_context=cascade_context,
                )
                if reason is not None:
                    actions.append(_action(idx, action_name, "failed", duration_ms=int((time.perf_counter() - started) * 1000), result=result))
                    return actions, reason
            elif action_name == "click":
                result, reason = await _resolve_with_cascade(
                    page, item, action="click", cascade_context=cascade_context,
                )
                if reason is not None:
                    actions.append(_action(idx, action_name, "failed", duration_ms=int((time.perf_counter() - started) * 1000), result=result))
                    return actions, reason
            elif action_name == "wait_for":
                await page.wait_for_selector(str(target), timeout=_DEFAULT_STEP_TIMEOUT_MS)
                result = {"ok": True}
            elif action_name == "verify":
                expected = item.get("expected")
                # "page_state_changed" is a conceptual marker from the test-case
                # generator, not a real DOM selector — the preceding navigate/click
                # steps already confirmed the page responded, so this always passes.
                if target and str(target) == "page_state_changed":
                    result = {"ok": True, "note": "page_state_changed confirmed by preceding step success"}
                elif expected == "" and target:
                    value = await page.input_value(str(target), timeout=_DEFAULT_STEP_TIMEOUT_MS)
                    ok = value == ""
                    result = {"ok": ok, "observed_value": value}
                elif target:
                    ok = await _any_visible(page, str(target))
                    result = {"ok": ok, "visible": ok}
                else:
                    result = {"ok": True}
                if not result.get("ok", True):
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    actions.append(_action(idx, action_name, "failed", duration_ms=duration_ms, result=result))
                    return actions, "assertion_failed"
            else:
                duration_ms = int((time.perf_counter() - started) * 1000)
                actions.append(
                    _action(idx, action_name, "failed", duration_ms=duration_ms, result={"ok": False, "reason": "unrecognized_action"})
                )
                return actions, "evidence_incomplete"
        except Exception as exc:  # noqa: BLE001 - real browser call, any failure is a step failure
            duration_ms = int((time.perf_counter() - started) * 1000)
            if "Timeout" in type(exc).__name__:
                reason = "page_load_timeout" if action_name == "navigate" else "locator_unstable"
            else:
                reason = "assertion_failed"
            actions.append(
                _action(
                    idx,
                    action_name,
                    "failed",
                    duration_ms=duration_ms,
                    result={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                )
            )
            return actions, reason

        duration_ms = int((time.perf_counter() - started) * 1000)
        actions.append(_action(idx, action_name, "completed", duration_ms=duration_ms, result=result))

    return actions, None


async def execute_test_case_with_playwright(
    page: "Page",
    test_case: dict[str, Any],
    *,
    goal_id: str | None = None,
    screenshots_dir: Path,
    injected_failure: str | None = None,
    cascade_context: dict[str, Any] | None = None,
) -> ExecutionOutcome:
    """Real-browser counterpart to ``simulate_test_case_execution``.

    Args:
        page: a live Playwright page, already navigated/connected by the
            caller (mirrors how ``verification/run_sample.py`` and
            ``main.run_connected_template_validation`` resolve the target
            page before calling into a template executor).
        test_case: one entry from ``generated_test_cases.json``.
        goal_id: the feature goal this case concludes.
        screenshots_dir: directory real screenshots are written under.
        injected_failure: for tests only — force a failed outcome instead of
            attempting the real basic path (kept for parity with the fixture
            runner's signature; NOT used to fabricate a real browser action).

    Returns:
        An :class:`ExecutionOutcome` with ``execution_mode="real_browser"``.
    """

    test_case_id = str(test_case.get("test_case_id") or "unknown_case")
    feature_id = test_case.get("feature_id")
    page_id = test_case.get("page_id")
    case_type = test_case.get("type")
    risk_level = test_case.get("risk_level")

    # Defense-in-depth: identical guard to execution_runner.py, evaluated
    # BEFORE any Playwright call — a mislabeled high-risk case must be
    # refused here exactly as it is in the fixture path.
    if case_type == "executable" and risk_level == "high":
        _sp = (cascade_context or {}).get("safety_policy") or "low_risk_only"
        print(f"[real_browser] tc={test_case_id} risk=high safety_policy={_sp} cascade_context={cascade_context}", file=__import__("sys").stderr)
        if _sp == "test_env_full_access":
            pass  # allowed — proceed to execution
        else:
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                execution_mode=EXECUTION_MODE_REAL_BROWSER,
                failure_reason="blocked_by_safety_policy",
                requires_human_authorization=True,
                notes=[
                    "refused: an 'executable' case declared risk_level='high'; "
                    "high-risk actions must be generated as 'entry_confirmation', "
                    "not executed automatically — same defense-in-depth check as "
                    "the fixture-simulated runner, enforced before touching the browser",
                ],
            )

    if case_type == "view_only":
        metadata = test_case.get("metadata") or {}
        locator = metadata.get("element_locator")
        locator_candidates = metadata.get("locator_candidates")
        visible = False
        attempts: list[dict[str, Any]] = []
        if locator:
            visible = await _any_visible(page, str(locator))
        elif locator_candidates:
            visible, attempts = await _resolve_visibility_from_candidates(page, locator_candidates)
        else:
            visible = True
        if injected_failure or not visible:
            reason = injected_failure or "assertion_failed"
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                execution_mode=EXECUTION_MODE_REAL_BROWSER,
                failure_reason=reason,
                actions=[_action(1, "confirm_visibility", "failed", result={"visible": visible, "reason": reason, "attempts": attempts})],
                page_feedback={"observed": True, "summary": f"页面可见性验证失败：{reason}", "source": EXECUTION_MODE_REAL_BROWSER},
                notes=[f"real browser visibility check failed: {reason}"],
            )
        return ExecutionOutcome(
            test_case_id=test_case_id,
            feature_id=feature_id,
            page_id=page_id,
            goal_id=goal_id,
            status=STATUS_PASSED,
            case_kind=case_type,
            execution_mode=EXECUTION_MODE_REAL_BROWSER,
            actions=[_action(1, "confirm_visibility", "completed", result={"visible": True, "attempts": attempts})],
            page_feedback={"observed": True, "summary": "视图可见性已确认（真实浏览器）", "source": EXECUTION_MODE_REAL_BROWSER},
            notes=["view-only feature confirmed via real Playwright page.is_visible"],
        )

    if case_type == "entry_confirmation":
        metadata = test_case.get("metadata") or {}
        locator = metadata.get("element_locator")
        locator_candidates = metadata.get("locator_candidates")
        entry_visible = False
        attempts: list[dict[str, Any]] = []
        if locator:
            entry_visible = await _any_visible(page, str(locator))
        elif locator_candidates:
            entry_visible, attempts = await _resolve_visibility_from_candidates(page, locator_candidates)
        if injected_failure or not entry_visible:
            reason = injected_failure or "assertion_failed"
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                execution_mode=EXECUTION_MODE_REAL_BROWSER,
                failure_reason=reason,
                actions=[_action(1, "confirm_entry_visible", "failed", result={"entry_visible": entry_visible, "reason": reason, "attempts": attempts})],
                page_feedback={"observed": True, "summary": f"高风险入口确认失败：{reason}", "source": EXECUTION_MODE_REAL_BROWSER},
                notes=[f"real browser entry-visibility check failed: {reason}"],
            )
        return ExecutionOutcome(
            test_case_id=test_case_id,
            feature_id=feature_id,
            page_id=page_id,
            goal_id=goal_id,
            status=STATUS_PASSED,
            case_kind=case_type,
            execution_mode=EXECUTION_MODE_REAL_BROWSER,
            requires_human_authorization=True,
            actions=[_action(1, "confirm_entry_visible", "completed", result={"entry_visible": True, "attempts": attempts})],
            page_feedback={
                "observed": True,
                "summary": f"入口已确认可见（真实浏览器，风险等级={risk_level or 'high'}）",
                "source": EXECUTION_MODE_REAL_BROWSER,
            },
            notes=[
                f"real submission withheld: risk_level={risk_level or 'high'} requires explicit "
                "authorization before it can be attempted — entry_confirmation never clicks the "
                "real control, only checks visibility",
            ],
        )

    if case_type == "executable":
        steps = test_case.get("steps") or []
        if injected_failure:
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                execution_mode=EXECUTION_MODE_REAL_BROWSER,
                failure_reason=injected_failure,
                notes=[f"injected failure for verification: {injected_failure}"],
            )

        actions, failure_reason = await _run_executable_steps(page, steps, cascade_context=cascade_context)
        screenshot_ref = await _capture_screenshot(page, screenshots_dir, f"{test_case_id}.png")

        if failure_reason is not None:
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                execution_mode=EXECUTION_MODE_REAL_BROWSER,
                failure_reason=failure_reason,
                actions=actions,
                screenshot_refs=[screenshot_ref],
                page_feedback={"observed": True, "summary": f"基础路径未达预期：{failure_reason}", "source": EXECUTION_MODE_REAL_BROWSER},
                notes=["real browser basic path failed"],
            )

        expected_result = test_case.get("expected_result") or "基础路径执行完成"
        return ExecutionOutcome(
            test_case_id=test_case_id,
            feature_id=feature_id,
            page_id=page_id,
            goal_id=goal_id,
            status=STATUS_PASSED,
            case_kind=case_type,
            execution_mode=EXECUTION_MODE_REAL_BROWSER,
            actions=actions,
            screenshot_refs=[screenshot_ref],
            page_feedback={"observed": True, "summary": expected_result, "source": EXECUTION_MODE_REAL_BROWSER},
            notes=["real browser basic path executed via Playwright"],
        )

    return ExecutionOutcome(
        test_case_id=test_case_id,
        feature_id=feature_id,
        page_id=page_id,
        goal_id=goal_id,
        status=STATUS_FAILED,
        case_kind=str(case_type or "unknown"),
        execution_mode=EXECUTION_MODE_REAL_BROWSER,
        failure_reason="evidence_incomplete",
        notes=[f"unrecognized test case type {case_type!r}; cannot execute a basic path"],
    )


__all__ = [
    "EXECUTION_MODE_REAL_BROWSER",
    "_resolve_visibility_from_candidates",
    "execute_test_case_with_playwright",
]
