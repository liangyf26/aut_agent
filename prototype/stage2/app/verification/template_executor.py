from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from playwright.async_api import Page

from prototype.stage2.app.runtime.artifacts import ArtifactWriter

from .template_runtime import TemplateRuntimeData

ActionHandler = Callable[
    [Page, ArtifactWriter, TemplateRuntimeData, dict[str, Any]],
    Awaitable[dict[str, Any] | list[dict[str, Any]] | None],
]


@dataclass(frozen=True)
class TemplateStepExecution:
    step_id: str
    action: str
    status: str
    started_at_monotonic: float
    finished_at_monotonic: float
    duration_ms: int
    result: dict[str, Any] | None = None
    substeps: list[dict[str, Any]] | None = None

    def to_attempt_action(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step_id,
            "action": self.action,
            "status": self.status,
            "duration_ms": self.duration_ms,
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.substeps is not None:
            payload["substeps"] = self.substeps
        return payload


class TemplateActionRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, action_name: str, handler: ActionHandler) -> None:
        self._handlers[action_name] = handler

    def get(self, action_name: str) -> ActionHandler:
        try:
            return self._handlers[action_name]
        except KeyError as exc:
            raise KeyError(f"未注册模板动作: {action_name}") from exc


class TemplateFlowExecutor:
    def __init__(self, registry: TemplateActionRegistry) -> None:
        self.registry = registry

    async def execute(
        self,
        *,
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        template: dict[str, Any],
    ) -> list[TemplateStepExecution]:
        executions: list[TemplateStepExecution] = []
        for index, step in enumerate(template.get("steps", []), start=1):
            if not isinstance(step, dict):
                continue
            action_name = str(step.get("action") or "").strip()
            if not action_name:
                continue
            step_id = str(step.get("id") or f"step_{index:02d}")
            handler = self.registry.get(action_name)
            started = time.perf_counter()
            status = "completed"
            result: dict[str, Any] | None = None
            substeps: list[dict[str, Any]] | None = None
            try:
                raw = await handler(page, artifacts, runtime, step)
                if isinstance(raw, list):
                    substeps = [item for item in raw if isinstance(item, dict)]
                    status = _status_from_substeps(substeps)
                elif isinstance(raw, dict):
                    result = raw
                    status = _status_from_result(raw)
                elif raw is None:
                    result = {"ok": True}
                    status = "completed"
                else:
                    result = {"ok": False, "unexpected_result_type": type(raw).__name__}
                    status = "failed"
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                status = "failed"
                finished = time.perf_counter()
                executions.append(
                    TemplateStepExecution(
                        step_id=step_id,
                        action=action_name,
                        status=status,
                        started_at_monotonic=started,
                        finished_at_monotonic=finished,
                        duration_ms=max(0, int((finished - started) * 1000)),
                        result=result,
                    )
                )
                raise
            finished = time.perf_counter()
            executions.append(
                TemplateStepExecution(
                    step_id=step_id,
                    action=action_name,
                    status=status,
                    started_at_monotonic=started,
                    finished_at_monotonic=finished,
                    duration_ms=max(0, int((finished - started) * 1000)),
                    result=result,
                    substeps=substeps,
                )
            )
        return executions


def _status_from_result(result: dict[str, Any]) -> str:
    ok = result.get("ok")
    if ok is True:
        return "completed"
    if ok is False:
        return "failed"
    return "completed"


def _status_from_substeps(substeps: list[dict[str, Any]]) -> str:
    for item in substeps:
        if str(item.get("status") or "").lower() == "failed":
            return "failed"
        result = item.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            return "failed"
    return "completed"
