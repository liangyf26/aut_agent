"""
Fixture-simulated test case execution for Stage E.

IMPORTANT — this module does NOT drive a real browser. Consistent with
Stages B/C/D (which are fixture/rule driven, no live SUT — see 实施计划
§2.6), Stage E's default execution path *simulates* running a generated
test case and produces honestly-labeled synthetic evidence:

- ``execution_mode`` is always stamped ``"fixture_simulated"`` on the
  outcome so nothing downstream can mistake this for a live-browser run.
- No screenshot files are fabricated. A real screenshot is a claim that a
  file exists on disk; inventing a path here would be a false evidence
  claim (技术方案 §5.7 / 实施计划 §11.2). Feedback evidence uses a plain
  JSON note instead, mirroring the existing ``dom://snapshot`` non-file
  evidence convention already used by Stage D's adapters.
- Network capture is honestly reported as unavailable in this mode
  (``capture_status="not_applicable_fixture_mode"``) rather than
  synthesizing fake request/response pairs.

Case-type semantics (方案 §4.7 — 高风险动作默认只验证入口和前置条件，不默认执行最终提交):

- ``view_only``: confirms visibility only. Always a basic-path success.
- ``executable``: runs the generated steps. Passes unless a failure is
  injected (for tests / future real-runner integration).
- ``entry_confirmation``: confirms only that the high-risk entry point is
  visible; the underlying real submission is never attempted here. This
  is itself a successful, low-risk basic path — the outcome is flagged
  with ``requires_human_authorization=True`` so a human task can be
  raised for the *real* action, without pausing the rest of the batch.

A real Playwright-backed runner (mirroring ``verification/template_executor.py``)
can be substituted later without changing the orchestrator's contract: it
would return the same :class:`ExecutionOutcome` shape with
``execution_mode="real_browser"`` and real screenshot/network evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"

EXECUTION_MODE_FIXTURE_SIMULATED = "fixture_simulated"


@dataclass(slots=True)
class ExecutionOutcome:
    test_case_id: str
    feature_id: str | None
    page_id: str | None
    goal_id: str | None
    status: str
    case_kind: str = "unknown"
    execution_mode: str = EXECUTION_MODE_FIXTURE_SIMULATED
    failure_reason: str | None = None
    requires_human_authorization: bool = False
    actions: list[dict[str, Any]] = field(default_factory=list)
    page_feedback: dict[str, Any] = field(default_factory=dict)
    network_events: list[dict[str, Any]] = field(default_factory=list)
    screenshot_refs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_case_id": self.test_case_id,
            "feature_id": self.feature_id,
            "page_id": self.page_id,
            "goal_id": self.goal_id,
            "status": self.status,
            "case_kind": self.case_kind,
            "execution_mode": self.execution_mode,
            "failure_reason": self.failure_reason,
            "requires_human_authorization": self.requires_human_authorization,
            "actions": self.actions,
            "page_feedback": self.page_feedback,
            "network_events": self.network_events,
            "screenshot_refs": self.screenshot_refs,
            "notes": self.notes,
        }


def _action(step: int, action: str, status: str, *, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "step": step,
        "action": action,
        "status": status,
        "duration_ms": 0,
        "result": result or {},
    }


def simulate_test_case_execution(
    test_case: dict[str, Any],
    *,
    goal_id: str | None = None,
    injected_failure: str | None = None,
) -> ExecutionOutcome:
    """Simulate running one Stage D test case.

    Args:
        test_case: one entry from ``generated_test_cases.json``.
        goal_id: the feature goal this case concludes (resolved by the loader
            via ``feature_points.json``).
        injected_failure: for tests / future real-runner integration — force
            the case to conclude as failed with this fixed failure class
            instead of the deterministic "pass" outcome.

    Returns:
        An :class:`ExecutionOutcome`. Never raises for a well-formed
        ``test_case``; a missing/unknown ``type`` degrades to a failed
        outcome with ``evidence_incomplete``, matching the conservative
        "unknown -> overflow bucket" posture used elsewhere in the goal loop.
    """

    test_case_id = str(test_case.get("test_case_id") or "unknown_case")
    feature_id = test_case.get("feature_id")
    page_id = test_case.get("page_id")
    case_type = test_case.get("type")
    risk_level = test_case.get("risk_level")

    # Defense-in-depth (技术方案 §2.4/§4.7): Stage D's classifier is supposed to
    # route every risk_level=="high" feature to an entry_confirmation case, but
    # Stage E must not TRUST that blindly — generated_test_cases.json can be
    # stale, hand-edited, or produced by a future classifier bug. An
    # "executable" case claiming risk_level=="high" is refused here rather
    # than executed, so a mislabeled high-risk case can never slip through as
    # a real basic-path execution (fixture-simulated today; a real_browser
    # runner substituted later inherits this same refusal since it is not
    # case-type-dispatch-specific).
    if case_type == "executable" and risk_level == "high":
        return ExecutionOutcome(
            test_case_id=test_case_id,
            feature_id=feature_id,
            page_id=page_id,
            goal_id=goal_id,
            status=STATUS_FAILED,
            case_kind=case_type,
            failure_reason="blocked_by_safety_policy",
            requires_human_authorization=True,
            notes=[
                "refused: an 'executable' case declared risk_level='high'; "
                "high-risk actions must be generated as 'entry_confirmation', "
                "not executed automatically — this is a defense-in-depth check "
                "independent of Stage D's own classification",
            ],
        )

    if case_type == "view_only":
        if injected_failure:
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                failure_reason=injected_failure,
                actions=[_action(1, "confirm_visibility", "failed", result={"visible": False, "reason": injected_failure})],
                page_feedback={"observed": True, "summary": f"页面可见性验证失败：{injected_failure}", "source": EXECUTION_MODE_FIXTURE_SIMULATED},
                notes=[f"injected failure for verification: {injected_failure}"],
            )
        return ExecutionOutcome(
            test_case_id=test_case_id,
            feature_id=feature_id,
            page_id=page_id,
            goal_id=goal_id,
            status=STATUS_PASSED,
            case_kind=case_type,
            actions=[_action(1, "confirm_visibility", "completed", result={"visible": True})],
            page_feedback={"observed": True, "summary": "视图可见性已确认", "source": EXECUTION_MODE_FIXTURE_SIMULATED},
            notes=["view-only feature carries no side effect; visibility confirmation is its basic path"],
        )

    if case_type == "entry_confirmation":
        risk_level = test_case.get("risk_level") or "high"
        if injected_failure:
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                failure_reason=injected_failure,
                actions=[_action(1, "confirm_entry_visible", "failed", result={"entry_visible": False, "reason": injected_failure})],
                page_feedback={"observed": True, "summary": f"高风险入口确认失败：{injected_failure}", "source": EXECUTION_MODE_FIXTURE_SIMULATED},
                notes=[f"injected failure for verification: {injected_failure}"],
            )
        return ExecutionOutcome(
            test_case_id=test_case_id,
            feature_id=feature_id,
            page_id=page_id,
            goal_id=goal_id,
            status=STATUS_PASSED,
            case_kind=case_type,
            requires_human_authorization=True,
            actions=[_action(1, "confirm_entry_visible", "completed", result={"entry_visible": True})],
            page_feedback={
                "observed": True,
                "summary": f"入口已确认可见（风险等级={risk_level}）",
                "source": EXECUTION_MODE_FIXTURE_SIMULATED,
            },
            notes=[f"real submission withheld: risk_level={risk_level} requires explicit authorization before it can be attempted"],
        )

    if case_type == "executable":
        steps = test_case.get("steps") or []
        if injected_failure:
            actions = [
                _action(idx, str(item.get("action") or "step"), "completed")
                for idx, item in enumerate(steps[:-1], start=1)
            ]
            last_index = len(steps) if steps else 1
            actions.append(
                _action(
                    last_index,
                    str(steps[-1].get("action")) if steps else "verify",
                    "failed",
                    result={"ok": False, "reason": injected_failure},
                )
            )
            return ExecutionOutcome(
                test_case_id=test_case_id,
                feature_id=feature_id,
                page_id=page_id,
                goal_id=goal_id,
                status=STATUS_FAILED,
                case_kind=case_type,
                failure_reason=injected_failure,
                actions=actions,
                page_feedback={
                    "observed": True,
                    "summary": f"基础路径未达预期：{injected_failure}",
                    "source": EXECUTION_MODE_FIXTURE_SIMULATED,
                },
                notes=[f"injected failure for verification: {injected_failure}"],
            )

        actions = [
            _action(idx, str(item.get("action") or f"step_{idx}"), "completed")
            for idx, item in enumerate(steps, start=1)
        ] or [_action(1, "basic_path", "completed")]
        expected_result = test_case.get("expected_result") or "基础路径执行完成"
        return ExecutionOutcome(
            test_case_id=test_case_id,
            feature_id=feature_id,
            page_id=page_id,
            goal_id=goal_id,
            status=STATUS_PASSED,
            case_kind=case_type,
            actions=actions,
            page_feedback={"observed": True, "summary": expected_result, "source": EXECUTION_MODE_FIXTURE_SIMULATED},
            notes=["fixture-simulated basic path; no live browser was driven"],
        )

    return ExecutionOutcome(
        test_case_id=test_case_id,
        feature_id=feature_id,
        page_id=page_id,
        goal_id=goal_id,
        status=STATUS_FAILED,
        case_kind=str(case_type or "unknown"),
        failure_reason="evidence_incomplete",
        notes=[f"unrecognized test case type {case_type!r}; cannot execute a basic path"],
    )


__all__ = [
    "STATUS_PASSED",
    "STATUS_FAILED",
    "EXECUTION_MODE_FIXTURE_SIMULATED",
    "ExecutionOutcome",
    "simulate_test_case_execution",
]
