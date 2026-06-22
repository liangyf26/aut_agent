from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from playwright.async_api import Page

from prototype.stage2.app.runtime.artifacts import ArtifactWriter

from .locator_resolution import run_action_with_locator_candidates, resolve_step_locator_candidates
from .rule_evaluator import (
    evaluate_template_rules,
    SharedTemplateVerificationResult,
    TemplateVerificationEvidence,
)
from .suyuan_shared_actions import register_suyuan_detail_view_actions, register_suyuan_wizard_drawer_actions
from .suyuan_submit_dialog_actions import register_suyuan_submit_dialog_actions
from .template_executor import TemplateActionRegistry, TemplateFlowExecutor, TemplateStepExecution
from .template_runtime import TemplateRuntimeData


async def navigate_to_url(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    url = runtime.resolve_ref(args.get("url_ref")) if args.get("url_ref") else args.get("url")
    if not url:
        return {"ok": False, "reason": "url-missing"}
    wait_until = str(args.get("wait_until") or "domcontentloaded")
    await page.goto(str(url), wait_until=wait_until)
    if args.get("wait_ms"):
        await page.wait_for_timeout(int(args["wait_ms"]))
    return {"ok": True, "url": page.url, "title": await page.title()}


async def fill_field_by_locator(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    locator_candidates = resolve_step_locator_candidates(runtime, step)
    if not locator_candidates:
        return {"ok": False, "reason": "locator-missing"}
    value = runtime.resolve_ref(args.get("data_ref")) if args.get("data_ref") else args.get("value", "")
    result, resolved_locator, attempts = await run_action_with_locator_candidates(
        page,
        locator_candidates=locator_candidates,
        timeout_ms=int(args.get("timeout_ms") or 10000),
        action=_fill_locator_value("" if value is None else str(value)),
    )
    return _with_locator_attempts(
        result,
        resolved_locator=resolved_locator,
        locator_candidates=locator_candidates,
        attempts=attempts,
        value=value,
    )


async def select_option_by_locator(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    locator_candidates = resolve_step_locator_candidates(runtime, step)
    if not locator_candidates:
        return {"ok": False, "reason": "locator-missing"}
    option_value = runtime.resolve_ref(args.get("data_ref")) if args.get("data_ref") else args.get("value")
    result, resolved_locator, attempts = await run_action_with_locator_candidates(
        page,
        locator_candidates=locator_candidates,
        timeout_ms=int(args.get("timeout_ms") or 10000),
        action=_select_locator_option("" if option_value is None else str(option_value)),
    )
    return _with_locator_attempts(
        result,
        resolved_locator=resolved_locator,
        locator_candidates=locator_candidates,
        attempts=attempts,
        value=option_value,
    )


async def click_by_locator(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    locator_candidates = resolve_step_locator_candidates(runtime, step)
    if not locator_candidates:
        return {"ok": False, "reason": "locator-missing"}
    result, resolved_locator, attempts = await run_action_with_locator_candidates(
        page,
        locator_candidates=locator_candidates,
        timeout_ms=int(args.get("timeout_ms") or 10000),
        action=_click_locator(force=bool(args.get("force", True))),
    )
    if args.get("wait_ms"):
        await page.wait_for_timeout(int(args["wait_ms"]))
    return _with_locator_attempts(
        result,
        resolved_locator=resolved_locator,
        locator_candidates=locator_candidates,
        attempts=attempts,
    )


async def assert_locator_value(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    locator_candidates = resolve_step_locator_candidates(runtime, step)
    if not locator_candidates:
        return {"ok": False, "reason": "locator-missing"}
    expected_value = runtime.resolve_ref(args.get("value_ref")) if args.get("value_ref") else args.get("value", "")
    wait_until_match_ms = int(args.get("wait_until_match_ms") or 0)
    poll_interval_ms = int(args.get("poll_interval_ms") or 100)
    expected_text = "" if expected_value is None else str(expected_value)
    result, resolved_locator, attempts = await run_action_with_locator_candidates(
        page,
        locator_candidates=locator_candidates,
        timeout_ms=int(args.get("timeout_ms") or 10000),
        action=_assert_locator_input_value(
            page=page,
            expected_text=expected_text,
            wait_until_match_ms=wait_until_match_ms,
            poll_interval_ms=poll_interval_ms,
        ),
    )
    return _with_locator_attempts(
        result,
        resolved_locator=resolved_locator,
        locator_candidates=locator_candidates,
        attempts=attempts,
        expected=expected_value,
    )


async def assert_text_visible(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    expected = runtime.resolve_ref(args.get("text_ref")) if args.get("text_ref") else args.get("text")
    if not expected:
        return {"ok": False, "reason": "text-missing"}
    locator = page.get_by_text(str(expected), exact=bool(args.get("exact", True)))
    await locator.first.wait_for(timeout=int(args.get("timeout_ms") or 10000))
    return {"ok": True, "text": expected}


async def capture_named_screenshot(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    file_name = str(args.get("file_name") or f"{step.get('id', 'step')}.png")
    target = artifacts.screenshots_dir / file_name
    await page.screenshot(path=str(target), full_page=bool(args.get("full_page", True)))
    return {"ok": True, "path": str(target)}


def _fill_locator_value(value: str):
    async def _handler(locator, _: str) -> dict[str, Any]:
        await locator.fill(value)
        return {"ok": True}

    return _handler


def _select_locator_option(value: str):
    async def _handler(locator, _: str) -> dict[str, Any]:
        await locator.select_option(value=value)
        return {"ok": True}

    return _handler


def _click_locator(*, force: bool):
    async def _handler(locator, _: str) -> dict[str, Any]:
        await locator.click(force=force)
        return {"ok": True}

    return _handler


def _assert_locator_input_value(
    *,
    page: Page,
    expected_text: str,
    wait_until_match_ms: int,
    poll_interval_ms: int,
):
    async def _handler(locator, _: str) -> dict[str, Any]:
        actual_value = await locator.input_value()
        if wait_until_match_ms > 0:
            deadline = time.perf_counter() + (wait_until_match_ms / 1000.0)
            while actual_value != expected_text and time.perf_counter() < deadline:
                await page.wait_for_timeout(poll_interval_ms)
                actual_value = await locator.input_value()
        return {
            "ok": actual_value == expected_text,
            "actual": actual_value,
        }

    return _handler


def _with_locator_attempts(
    result: dict[str, Any],
    *,
    resolved_locator: str,
    locator_candidates: list[str],
    attempts: list[dict[str, str]],
    **extra: Any,
) -> dict[str, Any]:
    payload = dict(result)
    payload["locator"] = resolved_locator
    payload["locator_candidates"] = locator_candidates
    if attempts:
        payload["locator_attempts"] = attempts
    for key, value in extra.items():
        payload[key] = value
    return payload


def build_generic_template_registry() -> TemplateActionRegistry:
    registry = TemplateActionRegistry()
    registry.register("navigate_to_url", navigate_to_url)
    registry.register("fill_field_by_locator", fill_field_by_locator)
    registry.register("select_option_by_locator", select_option_by_locator)
    registry.register("click_by_locator", click_by_locator)
    registry.register("assert_locator_value", assert_locator_value)
    registry.register("assert_text_visible", assert_text_visible)
    registry.register("capture_named_screenshot", capture_named_screenshot)
    register_suyuan_wizard_drawer_actions(registry)
    register_suyuan_detail_view_actions(registry)
    register_suyuan_submit_dialog_actions(registry)
    return registry


async def execute_generic_template(
    *,
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    template: dict[str, Any],
) -> list[TemplateStepExecution]:
    executor = TemplateFlowExecutor(build_generic_template_registry())
    return await executor.execute(
        page=page,
        artifacts=artifacts,
        runtime=runtime,
        template=template,
    )


async def execute_generic_template_with_shared_result(
    *,
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    template: dict[str, Any],
) -> SharedTemplateVerificationResult:
    network_events: list[dict[str, Any]] = []

    async def on_response(response: Any) -> None:
        body_text = ""
        try:
            body_text = await response.text()
        except Exception:
            body_text = ""
        network_events.append(
            {
                "type": "response",
                "method": response.request.method,
                "url": str(response.url),
                "status": response.status,
                "body": body_text[:4000],
                "post_data": response.request.post_data or "",
            }
        )

    page.on("response", on_response)
    try:
        step_executions = await execute_generic_template(
            page=page,
            artifacts=artifacts,
            runtime=runtime,
            template=template,
        )
    finally:
        page.remove_listener("response", on_response)

    final_title = await page.title()
    ui_snapshot = await _collect_ui_snapshot(page)
    evidence = TemplateVerificationEvidence(
        final_url=page.url,
        final_title=final_title,
        body_text=str(ui_snapshot.get("body_text") or ""),
        messages=[str(item) for item in ui_snapshot.get("messages", []) if str(item).strip()],
        errors=[str(item) for item in ui_snapshot.get("errors", []) if str(item).strip()],
        network_events=network_events,
    )
    rule_evaluation = evaluate_template_rules(
        template=template,
        evidence=evidence,
        step_executions=step_executions,
    )
    shared_result = SharedTemplateVerificationResult(
        template_name=str(template.get("template_name") or ""),
        step_executions=step_executions,
        final_url=page.url,
        final_title=final_title,
        evidence=evidence,
        rule_evaluation=rule_evaluation,
    )
    artifacts.write_json("verification_result.json", shared_result.to_dict()["verification_result"])
    artifacts.write_json("network_events.json", network_events)
    return shared_result


async def _collect_ui_snapshot(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return String(node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const messages = Array.from(document.querySelectorAll('.el-message, .el-notification, .el-message-box'))
            .filter(node => node.offsetParent !== null)
            .map(node => text(node))
            .filter(Boolean);
          const errors = Array.from(document.querySelectorAll('.el-form-item__error, .error, [role="alert"]'))
            .filter(node => node.offsetParent !== null)
            .map(node => text(node))
            .filter(Boolean);
          return {
            messages,
            errors,
            body_text: text(document.body).slice(0, 12000),
          };
        }
        """
    )
