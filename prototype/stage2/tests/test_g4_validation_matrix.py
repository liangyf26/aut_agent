from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.verification.validation_matrix import (  # noqa: E402
    VALIDATION_MODE_CONNECTED,
    VALIDATION_MODE_LOCAL,
    VALIDATION_STATUS_FAILED,
    VALIDATION_STATUS_PARTIAL,
    VALIDATION_STATUS_PASSED,
    VALIDATION_STATUS_SKIPPED,
    ValidationMatrixResult,
    ValidationMatrixTarget,
    build_default_g4_validation_targets,
    build_validation_matrix_payload,
    render_validation_matrix_markdown,
    summarize_validation_matrix,
)
from prototype.stage2.main import run_g4_validation_matrix  # noqa: E402


def test_default_g4_validation_targets_keep_cross_family_contract() -> None:
    targets = build_default_g4_validation_targets()

    assert len(targets) == len({item.target_id for item in targets})
    assert any(item.family == "lab" and item.mode == VALIDATION_MODE_LOCAL for item in targets)
    assert any(item.family == "suyuan" and item.mode == VALIDATION_MODE_CONNECTED for item in targets)
    assert any(item.template_name == "suyuan_online_detail_view" for item in targets)


def test_summarize_validation_matrix_groups_by_family_mode_and_system() -> None:
    results = [
        ValidationMatrixResult(
            target=ValidationMatrixTarget(
                target_id="lab_navigation_local",
                template_name="lab_navigation",
                family="lab",
                system_name="template_lab",
                mode=VALIDATION_MODE_LOCAL,
            ),
            status=VALIDATION_STATUS_PASSED,
            success=True,
        ),
        ValidationMatrixResult(
            target=ValidationMatrixTarget(
                target_id="lab_query_filter_local",
                template_name="lab_query_filter",
                family="lab",
                system_name="template_lab",
                mode=VALIDATION_MODE_LOCAL,
            ),
            status=VALIDATION_STATUS_PASSED,
            success=True,
        ),
        ValidationMatrixResult(
            target=ValidationMatrixTarget(
                target_id="suyuan_query_reset_connected",
                template_name="suyuan_online_query_reset",
                family="suyuan",
                system_name="suyuan_online_record",
                mode=VALIDATION_MODE_CONNECTED,
            ),
            status=VALIDATION_STATUS_SKIPPED,
            success=False,
            reason="cdp_url_missing",
        ),
        ValidationMatrixResult(
            target=ValidationMatrixTarget(
                target_id="suyuan_detail_view_connected",
                template_name="suyuan_online_detail_view",
                family="suyuan",
                system_name="suyuan_online_record",
                mode=VALIDATION_MODE_CONNECTED,
            ),
            status=VALIDATION_STATUS_PASSED,
            success=True,
        ),
    ]

    summary = summarize_validation_matrix(results)

    assert summary["status"] == VALIDATION_STATUS_PARTIAL
    assert summary["target_count"] == 4
    assert summary["executed_count"] == 3
    assert summary["passed_count"] == 3
    assert summary["skipped_count"] == 1
    assert summary["by_mode"][VALIDATION_MODE_LOCAL]["passed_count"] == 2
    assert summary["by_mode"][VALIDATION_MODE_CONNECTED]["status"] == VALIDATION_STATUS_PARTIAL
    assert summary["by_family"]["lab"]["target_count"] == 2
    assert summary["by_family"]["suyuan"]["skipped_count"] == 1
    assert summary["by_family"]["suyuan"]["passed_count"] == 1
    assert summary["by_system"]["template_lab"]["passed_count"] == 2


