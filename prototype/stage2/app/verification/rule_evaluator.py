from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .template_executor import TemplateStepExecution


@dataclass(frozen=True)
class TemplateVerificationEvidence:
    final_url: str
    final_title: str | None
    body_text: str
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    network_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def combined_text(self) -> str:
        parts = [self.body_text, *self.messages, *self.errors]
        return " ".join(part for part in parts if part).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_url": self.final_url,
            "final_title": self.final_title,
            "body_text": self.body_text,
            "messages": self.messages,
            "errors": self.errors,
            "network_event_count": len(self.network_events),
            "network_events": self.network_events,
        }


@dataclass(frozen=True)
class TemplateRuleEvaluationResult:
    status: str
    passed: bool
    step_success: bool
    executable_success_rule_count: int
    executable_failure_rule_count: int
    matched_success_rules: list[dict[str, Any]] = field(default_factory=list)
    unmatched_success_rules: list[dict[str, Any]] = field(default_factory=list)
    matched_failure_rules: list[dict[str, Any]] = field(default_factory=list)
    unmatched_failure_rules: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "step_success": self.step_success,
            "success_rule_mode": "any_match",
            "executable_success_rule_count": self.executable_success_rule_count,
            "executable_failure_rule_count": self.executable_failure_rule_count,
            "matched_success_rules": self.matched_success_rules,
            "unmatched_success_rules": self.unmatched_success_rules,
            "matched_failure_rules": self.matched_failure_rules,
            "unmatched_failure_rules": self.unmatched_failure_rules,
            "summary": self.summary,
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class SharedTemplateVerificationResult:
    template_name: str
    step_executions: list[TemplateStepExecution]
    final_url: str
    final_title: str | None
    evidence: TemplateVerificationEvidence
    rule_evaluation: TemplateRuleEvaluationResult

    @property
    def step_success(self) -> bool:
        return all(item.status == "completed" for item in self.step_executions)

    @property
    def success(self) -> bool:
        return self.step_success and self.rule_evaluation.passed

    @property
    def status(self) -> str:
        return "passed" if self.success else "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_name": self.template_name,
            "success": self.success,
            "status": self.status,
            "step_count": len(self.step_executions),
            "step_success": self.step_success,
            "steps": [item.to_attempt_action() for item in self.step_executions],
            "final_url": self.final_url,
            "final_title": self.final_title,
            "verification_result": {
                "status": self.status,
                "step_success": self.step_success,
                "rule_evaluation": self.rule_evaluation.to_dict(),
                "evidence": self.evidence.to_dict(),
            },
        }


def evaluate_template_rules(
    *,
    template: dict[str, Any],
    evidence: TemplateVerificationEvidence,
    step_executions: list[TemplateStepExecution],
) -> TemplateRuleEvaluationResult:
    step_success = all(item.status == "completed" for item in step_executions)
    success_rules = _normalize_mapping(template.get("success_rules"))
    failure_rules = _normalize_mapping(template.get("failure_rules"))

    matched_success_rules: list[dict[str, Any]] = []
    unmatched_success_rules: list[dict[str, Any]] = []
    matched_failure_rules: list[dict[str, Any]] = []
    unmatched_failure_rules: list[dict[str, Any]] = []
    warnings: list[str] = []

    combined_text = evidence.combined_text

    success_ui_rules = _normalize_string_list(success_rules.get("ui_texts"))
    matched, unmatched = _evaluate_text_rules(
        rule_owner="success_rules",
        rule_name="ui_texts",
        expected_values=success_ui_rules,
        haystack=combined_text,
    )
    matched_success_rules.extend(matched)
    unmatched_success_rules.extend(unmatched)

    success_keyword_rules = _normalize_string_list(success_rules.get("message_keywords"))
    matched, unmatched = _evaluate_text_rules(
        rule_owner="success_rules",
        rule_name="message_keywords",
        expected_values=success_keyword_rules,
        haystack=combined_text,
    )
    matched_success_rules.extend(matched)
    unmatched_success_rules.extend(unmatched)

    network_rules = _normalize_rule_list(success_rules.get("network_rules"))
    matched, unmatched = _evaluate_network_rules(network_rules, evidence.network_events)
    matched_success_rules.extend(matched)
    unmatched_success_rules.extend(unmatched)

    failure_keywords = _normalize_string_list(failure_rules.get("message_keywords"))
    matched, unmatched = _evaluate_text_rules(
        rule_owner="failure_rules",
        rule_name="message_keywords",
        expected_values=failure_keywords,
        haystack=combined_text,
    )
    matched_failure_rules.extend(matched)
    unmatched_failure_rules.extend(unmatched)

    failure_ui_rules = _normalize_string_list(failure_rules.get("ui_texts"))
    matched, unmatched = _evaluate_text_rules(
        rule_owner="failure_rules",
        rule_name="ui_texts",
        expected_values=failure_ui_rules,
        haystack=combined_text,
    )
    matched_failure_rules.extend(matched)
    unmatched_failure_rules.extend(unmatched)

    executable_success_rule_count = len(success_ui_rules) + len(success_keyword_rules) + len(network_rules)
    executable_failure_rule_count = len(failure_keywords) + len(failure_ui_rules)

    success_rule_hit = bool(matched_success_rules)
    failure_rule_hit = bool(matched_failure_rules)

    if not step_success:
        summary = "One or more template steps failed before the shared rule verdict could pass."
    elif failure_rule_hit:
        summary = "Failure rules matched the collected evidence."
    elif executable_success_rule_count == 0:
        summary = "No executable success rules were declared; completed step executions are treated as a pass."
    elif success_rule_hit:
        summary = "At least one declared success rule matched and no failure rule matched."
    else:
        summary = "Template steps completed, but no declared success rule matched the collected evidence."

    unsupported_success_keys = sorted(
        key for key in success_rules.keys() if key not in {"ui_texts", "message_keywords", "network_rules", "notes"}
    )
    unsupported_failure_keys = sorted(
        key for key in failure_rules.keys() if key not in {"ui_texts", "message_keywords", "notes"}
    )
    if unsupported_success_keys:
        warnings.append(f"Unsupported success rule keys were ignored: {', '.join(unsupported_success_keys)}")
    if unsupported_failure_keys:
        warnings.append(f"Unsupported failure rule keys were ignored: {', '.join(unsupported_failure_keys)}")

    passed = step_success and not failure_rule_hit and (
        executable_success_rule_count == 0 or success_rule_hit
    )

    return TemplateRuleEvaluationResult(
        status="passed" if passed else "failed",
        passed=passed,
        step_success=step_success,
        executable_success_rule_count=executable_success_rule_count,
        executable_failure_rule_count=executable_failure_rule_count,
        matched_success_rules=matched_success_rules,
        unmatched_success_rules=unmatched_success_rules,
        matched_failure_rules=matched_failure_rules,
        unmatched_failure_rules=unmatched_failure_rules,
        summary=summary,
        warnings=warnings,
    )


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text.strip() for text in (str(item) for item in value) if text.strip()]
    return []


