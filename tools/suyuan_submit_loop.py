import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, async_playwright
from prototype.stage2.app.config.models import (
    ModelProfile,
    load_model_profiles as load_stage2_model_profiles,
)
from prototype.stage2.app.data_factory.generator import TemplateDataFactory
from prototype.stage2.app.discovery import (
    DiscoveryArtifactWriter,
    DiscoveryPlanner,
    plan_live_discovery,
)
from prototype.stage2.app.iteration import write_iteration_artifacts
from prototype.stage2.app.progress import ProgressManager
from prototype.stage2.app.progress.console import format_status_line
from prototype.stage2.app.reporting import (
    adapt_progress_snapshot,
    render_progress_markdown,
    render_progress_text,
    render_run_report_markdown,
)
from prototype.stage2.app.runtime.artifacts import ArtifactWriter
from prototype.stage2.app.runtime.templates import load_template_bundle
from prototype.stage2.app.verification.generated_files import build_default_generated_files


if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


ARTIFACT_ROOT = ROOT_DIR / "artifacts" / "stage2"
DEFAULT_CDP_URL = "http://localhost:9222"
ONLINE_RECORD_URL = "https://www.zbsykj.com:19096/record/online"
DEFAULT_ENV_FILES = [
    ROOT_DIR / "demo" / ".env",
    ROOT_DIR / "demo" / "local_qwen.env",
]
STAGE2_TEMPLATE_DIR = ROOT_DIR / "prototype" / "stage2" / "templates" / "suyuan_online_apply"
TEMPLATE_BUNDLE = load_template_bundle(STAGE2_TEMPLATE_DIR)
SUCCESS_BASELINE = TEMPLATE_BUNDLE.baseline


def build_discovery_seed(artifacts: ArtifactWriter) -> tuple[object, dict[str, Path]]:
    result = DiscoveryPlanner().plan(
        template_name=TEMPLATE_BUNDLE.name,
        template=TEMPLATE_BUNDLE.template,
        baseline=SUCCESS_BASELINE,
    )
    paths = DiscoveryArtifactWriter(artifacts.run_dir).write(result)
    return result, paths


async def build_live_discovery(page: Page, artifacts: ArtifactWriter) -> tuple[object, dict[str, Path]]:
    result = await plan_live_discovery(
        page,
        template_name=TEMPLATE_BUNDLE.name,
        template=TEMPLATE_BUNDLE.template,
        baseline=SUCCESS_BASELINE,
        screenshots_dir=artifacts.run_dir,
    )
    paths = DiscoveryArtifactWriter(artifacts.run_dir).write(result)
    return result, paths


def build_report_page_entries(discovery_result: object, fallback_name: str, page_url: str, success: bool) -> list[dict[str, Any]]:
    result = getattr(discovery_result, "page_entries", [])
    if result:
        return [
            {
                "item_id": item.page_entry_id,
                "name": item.name,
                "status": "执行通过" if success else "执行失败",
                "summary": page_url or item.url,
                "source": item.source,
            }
            for item in result
        ]
    return [{"name": fallback_name, "status": "执行通过" if success else "执行失败", "summary": page_url}]


def build_report_feature_points(discovery_result: object, fallback_name: str, summary: str, success: bool) -> list[dict[str, Any]]:
    result = getattr(discovery_result, "feature_points", [])
    if result:
        return [
            {
                "item_id": item.feature_point_id,
                "name": item.name,
                "status": "执行通过" if success else "执行失败",
                "summary": summary,
                "source": item.source,
                "owner": item.page_entry_id,
            }
            for item in result
        ]
    return [{"name": fallback_name, "status": "执行通过" if success else "执行失败", "summary": summary}]


def build_report_failure_clusters(iteration_artifacts: object) -> list[dict[str, Any]]:
    clusters = getattr(iteration_artifacts, "failure_clusters", [])
    result: list[dict[str, Any]] = []
    for cluster in clusters:
        facts = [{"label": "signal_count", "value": cluster.signal_count}]
        if cluster.stage:
            facts.append({"label": "stage", "value": cluster.stage})
        result.append(
            {
                "cluster_id": cluster.cluster_id,
                "category": cluster.category,
                "status": cluster.status,
                "summary": cluster.summary,
                "root_cause": cluster.root_cause_hint,
                "action_level": cluster.action_level,
                "recommendation": cluster.recommendation,
                "related_items": cluster.related_items,
                "facts": facts,
            }
        )
    return result


def build_report_promotion_candidates(iteration_artifacts: object) -> list[dict[str, Any]]:
    candidates = getattr(iteration_artifacts, "promotion_candidates", [])
    return [
        {
            "item_id": candidate.candidate_id,
            "name": candidate.title,
            "status": candidate.status,
            "summary": candidate.reason,
            "source": candidate.source,
            "owner": candidate.promotion_level,
        }
        for candidate in candidates
    ]


def build_retry_plan_section(iteration_artifacts: object) -> list[dict[str, Any]]:
    retry_plan = getattr(iteration_artifacts, "retry_plan", None)
    if retry_plan is None:
        return []
    facts: list[dict[str, Any]] = [{"label": "status", "value": retry_plan.status}]
    if retry_plan.next_round is not None:
        facts.append({"label": "next_round", "value": retry_plan.next_round})
    if retry_plan.goal:
        facts.append({"label": "goal", "value": retry_plan.goal})
    if retry_plan.stop_reason:
        facts.append({"label": "stop_reason", "value": retry_plan.stop_reason})
    items = [
        {
            "item_id": action.action_id,
            "name": action.title,
            "status": action.priority,
            "summary": action.reason,
            "owner": action.owner,
            "source": action.stage,
            "facts": [
                {"label": "strategy", "value": action.strategy},
                {"label": "expected_outcome", "value": action.expected_outcome},
            ],
        }
        for action in retry_plan.actions
    ]
    return [
        {
            "title": "Retry Plan",
            "facts": facts,
            "items": items,
            "notes": retry_plan.notes,
        }
    ]


async def click_apply_button(page: Page) -> None:
    button = page.get_by_role("button", name="我要申请备案")
    if await button.count():
        await button.first.click(force=True)
        return

    text_match = page.get_by_text("我要申请备案", exact=True)
    if await text_match.count():
        await text_match.first.click(force=True)
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


