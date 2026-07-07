from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.verification.rule_evaluator import (  # noqa: E402
    evaluate_template_rules,
    TemplateVerificationEvidence,
)
from prototype.stage2.app.verification.template_executor import TemplateStepExecution  # noqa: E402


def _step(status: str = "completed", *, step_id: str = "s1", action: str = "click") -> TemplateStepExecution:
    return TemplateStepExecution(
        step_id=step_id,
        action=action,
        status=status,
        started_at_monotonic=0.0,
        finished_at_monotonic=1.0,
        duration_ms=1000,
        result={"ok": status == "completed"},
    )


def test_rule_evaluator_passes_when_success_text_matches_and_no_failure_matches() -> None:
    template = {
        "success_rules": {
            "ui_texts": ["query-completed"],
        }
    }
    evidence = TemplateVerificationEvidence(
        final_url="file:///demo",
        final_title="Demo",
        body_text="landing-ready query-completed",
    )

    result = evaluate_template_rules(
        template=template,
        evidence=evidence,
        step_executions=[_step()],
    )

    assert result.passed is True
    assert result.status == "passed"
    assert result.executable_success_rule_count == 1
    assert len(result.matched_success_rules) == 1
    assert result.matched_success_rules[0]["rule_source"] == "success_rules.ui_texts"


def test_rule_evaluator_fails_when_failure_keyword_matches_even_if_success_matches() -> None:
    template = {
        "success_rules": {"ui_texts": ["query-completed"]},
        "failure_rules": {"message_keywords": ["操作失败"]},
    }
    evidence = TemplateVerificationEvidence(
        final_url="file:///demo",
        final_title="Demo",
        body_text="query-completed",
        messages=["操作失败"],
    )

    result = evaluate_template_rules(
        template=template,
        evidence=evidence,
        step_executions=[_step()],
    )

    assert result.passed is False
    assert result.status == "failed"
    assert len(result.matched_success_rules) == 1
    assert len(result.matched_failure_rules) == 1


def test_rule_evaluator_matches_network_rule_against_response_event() -> None:
    template = {
        "success_rules": {
            "network_rules": [
                {
                    "method": "POST",
                    "path_contains": "/prod-api/zwsy/registration/apply/dept",
                    "response_contains": '"code":200',
                }
            ]
        }
    }
    evidence = TemplateVerificationEvidence(
        final_url="https://example.test",
        final_title="Example",
        body_text="",
        network_events=[
            {
                "type": "response",
                "method": "POST",
                "url": "https://example.test/prod-api/zwsy/registration/apply/dept",
                "status": 200,
                "body": '{"code":200,"msg":"操作成功"}',
                "post_data": "",
            }
        ],
    )

    result = evaluate_template_rules(
        template=template,
        evidence=evidence,
        step_executions=[_step()],
    )

    assert result.passed is True
    assert len(result.matched_success_rules) == 1
    assert result.matched_success_rules[0]["rule_type"] == "network_rule"


def test_rule_evaluator_allows_completed_steps_when_template_declares_no_executable_success_rules() -> None:
    template = {
        "success_rules": {
            "notes": ["assertions are already encoded as steps"],
        }
    }
    evidence = TemplateVerificationEvidence(
        final_url="file:///demo",
        final_title="Demo",
        body_text="",
    )

    result = evaluate_template_rules(
        template=template,
        evidence=evidence,
        step_executions=[_step()],
    )

    assert result.passed is True
    assert result.executable_success_rule_count == 0


def test_rule_evaluator_fails_when_template_steps_failed_before_rules() -> None:
    template = {
        "success_rules": {
            "ui_texts": ["landing-ready"],
        }
    }
    evidence = TemplateVerificationEvidence(
        final_url="file:///demo",
        final_title="Demo",
        body_text="landing-ready",
    )

    result = evaluate_template_rules(
        template=template,
        evidence=evidence,
        step_executions=[_step(status="failed")],
    )

    assert result.passed is False
    assert result.step_success is False
