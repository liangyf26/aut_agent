"""Computable predicates and named thresholds (技术方案 §6.6).

Every success and stop decision in the goal loop must reduce to a computable
predicate over evidence, carrying:

- an ``expression`` string (e.g. ``"attempt_count >= max_rounds"``), and
- the named ``params`` that were read (thresholds + observed values).

This is what makes verification standard #6 hold: a reviewer can trace every
"why did it stop / why did it pass" back to a predicate and a named threshold,
instead of a natural-language sentence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import (
    GOAL_TYPE_FEATURE,
    GOAL_TYPE_MENU,
    GOAL_TYPE_PAGE,
    STATUS_BLOCKED_BY_EXECUTOR,
    STATUS_BLOCKED_BY_POLICY,
    STATUS_FAILED_MAX_ROUNDS,
    STATUS_STOPPED_NO_PROGRESS,
    STATUS_SUCCEEDED,
    STATUS_WAITING_HUMAN,
    _compact_dict,
)
from .playbook import HUMAN_REQUIRED_CLASSES


@dataclass(frozen=True, slots=True)
class Thresholds:
    """All tunable parameters for goal-loop predicates, in one place."""

    default_max_rounds: int = 3
    max_rounds_by_type: dict[str, int] = field(
        default_factory=lambda: {GOAL_TYPE_MENU: 3, GOAL_TYPE_PAGE: 3, GOAL_TYPE_FEATURE: 4}
    )
    no_progress_threshold: int = 2
    min_visible_text_len: int = 20
    min_dom_nodes: int = 5
    blank_screenshot_ratio: float = 0.98
    human_required_classes: frozenset[str] = HUMAN_REQUIRED_CLASSES
    escalation_occurrence_threshold: int = 3
    escalation_success_floor: float = 0.0

    def max_rounds_for(self, goal_type: str) -> int:
        return int(self.max_rounds_by_type.get(goal_type, self.default_max_rounds))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["human_required_classes"] = sorted(self.human_required_classes)
        return payload


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(slots=True)
class PredicateResult:
    name: str
    expression: str
    value: bool
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(asdict(self))


@dataclass(slots=True)
class StopEvaluation:
    should_stop: bool
    primary_reason: str | None
    target_status: str | None
    conditions: list[PredicateResult] = field(default_factory=list)

    @property
    def triggered(self) -> list[PredicateResult]:
        return [c for c in self.conditions if c.value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_stop": self.should_stop,
            "primary_reason": self.primary_reason,
            "target_status": self.target_status,
            "triggered": [c.name for c in self.triggered],
            "conditions": [c.to_dict() for c in self.conditions],
        }


# Stop conditions in descending priority. When several fire at once, the first
# in this order decides primary_reason / target_status.
_STOP_PRIORITY = (
    ("blocked_by_policy", STATUS_BLOCKED_BY_POLICY),
    ("blocked_by_executor", STATUS_BLOCKED_BY_EXECUTOR),
    ("waiting_human", STATUS_WAITING_HUMAN),
    ("no_progress_repeated", STATUS_STOPPED_NO_PROGRESS),
    ("max_rounds_reached", STATUS_FAILED_MAX_ROUNDS),
)


def evaluate_stop_conditions(
    *,
    goal_type: str,
    attempt_count: int,
    max_rounds: int,
    no_improvement_streak: int,
    active_failure_class: str | None = None,
    allow_human_intervention: bool = True,
    policy_blocked: bool = False,
    executor_unavailable: bool = False,
    playwright_required: bool = True,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> StopEvaluation:
    conditions: list[PredicateResult] = [
        PredicateResult(
            name="blocked_by_policy",
            expression="policy_gate.blocked == true",
            value=bool(policy_blocked),
            params={"policy_blocked": bool(policy_blocked)},
        ),
        PredicateResult(
            name="blocked_by_executor",
            expression="browser_use_available == false AND playwright_required",
            value=bool(executor_unavailable and playwright_required),
            params={
                "executor_unavailable": bool(executor_unavailable),
                "playwright_required": bool(playwright_required),
            },
        ),
        PredicateResult(
            name="waiting_human",
            expression="allow_human_intervention AND active_failure_class in human_required_classes",
            value=bool(
                allow_human_intervention
                and active_failure_class in thresholds.human_required_classes
            ),
            params={
                "active_failure_class": active_failure_class,
                "allow_human_intervention": bool(allow_human_intervention),
                "human_required_classes": sorted(thresholds.human_required_classes),
            },
        ),
        PredicateResult(
            name="no_progress_repeated",
            expression="no_improvement_streak >= no_progress_threshold",
            value=bool(no_improvement_streak >= thresholds.no_progress_threshold),
            params={
                "no_improvement_streak": int(no_improvement_streak),
                "no_progress_threshold": thresholds.no_progress_threshold,
            },
        ),
        PredicateResult(
            name="max_rounds_reached",
            expression="attempt_count >= max_rounds",
            value=bool(attempt_count >= max_rounds),
            params={"attempt_count": int(attempt_count), "max_rounds": int(max_rounds)},
        ),
    ]

    by_name = {c.name: c for c in conditions}
    primary_reason: str | None = None
    target_status: str | None = None
    for name, status in _STOP_PRIORITY:
        if by_name[name].value:
            primary_reason = name
            target_status = status
            break

    return StopEvaluation(
        should_stop=primary_reason is not None,
        primary_reason=primary_reason,
        target_status=target_status,
        conditions=conditions,
    )


def _is_blank(signals: dict[str, Any], thresholds: Thresholds) -> bool:
    visible_text_len = int(signals.get("visible_text_len", 0) or 0)
    dom_nodes = int(signals.get("dom_nodes", 0) or 0)
    blank_ratio = float(signals.get("blank_screenshot_ratio", 0.0) or 0.0)
    return (
        visible_text_len < thresholds.min_visible_text_len
        or dom_nodes < thresholds.min_dom_nodes
        or blank_ratio >= thresholds.blank_screenshot_ratio
    )


def evaluate_success(
    *,
    goal_type: str,
    signals: dict[str, Any] | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> PredicateResult:
    """Evaluate a goal-type-specific success predicate over structured signals."""

    signals = signals or {}

    if goal_type == GOAL_TYPE_MENU:
        has_menu_text = bool(signals.get("menu_text"))
        has_path = bool(signals.get("path"))
        has_screenshot = bool(signals.get("screenshot"))
        value = has_menu_text and has_path and has_screenshot
        return PredicateResult(
            name="menu_goal_success",
            expression="has_menu_text AND has_path AND has_screenshot",
            value=value,
            params={
                "has_menu_text": has_menu_text,
                "has_path": has_path,
                "has_screenshot": has_screenshot,
            },
        )

    if goal_type == GOAL_TYPE_PAGE:
        http_ok = bool(signals.get("http_ok"))
        has_main_content = bool(signals.get("has_main_content"))
        is_blank = _is_blank(signals, thresholds)
        value = http_ok and has_main_content and not is_blank
        return PredicateResult(
            name="page_goal_success",
            expression="http_ok AND has_main_content AND NOT is_blank",
            value=value,
            params={
                "http_ok": http_ok,
                "has_main_content": has_main_content,
                "is_blank": is_blank,
                "visible_text_len": int(signals.get("visible_text_len", 0) or 0),
                "dom_nodes": int(signals.get("dom_nodes", 0) or 0),
                "blank_screenshot_ratio": float(signals.get("blank_screenshot_ratio", 0.0) or 0.0),
                "min_visible_text_len": thresholds.min_visible_text_len,
                "min_dom_nodes": thresholds.min_dom_nodes,
                "blank_screenshot_threshold": thresholds.blank_screenshot_ratio,
            },
        )

    if goal_type == GOAL_TYPE_FEATURE:
        feature_identified = bool(signals.get("feature_identified"))
        case_generated = bool(signals.get("case_generated"))
        basic_path_executed = bool(signals.get("basic_path_executed"))
        has_feedback = bool(signals.get("has_feedback"))
        value = feature_identified and case_generated and basic_path_executed and has_feedback
        return PredicateResult(
            name="feature_goal_success",
            expression="feature_identified AND case_generated AND basic_path_executed AND has_feedback",
            value=value,
            params={
                "feature_identified": feature_identified,
                "case_generated": case_generated,
                "basic_path_executed": basic_path_executed,
                "has_feedback": has_feedback,
            },
        )

    return PredicateResult(
        name="unknown_goal_type_success",
        expression="false",
        value=False,
        params={"goal_type": goal_type},
    )


# Threshold params that are relevant when a goal type's success is declared up
# front (before any signal is observed).
_DECLARED_SUCCESS_PARAMS: dict[str, dict[str, str]] = {
    GOAL_TYPE_MENU: {},
    GOAL_TYPE_PAGE: {
        "min_visible_text_len": "min_visible_text_len",
        "min_dom_nodes": "min_dom_nodes",
        "blank_screenshot_threshold": "blank_screenshot_ratio",
    },
    GOAL_TYPE_FEATURE: {},
}


def success_criteria_for(goal_type: str, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> list[dict[str, Any]]:
    """Return the declared success criterion (expression + threshold params).

    Used when registering a goal so its success condition is recorded up front
    as a computable predicate rather than as prose. Params here are the named
    *thresholds* the predicate will read, not observed values.
    """

    declared = evaluate_success(goal_type=goal_type, signals={}, thresholds=thresholds)
    param_map = _DECLARED_SUCCESS_PARAMS.get(goal_type, {})
    params = {name: getattr(thresholds, attr) for name, attr in param_map.items()}
    return [
        {
            "predicate_name": declared.name,
            "expression": declared.expression,
            "params": params,
        }
    ]


__all__ = [
    "Thresholds",
    "DEFAULT_THRESHOLDS",
    "PredicateResult",
    "StopEvaluation",
    "evaluate_stop_conditions",
    "evaluate_success",
    "success_criteria_for",
]