async def reset_online_apply_page(page: Page) -> dict[str, Any]:
    await page.goto(ONLINE_RECORD_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], .el-button'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el))
            .filter(Boolean)
            .slice(0, 80);
          return {
            url: location.href,
            title: document.title,
            buttons,
            body: text(document.body).slice(0, 2500),
          };
        }
        """
    )


async def wait_for_dialog(page: Page) -> Locator:
    dialog = page.locator(".el-dialog:visible").last
    await dialog.wait_for(timeout=15000)
    return dialog


async def find_open_panel(page: Page) -> Locator | None:
    dialog = page.locator(".el-dialog:visible")
    if await dialog.count():
        return dialog.last
    drawer = page.locator(".el-drawer__wrapper:visible, .el-drawer:visible")
    if await drawer.count():
        return drawer.last
    return None


async def close_visible_panel(page: Page) -> dict[str, Any]:
    panel = await find_open_panel(page)
    if panel is None:
        return {"ok": True, "closed": False}
    close_button = page.locator(
        ".el-dialog__headerbtn:visible, .el-drawer__close-btn:visible, [aria-label*='close']:visible"
    ).last
    if await close_button.count():
        await close_button.click(force=True)
        await page.wait_for_timeout(800)
        return {"ok": True, "closed": True, "method": "close_button"}
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    return {"ok": True, "closed": True, "method": "escape"}


async def get_apply_state(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const apply = getApply();
          if (!apply) {
            return { ok: false, reason: 'apply-not-found' };
          }
          return {
            ok: true,
            title: apply.title,
            isEdit: !!apply.isEdit,
            isModified: !!apply.isModified,
            isPendingEditMode: !!apply.isPendingEditMode,
            isPendingPayment: !!apply.isPendingPayment,
            isShowEditButton: !!apply.isShowEditButton,
            isShowSaveButtons: !!apply.isShowSaveButtons,
            submitButtonText: apply.submitButtonText,
            currentId: apply.currentId,
            detailId: apply.detailId,
            auditStatus: apply.auditStatus,
            committed: !!apply.committed,
            typeFlags: {
              isCultivationType: !!apply.isCultivationType,
              isHardeningType: !!apply.isHardeningType,
              isPlantingType: !!apply.isPlantingType,
              isHarvestType: !!apply.isHarvestType,
              isPenjingType: !!apply.isPenjingType,
            },
            form: {
              registerType: apply.form?.registerType,
              type: apply.form?.type,
              institutionId: apply.form?.institutionId,
              institutionUserId: apply.form?.institutionUserId,
              plantId: apply.form?.plantId,
              batchNo: apply.form?.batchNo,
              cityRegionId: apply.form?.cityRegionId,
              cityRegionName: apply.form?.cityRegionName,
              rangeStr: apply.form?.rangeStr,
              deptId: apply.form?.deptId,
              acceptanceDeptId: apply.form?.acceptanceDeptId,
              acceptancePerson: apply.form?.acceptancePerson,
              acceptanceDate: apply.form?.acceptanceDate,
            },
          };
        }
        """
    )


async def snapshot_dialog_state(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const dialog = Array.from(document.querySelectorAll('.el-dialog')).find(el => el.offsetParent !== null);
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const errors = Array.from(document.querySelectorAll('.el-form-item__error'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const required = ['备案品种', '育苗开始日期', '育苗地点', '验收日期', '育苗人员信息表', '验收文件'];
          const requiredPresent = Object.fromEntries(required.map(label => [label, text(dialog).includes(label)]));
          const fields = Array.from((dialog || document).querySelectorAll('input, textarea, select'))
            .map((el) => ({
              tag: el.tagName,
              type: el.type || '',
              name: el.name || '',
              placeholder: el.placeholder || '',
              value: el.value || '',
              disabled: !!el.disabled,
              visible: el.offsetParent !== null,
              className: typeof el.className === 'string' ? el.className : '',
              parentText: text(el.parentElement).slice(0, 200),
            }));
          const fileInputs = Array.from((dialog || document).querySelectorAll('input[type=file]'))
            .map((el, idx) => ({
              idx,
              accept: el.accept || '',
              multiple: !!el.multiple,
              visible: el.offsetParent !== null,
              parentText: text(el.parentElement).slice(0, 200),
              grandText: text(el.parentElement?.parentElement).slice(0, 280),
            }));
          return {
            url: location.href,
            title: document.title,
            dialogText: text(dialog).slice(0, 2500),
            errors,
            requiredPresent,
            fields,
            fileInputs
          };
        }
        """
    )


async def snapshot_drawer_state(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const drawer = Array.from(document.querySelectorAll('.el-drawer, .el-drawer__wrapper'))
            .find(el => el.offsetParent !== null) || document;
          const errors = Array.from(drawer.querySelectorAll('.el-form-item__error, .el-alert__content'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const toast = Array.from(document.querySelectorAll('.el-message, .el-notification'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const requiredLabels = Array.from(drawer.querySelectorAll('.el-form-item.is-required .el-form-item__label'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const inputs = Array.from(drawer.querySelectorAll('input, textarea, .el-input__inner'))
            .filter(el => el.offsetParent !== null)
            .map(el => ({
              tag: el.tagName,
              value: el.value || '',
              placeholder: el.placeholder || '',
              className: typeof el.className === 'string' ? el.className : '',
            }))
            .slice(0, 120);
          return {
            title: document.title,
            body: text(drawer).slice(0, 9000),
            errors,
            toast,
            requiredLabels,
            inputs,
          };
        }
        """
    )


async def snapshot_submit_dialog(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const dialog = Array.from(document.querySelectorAll('.el-dialog, .el-message-box__wrapper'))
            .find(el => el.offsetParent !== null);
          const messages = Array.from(document.querySelectorAll('.el-message, .el-notification, .el-alert__content'))
            .filter(el => el.offsetParent !== null)
            .map(el => text(el));
          const fileInputs = dialog ? Array.from(dialog.querySelectorAll('input[type=file]')).map((el, idx) => ({
            idx,
            accept: el.accept || '',
            multiple: !!el.multiple,
            visible: el.offsetParent !== null,
          })) : [];
          return {
            dialogText: text(dialog).slice(0, 3000),
            messages,
            fileInputs,
            body: text(document.body).slice(0, 12000),
          };
        }
        """
    )


async def fill_select_by_label(page: Page, label: str, choice_text: str) -> dict[str, Any]:
    js = """
    async ({ label, choiceText }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      function findLabelNode(targetLabel) {
        const labels = Array.from(document.querySelectorAll('.el-form-item__label'));
        return labels.find(node => text(node).includes(targetLabel));
      }
      const labelNode = findLabelNode(label);
      if (!labelNode) return { ok: false, reason: 'label-not-found' };
      const formItem = labelNode.closest('.el-form-item');
      if (!formItem) return { ok: false, reason: 'form-item-not-found' };
      const trigger = formItem.querySelector('.el-select .el-input__inner, .el-cascader .el-input__inner');
      if (!trigger) return { ok: false, reason: 'trigger-not-found' };
      trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      trigger.click();
      await new Promise(resolve => setTimeout(resolve, 600));
      const options = Array.from(document.querySelectorAll('.el-select-dropdown__item, .el-cascader-node'))
        .filter(node => node.offsetParent !== null);
      const target = options.find(node => text(node).includes(choiceText));
      if (!target) {
        return {
          ok: false,
          reason: 'option-not-found',
          visibleOptions: options.map(node => text(node)).slice(0, 20),
        };
      }
      target.click();
      return { ok: true, chosen: text(target) };
    }
    """
    return await page.evaluate(js, {"label": label, "choiceText": choice_text})


async def set_plant_by_component(page: Page, plant_name: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        async ({ plantName }) => {
          const input = document.querySelector('.el-form-item input[placeholder="请输入备案品种"]');
          const plantComp = input?.parentElement?.parentElement?.parentElement?.__vue__;
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          if (!plantComp || !apply) return { ok: false, reason: 'plant-component-not-found' };
          await plantComp.getList(plantName);
          const target = (plantComp.list || []).find(item => (item.plantName || '').includes(plantName));
          if (!target) {
            return {
              ok: false,
              reason: 'plant-not-found',
              loaded: (plantComp.list || []).map(item => item.plantName || item.label).slice(0, 20),
            };
          }
          apply.plantChange(target);
          const field = apply.$refs?.form?.fields?.find(f => f.prop === 'plantId');
          if (field) {
            field.validateState = 'success';
            field.validateMessage = '';
            field.onFieldChange && field.onFieldChange();
          }
          return {
            ok: true,
            plantId: apply.form.plantId,
            plantName: apply.form.plantName,
            target,
          };
        }
        """,
        {"plantName": plant_name},
    )


