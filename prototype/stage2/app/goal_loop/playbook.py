"""Fixed failure-class -> playbook mapping (技术方案 §13).

Every failure class is bound to exactly one fixed playbook. A playbook is a
固定、可复用、可回放、可审计 action sequence plus an *exit* that constrains which
branch the goal loop may take afterwards. The test model may only *select* from
this table; it may not invent actions.

Exits are a closed set so failure handling is a finite state, not free play:

- ``retry``    -> re-attempt the same goal with the playbook applied
- ``continue`` -> advance the frontier to the next goal (target discovered but
                  not yet covered)
- ``human``    -> raise a human task; goal enters ``waiting_human``
- ``stop``     -> trigger a stop condition
- ``degrade``  -> switch executor (e.g. semantic takeover unavailable)
- ``escalate`` -> feed the systematic-defect escalation counter (§7.4)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import classification as fc


EXIT_RETRY = "retry"
EXIT_CONTINUE = "continue"
EXIT_HUMAN = "human"
EXIT_STOP = "stop"
EXIT_DEGRADE = "degrade"
EXIT_ESCALATE = "escalate"

VALID_EXITS: frozenset[str] = frozenset(
    {EXIT_RETRY, EXIT_CONTINUE, EXIT_HUMAN, EXIT_STOP, EXIT_DEGRADE, EXIT_ESCALATE}
)


@dataclass(frozen=True, slots=True)
class PlaybookSpec:
    playbook_id: str
    trigger_class: str
    action_steps: tuple[str, ...]
    expected_effect: str
    exit: str
    safety_constraints: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook_id": self.playbook_id,
            "trigger_class": self.trigger_class,
            "action_steps": list(self.action_steps),
            "expected_effect": self.expected_effect,
            "exit": self.exit,
            "safety_constraints": list(self.safety_constraints),
        }


def _spec(trigger_class: str, steps: tuple[str, ...], effect: str, exit_: str,
          safety: tuple[str, ...] = ()) -> PlaybookSpec:
    return PlaybookSpec(
        playbook_id=f"pb_{trigger_class}",
        trigger_class=trigger_class,
        action_steps=steps,
        expected_effect=effect,
        exit=exit_,
        safety_constraints=safety,
    )


PLAYBOOK_TABLE: dict[str, PlaybookSpec] = {
    fc.MENU_NOT_FOUND: _spec(
        fc.MENU_NOT_FOUND,
        ("expand_menu_candidates", "normalize_menu_shell", "enable_semantic_menu_search"),
        "更多菜单候选进入可扫描集合", EXIT_RETRY,
    ),
    fc.MENU_EXPAND_FAILED: _spec(
        fc.MENU_EXPAND_FAILED,
        ("normalize_menu_shell", "switch_expand_strategy_hover_click", "retry_expand"),
        "折叠菜单被展开为可交互状态", EXIT_RETRY,
    ),
    fc.MENU_CLICK_FAILED: _spec(
        fc.MENU_CLICK_FAILED,
        ("scroll_into_viewport", "wait_interactable", "semantic_takeover_click"),
        "菜单点击产生导航跳转", EXIT_RETRY,
    ),
    fc.PAGE_BLANK: _spec(
        fc.PAGE_BLANK,
        ("reenter_page", "wait_page_stable", "return_to_start_and_retry"),
        "主内容区渲染、白屏消除", EXIT_RETRY,
    ),
    fc.PAGE_LOAD_TIMEOUT: _spec(
        fc.PAGE_LOAD_TIMEOUT,
        ("extend_wait", "reload_page", "degrade_to_semantic_confirmation"),
        "页面在延长等待后可交互", EXIT_RETRY,
    ),
    fc.PERMISSION_BLOCKED: _spec(
        fc.PERMISSION_BLOCKED,
        ("record_permission_block", "mark_discovered_but_uncovered", "raise_human_task"),
        "权限阻塞被记录并转人工", EXIT_HUMAN,
        safety=("do_not_bypass_permission",),
    ),
    fc.LOGIN_REQUIRED: _spec(
        fc.LOGIN_REQUIRED,
        ("raise_login_handoff_task", "pause_goal"),
        "登录接管人工任务生成，目标暂停", EXIT_HUMAN,
    ),
    fc.TARGET_DISCOVERED_BUT_UNCOVERED: _spec(
        fc.TARGET_DISCOVERED_BUT_UNCOVERED,
        ("keep_tracking_next_round",),
        "目标继续追踪，不判完成", EXIT_CONTINUE,
    ),
    fc.FEATURE_NOT_IDENTIFIED: _spec(
        fc.FEATURE_NOT_IDENTIFIED,
        ("light_interaction_to_expose_features", "supplement_page_semantic_analysis"),
        "更多真实功能点被暴露", EXIT_RETRY,
    ),
    fc.LOCATOR_UNSTABLE: _spec(
        fc.LOCATOR_UNSTABLE,
        ("switch_locator_strategy", "semantic_fallback_locate", "record_unstable_locator"),
        "定位稳定命中唯一元素", EXIT_RETRY,
    ),
    fc.ACTION_NOT_OBSERVED: _spec(
        fc.ACTION_NOT_OBSERVED,
        ("capture_step_screenshot", "capture_network_events", "retry_and_observe"),
        "动作产生可观测反馈", EXIT_RETRY,
    ),
    fc.ASSERTION_FAILED: _spec(
        fc.ASSERTION_FAILED,
        ("record_failure_evidence", "attribute_root_cause", "raise_human_confirmation"),
        "失败证据被记录，等待人工确认是否缺陷", EXIT_HUMAN,
    ),
    fc.MISSING_PREREQUISITE_DATA: _spec(
        fc.MISSING_PREREQUISITE_DATA,
        ("raise_prerequisite_data_task", "skip_side_effect_execution"),
        "前置数据人工任务生成，副作用执行被跳过", EXIT_HUMAN,
    ),
    fc.BLOCKED_BY_SAFETY_POLICY: _spec(
        fc.BLOCKED_BY_SAFETY_POLICY,
        ("stop_at_entry_confirmation", "raise_high_risk_authorization_task"),
        "停在入口确认，高风险授权转人工", EXIT_HUMAN,
        safety=("no_real_submit_without_authorization",),
    ),
    fc.BROWSER_USE_UNAVAILABLE: _spec(
        fc.BROWSER_USE_UNAVAILABLE,
        ("degrade_to_playwright", "mark_executor_unavailable_if_required"),
        "降级 Playwright 或标记执行器不可用", EXIT_DEGRADE,
    ),
    fc.EVIDENCE_INCOMPLETE: _spec(
        fc.EVIDENCE_INCOMPLETE,
        ("force_action_log", "force_step_screenshot"),
        "动作日志与步骤级截图补齐", EXIT_RETRY,
    ),
    fc.NO_PROGRESS_REPEATED: _spec(
        fc.NO_PROGRESS_REPEATED,
        ("trigger_stop_condition", "route_to_review_or_escalation"),
        "循环停止，进入复盘或升级", EXIT_STOP,
    ),
    fc.UNKNOWN: _spec(
        fc.UNKNOWN,
        ("temporary_fallback", "aggregate_recurrence", "escalate_if_repeated"),
        "临时兜底，复现即升级评审", EXIT_ESCALATE,
    ),
}

# The set of classes whose playbook exit routes to a human task. This is the
# single source of truth consumed by predicates.human_required_classes so the
# two never drift.
HUMAN_REQUIRED_CLASSES: frozenset[str] = frozenset(
    trigger for trigger, spec in PLAYBOOK_TABLE.items() if spec.exit == EXIT_HUMAN
)


def select_playbook(failure_class: str) -> PlaybookSpec:
    """Return the fixed playbook for a failure class (falls back to unknown)."""

    return PLAYBOOK_TABLE.get(failure_class, PLAYBOOK_TABLE[fc.UNKNOWN])


def assert_table_complete() -> None:
    """Guard: every fixed failure class must have a playbook with a valid exit.

    Called from tests; cheap enough to also run at import if desired.
    """

    missing = fc.FIXED_FAILURE_CLASSES - set(PLAYBOOK_TABLE)
    if missing:
        raise AssertionError(f"playbook table missing classes: {sorted(missing)}")
    bad_exits = {
        trigger: spec.exit
        for trigger, spec in PLAYBOOK_TABLE.items()
        if spec.exit not in VALID_EXITS
    }
    if bad_exits:
        raise AssertionError(f"playbook table has invalid exits: {bad_exits}")


__all__ = [
    "PlaybookSpec",
    "PLAYBOOK_TABLE",
    "HUMAN_REQUIRED_CLASSES",
    "VALID_EXITS",
    "EXIT_RETRY",
    "EXIT_CONTINUE",
    "EXIT_HUMAN",
    "EXIT_STOP",
    "EXIT_DEGRADE",
    "EXIT_ESCALATE",
    "select_playbook",
    "assert_table_complete",
]
