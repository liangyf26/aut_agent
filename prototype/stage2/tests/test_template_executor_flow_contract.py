from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.runtime.artifacts import ArtifactWriter  # noqa: E402
from prototype.stage2.app.verification import template_executor as template_executor_module  # noqa: E402
from prototype.stage2.app.verification.template_executor import (  # noqa: E402
    TemplateActionRegistry,
    TemplateFlowExecutor,
)
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData  # noqa: E402


class _FakePage:
    pass


def test_flow_substep_failure_marks_parent_step_failed_and_preserves_substeps() -> None:
    registry = TemplateActionRegistry()

    async def flow_handler(page: object, artifacts: object, runtime: object, step: dict[str, object]) -> list[dict[str, object]]:
        return [
            {"step": "open_dialog", "result": {"ok": True}, "status": "completed"},
            {
                "step": "click_agreement_accept",
                "result": {"ok": False, "reason": "button-not-found"},
                "status": "failed",
            },
        ]

    async def next_handler(page: object, artifacts: object, runtime: object, step: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "step": step.get("id")}

    registry.register("run_apply_wizard", flow_handler)
    registry.register("fill_success_template", next_handler)

    with tempfile.TemporaryDirectory() as tmpdir:
        artifacts = ArtifactWriter(Path(tmpdir), "flow_contract")
        runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

        executions = asyncio.run(
            TemplateFlowExecutor(registry).execute(
                page=_FakePage(),
                artifacts=artifacts,
                runtime=runtime,
                template={
                    "steps": [
                        {"id": "run_apply_wizard", "action": "run_apply_wizard"},
                        {"id": "fill_cultivation_template", "action": "fill_success_template"},
                    ]
                },
            )
        )

    assert [item.step_id for item in executions] == [
        "run_apply_wizard",
        "fill_cultivation_template",
    ]
    assert executions[0].status == "failed"
    assert executions[0].substeps is not None
    assert executions[0].substeps[1]["result"]["reason"] == "button-not-found"
    assert executions[0].to_attempt_action()["substeps"][1]["status"] == "failed"
    assert executions[1].status == "completed"


def test_flow_substep_ok_false_without_explicit_status_still_marks_parent_step_failed() -> None:
    registry = TemplateActionRegistry()

    async def flow_handler(page: object, artifacts: object, runtime: object, step: dict[str, object]) -> list[dict[str, object]]:
        return [
            {"step": "click_apply_button", "result": {"ok": True}},
            {"step": "click_enter_initial_form", "result": {"ok": False, "reason": "text-not-found"}},
        ]

    registry.register("run_apply_wizard", flow_handler)

    with tempfile.TemporaryDirectory() as tmpdir:
        artifacts = ArtifactWriter(Path(tmpdir), "flow_substep_result_contract")
        runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

        executions = asyncio.run(
            TemplateFlowExecutor(registry).execute(
                page=_FakePage(),
                artifacts=artifacts,
                runtime=runtime,
                template={"steps": [{"id": "run_apply_wizard", "action": "run_apply_wizard"}]},
            )
        )

    assert len(executions) == 1
    assert executions[0].status == "failed"
    assert executions[0].substeps is not None
    assert executions[0].substeps[1]["result"]["ok"] is False


def test_handler_exception_records_failed_execution_before_reraising(monkeypatch) -> None:
    registry = TemplateActionRegistry()

    async def failing_handler(page: object, artifacts: object, runtime: object, step: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("submit exploded")

    registry.register("submit_filing_dialog", failing_handler)
    recorded_executions: list[object] = []
    original_template_step_execution = template_executor_module.TemplateStepExecution

    def record_step_execution(*args: object, **kwargs: object) -> object:
        execution = original_template_step_execution(*args, **kwargs)
        recorded_executions.append(execution)
        return execution

    monkeypatch.setattr(
        template_executor_module,
        "TemplateStepExecution",
        record_step_execution,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        artifacts = ArtifactWriter(Path(tmpdir), "flow_exception_contract")
        runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

        try:
            asyncio.run(
                TemplateFlowExecutor(registry).execute(
                    page=_FakePage(),
                    artifacts=artifacts,
                    runtime=runtime,
                    template={"steps": [{"id": "submit_filing_dialog", "action": "submit_filing_dialog"}]},
                )
            )
        except RuntimeError as exc:
            assert str(exc) == "submit exploded"
        else:
            raise AssertionError("expected handler exception to be re-raised")

    assert len(recorded_executions) == 1
    execution = recorded_executions[0]
    assert execution.step_id == "submit_filing_dialog"
    assert execution.action == "submit_filing_dialog"
    assert execution.status == "failed"
    assert execution.result == {"ok": False, "error": "RuntimeError: submit exploded"}
    assert execution.substeps is None
    assert execution.to_attempt_action()["result"]["error"] == "RuntimeError: submit exploded"


def test_unexpected_handler_result_type_marks_step_failed_with_observable_payload() -> None:
    registry = TemplateActionRegistry()

    async def unexpected_handler(page: object, artifacts: object, runtime: object, step: dict[str, object]) -> str:
        return "not-a-dict-or-list"

    registry.register("submit_filing_dialog", unexpected_handler)

    with tempfile.TemporaryDirectory() as tmpdir:
        artifacts = ArtifactWriter(Path(tmpdir), "flow_unexpected_result_contract")
        runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

        executions = asyncio.run(
            TemplateFlowExecutor(registry).execute(
                page=_FakePage(),
                artifacts=artifacts,
                runtime=runtime,
                template={"steps": [{"id": "submit_filing_dialog", "action": "submit_filing_dialog"}]},
            )
        )

    assert len(executions) == 1
    execution = executions[0]
    assert execution.step_id == "submit_filing_dialog"
    assert execution.status == "failed"
    assert execution.result == {"ok": False, "unexpected_result_type": "str"}
    assert execution.substeps is None
    assert execution.to_attempt_action()["result"]["unexpected_result_type"] == "str"
