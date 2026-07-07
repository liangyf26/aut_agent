from __future__ import annotations

from typing import Any

from playwright.async_api import Locator, Page

from prototype.stage2.app.runtime.artifacts import ArtifactWriter

from .template_executor import TemplateActionRegistry
from .template_runtime import TemplateRuntimeData


async def click_apply_button(page: Page) -> None:
    button = page.get_by_role("button", name="我要申请备案")
    if await button.count():
        await button.first.click(force=True)
        await page.wait_for_timeout(1200)
        return

    text_match = page.get_by_text("我要申请备案", exact=True)
    if await text_match.count():
        await text_match.first.click(force=True)
        await page.wait_for_timeout(1200)
        return

    raise RuntimeError("未找到“我要申请备案”按钮")


async def click_exact_button(page: Page, name: str) -> dict[str, Any]:
    locator = page.get_by_role("button", name=name)
    if await locator.count():
        await locator.first.click(force=True)
        await page.wait_for_timeout(1200)
        return {"ok": True, "name": name, "method": "role"}
    locator = page.get_by_text(name, exact=True)
    if await locator.count():
        await locator.first.click(force=True)
        await page.wait_for_timeout(1200)
        return {"ok": True, "name": name, "method": "text"}
    return {"ok": False, "name": name, "reason": "button-not-found"}


async def click_partial_text(page: Page, text_fragment: str) -> dict[str, Any]:
    result = await page.evaluate(
        """
        ({ textFragment }) => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const candidates = Array.from(document.querySelectorAll('button, .el-button, [role="button"], span, a'))
            .filter(el => el.offsetParent !== null && text(el).includes(textFragment));
          if (!candidates.length) {
            return { ok: false, reason: 'text-not-found', textFragment };
          }
          const target = candidates[candidates.length - 1];
          target.click();
          return {
            ok: true,
            textFragment,
            clickedText: text(target),
            candidates: candidates.map(text).slice(0, 20),
          };
        }
        """,
        {"textFragment": text_fragment},
    )
    await page.wait_for_timeout(1500)
    return result


async def wait_for_dialog(page: Page) -> Locator:
    dialog = page.locator(".el-dialog:visible").last
    await dialog.wait_for(timeout=15000)
    return dialog


async def run_apply_wizard(page: Page) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    await click_apply_button(page)
    dialog = await wait_for_dialog(page)
    steps.append({"step": "click_apply_button", "result": {"ok": await dialog.is_visible(), "method": "entry_opened"}})
    steps.append(
        {
            "step": "click_intro_confirm",
            "result": await click_exact_button(page, "拟备案信息纳入溯源系统"),
        }
    )
    steps.append(
        {
            "step": "click_agreement_open",
            "result": await click_exact_button(page, "签署溯源服务协议"),
        }
    )
    steps.append({"step": "click_agreement_accept", "result": await click_exact_button(page, "同意签署")})
    steps.append(
        {
            "step": "click_enter_initial_form",
            "result": await click_partial_text(page, "纳入溯源系统的拟备案信息录入"),
        }
    )
    return steps


async def select_drawer_option(page: Page, label: str, option_keyword: str) -> dict[str, Any]:
    drawer = page.locator(".el-drawer:visible, .el-drawer__wrapper:visible").last
    items = drawer.locator(".el-form-item").filter(has=page.locator(".el-form-item__label", has_text=label))
    count = await items.count()
    if not count:
        return {"ok": False, "label": label, "reason": "container-not-found"}
    container = items.last
    trigger = container.locator(".el-select .el-input__inner").first
    if not await trigger.count():
        trigger = container.locator(".el-input__inner").first
    if not await trigger.count():
        return {"ok": False, "label": label, "reason": "trigger-not-found"}
    await trigger.click(force=True)
    await page.wait_for_timeout(1200)
    options = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
    option_count = await options.count()
    candidates: list[str] = []
    for idx in range(option_count):
        text = (await options.nth(idx).inner_text()).strip()
        candidates.append(text)
        if option_keyword in text:
            await options.nth(idx).click(force=True)
            await page.wait_for_timeout(900)
            return {"ok": True, "label": label, "selected": text, "candidates": candidates}
    return {"ok": False, "label": label, "reason": "option-not-found", "candidates": candidates}