def test_render_validation_matrix_markdown_contains_targets_and_statuses() -> None:
    result = ValidationMatrixResult(
        target=ValidationMatrixTarget(
            target_id="suyuan_query_reset_connected",
            template_name="suyuan_online_query_reset",
            family="suyuan",
            system_name="suyuan_online_record",
            mode=VALIDATION_MODE_CONNECTED,
        ),
        status=VALIDATION_STATUS_FAILED,
        success=False,
        reason="RuntimeError: page missing",
    )

    payload = build_validation_matrix_payload(goal="G4", results=[result])
    markdown = render_validation_matrix_markdown(payload)

    assert "# G4 Cross-System Validation Matrix" in markdown
    assert "suyuan_query_reset_connected" in markdown
    assert "suyuan_online_query_reset" in markdown
    assert VALIDATION_STATUS_FAILED in markdown
    assert "RuntimeError: page missing" in markdown


def test_render_validation_matrix_markdown_contains_third_real_template_target() -> None:
    result = ValidationMatrixResult(
        target=ValidationMatrixTarget(
            target_id="suyuan_detail_view_connected",
            template_name="suyuan_online_detail_view",
            family="suyuan",
            system_name="suyuan_online_record",
            mode=VALIDATION_MODE_CONNECTED,
        ),
        status=VALIDATION_STATUS_PASSED,
        success=True,
        reason="detail drawer actions visible",
    )

    payload = build_validation_matrix_payload(goal="G4", results=[result])
    markdown = render_validation_matrix_markdown(payload)

    assert "suyuan_detail_view_connected" in markdown
    assert "suyuan_online_detail_view" in markdown