def _normalize_rule_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(dict(item))
    return result


def _evaluate_text_rules(
    *,
    rule_owner: str,
    rule_name: str,
    expected_values: list[str],
    haystack: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for index, expected in enumerate(expected_values, start=1):
        payload = {
            "rule_source": f"{rule_owner}.{rule_name}",
            "rule_type": rule_name[:-1] if rule_name.endswith("s") else rule_name,
            "rule_index": index,
            "expected": expected,
        }
        if expected in haystack:
            matched.append(
                {
                    **payload,
                    "matched": True,
                    "evidence": {
                        "matched_text": expected,
                    },
                }
            )
            continue
        unmatched.append({**payload, "matched": False})
    return matched, unmatched


def _evaluate_network_rules(
    rules: list[dict[str, Any]],
    network_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    response_events = [item for item in network_events if item.get("type") == "response"]
    for index, rule in enumerate(rules, start=1):
        match_event = next((item for item in response_events if _network_rule_matches(rule, item)), None)
        payload = {
            "rule_source": "success_rules.network_rules",
            "rule_type": "network_rule",
            "rule_index": index,
            "expected": rule,
        }
        if match_event is not None:
            matched.append(
                {
                    **payload,
                    "matched": True,
                    "evidence": {
                        "matched_event": {
                            "method": match_event.get("method"),
                            "url": match_event.get("url"),
                            "status": match_event.get("status"),
                            "body_snippet": match_event.get("body"),
                        }
                    },
                }
            )
            continue
        unmatched.append({**payload, "matched": False})
    return matched, unmatched


def _network_rule_matches(rule: dict[str, Any], event: dict[str, Any]) -> bool:
    method = str(rule.get("method") or "").strip().upper()
    if method and str(event.get("method") or "").strip().upper() != method:
        return False

    path_contains = str(rule.get("path_contains") or "").strip()
    if path_contains and path_contains not in str(event.get("url") or ""):
        return False

    url_contains = str(rule.get("url_contains") or "").strip()
    if url_contains and url_contains not in str(event.get("url") or ""):
        return False

    response_contains = str(rule.get("response_contains") or "").strip()
    if response_contains and response_contains not in str(event.get("body") or ""):
        return False

    request_contains = str(rule.get("request_contains") or "").strip()
    if request_contains and request_contains not in str(event.get("post_data") or ""):
        return False

    status_code = rule.get("status") if "status" in rule else rule.get("status_code")
    if status_code is not None:
        try:
            expected_status = int(status_code)
        except (TypeError, ValueError):
            expected_status = None
        if expected_status is not None and int(event.get("status") or 0) != expected_status:
            return False

    return True