async def ensure_drawer_checkbox(page: Page, label_text: str) -> dict[str, Any]:
    drawer = page.locator(".el-drawer:visible, .el-drawer__wrapper:visible").last
    checkbox = drawer.locator("label.el-checkbox").filter(has_text=label_text).first
    if await checkbox.count():
        await checkbox.click(force=True)
        await page.wait_for_timeout(500)
        return {"ok": True, "method": "label"}
    text_match = drawer.get_by_text(label_text, exact=False)
    if await text_match.count():
        await text_match.first.click(force=True)
        await page.wait_for_timeout(500)
        return {"ok": True, "method": "text"}
    return {"ok": False, "reason": "checkbox-not-found"}


async def expand_cultivation_form(page: Page) -> dict[str, Any]:
    drawer = page.locator(".el-drawer:visible, .el-drawer__wrapper:visible").last
    submit = drawer.get_by_role("button", name="信息纳入溯源系统")
    if await submit.count():
        await submit.first.click(force=True)
        await page.wait_for_timeout(3000)
        return {"ok": True, "method": "role"}
    return {"ok": False, "reason": "submit-not-found"}


async def assert_detail_panel_ready(
    page: Page,
    expected_action_texts: list[str] | None = None,
) -> dict[str, Any]:
    action_texts = [text for text in (expected_action_texts or ["修改", "删除", "去支付"]) if str(text).strip()]
    drawer = page.locator(".el-drawer__wrapper:visible, .el-drawer:visible, section.el-drawer__body:visible").last
    try:
        await drawer.wait_for(timeout=10000)
    except Exception:
        return {"ok": False, "reason": "detail-panel-not-visible"}

    form_item_count = await drawer.locator(".el-form-item").count()
    visible_action_texts: list[str] = []
    for text in action_texts:
        role_match = drawer.get_by_role("button", name=text)
        if await role_match.count():
            visible_action_texts.append(text)
            continue
        text_match = drawer.get_by_text(text, exact=False)
        if await text_match.count():
            visible_action_texts.append(text)

    ok = form_item_count > 0 or bool(visible_action_texts)
    return {
        "ok": ok,
        "method": "drawer",
        "form_item_count": form_item_count,
        "visible_action_texts": visible_action_texts,
        "expected_action_texts": action_texts,
        "reason": "" if ok else "detail-panel-empty",
    }


def register_suyuan_wizard_drawer_actions(registry: TemplateActionRegistry) -> TemplateActionRegistry:
    async def handle_run_apply_wizard(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return await run_apply_wizard(page)

    async def handle_select_drawer_option(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        label = str(step.get("args", {}).get("label", "") or "")
        ref = str(step.get("args", {}).get("data_ref", "") or "")
        option_value = runtime.resolve_ref(ref)
        return await select_drawer_option(page, label, str(option_value or ""))

    async def handle_ensure_drawer_checkbox(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        label_text = str(step.get("args", {}).get("label_text", "") or "")
        return await ensure_drawer_checkbox(page, label_text)

    async def handle_expand_cultivation_form(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        return await expand_cultivation_form(page)

    registry.register("run_apply_wizard", handle_run_apply_wizard)
    registry.register("select_drawer_option", handle_select_drawer_option)
    registry.register("ensure_drawer_checkbox", handle_ensure_drawer_checkbox)
    registry.register("expand_cultivation_form", handle_expand_cultivation_form)
    return registry


def register_suyuan_detail_view_actions(registry: TemplateActionRegistry) -> TemplateActionRegistry:
    async def handle_assert_detail_panel_ready(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        args = step.get("args", {}) if isinstance(step, dict) else {}
        action_texts_ref = str(args.get("action_texts_ref", "") or "")
        resolved = runtime.resolve_ref(action_texts_ref) if action_texts_ref else args.get("action_texts")
        expected_action_texts = [str(item).strip() for item in resolved if str(item).strip()] if isinstance(resolved, list) else None
        return await assert_detail_panel_ready(page, expected_action_texts=expected_action_texts)

    registry.register("assert_detail_panel_ready", handle_assert_detail_panel_ready)
    return registry