async def fill_date_by_label(page: Page, label: str, value: str) -> dict[str, Any]:
    js = """
    ({ label, value }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      const labels = Array.from(document.querySelectorAll('.el-form-item__label'));
      const labelNode = labels.find(node => text(node).includes(label));
      if (!labelNode) return { ok: false, reason: 'label-not-found' };
      const formItem = labelNode.closest('.el-form-item');
      if (!formItem) return { ok: false, reason: 'form-item-not-found' };
      const input = formItem.querySelector('input');
      if (!input) return { ok: false, reason: 'input-not-found' };
      input.focus();
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
      return { ok: true, value: input.value };
    }
    """
    return await page.evaluate(js, {"label": label, "value": value})


async def set_city_by_component(page: Page, city_code: str, city_label: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ cityCode, cityLabel }) => {
          const input = document.querySelector('.el-form-item input[placeholder="请选择"]');
          const cityComp = input?.parentElement?.parentElement?.parentElement?.__vue__;
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const cultivation = apply?.$refs?.formCultivation;
          if (!cityComp || !apply || !cultivation) return { ok: false, reason: 'city-component-not-found' };
          cityComp.value = cityCode;
          cityComp.valueStr = cityCode;
          cultivation.handleAddressChange({ value: cityCode, label: cityLabel });
          const field = apply.$refs?.form?.fields?.find(f => f.prop === 'cityRegionId');
          if (field) {
            field.validateState = 'success';
            field.validateMessage = '';
            field.onFieldChange && field.onFieldChange();
          }
          const inputEl = input;
          if (inputEl) {
            inputEl.removeAttribute('readonly');
            inputEl.value = cityLabel;
            inputEl.dispatchEvent(new Event('input', { bubbles: true }));
            inputEl.dispatchEvent(new Event('change', { bubbles: true }));
            inputEl.setAttribute('readonly', 'readonly');
          }
          return {
            ok: true,
            cityRegionId: apply.form.cityRegionId,
            cityRegionName: apply.form.cityRegionName,
          };
        }
        """,
        {"cityCode": city_code, "cityLabel": city_label},
    )


async def fill_text_by_label(page: Page, label: str, value: str) -> dict[str, Any]:
    js = """
    ({ label, value }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      const labels = Array.from(document.querySelectorAll('.el-form-item__label'));
      const labelNode = labels.find(node => text(node).includes(label));
      if (!labelNode) return { ok: false, reason: 'label-not-found' };
      const formItem = labelNode.closest('.el-form-item');
      if (!formItem) return { ok: false, reason: 'form-item-not-found' };
      const input = formItem.querySelector('input, textarea');
      if (!input) return { ok: false, reason: 'input-not-found' };
      input.focus();
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('blur', { bubbles: true }));
      return { ok: true, value: input.value };
    }
    """
    return await page.evaluate(js, {"label": label, "value": value})


async def set_date_and_validate(page: Page, label: str, value: str, prop: str) -> dict[str, Any]:
    result = await fill_date_by_label(page, label, value)
    validate = await page.evaluate(
        """
        ({ prop }) => {
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const field = apply?.$refs?.form?.fields?.find(f => f.prop === prop);
          if (!field) return { ok: false, reason: 'field-not-found' };
          field.validateState = 'success';
          field.validateMessage = '';
          field.onFieldChange && field.onFieldChange();
          return { ok: true, validateState: field.validateState, validateMessage: field.validateMessage };
        }
        """,
        {"prop": prop},
    )
    return {"input": result, "validate": validate}


async def ensure_checkbox(page: Page, label_text: str) -> dict[str, Any]:
    js = """
    ({ labelText }) => {
      function text(node) {
        return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
      }
      const labels = Array.from(document.querySelectorAll('label, span, div'));
      const target = labels.find(node => node.offsetParent !== null && text(node).includes(labelText));
      if (!target) return { ok: false, reason: 'label-not-found' };
      const checkbox = target.closest('label')?.querySelector('input[type=checkbox]') || target.parentElement?.querySelector('input[type=checkbox]');
      if (!checkbox) return { ok: false, reason: 'checkbox-not-found' };
      if (!checkbox.checked) {
        checkbox.click();
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
      }
      return { ok: true, checked: checkbox.checked };
    }
    """
    return await page.evaluate(js, {"labelText": label_text})


async def upload_by_label(page: Page, label: str, file_path: Path, expected_prop: str) -> dict[str, Any]:
    file_path = file_path.resolve()
    form_item = page.locator(".el-form-item", has=page.locator(".el-form-item__label", has_text=label)).first
    input = form_item.locator("input[type=file]").first
    await input.set_input_files(str(file_path))
    await page.wait_for_timeout(2500)
    state = await page.evaluate(
        """
        ({ prop }) => {
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const field = apply?.$refs?.form?.fields?.find(f => f.prop === prop);
          const value = apply?.form?.[prop];
          return {
            value,
            fieldState: field ? { validateState: field.validateState, validateMessage: field.validateMessage } : null,
          };
        }
        """,
        {"prop": expected_prop},
    )
    return {"ok": True, "label": label, "file": str(file_path), "state": state}


async def set_attachment_validation_success(page: Page, prop: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ prop }) => {
          const formVue = document.querySelectorAll('.el-form')[1]?.__vue__;
          const apply = formVue?.$parent?.$parent;
          const field = apply?.$refs?.form?.fields?.find(f => f.prop === prop);
          if (!field) return { ok: false, reason: 'field-not-found' };
          field.validateState = 'success';
          field.validateMessage = '';
          field.onFieldChange && field.onFieldChange();
          return { ok: true, validateState: field.validateState, validateMessage: field.validateMessage, value: apply.form[prop] };
        }
        """,
        {"prop": prop},
    )


async def run_apply_wizard(page: Page) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    steps.append({"step": "click_apply_button", "result": await click_exact_button(page, "我要申请备案")})
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


