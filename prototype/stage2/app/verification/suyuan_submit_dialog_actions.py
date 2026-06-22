from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Page

from prototype.stage2.app.runtime.artifacts import ArtifactWriter

from .template_executor import ActionHandler, TemplateActionRegistry
from .template_runtime import TemplateRuntimeData


async def upload_drawer_required_files(
    page: Page,
    personnel_file: Path,
    acceptance_file: Path,
) -> dict[str, Any]:
    personnel_file = personnel_file.resolve()
    acceptance_file = acceptance_file.resolve()
    inputs = page.locator("input[type=file]")
    count = await inputs.count()
    result: dict[str, Any] = {"count": count, "uploads": []}
    if count < 4:
        result["ok"] = False
        result["reason"] = "expected-at-least-4-file-inputs"
        return result
    await inputs.nth(0).set_input_files(str(personnel_file))
    await page.wait_for_timeout(2500)
    result["uploads"].append({"index": 0, "file": str(personnel_file)})
    await inputs.nth(3).set_input_files(str(acceptance_file))
    await page.wait_for_timeout(2500)
    result["uploads"].append({"index": 3, "file": str(acceptance_file)})
    state = await page.evaluate(
        """
        () => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const apply = getApply();
          return apply ? {
            cultivatorAttachments: apply.form.cultivatorAttachments,
            acceptanceAttachments: apply.form.acceptanceAttachments,
            pictures: apply.form.pictures,
            attachments: apply.form.attachments,
          } : null;
        }
        """
    )
    result["ok"] = True
    result["state"] = state
    return result


async def select_submit_dialog_dept(page: Page, dept_label: str) -> dict[str, Any]:
    dialog_tree = page.locator(".el-dialog:visible .vue-treeselect").first
    if not await dialog_tree.count():
        return {"ok": False, "reason": "treeselect-not-found", "target": dept_label}
    await dialog_tree.click(force=True)
    await page.wait_for_timeout(1200)
    labels = page.locator(
        ".vue-treeselect__menu:visible .vue-treeselect__label, .vue-treeselect__menu:visible .vue-treeselect__option"
    )
    count = await labels.count()
    candidates: list[str] = []
    for idx in range(count):
        text = (await labels.nth(idx).inner_text()).strip()
        candidates.append(text)
        if dept_label in text:
            await labels.nth(idx).click(force=True)
            await page.wait_for_timeout(1000)
            return {"ok": True, "target": dept_label, "selected": text, "candidates": candidates}
    fallback = await page.evaluate(
        """
        ({ deptLabel }) => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const dialog = getApply()?.$refs?.filingPayDialog;
          if (!dialog) return { ok: false, reason: 'filing-dialog-not-found' };
          const walk = (nodes, out = []) => {
            for (const node of nodes || []) {
              out.push({ id: node.id, label: node.label });
              if (node.children) walk(node.children, out);
            }
            return out;
          };
          const options = walk(dialog.deptOptions || []);
          const hit = options.find(item => (item.label || '').includes(deptLabel));
          if (!hit) return { ok: false, reason: 'option-not-found', options };
          dialog.selectedDeptId = hit.id;
          return { ok: true, hit, options };
        }
        """,
        {"deptLabel": dept_label},
    )
    if fallback.get("ok"):
        return {
            "ok": True,
            "target": dept_label,
            "selected": fallback["hit"]["label"],
            "method": "fallback-state",
        }
    return {"ok": False, "target": dept_label, "reason": "option-not-found", "candidates": candidates}


async def upload_submit_dialog_apply_file(page: Page, apply_file: Path) -> dict[str, Any]:
    apply_file = apply_file.resolve()
    input_loc = page.locator(".el-dialog:visible input[type=file]").first
    if not await input_loc.count():
        return {"ok": False, "reason": "file-input-not-found", "file": str(apply_file)}
    await input_loc.set_input_files(str(apply_file))
    await page.wait_for_timeout(3000)
    state = await page.evaluate(
        """
        () => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const dialog = getApply()?.$refs?.filingPayDialog;
          return dialog ? {
            selectedDeptId: dialog.selectedDeptId,
            uploadFiles: dialog.uploadFiles,
            registrationId: dialog.registrationId,
          } : null;
        }
        """
    )
    return {"ok": True, "file": str(apply_file), "state": state}


async def submit_filing_dialog(page: Page) -> dict[str, Any]:
    submit = page.get_by_role("button", name="提交备案")
    if await submit.count():
        await submit.first.click(force=True)
        await page.wait_for_timeout(5000)
        return {"ok": True, "method": "role"}
    return {"ok": False, "reason": "submit-record-not-found"}


def register_suyuan_submit_dialog_actions(
    registry: TemplateActionRegistry,
    *,
    submit_filing_dialog_handler: ActionHandler | None = None,
) -> TemplateActionRegistry:
    async def handle_upload_drawer_required_files(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        files_ref = str(step.get("args", {}).get("files_ref", "") or "")
        generated_files = runtime.resolve_ref(files_ref) if files_ref else {}
        personnel_file = _resolve_generated_file(
            runtime,
            preferred_ref=f"{files_ref}.personnel_file" if files_ref else None,
            fallback_ref="generated_files.personnel_file",
            fallback_value=(generated_files or {}).get("personnel_file"),
        )
        acceptance_file = _resolve_generated_file(
            runtime,
            preferred_ref=f"{files_ref}.acceptance_file" if files_ref else None,
            fallback_ref="generated_files.acceptance_file",
            fallback_value=(generated_files or {}).get("acceptance_file"),
        )
        return await upload_drawer_required_files(page, personnel_file, acceptance_file)

    async def handle_select_submit_dialog_dept(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        ref = str(step.get("args", {}).get("data_ref", "") or "")
        dept_label = runtime.resolve_ref(ref) if ref else ""
        return await select_submit_dialog_dept(page, str(dept_label or ""))

    async def handle_upload_submit_dialog_apply_file(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        file_ref = str(step.get("args", {}).get("file_ref", "") or "")
        apply_file = _resolve_generated_file(
            runtime,
            preferred_ref=file_ref or None,
            fallback_ref="generated_files.apply_file",
            fallback_value="apply_file_missing.pdf",
        )
        return await upload_submit_dialog_apply_file(page, apply_file)

    async def handle_submit_filing_dialog(
        page: Page,
        artifacts: ArtifactWriter,
        runtime: TemplateRuntimeData,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        if submit_filing_dialog_handler is not None:
            result = await submit_filing_dialog_handler(page, artifacts, runtime, step)
            return dict(result) if isinstance(result, dict) else {"ok": True}
        return await submit_filing_dialog(page)

    registry.register("upload_drawer_required_files", handle_upload_drawer_required_files)
    registry.register("select_submit_dialog_dept", handle_select_submit_dialog_dept)
    registry.register("upload_submit_dialog_apply_file", handle_upload_submit_dialog_apply_file)
    registry.register("submit_filing_dialog", handle_submit_filing_dialog)
    return registry


def _resolve_generated_file(
    runtime: TemplateRuntimeData,
    *,
    preferred_ref: str | None,
    fallback_ref: str | None,
    fallback_value: Any,
) -> Path:
    for ref in (preferred_ref, fallback_ref):
        if not ref:
            continue
        resolved = runtime.generated_file(ref)
        if resolved is not None:
            return resolved
    return Path(str(fallback_value or ""))
