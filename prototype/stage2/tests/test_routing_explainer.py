from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.reporting.routing_explainer import (
    build_routing_explanation,
    build_routing_section,
)


def test_routing_explainer_summarizes_allowed_browser_use_route_and_policy_allowlist() -> None:
    explanation = build_routing_explanation(
        capability_decision={
            "status": "allowed",
            "mode": "browser_use_chatopenai_structured",
            "profile_name": "Qwen profile",
            "reason": "Capability probe matched the structured Browser Use path.",
            "required_tags": [
                "chat_completion",
                "json_schema_response_format",
                "browser_use_chatopenai_structured",
            ],
            "capability_tags": {
                "chat_completion": True,
                "json_schema_response_format": True,
                "browser_use_chatopenai_structured": True,
            },
        },
        route_decision={
            "status": "allowed",
            "model_name": "Qwen3.6-35B",
            "requested_mode": "browser_use_chatopenai_structured",
            "selected_mode": "browser_use_chatopenai_structured",
            "assigned_role": "controlled_discovery",
            "reason": "Structured discovery stays on the preferred Browser Use path.",
        },
        policy_decision={
            "status": "allowed",
            "action_id": "submit-001",
            "action_name": "Submit record",
            "risk_level": "risky_submit",
            "reason": "risky_submit action was explicitly resolved by project policy allowlist.",
            "policy_source": "run_policy.whitelist",
            "matched_allowlist": True,
            "requires_allowlist": True,
        },
    )

    assert explanation.status == "allowed"
    assert explanation.browser_use_structured_available is True
    assert explanation.browser_use_wrapper == "browser_use_chatopenai_structured"
    assert explanation.assigned_role == "controlled_discovery"
    assert explanation.policy_status == "allowed"
    assert explanation.matched_allowlist is True
    assert explanation.headline == "Browser Use structured path available via browser_use_chatopenai_structured"
    assert "project policy allowlist" in explanation.summary

    section = build_routing_section(explanation)
    assert section.title == "Routing Explanation"
    assert section.extra["browser_use_structured_available"] is True
    assert any(fact.label == "routing_status" and fact.value == "allowed" for fact in section.facts)
    assert any(item.item_id == "submit-001" and item.status == "allowed" for item in section.items)


def test_routing_explainer_marks_policy_blocked_even_when_route_is_available() -> None:
    explanation = build_routing_explanation(
        capability_decision={
            "status": "allowed",
            "mode": "stage2_run_sample",
            "reason": "Basic chat capability is available for deterministic execution.",
            "required_tags": ["chat_completion"],
            "capability_tags": {"chat_completion": True},
        },
        route_decision={
            "status": "allowed",
            "model_name": "AI-tester",
            "requested_mode": "stage2_run_sample",
            "selected_mode": "stage2_run_sample",
            "assigned_role": "verification",
            "reason": "Playwright-only verification remains available.",
        },
        policy_decision={
            "status": "blocked",
            "action_name": "Submit filing",
            "risk_level": "risky_submit",
            "reason": "risky_submit actions are blocked by default unless a project whitelist explicitly allows them.",
            "policy_source": "default",
            "matched_allowlist": False,
            "requires_allowlist": True,
        },
    )

    assert explanation.status == "blocked"
    assert explanation.policy_status == "blocked"
    assert explanation.browser_use_structured_available is None
    assert explanation.manual_review_required is False
    assert any("allowlist resolves it" in note for note in explanation.notes)

    section = build_routing_section(explanation)
    assert section.extra["routing_status"] == "blocked"
    assert any(fact.label == "policy_status" and fact.value == "blocked" for fact in section.facts)
    assert any(item.name == "Submit filing" and item.status == "blocked" for item in section.items)


def test_routing_explainer_marks_manual_review_for_risky_submit() -> None:
    explanation = build_routing_explanation(
        capability_decision={
            "status": "allowed",
            "mode": "browser_use_chatdeepseek_structured",
            "reason": "DeepSeek wrapper capability was recorded in the probe.",
            "required_tags": ["chat_completion", "browser_use_chatdeepseek_structured"],
            "capability_tags": {
                "chat_completion": True,
                "browser_use_chatdeepseek_structured": True,
            },
        },
        route_decision={
            "status": "allowed",
            "model_name": "DeepSeek-v4-flash",
            "requested_mode": "browser_use_chatdeepseek_structured",
            "selected_mode": "browser_use_chatdeepseek_structured",
            "assigned_role": "controlled_discovery",
            "reason": "The DeepSeek wrapper remains usable for controlled exploration.",
        },
        policy_decision={
            "status": "needs_review",
            "action_name": "Submit filing",
            "risk_level": "risky_submit",
            "reason": "risky_submit action is allowlisted but still requires manual review.",
            "policy_source": "run_policy.whitelist",
            "matched_allowlist": True,
            "requires_allowlist": True,
        },
    )

    assert explanation.status == "needs_review"
    assert explanation.manual_review_required is True
    assert explanation.browser_use_structured_available is True
    assert explanation.browser_use_wrapper == "browser_use_chatdeepseek_structured"
    assert explanation.headline == "Routing requires manual review"
    assert any("Manual review is required" in note for note in explanation.notes)

    section = build_routing_section(explanation, title="Routing Handoff")
    assert section.title == "Routing Handoff"
    assert section.extra["manual_review_required"] is True
    assert any(item.name == "Submit filing" and item.status == "needs_review" for item in section.items)


def test_routing_explainer_describes_downgraded_route_when_browser_use_structured_is_unavailable() -> None:
    explanation = build_routing_explanation(
        capability_decision={
            "status": "blocked",
            "mode": "browser_use_chatopenai_structured",
            "reason": "Capability probe does not satisfy the structured Browser Use path.",
            "required_tags": [
                "chat_completion",
                "json_schema_response_format",
                "browser_use_chatopenai_structured",
            ],
            "missing_tags": ["json_schema_response_format", "browser_use_chatopenai_structured"],
            "capability_tags": {
                "chat_completion": True,
                "json_schema_response_format": False,
                "browser_use_chatopenai_structured": False,
            },
        },
        route_decision={
            "status": "degraded",
            "model_name": "AI-tester",
            "requested_mode": "browser_use_chatopenai_structured",
            "selected_mode": "stage2_run_sample",
            "assigned_role": "verification",
            "fallback_mode": "stage2_run_sample",
            "fallback_role": "verification",
            "reason": "Routing downgraded to deterministic verification because structured Browser Use is unavailable.",
        },
    )

    assert explanation.status == "degraded"
    assert explanation.degraded is True
    assert explanation.browser_use_structured_available is False
    assert explanation.browser_use_wrapper == "browser_use_chatopenai_structured"
    assert explanation.missing_tags == ["json_schema_response_format", "browser_use_chatopenai_structured"]
    assert explanation.headline == "Model downgraded from browser_use_chatopenai_structured to stage2_run_sample"
    assert "unavailable for browser_use_chatopenai_structured" in explanation.summary

    section = build_routing_section(explanation)
    assert section.extra["degraded"] is True
    assert any(fact.label == "missing_tags" for fact in section.facts)
    assert any(item.item_id == "capability-routing" and item.status == "degraded" for item in section.items)