async def fill_success_template(page: Page, template: dict[str, Any]) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ template }) => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const apply = getApply();
          if (!apply) return { ok: false, reason: 'apply-not-found' };
          Object.assign(apply.form, template);
          if (apply.$refs?.formCultivation?.handleAddressChange) {
            apply.$refs.formCultivation.handleAddressChange({
              value: template.cityRegionId,
              label: template.cityRegionName,
            });
          }
          if (apply.$refs?.formCultivation?.handleDeptChange) {
            apply.$refs.formCultivation.handleDeptChange(template.acceptanceDeptId);
          }
          for (const field of (apply.$refs?.form?.fields || [])) {
            if (!field?.prop) continue;
            const value = apply.form[field.prop];
            if (value !== '' && value !== null && value !== undefined) {
              field.validateState = '';
              field.validateMessage = '';
            }
          }
          return {
            ok: true,
            form: {
              deptId: apply.form.deptId,
              cityRegionId: apply.form.cityRegionId,
              cityRegionName: apply.form.cityRegionName,
              rangeStr: apply.form.rangeStr,
              seedlingSource: apply.form.seedlingSource,
              cultivateType: apply.form.cultivateType,
              cultivateDate: apply.form.cultivateDate,
              cultivateNum: apply.form.cultivateNum,
              cultivateArea: apply.form.cultivateArea,
              acceptanceNum: apply.form.acceptanceNum,
              cultivatePurpose: apply.form.cultivatePurpose,
              acceptanceDeptId: apply.form.acceptanceDeptId,
              acceptancePerson: apply.form.acceptancePerson,
              acceptanceDate: apply.form.acceptanceDate,
              batchNo: apply.form.batchNo,
            },
          };
        }
        """,
        {"template": template},
    )


async def expand_cultivation_form(page: Page) -> dict[str, Any]:
    drawer = page.locator(".el-drawer:visible, .el-drawer__wrapper:visible").last
    submit = drawer.get_by_role("button", name="信息纳入溯源系统")
    if await submit.count():
        await submit.first.click(force=True)
        await page.wait_for_timeout(3000)
        return {"ok": True, "method": "role"}
    return {"ok": False, "reason": "submit-not-found"}


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


def template_data_ref(runtime_data: dict[str, Any], ref: str) -> Any:
    current: Any = runtime_data
    for part in ref.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


async def execute_verified_new_application_flow(
    page: Page,
    artifacts: ArtifactWriter,
    runtime_data: dict[str, Any],
    personnel_file: Path,
    acceptance_file: Path,
    apply_file: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for step in TEMPLATE_BUNDLE.template.get("steps", []):
        action_name = step.get("action")
        step_id = step.get("id", action_name)
        if action_name == "run_apply_wizard":
            actions.extend(await run_apply_wizard(page))
        elif action_name == "select_drawer_option":
            label = step.get("args", {}).get("label", "")
            ref = step.get("args", {}).get("data_ref", "")
            option_value = template_data_ref(runtime_data, ref) if ref else None
            actions.append(
                {
                    "step": step_id,
                    "result": await select_drawer_option(page, label, str(option_value or "")),
                }
            )
        elif action_name == "ensure_drawer_checkbox":
            label_text = step.get("args", {}).get("label_text", "")
            actions.append({"step": step_id, "result": await ensure_drawer_checkbox(page, label_text)})
        elif action_name == "expand_cultivation_form":
            actions.append({"step": step_id, "result": await expand_cultivation_form(page)})
        elif action_name == "fill_success_template":
            ref = step.get("args", {}).get("data_ref", "")
            data = template_data_ref(runtime_data, ref) if ref else {}
            actions.append({"step": step_id, "result": await fill_success_template(page, data or {})})
        elif action_name == "upload_drawer_required_files":
            actions.append(
                {
                    "step": step_id,
                    "result": await upload_drawer_required_files(page, personnel_file, acceptance_file),
                }
            )
        elif action_name == "select_submit_dialog_dept":
            ref = step.get("args", {}).get("data_ref", "")
            dept_label = template_data_ref(runtime_data, ref) if ref else ""
            actions.append({"step": step_id, "result": await select_submit_dialog_dept(page, str(dept_label or ""))})
        elif action_name == "upload_submit_dialog_apply_file":
            actions.append({"step": step_id, "result": await upload_submit_dialog_apply_file(page, apply_file)})
        elif action_name == "submit_filing_dialog":
            actions.append({"step": step_id, "result": await submit_filing_dialog(page)})

        if step_id == "check_initial_promise":
            await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_initial_form.png"), full_page=True)
        elif step_id == "upload_required_files":
            await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_full_form.png"), full_page=True)
        elif step_id == "upload_apply_form":
            await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_submit_dialog.png"), full_page=True)

    await page.screenshot(path=str(artifacts.screenshots_dir / "verified_flow_final_result.png"), full_page=True)
    final_state = await snapshot_submit_dialog(page)
    return actions, final_state


async def dismiss_overlays(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const visible = Array.from(document.querySelectorAll('body > div'))
            .filter(node => node.offsetParent !== null)
            .map(node => ({
              cls: typeof node.className === 'string' ? node.className : '',
              text: (node.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
            }));
          document.body.click();
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
          return { ok: true, visible };
        }
        """
    )


async def submit_dialog(page: Page) -> dict[str, Any]:
    found = await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const buttons = Array.from(document.querySelectorAll('button')).filter(btn => btn.offsetParent !== null);
          const target = buttons.find(btn => text(btn).includes('信息纳入溯源系统'));
          if (!target) {
            return { ok: false, visibleButtons: buttons.map(text) };
          }
          target.click();
          return { ok: true, text: text(target) };
        }
        """
    )
    if not found.get("ok"):
        raise RuntimeError(f"未找到“信息纳入溯源系统”提交按钮: {found.get('visibleButtons', [])}")
    await page.wait_for_timeout(2500)
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const messages = Array.from(document.querySelectorAll('.el-message, .el-notification, .el-message-box'))
            .filter(node => node.offsetParent !== null)
            .map(node => text(node))
            .filter(Boolean);
          const errors = Array.from(document.querySelectorAll('.el-form-item__error'))
            .filter(node => node.offsetParent !== null)
            .map(node => text(node))
            .filter(Boolean);
          return {
            messages,
            errors,
            bodySnippet: document.body.innerText.slice(0, 2000)
          };
        }
        """
    )


def success_from_submission(result: dict[str, Any]) -> bool:
    joined = " ".join(result.get("messages", []))
    body = result.get("bodySnippet", "")
    error_text = " ".join(result.get("errors", []))

    if any(
        token in " ".join([joined, body, error_text])
        for token in [
            "操作失败",
            "无法新增备案信息",
            "primaryValues array can not be null or empty",
            "请先勾选",
            "失败",
            "异常",
        ]
    ):
        return False

    if any(key in joined for key in ["成功", "已提交", "提交成功", "保存成功"]):
        return True
    if "提交完成，待备案登记/监管单位登记备案" in body:
        return True
    if result.get("errors"):
        return False
    return False


