from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from playwright.async_api import Page

from prototype.stage2.app.runtime.artifacts import ArtifactWriter

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
    locator_expr = str(args.get("locator") or "").strip()
    if not locator_expr:
        return {"ok": False, "reason": "locator-missing"}
    value = runtime.resolve_ref(args.get("data_ref")) if args.get("data_ref") else args.get("value", "")
    locator = page.locator(locator_expr).first
    await locator.wait_for(timeout=int(args.get("timeout_ms") or 10000))
    await locator.fill("" if value is None else str(value))
    return {"ok": True, "locator": locator_expr, "value": value}


async def select_option_by_locator(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    locator_expr = str(args.get("locator") or "").strip()
    if not locator_expr:
        return {"ok": False, "reason": "locator-missing"}
    option_value = runtime.resolve_ref(args.get("data_ref")) if args.get("data_ref") else args.get("value")
    locator = page.locator(locator_expr).first
    await locator.wait_for(timeout=int(args.get("timeout_ms") or 10000))
    await locator.select_option(value="" if option_value is None else str(option_value))
    return {"ok": True, "locator": locator_expr, "value": option_value}


async def click_by_locator(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    locator_expr = str(args.get("locator") or "").strip()
    if not locator_expr:
        return {"ok": False, "reason": "locator-missing"}
    locator = page.locator(locator_expr).first
    await locator.wait_for(timeout=int(args.get("timeout_ms") or 10000))
    await locator.click(force=bool(args.get("force", True)))
    if args.get("wait_ms"):
        await page.wait_for_timeout(int(args["wait_ms"]))
    return {"ok": True, "locator": locator_expr}


async def assert_locator_value(
    page: Page,
    artifacts: ArtifactWriter,
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> dict[str, Any]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    locator_expr = str(args.get("locator") or "").strip()
    if not locator_expr:
        return {"ok": False, "reason": "locator-missing"}
    expected_value = runtime.resolve_ref(args.get("value_ref")) if args.get("value_ref") else args.get("value", "")
    locator = page.locator(locator_expr).first
    await locator.wait_for(timeout=int(args.get("timeout_ms") or 10000))
    wait_until_match_ms = int(args.get("wait_until_match_ms") or 0)
    poll_interval_ms = int(args.get("poll_interval_ms") or 100)
    actual_value = await locator.input_value()
    if wait_until_match_ms > 0:
        deadline = time.perf_counter() + (wait_until_match_ms / 1000.0)
        expected_text = "" if expected_value is None else str(expected_value)
        while actual_value != expected_text and time.perf_counter() < deadline:
            await page.wait_for_timeout(poll_interval_ms)
            actual_value = await locator.input_value()
    return {
        "ok": actual_value == ("" if expected_value is None else str(expected_value)),
        "locator": locator_expr,
        "expected": expected_value,
        "actual": actual_value,
    }


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


def build_generic_template_registry() -> TemplateActionRegistry:
    registry = TemplateActionRegistry()
    registry.register("navigate_to_url", navigate_to_url)
    registry.register("fill_field_by_locator", fill_field_by_locator)
    registry.register("select_option_by_locator", select_option_by_locator)
    registry.register("click_by_locator", click_by_locator)
    registry.register("assert_locator_value", assert_locator_value)
    registry.register("assert_text_visible", assert_text_visible)
    registry.register("capture_named_screenshot", capture_named_screenshot)
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