def test_run_g4_validation_matrix_aggregates_local_and_connected_targets() -> None:
    targets = [
        ValidationMatrixTarget(
            target_id="lab_navigation_local",
            template_name="lab_navigation",
            family="lab",
            system_name="template_lab",
            mode=VALIDATION_MODE_LOCAL,
        ),
        ValidationMatrixTarget(
            target_id="suyuan_query_reset_connected",
            template_name="suyuan_online_query_reset",
            family="suyuan",
            system_name="suyuan_online_record",
            mode=VALIDATION_MODE_CONNECTED,
        ),
    ]
    local_calls: list[str] = []
    connected_calls: list[tuple[str, str]] = []

    async def fake_local_runner(template_name: str) -> dict[str, object]:
        local_calls.append(template_name)
        return {
            "template": template_name,
            "template_name": template_name,
            "run_dir": "C:/artifacts/lab_navigation",
            "status": "passed",
            "success": True,
            "rule_summary": "local ok",
        }

    async def fake_connected_runner(template_name: str, *, cdp_url: str) -> dict[str, object]:
        connected_calls.append((template_name, cdp_url))
        return {
            "template": template_name,
            "template_name": template_name,
            "run_dir": "C:/artifacts/suyuan_query_reset",
            "status": "passed",
            "success": True,
            "rule_summary": "connected ok",
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        payload = asyncio.run(
            run_g4_validation_matrix(
                cdp_url="http://localhost:9222",
                targets=targets,
                output_root=Path(tmpdir),
                local_runner=fake_local_runner,
                connected_runner=fake_connected_runner,
            )
        )

        assert local_calls == ["lab_navigation"]
        assert connected_calls == [("suyuan_online_query_reset", "http://localhost:9222")]
        assert payload["summary"]["status"] == VALIDATION_STATUS_PASSED
        assert payload["summary"]["passed_count"] == 2
        assert Path(payload["json_path"]).exists()
        assert Path(payload["markdown_path"]).exists()

        persisted = json.loads(Path(payload["json_path"]).read_text(encoding="utf-8"))
        assert persisted["summary"]["target_count"] == 2
        assert len(persisted["results"]) == 2
        assert persisted["results"][1]["mode"] == VALIDATION_MODE_CONNECTED


def test_run_g4_validation_matrix_dispatches_default_targets_by_mode() -> None:
    targets = build_default_g4_validation_targets()
    expected_local = [item.template_name for item in targets if item.mode == VALIDATION_MODE_LOCAL]
    expected_connected = [item.template_name for item in targets if item.mode == VALIDATION_MODE_CONNECTED]
    local_calls: list[str] = []
    connected_calls: list[tuple[str, str]] = []

    async def fake_local_runner(template_name: str) -> dict[str, object]:
        local_calls.append(template_name)
        return {
            "template": template_name,
            "template_name": template_name,
            "run_dir": f"C:/artifacts/{template_name}",
            "status": "passed",
            "success": True,
            "rule_summary": "local ok",
        }

    async def fake_connected_runner(template_name: str, *, cdp_url: str) -> dict[str, object]:
        connected_calls.append((template_name, cdp_url))
        return {
            "template": template_name,
            "template_name": template_name,
            "run_dir": f"C:/artifacts/{template_name}",
            "status": "passed",
            "success": True,
            "rule_summary": "connected ok",
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        payload = asyncio.run(
            run_g4_validation_matrix(
                cdp_url="http://localhost:9222",
                targets=None,
                output_root=Path(tmpdir),
                local_runner=fake_local_runner,
                connected_runner=fake_connected_runner,
            )
        )

    assert local_calls == expected_local
    assert connected_calls == [(template_name, "http://localhost:9222") for template_name in expected_connected]
    assert payload["summary"]["by_family"]["lab"]["target_count"] == len(
        [item for item in targets if item.family == "lab"]
    )
    assert payload["summary"]["by_family"]["suyuan"]["target_count"] == len(
        [item for item in targets if item.family == "suyuan"]
    )


def test_run_g4_validation_matrix_skips_connected_target_without_cdp_url() -> None:
    targets = [
        ValidationMatrixTarget(
            target_id="lab_navigation_local",
            template_name="lab_navigation",
            family="lab",
            system_name="template_lab",
            mode=VALIDATION_MODE_LOCAL,
        ),
        ValidationMatrixTarget(
            target_id="suyuan_query_reset_connected",
            template_name="suyuan_online_query_reset",
            family="suyuan",
            system_name="suyuan_online_record",
            mode=VALIDATION_MODE_CONNECTED,
        ),
    ]

    async def fake_local_runner(template_name: str) -> dict[str, object]:
        return {
            "template": template_name,
            "template_name": template_name,
            "run_dir": "C:/artifacts/lab_navigation",
            "status": "passed",
            "success": True,
            "rule_summary": "local ok",
        }

    async def fake_connected_runner(template_name: str, *, cdp_url: str) -> dict[str, object]:
        raise AssertionError("connected runner should not be called when cdp_url is missing")

    with tempfile.TemporaryDirectory() as tmpdir:
        payload = asyncio.run(
            run_g4_validation_matrix(
                cdp_url="",
                targets=targets,
                output_root=Path(tmpdir),
                local_runner=fake_local_runner,
                connected_runner=fake_connected_runner,
            )
        )

    assert payload["summary"]["status"] == VALIDATION_STATUS_PARTIAL
    assert payload["summary"]["passed_count"] == 1
    assert payload["summary"]["skipped_count"] == 1
    skipped_item = next(item for item in payload["results"] if item["status"] == VALIDATION_STATUS_SKIPPED)
    assert skipped_item["target_id"] == "suyuan_query_reset_connected"
    assert skipped_item["reason"] == "cdp_url_missing"


def test_run_g4_validation_matrix_marks_runner_exception_as_failed() -> None:
    targets = [
        ValidationMatrixTarget(
            target_id="lab_navigation_local",
            template_name="lab_navigation",
            family="lab",
            system_name="template_lab",
            mode=VALIDATION_MODE_LOCAL,
        )
    ]

    async def fake_local_runner(template_name: str) -> dict[str, object]:
        raise RuntimeError("browser launch failed")

    with tempfile.TemporaryDirectory() as tmpdir:
        payload = asyncio.run(
            run_g4_validation_matrix(
                cdp_url="http://localhost:9222",
                targets=targets,
                output_root=Path(tmpdir),
                local_runner=fake_local_runner,
            )
        )

    assert payload["summary"]["status"] == VALIDATION_STATUS_FAILED
    assert payload["summary"]["failed_count"] == 1
    assert payload["results"][0]["status"] == VALIDATION_STATUS_FAILED
    assert payload["results"][0]["reason"] == "RuntimeError: browser launch failed"
