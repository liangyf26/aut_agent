from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.verification.suyuan_shared_actions import (  # noqa: E402
    register_suyuan_wizard_drawer_actions,
)
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData  # noqa: E402


class _FakeRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register(self, action_name: str, handler: object) -> None:
        self.handlers[action_name] = handler


class _FakePage:
    pass


def test_register_suyuan_wizard_drawer_actions_registers_expected_actions() -> None:
    registry = _FakeRegistry()

    register_suyuan_wizard_drawer_actions(registry)

    assert sorted(registry.handlers) == [
        "ensure_drawer_checkbox",
        "expand_cultivation_form",
        "run_apply_wizard",
        "select_drawer_option",
    ]


def test_register_suyuan_wizard_drawer_actions_returns_same_registry_instance() -> None:
    registry = _FakeRegistry()

    returned = register_suyuan_wizard_drawer_actions(registry)

    assert returned is registry


def test_select_drawer_option_handler_reads_runtime_ref_and_passes_string(monkeypatch) -> None:
    registry = _FakeRegistry()
    register_suyuan_wizard_drawer_actions(registry)
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={"initial_form": {"plant_name": "番茄"}},
        generated_files={},
    )

    calls: list[tuple[str, str]] = []

    async def fake_select_drawer_option(page: object, label: str, option_keyword: str) -> dict[str, object]:
        calls.append((label, option_keyword))
        return {"ok": True, "label": label, "selected": option_keyword}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_shared_actions.select_drawer_option",
        fake_select_drawer_option,
    )

    result = asyncio.run(
        registry.handlers["select_drawer_option"](
            _FakePage(),
            object(),
            runtime,
            {
                "args": {
                    "label": "备案品种",
                    "data_ref": "initial_form.plant_name",
                }
            },
        )
    )

    assert calls == [("备案品种", "番茄")]
    assert result == {"ok": True, "label": "备案品种", "selected": "番茄"}


def test_select_drawer_option_handler_stringifies_missing_runtime_ref(monkeypatch) -> None:
    registry = _FakeRegistry()
    register_suyuan_wizard_drawer_actions(registry)
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={},
    )

    calls: list[tuple[str, str]] = []

    async def fake_select_drawer_option(page: object, label: str, option_keyword: str) -> dict[str, object]:
        calls.append((label, option_keyword))
        return {"ok": False, "label": label, "reason": "option-not-found", "candidates": []}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_shared_actions.select_drawer_option",
        fake_select_drawer_option,
    )

    result = asyncio.run(
        registry.handlers["select_drawer_option"](
            _FakePage(),
            object(),
            runtime,
            {
                "args": {
                    "label": "备案类型",
                    "data_ref": "initial_form.register_type_text",
                }
            },
        )
    )

    assert calls == [("备案类型", "")]
    assert result == {
        "ok": False,
        "label": "备案类型",
        "reason": "option-not-found",
        "candidates": [],
    }


def test_checkbox_and_expand_handlers_forward_label_and_page(monkeypatch) -> None:
    registry = _FakeRegistry()
    register_suyuan_wizard_drawer_actions(registry)
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})
    page = _FakePage()

    checkbox_calls: list[str] = []
    expand_calls: list[object] = []

    async def fake_checkbox(target_page: object, label_text: str) -> dict[str, object]:
        assert target_page is page
        checkbox_calls.append(label_text)
        return {"ok": True, "method": "label"}

    async def fake_expand(target_page: object) -> dict[str, object]:
        expand_calls.append(target_page)
        return {"ok": True, "method": "role"}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_shared_actions.ensure_drawer_checkbox",
        fake_checkbox,
    )
    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_shared_actions.expand_cultivation_form",
        fake_expand,
    )

    checkbox_result = asyncio.run(
        registry.handlers["ensure_drawer_checkbox"](
            page,
            object(),
            runtime,
            {"args": {"label_text": "本人承诺"}},
        )
    )
    expand_result = asyncio.run(
        registry.handlers["expand_cultivation_form"](
            page,
            object(),
            runtime,
            {"args": {}},
        )
    )

    assert checkbox_calls == ["本人承诺"]
    assert expand_calls == [page]
    assert checkbox_result["ok"] is True
    assert expand_result["ok"] is True


def test_run_apply_wizard_handler_preserves_substep_shape(monkeypatch) -> None:
    registry = _FakeRegistry()
    register_suyuan_wizard_drawer_actions(registry)
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

    substeps = [
        {"step": "click_apply_button", "result": {"ok": True, "method": "entry_opened"}},
        {"step": "click_intro_confirm", "result": {"ok": True, "name": "拟备案信息纳入溯源系统", "method": "role"}},
    ]

    async def fake_run_apply_wizard(page: object) -> list[dict[str, object]]:
        return substeps

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_shared_actions.run_apply_wizard",
        fake_run_apply_wizard,
    )

    result = asyncio.run(
        registry.handlers["run_apply_wizard"](
            _FakePage(),
            object(),
            runtime,
            {"id": "run_apply_wizard"},
        )
    )

    assert result == substeps
