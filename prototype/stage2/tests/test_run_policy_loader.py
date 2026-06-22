from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.config.run_policy_loader import (
    RUN_POLICY_SCHEMA_VERSION,
    load_run_policy,
    resolve_run_policy_payload,
)
from prototype.stage2.app.runtime import (
    POLICY_ALLOWED,
    POLICY_BLOCKED,
    POLICY_NEEDS_REVIEW,
    RISK_RISKY_SUBMIT,
    evaluate_action_policy,
)


def test_load_run_policy_returns_safe_defaults_when_file_is_missing(tmp_path: Path) -> None:
    policy_path = tmp_path / "run_policy.json"

    result = load_run_policy(
        policy_path,
        project_name="demo-project",
        template_name="demo-template",
    )

    assert result.load_status == "missing"
    assert result.exists is False
    assert result.risky_submit_default_decision == POLICY_BLOCKED
    assert result.resolved_default_source == "built_in_default"
    assert result.allow_rules == []
    assert result.project_name == "demo-project"
    assert result.template_name == "demo-template"
    payload = result.to_policy_gate_payload()
    assert payload["run_policy"]["risky_submit_default_decision"] == POLICY_BLOCKED
    assert payload["run_policy"]["whitelist"] == []
    assert payload["run_policy"]["source_resolution"]["document_source"] == str(policy_path)


def test_load_run_policy_handles_empty_file_without_crashing(tmp_path: Path) -> None:
    policy_path = tmp_path / "run_policy.json"
    policy_path.write_text("   \n", encoding="utf-8")

    result = load_run_policy(policy_path, project_name="demo-project")

    assert result.load_status == "empty"
    assert result.exists is True
    assert result.risky_submit_default_decision == POLICY_BLOCKED
    assert result.allow_rules == []
    assert any("empty" in note.lower() for note in result.notes)


def test_resolve_run_policy_merges_allowlist_and_whitelist_with_fixed_rule_shape() -> None:
    payload = {
        "run_policy": {
            "projects": {
                "Demo Project": {
                    "allowlist": [
                        {
                            "action_id": "submit_alpha",
                            "note": "project allowlist default rule shape",
                        }
                    ],
                    "whitelist": [
                        {
                            "action_id": "submit_beta",
                            "decision": "review",
                            "enabled": True,
                        }
                    ],
                }
            }
        }
    }

    result = resolve_run_policy_payload(
        payload,
        project_name="demo project",
        policy_path=Path("fixture-run-policy.json"),
        source_name="fixture",
    )

    assert result.load_status == "loaded"
    assert result.schema_version == RUN_POLICY_SCHEMA_VERSION
    assert result.risky_submit_default_decision == POLICY_BLOCKED
    assert result.applied_sources == [
        "fixture.run_policy",
        "fixture.run_policy.projects.demo_project",
    ]

    merged_rules = {rule.action_id: rule for rule in result.allow_rules}
    assert set(merged_rules) == {"submit_alpha", "submit_beta"}
    assert merged_rules["submit_alpha"].risk_level == RISK_RISKY_SUBMIT
    assert merged_rules["submit_alpha"].decision == POLICY_ALLOWED
    assert merged_rules["submit_alpha"].project_name == "Demo Project"
    assert merged_rules["submit_alpha"].source == "fixture.run_policy.projects.demo_project.allowlist"
    assert merged_rules["submit_beta"].decision == POLICY_NEEDS_REVIEW
    assert merged_rules["submit_beta"].source == "fixture.run_policy.projects.demo_project.whitelist"

    policy_gate_payload = result.to_policy_gate_payload()
    serialized_rules = {item["action_id"]: item for item in policy_gate_payload["run_policy"]["whitelist"]}
    assert serialized_rules["submit_alpha"]["risk_level"] == RISK_RISKY_SUBMIT
    assert serialized_rules["submit_beta"]["decision"] == POLICY_NEEDS_REVIEW


def test_template_section_overrides_project_default_and_precedes_project_rules() -> None:
    payload = {
        "run_policy": {
            "risky_submit_default_decision": "blocked",
            "projects": {
                "Demo Project": {
                    "risky_submit_default_decision": "needs_review",
                    "allowlist": [
                        {
                            "action_id": "submit_case",
                            "decision": "blocked",
                            "note": "project-level stop",
                        }
                    ],
                    "templates": {
                        "Online Apply": {
                            "risky_submit_default_decision": "blocked",
                            "whitelist": [
                                {
                                    "action_id": "submit_case",
                                    "decision": "allowed",
                                    "note": "template-level allow",
                                }
                            ],
                        }
                    },
                }
            },
        }
    }

    result = resolve_run_policy_payload(
        payload,
        project_name="demo project",
        template_name="online apply",
        policy_path=Path("fixture-run-policy.json"),
        source_name="fixture",
    )

    assert result.risky_submit_default_decision == POLICY_BLOCKED
    assert result.resolved_default_source == (
        "fixture.run_policy.projects.demo_project.templates.online_apply"
    )
    assert [rule.source for rule in result.allow_rules] == [
        "fixture.run_policy.projects.demo_project.templates.online_apply.whitelist",
        "fixture.run_policy.projects.demo_project.allowlist",
    ]

    payload_for_gate = result.to_policy_gate_payload()
    decision = evaluate_action_policy(
        {
            "action_id": "submit_case",
            "project_name": "Demo Project",
            "template_name": "Online Apply",
        },
        RISK_RISKY_SUBMIT,
        payload=payload_for_gate,
    )

    assert decision.status == POLICY_ALLOWED
    assert decision.reason_code == "risky_submit_allowed"


def test_load_run_policy_accepts_partial_fields_and_template_specific_file_config(tmp_path: Path) -> None:
    policy_path = tmp_path / "run_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 3,
                "projects": {
                    "Project A": {
                        "templates": {
                            "Template X": {
                                "allowlist": [
                                    {
                                        "action_id": "submit_gamma",
                                    }
                                ]
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = load_run_policy(
        policy_path,
        project_name="project a",
        template_name="template x",
    )

    assert result.load_status == "loaded"
    assert result.schema_version == 3
    assert result.risky_submit_default_decision == POLICY_BLOCKED
    assert result.resolved_default_source == "built_in_default"
    assert result.project_name == "Project A"
    assert result.template_name == "Template X"
    assert len(result.allow_rules) == 1
    assert result.allow_rules[0].action_id == "submit_gamma"
    assert result.allow_rules[0].risk_level == RISK_RISKY_SUBMIT
    assert result.allow_rules[0].project_name == "Project A"
    assert result.allow_rules[0].template_name == "Template X"
