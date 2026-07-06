"""
LLM-driven structured failure attribution for Stage E (P1-1).

When a Stage E execution round produces failures, this module generates
structured ``FailureAdvice`` — a constrained multiple-choice analysis
suitable for local LLMs with small context windows (§5.1–5.3).

Design constraints (from the spec):
- Max 3 yes/no or ranking questions per call, merged into one prompt.
- Forced JSON output via ``response_format: json_object``.
- Concise input: page title + page text excerpt (≤200 chars) + attempted
  strategies + failure class.
- 1 call per goal max; only triggered for ``unknown`` /
  ``feature_not_identified``.
- Async, non-blocking — a 3-second timeout degrades gracefully to
  rule-based advice.
- Output written to ``round_analysis.json``'s ``llm_advice`` field.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

# ── dataclass ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class FailureAdvice:
    """Structured root-cause analysis for a single failure event."""

    primary_cause: str
    """ One of: locator_instability | page_structure_changed | timing_issue |
        element_absent | application_error | unknown """

    confidence: str
    """ high | medium | low """

    suggested_action: str
    """ retry_l2 | retry_l3 | retry_l4 | human_review | ignore """

    explanation: str = ""
    source: str = "rule_based"
    model: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_cause": self.primary_cause,
            "confidence": self.confidence,
            "suggested_action": self.suggested_action,
            "explanation": self.explanation,
            "source": self.source,
            "model": self.model,
            "notes": list(self.notes),
        }


# ── failure class → rule-based cause mapping ───────────────────────────

_RULE_CAUSE: dict[str, FailureAdvice] = {
    "locator_unstable": FailureAdvice(
        primary_cause="locator_instability",
        confidence="high",
        suggested_action="retry_l2",
        explanation="All L2 candidates failed; the page DOM structure may have changed or the original locators are stale.",
    ),
    "assertion_failed": FailureAdvice(
        primary_cause="application_error",
        confidence="medium",
        suggested_action="human_review",
        explanation="The action completed but the expected result was not observed — possible application-level regression or incorrect expected value.",
    ),
    "page_load_timeout": FailureAdvice(
        primary_cause="timing_issue",
        confidence="medium",
        suggested_action="retry_l3",
        explanation="The page did not load within the timeout window; network latency or server-side slowdown is likely.",
    ),
    "evidence_incomplete": FailureAdvice(
        primary_cause="unknown",
        confidence="low",
        suggested_action="human_review",
        explanation="Not enough evidence was captured to classify the root cause; manual inspection recommended.",
    ),
    "blocked_by_safety_policy": FailureAdvice(
        primary_cause="unknown",
        confidence="high",
        suggested_action="human_review",
        explanation="Execution was blocked by safety policy — no automatic retry is appropriate; human authorization required.",
    ),
    "login_required": FailureAdvice(
        primary_cause="application_error",
        confidence="high",
        suggested_action="human_review",
        explanation="The target page requires authentication that cannot be resolved automatically.",
    ),
    "all_locator_layers_failed": FailureAdvice(
        primary_cause="locator_instability",
        confidence="high",
        suggested_action="human_review",
        explanation="L2, L3, and L4 all failed to locate the target element; the page may have undergone a major structural change.",
    ),
}

_MISSING_DATA_CAUSE = FailureAdvice(
    primary_cause="element_absent",
    confidence="medium",
    suggested_action="human_review",
    explanation="The target element was not found on the page; it may have been removed or renamed.",
)


def _rule_based_advice(
    failure_class: str | None,
    attempted_strategies: list[str] | None = None,
) -> FailureAdvice:
    strategies = attempted_strategies or []
    if failure_class and failure_class in _RULE_CAUSE:
        advice = _RULE_CAUSE[failure_class]
        if strategies:
            advice.notes.append(f"Strategies attempted before failure: {', '.join(strategies[:5])}.")
        return advice
    return _MISSING_DATA_CAUSE


# ── LLM prompt builder ────────────────────────────────────────────────


def _build_advice_prompt(
    failure_class: str | None,
    attempted_strategies: list[str] | None,
    element_text: str | None,
    page_snapshot: str | None,
) -> str:
    strategies = ", ".join(attempted_strategies or []) or "none"
    text = (element_text or "unknown")[:80]
    snapshot = (page_snapshot or "")[:200]
    return (
        "你是一个 Web UI 自动化测试的故障归因助手。请根据以下信息，从预设选项中选择最可能的根因。\n\n"
        f"失败分类: {failure_class or 'unknown'}\n"
        f"已尝试的定位策略: {strategies}\n"
        f"目标元素描述: {text}\n"
        f"页面摘要: {snapshot}\n\n"
        "请在以下三个问题中选择答案，并以 JSON 格式返回。\n\n"
        "Q1: 最可能的根因是什么？\n"
        "  选项: locator_instability, page_structure_changed, timing_issue, element_absent, application_error, unknown\n"
        "Q2: 你对这个判断有多大把握？\n"
        "  选项: high, medium, low\n"
        "Q3: 建议下一步动作？\n"
        "  选项: retry_l2, retry_l3, retry_l4, human_review, ignore\n\n"
        '返回格式: {"primary_cause": "...", "confidence": "...", "suggested_action": "...", "explanation": "简要分析，≤100字"}\n'
    )


# ── main entrypoint ─────────────────────────────────────────────────────


async def analyze_failure(
    failure_class: str | None = None,
    *,
    attempted_strategies: list[str] | None = None,
    element_text: str | None = None,
    page_snapshot: str | None = None,
    profile: dict[str, Any] | None = None,
    timeout_s: float = 3.0,
) -> FailureAdvice:
    """Analyze a Stage E failure and return structured advice.

    When *profile* is ``None`` or the LLM is unavailable, returns rule-based
    advice immediately.  With a valid profile, attempts a single LLM call
    with a 3-second timeout; on timeout or any error, falls back to
    rule-based advice (§5.3: timeout → "模型不可用"，降级为规则判断).
    """
    if profile is None:
        return _rule_based_advice(failure_class, attempted_strategies)

    try:
        result = await asyncio.wait_for(
            _call_llm(profile, failure_class, attempted_strategies, element_text, page_snapshot),
            timeout=timeout_s,
        )
        return result
    except (asyncio.TimeoutError, Exception):
        return _rule_based_advice(failure_class, attempted_strategies)


async def _call_llm(
    profile: dict[str, Any],
    failure_class: str | None,
    attempted_strategies: list[str] | None,
    element_text: str | None,
    page_snapshot: str | None,
) -> FailureAdvice:
    """Send a structured prompt to the LLM and parse the JSON response."""
    prompt = _build_advice_prompt(failure_class, attempted_strategies, element_text, page_snapshot)

    model = profile.get("model") or "unknown"
    api_key = profile.get("apiKey") or profile.get("api_key") or "EMPTY"
    base_url = profile.get("baseUrl") or profile.get("base_url")

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        # Fall back to rule-based if langchain not installed
        return _rule_based_advice(failure_class, attempted_strategies)

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
    resp = await llm.ainvoke(prompt)

    return _parse_llm_response(str(resp.content if hasattr(resp, "content") else resp), model)


def _parse_llm_response(text: str, model: str) -> FailureAdvice:
    """Parse a JSON response string into FailureAdvice, with fallback."""
    try:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        data = json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError):
        # Extract JSON from the response via regex
        import re
        m = re.search(r'\{[^{}]*"primary_cause"[^{}]*\}', text)
        if not m:
            return FailureAdvice(
                primary_cause="unknown",
                confidence="low",
                suggested_action="human_review",
                explanation=f"LLM response was not valid JSON: {text[:100]}",
                source="llm_failed",
                model=model,
            )
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return FailureAdvice(
                primary_cause="unknown",
                confidence="low",
                suggested_action="human_review",
                explanation=f"LLM response could not be parsed: {text[:100]}",
                source="llm_failed",
                model=model,
            )

    valid_causes = {"locator_instability", "page_structure_changed", "timing_issue", "element_absent", "application_error", "unknown"}
    valid_conf = {"high", "medium", "low"}
    valid_actions = {"retry_l2", "retry_l3", "retry_l4", "human_review", "ignore"}

    return FailureAdvice(
        primary_cause=data.get("primary_cause") if data.get("primary_cause") in valid_causes else "unknown",
        confidence=data.get("confidence") if data.get("confidence") in valid_conf else "low",
        suggested_action=data.get("suggested_action") if data.get("suggested_action") in valid_actions else "human_review",
        explanation=str(data.get("explanation", ""))[:200],
        source="llm",
        model=model,
    )


# ── batch analysis for a full round ─────────────────────────────────────


async def analyze_round_failures(
    engine: Any,
    adapter: Any,
    *,
    profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Analyze all failed goals in the current round.

    Returns a list of dicts suitable for ``round_analysis.json``'s
    ``llm_advice`` field.
    """
    goals = [
        g for g in engine.goals.values()
        if getattr(g, "origin", None) and str(g.origin).startswith("feature_execution::")
    ]
    from ..goal_loop.models import STATUS_SUCCEEDED, TERMINAL_STATUSES

    failed = [g for g in goals if g.status in TERMINAL_STATUSES and g.status != STATUS_SUCCEEDED]
    advice_list: list[dict[str, Any]] = []

    for goal in failed:
        context = adapter.get_execution_context(goal.goal_id)
        test_case = context.get("test_case", {}) if context else {}
        last_attempt = engine.last_attempt_for(goal.goal_id)
        failure_class = last_attempt.failure_class if last_attempt else None

        element_text = None
        if isinstance(test_case, dict):
            meta = test_case.get("metadata", {}) or {}
            element_text = meta.get("element_text") or test_case.get("description")

        advice = await analyze_failure(
            failure_class=failure_class,
            attempted_strategies=_extract_strategies(last_attempt),
            element_text=element_text,
            profile=profile,
        )

        advice_list.append({
            "goal_id": goal.goal_id,
            "failure_class": failure_class,
            "advice": advice.to_dict(),
        })

    return advice_list


def _extract_strategies(attempt: Any) -> list[str]:
    """Extract attempted locator strategies from the last attempt record."""
    strategies: list[str] = []
    if attempt is None:
        return strategies
    actions = getattr(attempt, "actions", []) or []
    for action in actions:
        if isinstance(action, dict):
            result = action.get("result", {}) or {}
            strategy = result.get("winning_strategy")
            if strategy:
                strategies.append(str(strategy))
            layer = result.get("layer")
            if layer:
                strategies.append(str(layer))
    notes = getattr(attempt, "notes", []) or []
    for note in notes:
        if isinstance(note, str) and "attempted" in note.lower():
            strategies.append(note[:80])
    return strategies


__all__ = [
    "FailureAdvice",
    "analyze_failure",
    "analyze_round_failures",
]
