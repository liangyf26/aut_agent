from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

VALIDATION_MODE_LOCAL = "local"
VALIDATION_MODE_CONNECTED = "connected_cdp"

VALIDATION_STATUS_PASSED = "passed"
VALIDATION_STATUS_FAILED = "failed"
VALIDATION_STATUS_SKIPPED = "skipped"
VALIDATION_STATUS_PARTIAL = "partial"


@dataclass(frozen=True)
class ValidationMatrixTarget:
    target_id: str
    template_name: str
    family: str
    system_name: str
    mode: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "template_name": self.template_name,
            "family": self.family,
            "system_name": self.system_name,
            "mode": self.mode,
            "description": self.description,
        }


@dataclass(frozen=True)
class ValidationMatrixResult:
    target: ValidationMatrixTarget
    status: str
    success: bool
    run_dir: str = ""
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = self.target.to_dict()
        data.update(
            {
                "status": self.status,
                "success": self.success,
                "run_dir": self.run_dir,
                "reason": self.reason,
            }
        )
        if self.payload:
            data["payload"] = self.payload
        return data


def build_default_g4_validation_targets() -> list[ValidationMatrixTarget]:
    return [
        ValidationMatrixTarget(
            target_id="lab_navigation_local",
            template_name="lab_navigation",
            family="lab",
            system_name="template_lab",
            mode=VALIDATION_MODE_LOCAL,
            description="Local lab navigation smoke through the shared generic template executor.",
        ),
        ValidationMatrixTarget(
            target_id="lab_query_filter_local",
            template_name="lab_query_filter",
            family="lab",
            system_name="template_lab",
            mode=VALIDATION_MODE_LOCAL,
            description="Local lab query/filter workflow through locator-hint-backed generic actions.",
        ),
        ValidationMatrixTarget(
            target_id="lab_create_add_local",
            template_name="lab_create_add",
            family="lab",
            system_name="template_lab",
            mode=VALIDATION_MODE_LOCAL,
            description="Local lab create/add workflow through the shared generic verification path.",
        ),
        ValidationMatrixTarget(
            target_id="suyuan_query_reset_connected",
            template_name="suyuan_online_query_reset",
            family="suyuan",
            system_name="suyuan_online_record",
            mode=VALIDATION_MODE_CONNECTED,
            description="Real-site query/reset workflow validated through the same shared generic template executor.",
        ),
        ValidationMatrixTarget(
            target_id="suyuan_detail_view_connected",
            template_name="suyuan_online_detail_view",
            family="suyuan",
            system_name="suyuan_online_record",
            mode=VALIDATION_MODE_CONNECTED,
            description="Real-site detail-view workflow validated through the same shared generic template executor.",
        ),
    ]


def summarize_validation_matrix(results: list[ValidationMatrixResult]) -> dict[str, Any]:
    summary = _build_bucket(results)
    summary["by_mode"] = _group_bucket(results, key_name="mode")
    summary["by_family"] = _group_bucket(results, key_name="family")
    summary["by_system"] = _group_bucket(results, key_name="system_name")
    return summary


def render_validation_matrix_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# G4 Cross-System Validation Matrix",
        "",
        f"- generated_at: {payload.get('generated_at', '')}",
        f"- status: {summary.get('status', '')}",
        f"- target_count: {summary.get('target_count', 0)}",
        f"- executed_count: {summary.get('executed_count', 0)}",
        f"- passed_count: {summary.get('passed_count', 0)}",
        f"- failed_count: {summary.get('failed_count', 0)}",
        f"- skipped_count: {summary.get('skipped_count', 0)}",
        "",
        "| target | template | family | system | mode | status | reason |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload.get("results", []):
        lines.append(
            "| {target_id} | {template_name} | {family} | {system_name} | {mode} | {status} | {reason} |".format(
                target_id=item.get("target_id", ""),
                template_name=item.get("template_name", ""),
                family=item.get("family", ""),
                system_name=item.get("system_name", ""),
                mode=item.get("mode", ""),
                status=item.get("status", ""),
                reason=str(item.get("reason", "")).replace("\n", " "),
            )
        )
    return "\n".join(lines) + "\n"


def build_validation_matrix_payload(
    *,
    goal: str,
    results: list[ValidationMatrixResult],
) -> dict[str, Any]:
    return {
        "goal": goal,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summarize_validation_matrix(results),
        "results": [item.to_dict() for item in results],
    }


def _group_bucket(results: list[ValidationMatrixResult], *, key_name: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ValidationMatrixResult]] = {}
    for item in results:
        key = str(getattr(item.target, key_name))
        grouped.setdefault(key, []).append(item)
    return {key: _build_bucket(items) for key, items in grouped.items()}


def _build_bucket(results: list[ValidationMatrixResult]) -> dict[str, Any]:
    passed_count = sum(1 for item in results if item.status == VALIDATION_STATUS_PASSED)
    failed_count = sum(1 for item in results if item.status == VALIDATION_STATUS_FAILED)
    skipped_count = sum(1 for item in results if item.status == VALIDATION_STATUS_SKIPPED)
    target_count = len(results)
    executed_count = target_count - skipped_count
    if failed_count > 0:
        status = VALIDATION_STATUS_FAILED
    elif passed_count > 0 and skipped_count > 0:
        status = VALIDATION_STATUS_PARTIAL
    elif passed_count == target_count and target_count > 0:
        status = VALIDATION_STATUS_PASSED
    elif skipped_count == target_count and target_count > 0:
        status = VALIDATION_STATUS_SKIPPED
    else:
        status = VALIDATION_STATUS_FAILED if target_count > 0 else VALIDATION_STATUS_SKIPPED
    return {
        "status": status,
        "target_count": target_count,
        "executed_count": executed_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "success": failed_count == 0 and executed_count > 0,
        "target_ids": [item.target.target_id for item in results],
    }