def classify_submission_result(
    submit_result: dict[str, Any],
    network_events: list[dict[str, Any]],
    apply_state: dict[str, Any],
) -> dict[str, Any]:
    joined = " ".join(submit_result.get("messages", []))
    body = submit_result.get("bodySnippet", "")
    all_text = " ".join([joined, body, " ".join(submit_result.get("errors", []))])

    registration_events = [
        item for item in network_events if "/prod-api/zwsy/registration/apply/" in item.get("url", "")
    ]
    response_events = [item for item in registration_events if item.get("type") == "response"]
    request_events = [item for item in registration_events if item.get("type") == "request"]

    latest_apply_response = response_events[-1] if response_events else None
    latest_apply_request = request_events[-1] if request_events else None
    submit_request = None
    submit_response = None
    for request in reversed(request_events):
        if request.get("method") == "POST" and any(
            request.get("url", "").endswith(suffix) for suffix in ["/save", "/update", "/dept"]
        ):
            submit_request = request
            break
    if submit_request:
        target_url = submit_request.get("url")
        for response in reversed(response_events):
            if response.get("url") == target_url:
                submit_response = response
                break
    submit_response_body = (submit_response or {}).get("body", "")

    if any(token in " ".join([all_text, submit_response_body]) for token in ["当前用户无机构信息，无法新增备案信息", "无法新增备案信息"]):
        return {
            "success": False,
            "category": "account_policy_block",
            "reason": "账号缺少新增备案所需机构信息，新增分支被后台拒绝",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if any(token in " ".join([all_text, submit_response_body]) for token in ["primaryValues array can not be null or empty"]):
        return {
            "success": False,
            "category": "backend_update_primary_key_error",
            "reason": "编辑更新分支触发后台主键依赖异常",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if any(token in all_text for token in ['请先勾选"本人承诺', "请先勾选"]):
        return {
            "success": False,
            "category": "front_validation_missing_commitment",
            "reason": "提交前未满足承诺勾选条件",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if submit_response:
        body_text = submit_response.get("body", "")
        if any(token in body_text for token in ["\"code\":200", "\"msg\":\"操作成功\"", "\"msg\":\"提交成功\""]):
            category = "network_success"
            reason = "后台接口响应成功"
            if submit_request and submit_request.get("url", "").endswith("/dept"):
                category = "network_success_final_filing"
                reason = "最终备案提交接口响应成功"
            return {
                "success": True,
                "category": category,
                "reason": reason,
                "latest_request": submit_request,
                "latest_response": submit_response,
            }

    if success_from_submission(submit_result):
        return {
            "success": True,
            "category": "ui_success",
            "reason": "页面成功提示命中",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    if apply_state.get("ok") and apply_state.get("isPendingPayment") and apply_state.get("isShowSaveButtons"):
        return {
            "success": False,
            "category": "pending_payment_modify_mode",
            "reason": "已进入待支付记录的修改态，需走提交申请/支付分支而非 update",
            "latest_request": submit_request or latest_apply_request,
            "latest_response": submit_response or latest_apply_response,
        }

    return {
        "success": False,
        "category": "unknown_failure",
        "reason": "未命中已知成功或失败模式",
        "latest_request": submit_request or latest_apply_request,
        "latest_response": submit_response or latest_apply_response,
    }


def write_structured_stage2_report(
    artifacts: ArtifactWriter,
    profile: ModelProfile,
    progress: ProgressManager,
    *,
    page_url: str,
    success: bool,
    classification: dict[str, Any],
    attempts: list[dict[str, Any]],
    generated_files: dict[str, Path],
    discovery_result: object | None = None,
    discovery_paths: dict[str, Path] | None = None,
    notes: list[str] | None = None,
) -> None:
    progress_snapshot = adapt_progress_snapshot(progress.snapshot)
    report_payload = {
        "summary": {
            "run_id": artifacts.run_dir.name,
            "status": "completed" if success else "failed",
            "project_name": "AI Agent 软件自动化评测平台第二阶段原型",
            "template_name": TEMPLATE_BUNDLE.name,
            "started_at": progress.snapshot.started_at,
            "finished_at": progress.snapshot.updated_at,
            "stop_reason": classification.get("reason"),
            "next_action": progress.snapshot.next_action,
            "counts": [
                {"label": "attempts", "value": len(attempts)},
                {"label": "template_steps", "value": len(TEMPLATE_BUNDLE.template.get("steps", []))},
            ],
            "facts": [
                {"label": "page_url", "value": page_url},
                {"label": "model_name", "value": profile.name},
                {"label": "classification_category", "value": classification.get("category")},
            ],
        },
        "page_entries": build_report_page_entries(
            discovery_result,
            TEMPLATE_BUNDLE.template.get("page_entry", {}).get("name", "页面入口"),
            page_url,
            success,
        ),
        "feature_points": build_report_feature_points(
            discovery_result,
            TEMPLATE_BUNDLE.template.get("feature_point", {}).get("name", "功能点"),
            classification.get("reason"),
            success,
        ),
        "success_items": [
            {
                "name": "线上申请备案模板样本",
                "status": "passed",
                "summary": classification.get("reason"),
            }
        ]
        if success
        else [],
        "failure_items": []
        if success
        else [
            {
                "name": "线上申请备案模板样本",
                "status": "failed",
                "summary": classification.get("reason"),
            }
        ],
        "project_assets": [
            {
                "name": "Template Snapshot",
                "status": "generated",
                "artifacts": [
                    {"label": "template_snapshot.json", "path": str(artifacts.run_dir / "template_snapshot.json")}
                ],
            },
            {
                "name": "Runtime Data",
                "status": "generated",
                "artifacts": [
                    {"label": "runtime_data.json", "path": str(artifacts.run_dir / "runtime_data.json")}
                ],
            },
            {
                "name": "Generated Attachments",
                "status": "generated",
                "artifacts": [
                    {"label": key, "path": str(path)} for key, path in generated_files.items()
                ],
            },
        ],
        "network_highlights": [
            {
                "name": f"Attempt {item.get('attempt')}",
                "status": item.get("classification", {}).get("category", "unknown"),
                "summary": item.get("classification", {}).get("reason", ""),
            }
            for item in attempts
        ],
        "efficiency_observations": [
            {"label": "attempt_count", "value": len(attempts)},
            {"label": "platform_status", "value": progress.snapshot.overall_status},
        ],
        "notes": (notes or []) + [
            f"当前平台级状态：{progress.snapshot.overall_status} / {progress.snapshot.current_phase}",
            format_status_line(progress.snapshot),
        ],
    }
    if discovery_paths:
        report_payload["project_assets"].append(
            {
                "name": "Discovery Outputs",
                "status": "generated",
                "artifacts": [
                    {"label": key, "path": str(path)}
                    for key, path in discovery_paths.items()
                ],
            }
        )
    iteration_artifacts = write_iteration_artifacts(
        artifacts.run_dir,
        run_report=report_payload,
        status_snapshot=progress_snapshot,
        attempts=attempts,
    )
    report_payload["project_assets"].append(
        {
            "name": "Iteration Outputs",
            "status": "generated",
            "artifacts": [
                {"label": "failure_clusters.json", "path": str(artifacts.run_dir / "failure_clusters.json")},
                {"label": "retry_plan.json", "path": str(artifacts.run_dir / "retry_plan.json")},
                {"label": "promotion_candidates.json", "path": str(artifacts.run_dir / "promotion_candidates.json")},
            ],
        }
    )
    report_payload["failure_clusters"] = build_report_failure_clusters(iteration_artifacts)
    report_payload["promotion_candidates"] = build_report_promotion_candidates(iteration_artifacts)
    report_payload["extra_sections"] = build_retry_plan_section(iteration_artifacts)
    artifacts.write_text("reports/run_report.md", render_run_report_markdown(report_payload))
    artifacts.write_text("reports/progress_view.md", render_progress_markdown(progress_snapshot))
    artifacts.write_text("reports/progress_view.txt", render_progress_text(progress_snapshot))


async def collect_registration_list(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        async () => {
          const tokenMatch = (document.cookie || '').match(/(?:^|;\\s*)Admin-Token=([^;]+)/);
          const token = tokenMatch ? decodeURIComponent(tokenMatch[1]) : '';
          const headers = token ? { Authorization: `Bearer ${token}` } : {};
          const response = await fetch('/prod-api/zwsy/registration/apply/list?pageNum=1&pageSize=20', {
            headers,
            credentials: 'include',
          });
          const payload = await response.json();
          return payload;
        }
        """
    )


async def open_pending_record(page: Page, record_id: str, enter_modify_mode: bool) -> dict[str, Any]:
    return await page.evaluate(
        """
        async ({ recordId, enterModifyMode }) => {
          function getApply() {
            const root = document.querySelector('.app-main')?.__vue__;
            const online = root?.$children?.find(x => x?.$options?.name === 'RegistrationOnline') ||
              Array.from(document.querySelectorAll('*')).map(n => n.__vue__).find(x => x?.$options?.name === 'RegistrationOnline');
            return online?.$refs?.apply || null;
          }
          const apply = getApply();
          if (!apply) return { ok: false, reason: 'apply-not-found' };
          apply.show(recordId);
          await new Promise(resolve => setTimeout(resolve, 2200));
          if (enterModifyMode) {
            apply.handleEdit();
            const checkbox = Array.from(document.querySelectorAll('input[type=checkbox]')).find(el => el.offsetParent !== null);
            if (checkbox && !checkbox.checked) {
              checkbox.click();
              checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
          } else {
            apply.handlePendingEdit();
            const checkbox = Array.from(document.querySelectorAll('input[type=checkbox]')).find(el => el.offsetParent !== null);
            if (checkbox && !checkbox.checked) {
              checkbox.click();
              checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }
          await new Promise(resolve => setTimeout(resolve, 600));
          return { ok: true };
        }
        """,
        {"recordId": record_id, "enterModifyMode": enter_modify_mode},
    )


async def click_button_by_text(page: Page, text_fragment: str) -> dict[str, Any]:
    return await page.evaluate(
        """
        ({ textFragment }) => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const buttons = Array.from(document.querySelectorAll('button')).filter(btn => btn.offsetParent !== null);
          const target = buttons.find(btn => text(btn).includes(textFragment));
          if (!target) {
            return { ok: false, reason: 'button-not-found', visibleButtons: buttons.map(text) };
          }
          target.click();
          return { ok: true, text: text(target) };
        }
        """,
        {"textFragment": text_fragment},
    )


async def click_modify_button(page: Page) -> dict[str, Any]:
    return await click_button_by_text(page, "修")


async def click_first_continue_action(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          function text(node) {
            return (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
          }
          const rows = Array.from(document.querySelectorAll('.el-table__body-wrapper tbody tr'));
          for (let idx = 0; idx < rows.length; idx += 1) {
            const row = rows[idx];
            const buttons = Array.from(row.querySelectorAll('button')).filter(btn => btn.offsetParent !== null);
            const target = buttons.find(btn => text(btn).includes('继续操作'));
            if (!target) continue;
            const cells = Array.from(row.querySelectorAll('td')).map(td => text(td));
            target.click();
            return { ok: true, rowIndex: idx, cells };
          }
          return { ok: false, reason: 'continue-action-not-found' };
        }
        """
    )


async def detect_submission_path(page: Page) -> dict[str, Any]:
    state = await get_apply_state(page)
    path = "new_application"
    if state.get("ok") and state.get("isEdit"):
        if state.get("isPendingPayment"):
            path = "pending_payment_resubmit"
        elif state.get("isPendingEditMode"):
            path = "pending_edit_update"
        else:
            path = "record_detail_view"
    return {"path": path, "state": state}


async def run_single_profile(profile: ModelProfile, cdp_url: str, max_attempts: int) -> Path:
    artifacts = ArtifactWriter(ARTIFACT_ROOT, profile.name)
    runtime_data = TemplateDataFactory(artifacts.run_dir.name).build(
        baseline=SUCCESS_BASELINE,
        schema=TEMPLATE_BUNDLE.data_schema,
    )
    progress = ProgressManager(
        run_id=artifacts.run_dir.name,
        output_dir=artifacts.run_dir,
        template_name=TEMPLATE_BUNDLE.name,
        model_name=profile.name,
        project_name="AI Agent 软件自动化评测平台第二阶段原型",
    )
    progress.start_phase(
        "preflight",
        phase_label="预检",
        message="初始化线上备案申请模板样本运行",
        next_action="写入运行时快照并准备生成附件",
        stats={"template_steps": len(TEMPLATE_BUNDLE.template.get("steps", []))},
    )
    artifacts.write_json(
        "run_meta.json",
        {
            "profile": {
                "name": profile.name,
                "env_file": str(profile.env_file),
                "base_url": profile.base_url,
                "model": profile.model,
            },
            "cdp_url": cdp_url,
            "started_at": datetime.now().isoformat(),
            "max_attempts": max_attempts,
        },
    )
    artifacts.write_json("template_snapshot.json", TEMPLATE_BUNDLE.template)
    artifacts.write_json("baseline_snapshot.json", SUCCESS_BASELINE)
    artifacts.write_json("runtime_data.json", runtime_data)

    generated_files = build_default_generated_files(artifacts.generated_dir, profile.name)
    person_pdf = generated_files["personnel_file"]
    accept_pdf = generated_files["acceptance_file"]
    apply_pdf = generated_files["apply_file"]
    progress.complete_phase(
        "preflight",
        phase_label="预检",
        message="运行时数据和生成附件已准备完成",
        next_action="进入发现阶段并连接浏览器执行受控遍历",
    )
    progress.start_phase(
        "discovery",
        phase_label="发现",
        message="先生成模板播种结果，并尝试升级到真实页面受控遍历",
        next_action="连接浏览器并 enrich discovery 结果",
    )
    discovery_result, discovery_paths = build_discovery_seed(artifacts)

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("未发现已连接的浏览器上下文")
            pages: list[Page] = []
            for ctx in contexts:
                pages.extend(ctx.pages)
            if not pages:
                raise RuntimeError("未发现已登录页面")

            target = None
            for page in pages:
                if "record/online" in page.url:
                    target = page
                    break
            target = target or pages[0]
            page = target
            await page.bring_to_front()
            await page.wait_for_load_state("domcontentloaded")
            if "record/online" not in page.url:
                await page.goto(ONLINE_RECORD_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

            discovery_result, discovery_paths = await build_live_discovery(page, artifacts)
            progress.complete_phase(
                "discovery",
                phase_label="发现",
                message="已完成真实页面受控遍历并生成页面入口清单和功能点清单",
                next_action="进入验证阶段执行模板样本",
                stats={
                    "page_entries_discovered": len(getattr(discovery_result, "page_entries", [])),
                    "feature_points_discovered": len(getattr(discovery_result, "feature_points", [])),
                },
            )
            progress.start_phase(
                "verification",
                phase_label="验证",
                round_kind="verification",
                round_index=1,
                round_label="验证第 1 轮",
                message="连接可见浏览器并准备执行模板样本",
                next_action="执行线上备案申请主路径",
            )

            artifacts.write_json(
                "page_entry.json",
                {
                    "url": page.url,
                    "title": await page.title(),
                    "pages": [p.url for p in pages],
                },
            )
            await page.screenshot(path=str(artifacts.screenshots_dir / "page_before_apply.png"), full_page=True)

            network_events: list[dict[str, Any]] = []

            async def on_request(request: Any) -> None:
                url = request.url
                if "/prod-api/zwsy/registration/apply/" not in url:
                    return
                payload = {
                    "type": "request",
                    "method": request.method,
                    "url": url,
                    "post_data": request.post_data,
                }
                network_events.append(payload)
                artifacts.append_network(payload)

            async def on_response(response: Any) -> None:
                url = response.url
                if "/prod-api/zwsy/registration/apply/" not in url:
                    return
                try:
                    body = await response.text()
                except Exception as exc:  # pragma: no cover - best effort capture
                    body = f"<response.text error: {exc}>"
                payload = {
                    "type": "response",
                    "status": response.status,
                    "url": url,
                    "body": body,
                }
                network_events.append(payload)
                artifacts.append_network(payload)

            page.on("request", on_request)
            page.on("response", on_response)

            dialog = await find_open_panel(page)
            if dialog is None:
                await click_apply_button(page)
                dialog = await wait_for_dialog(page)
            await page.screenshot(path=str(artifacts.screenshots_dir / "dialog_opened.png"), full_page=True)
            before_state = await snapshot_dialog_state(page)
            artifacts.write_json("dialog_before.json", before_state)

            last_errors: list[str] = before_state.get("errors", [])
            final_submit: dict[str, Any] | None = None
            attempt_records: list[dict[str, Any]] = []

            for attempt in range(1, max_attempts + 1):
                actions: list[dict[str, Any]] = []
                timestamp = datetime.now().isoformat()
                network_start = len(network_events)
                submit_result: dict[str, Any]

                try:
                    actions.append({"step": "reset_online_apply_page", "result": await reset_online_apply_page(page)})
                    actions.append({"step": "close_visible_panel", "result": await close_visible_panel(page)})
                    await page.wait_for_load_state("domcontentloaded")
                    dialog = await find_open_panel(page)
                    if dialog is None:
                        await click_apply_button(page)
                        dialog = await wait_for_dialog(page)
                    path_info = await detect_submission_path(page)
                    actions.append({"step": "detect_submission_path", "result": path_info})

                    if path_info["path"] == "new_application":
                        progress.start_step(
                            "verification",
                            phase_label="验证",
                            round_kind="verification",
                            round_index=attempt,
                            round_label=f"验证第 {attempt} 轮",
                            step_key="execute_template",
                            step_label="执行模板回放",
                            target_kind="feature_point",
                            target_id="online_apply",
                            target_label="线上申请备案",
                            message="按模板执行线上备案申请成功路径",
                        )
                        actions.append({"step": "dialog_visible", "ok": await dialog.is_visible()})
                        flow_actions, final_state = await execute_verified_new_application_flow(
                            page,
                            artifacts,
                            runtime_data,
                            person_pdf,
                            accept_pdf,
                            apply_pdf,
                        )
                        actions.extend(flow_actions)
                        submit_result = {
                            "messages": final_state.get("messages", []),
                            "errors": [],
                            "bodySnippet": final_state.get("body", ""),
                        }
                    else:
                        actions.append({"step": "click_first_continue_action", "result": await click_first_continue_action(page)})
                        await page.wait_for_timeout(1500)
                        after_click_state = await get_apply_state(page)
                        actions.append({"step": "after_click_apply_state", "result": after_click_state})
                        registration_list = await collect_registration_list(page)
                        artifacts.write_json("registration_list_snapshot.json", registration_list)
                        rows = registration_list.get("data", []) if isinstance(registration_list, dict) else []
                        pending_candidates = [
                            row
                            for row in rows
                            if str(row.get("auditStatus")) == "0"
                        ]
                        actions.append(
                            {
                                "step": "pending_candidates",
                                "result": {
                                    "count": len(pending_candidates),
                                    "ids": [row.get("id") for row in pending_candidates[:10]],
                                },
                            }
                        )
                        selected = None
                        for row in pending_candidates:
                            if row.get("deptId"):
                                selected = row
                                break
                        selected = selected or (pending_candidates[0] if pending_candidates else None)
                        if selected is None:
                            raise RuntimeError("未发现可继续处理的待提交记录")

                        enter_modify_mode = after_click_state.get("isPendingPayment", False) or not bool(selected.get("deptId"))
                        if enter_modify_mode:
                            actions.append(
                                {
                                    "step": "click_modify_button",
                                    "result": await click_modify_button(page),
                                }
                            )
                            await page.wait_for_timeout(800)
                            actions.append(
                                {
                                    "step": "ensure_checkbox_after_modify",
                                    "result": await ensure_checkbox(page, "本人承诺"),
                                }
                            )
                        elif not after_click_state.get("ok"):
                            actions.append(
                                {
                                    "step": "open_pending_record_fallback",
                                    "result": await open_pending_record(page, str(selected["id"]), enter_modify_mode),
                                }
                            )
                        actions.append(
                            {
                                "step": "selected_record",
                                "result": {
                                    "id": selected.get("id"),
                                    "deptId": selected.get("deptId"),
                                    "auditStatus": selected.get("auditStatus"),
                                    "enter_modify_mode": enter_modify_mode,
                                },
                            }
                        )

                    await page.screenshot(
                        path=str(artifacts.screenshots_dir / f"attempt_{attempt:02d}_before_submit.png"),
                        full_page=True,
                    )
                    if path_info["path"] != "new_application":
                        submit_result = await submit_dialog(page)
                    final_submit = submit_result
                except (PlaywrightError, RuntimeError) as exc:
                    submit_result = {
                        "messages": [],
                        "errors": [f"{type(exc).__name__}: {exc}"],
                        "bodySnippet": "",
                    }

                after_state = await snapshot_dialog_state(page)
                apply_state = await get_apply_state(page)
                relevant_network = network_events[network_start:]
                classification = classify_submission_result(submit_result, relevant_network, apply_state)
                await page.screenshot(
                    path=str(artifacts.screenshots_dir / f"attempt_{attempt:02d}_after_submit.png"),
                    full_page=True,
                )

                payload = {
                    "attempt": attempt,
                    "timestamp": timestamp,
                    "actions": actions,
                    "submit_result": submit_result,
                    "apply_state": apply_state,
                    "classification": classification,
                    "network_events": relevant_network,
                    "dialog_state": after_state,
                }
                attempt_records.append(payload)
                artifacts.append_attempt(payload)
                artifacts.write_json(f"dialog_after_attempt_{attempt:02d}.json", after_state)
                artifacts.write_json(f"apply_state_after_attempt_{attempt:02d}.json", apply_state)
                artifacts.write_json(f"classification_attempt_{attempt:02d}.json", classification)

                current_errors = after_state.get("errors", [])
                if classification["success"]:
                    progress.complete_step(
                        "verification",
                        phase_label="验证",
                        round_kind="verification",
                        round_index=attempt,
                        round_label=f"验证第 {attempt} 轮",
                        step_key="execute_template",
                        step_label="执行模板回放",
                        target_kind="feature_point",
                        target_id="online_apply",
                        target_label="线上申请备案",
                        message=classification["reason"],
                        next_action="生成运行报告",
                        stats={"verification_successes": 1},
                    )
                    progress.complete_phase(
                        "verification",
                        phase_label="验证",
                        message="模板样本执行成功",
                        next_action="输出报告与运行态视图",
                    )
                    report = [
                        f"# 线上申请备案提交结果 - {profile.name}",
                        "",
                        f"- 运行目录: `{artifacts.run_dir}`",
                        f"- 页面: `{page.url}`",
                        f"- 提交结论: 成功",
                        f"- 判定类别: `{classification['category']}`",
                        f"- 原因: `{classification['reason']}`",
                        f"- 提示消息: `{'; '.join(submit_result.get('messages', [])) or '无显式消息'}`",
                    ]
                    artifacts.write_text("final_report.md", "\n".join(report))
                    write_structured_stage2_report(
                        artifacts,
                        profile,
                        progress,
                        page_url=page.url,
                        success=True,
                        classification=classification,
                        attempts=attempt_records,
                        generated_files=generated_files,
                        discovery_result=discovery_result,
                        discovery_paths=discovery_paths,
                        notes=["真实执行样本已成功完成最终备案提交。"],
                    )
                    return artifacts.run_dir

                if classification["category"] in {
                    "account_policy_block",
                    "backend_update_primary_key_error",
                    "pending_payment_modify_mode",
                }:
                    progress.fail_step(
                        "verification",
                        phase_label="验证",
                        round_kind="verification",
                        round_index=attempt,
                        round_label=f"验证第 {attempt} 轮",
                        step_key="execute_template",
                        step_label="执行模板回放",
                        target_kind="feature_point",
                        target_id="online_apply",
                        target_label="线上申请备案",
                        message=classification["reason"],
                        next_action="输出阻塞报告",
                        stats={"verification_failures": 1},
                    )
                    progress.fail_phase(
                        "verification",
                        phase_label="验证",
                        message="模板样本执行被阻塞",
                        next_action="等待人工处理或后续归因",
                    )
                    report = [
                        f"# 线上申请备案提交结果 - {profile.name}",
                        "",
                        f"- 运行目录: `{artifacts.run_dir}`",
                        f"- 页面: `{page.url}`",
                        f"- 提交结论: 未成功，已识别阻塞类型",
                        f"- 判定类别: `{classification['category']}`",
                        f"- 原因: `{classification['reason']}`",
                        f"- 当前错误: `{'; '.join(current_errors) or '无'}`",
                        f"- 最近消息: `{'; '.join(submit_result.get('messages', [])) or '无'}`",
                    ]
                    artifacts.write_text("final_report.md", "\n".join(report))
                    write_structured_stage2_report(
                        artifacts,
                        profile,
                        progress,
                        page_url=page.url,
                        success=False,
                        classification=classification,
                        attempts=attempt_records,
                        generated_files=generated_files,
                        discovery_result=discovery_result,
                        discovery_paths=discovery_paths,
                        notes=["真实执行样本命中了已识别的阻塞类型。"],
                    )
                    return artifacts.run_dir

                if current_errors == last_errors:
                    progress.fail_step(
                        "verification",
                        phase_label="验证",
                        round_kind="verification",
                        round_index=attempt,
                        round_label=f"验证第 {attempt} 轮",
                        step_key="execute_template",
                        step_label="执行模板回放",
                        target_kind="feature_point",
                        target_id="online_apply",
                        target_label="线上申请备案",
                        message="错误集未继续收敛",
                        next_action="输出失败报告",
                    )
                    report = [
                        f"# 线上申请备案提交结果 - {profile.name}",
                        "",
                        f"- 运行目录: `{artifacts.run_dir}`",
                        f"- 页面: `{page.url}`",
                        f"- 提交结论: 未成功，错误集未继续收敛",
                        f"- 判定类别: `{classification['category']}`",
                        f"- 当前错误: `{'; '.join(current_errors) or '无'}`",
                        f"- 最近消息: `{'; '.join(submit_result.get('messages', [])) or '无'}`",
                    ]
                    artifacts.write_text("final_report.md", "\n".join(report))
                    write_structured_stage2_report(
                        artifacts,
                        profile,
                        progress,
                        page_url=page.url,
                        success=False,
                        classification=classification,
                        attempts=attempt_records,
                        generated_files=generated_files,
                        discovery_result=discovery_result,
                        discovery_paths=discovery_paths,
                        notes=["真实执行样本未继续收敛，已提前结束当前 run。"],
                    )
                    return artifacts.run_dir

                last_errors = current_errors

            report = [
                f"# 线上申请备案提交结果 - {profile.name}",
                "",
                f"- 运行目录: `{artifacts.run_dir}`",
                f"- 页面: `{page.url}`",
                f"- 提交结论: 达到最大尝试次数仍未成功",
                f"- 最终错误: `{'; '.join(last_errors) or '无'}`",
                f"- 最近消息: `{'; '.join((final_submit or {}).get('messages', [])) or '无'}`",
            ]
            artifacts.write_text("final_report.md", "\n".join(report))
            progress.fail_phase(
                "verification",
                phase_label="验证",
                message="达到最大尝试次数仍未成功",
                next_action="输出失败报告",
            )
            write_structured_stage2_report(
                artifacts,
                profile,
                progress,
                page_url=page.url,
                success=False,
                classification={
                    "category": "max_attempts_exhausted",
                    "reason": "达到最大尝试次数仍未成功",
                },
                attempts=attempt_records,
                generated_files=generated_files,
                discovery_result=discovery_result,
                discovery_paths=discovery_paths,
                notes=["真实执行样本耗尽最大尝试次数。"],
            )
            return artifacts.run_dir
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            if progress.snapshot.phase_statuses.get("discovery") != "completed":
                progress.complete_phase(
                    "discovery",
                    phase_label="发现",
                    message="浏览器不可用，保留模板播种 discovery 结果",
                    next_action="输出受限发现结果并结束验证",
                    stats={
                        "page_entries_discovered": len(getattr(discovery_result, "page_entries", [])),
                        "feature_points_discovered": len(getattr(discovery_result, "feature_points", [])),
                    },
                )
            progress.fail_phase(
                "verification",
                phase_label="验证",
                message=error_text,
                next_action="检查 CDP 浏览器、远程调试端口和登录会话",
            )
            classification = {
                "category": "environment_bootstrap_failure",
                "reason": error_text,
            }
            report = [
                f"# 线上申请备案提交结果 - {profile.name}",
                "",
                f"- 运行目录: `{artifacts.run_dir}`",
                f"- 页面: `{ONLINE_RECORD_URL}`",
                f"- 提交结论: 未启动执行链路",
                f"- 判定类别: `{classification['category']}`",
                f"- 原因: `{classification['reason']}`",
            ]
            artifacts.write_text("final_report.md", "\n".join(report))
            write_structured_stage2_report(
                artifacts,
                profile,
                progress,
                page_url=ONLINE_RECORD_URL,
                success=False,
                classification=classification,
                attempts=[],
                generated_files=generated_files,
                discovery_result=discovery_result,
                discovery_paths=discovery_paths,
                notes=["验证阶段在连接浏览器或接管已登录会话之前失败。"],
            )
            return artifacts.run_dir
        finally:
            if browser is not None:
                await browser.close()


async def main() -> None:
    profiles = load_stage2_model_profiles(DEFAULT_ENV_FILES)
    if not profiles:
        raise RuntimeError("未从 demo 目录加载到模型配置")

    cdp_url = os.getenv("SUYUAN_CDP_URL", DEFAULT_CDP_URL)
    max_attempts = int(os.getenv("SUYUAN_MAX_ATTEMPTS", "3"))
    results = []
    for profile in profiles:
        run_dir = await run_single_profile(profile, cdp_url, max_attempts)
        results.append({"model": profile.name, "run_dir": str(run_dir)})

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
