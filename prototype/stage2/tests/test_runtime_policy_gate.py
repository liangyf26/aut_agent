from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime import (
    POLICY_ALLOWED,
    POLICY_BLOCKED,
    POLICY_NEEDS_REVIEW,
    RISK_FORBIDDEN_MUTATION,
    RISK_RISKY_SUBMIT,
    RISK_SAFE_INTERACT,
    RISK_SAFE_READ,
    build_policy_gate_config,
    evaluate_action_policy,
)


def test_safe_levels_are_allowed_by_default() -> None:
    read_decision = evaluate_action_policy(
        {"action_id": "open_dashboard"},
        RISK_SAFE_READ,
    )
    interact_decision = evaluate_action_policy(
        {"action_id": "fill_search_form"},
        RISK_SAFE_INTERACT,
    )

    assert read_decision.status == POLICY_ALLOWED
    assert read_decision.reason_code == "safe_read_default"
    assert interact_decision.status == POLICY_ALLOWED
    assert interact_decision.reason_code == "safe_interact_default"


def test_risky_submit_is_blocked_without_project_allowlist() -> None:
    decision = evaluate_action_policy(
        {
            "action_id": "submit_filing_dialog",
            "template_name": "suyuan_online_apply",
        },
        RISK_RISKY_SUBMIT,
    )

    assert decision.status == POLICY_BLOCKED
    assert decision.requires_allowlist is True
    assert decision.reason_code == "risky_submit_unlisted_blocked"
    assert decision.matched_allowlist is False


def test_risky_submit_can_be_allowed_from_payload_whitelist() -> None:
    payload = {
        "run_policy": {
            "whitelist": [
                {
                    "action_id": "submit_filing_dialog",
                    "template_name": "suyuan_online_apply",
                    "risk_level": "risky_submit",
                    "note": "sandbox project allows final submit probe",
                }
            ]
        }
    }

    decision = evaluate_action_policy(
        {
            "action_id": "submit_filing_dialog",
            "template_name": "suyuan_online_apply",
        },
        RISK_RISKY_SUBMIT,
        payload=payload,
    )

    assert decision.status == POLICY_ALLOWED
    assert decision.matched_allowlist is True
    assert decision.policy_source == "payload.run_policy.whitelist"
    assert decision.reason_code == "risky_submit_allowed"
    assert "sandbox project allows final submit probe" in decision.notes


def test_risky_submit_can_fall_back_to_manual_review_mode() -> None:
    config = {
        "run_policy": {
            "require_review_for_unlisted_risky_submit": True,
        }
    }

    decision = evaluate_action_policy(
        {"action_id": "submit_create_form"},
        RISK_RISKY_SUBMIT,
        config=config,
    )

    assert decision.status == POLICY_NEEDS_REVIEW
    assert decision.reason_code == "risky_submit_unlisted_review"
    assert decision.requires_allowlist is True


def test_forbidden_mutation_stays_blocked_even_with_allowlist_entry() -> None:
    payload = {
        "policy": {
            "allowlist": [
                {
                    "action_id": "delete_record",
                    "risk_level": "forbidden_mutation",
                    "note": "should never be auto-run",
                }
            ]
        }
    }

    decision = evaluate_action_policy(
        {"action_id": "delete_record"},
        RISK_FORBIDDEN_MUTATION,
        payload=payload,
    )

    assert decision.status == POLICY_BLOCKED
    assert decision.reason_code == "forbidden_mutation_blocked"
    assert decision.matched_allowlist is True
    assert decision.requires_allowlist is True


def test_policy_gate_config_collects_allow_rules_from_config_and_payload() -> None:
    gate_config = build_policy_gate_config(
        config={
            "run_policy": {
                "allowlist": [
                    {"action_id": "submit_a", "risk_level": "risky_submit"},
                ]
            }
        },
        payload={
            "policy": {
                "whitelist": [
                    {"action_id": "submit_b", "risk_level": "risky_submit"},
                ]
            }
        },
    )

    assert len(gate_config.allow_rules) == 2
    assert gate_config.sources_checked == [
        "config",
        "config.run_policy",
        "payload",
        "payload.policy",
    ]
