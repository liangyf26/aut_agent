from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.verification.suyuan_submit_dialog_actions import (  # noqa: E402
    _resolve_generated_file,
    register_suyuan_submit_dialog_actions,
    upload_drawer_required_files,
    upload_submit_dialog_apply_file,
)
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData  # noqa: E402


class _FakeRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register(self, action_name: str, handler: object) -> None:
        self.handlers[action_name] = handler


class _FakePage:
    pass


class _FakeUploadInput:
    def __init__(self, existing_name: str = "") -> None:
        self.existing_name = existing_name
        self.set_files: list[str] = []

    async def evaluate(self, script: str) -> str:
        return self.existing_name

    async def count(self) -> int:
        return 1

    async def set_input_files(self, file_path: str) -> None:
        self.set_files.append(file_path)


class _FakeInputCollection:
    def __init__(self, inputs: list[_FakeUploadInput]) -> None:
        self.inputs = inputs

    async def count(self) -> int:
        return len(self.inputs)

    def nth(self, index: int) -> _FakeUploadInput:
        return self.inputs[index]

    @property
    def first(self) -> _FakeUploadInput:
        return self.inputs[0]


class _FakeUploadPage:
    def __init__(self, inputs: list[_FakeUploadInput]) -> None:
        self.inputs = _FakeInputCollection(inputs)
        self.waits: list[int] = []

    def locator(self, selector: str) -> _FakeInputCollection:
        return self.inputs

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)

    async def evaluate(self, script: str) -> dict[str, object]:
        return {}


def test_register_suyuan_submit_dialog_actions_registers_expected_actions() -> None:
    registry = _FakeRegistry()

    register_suyuan_submit_dialog_actions(registry)

    assert sorted(registry.handlers) == [
        "select_submit_dialog_dept",
        "submit_filing_dialog",
        "upload_drawer_required_files",
        "upload_submit_dialog_apply_file",
    ]


def test_register_suyuan_submit_dialog_actions_returns_same_registry_instance() -> None:
    registry = _FakeRegistry()

    returned = register_suyuan_submit_dialog_actions(registry)

    assert returned is registry


def test_upload_handlers_resolve_runtime_generated_files(monkeypatch, tmp_path: Path) -> None:
    registry = _FakeRegistry()
    register_suyuan_submit_dialog_actions(registry)
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={
            "personnel_file": tmp_path / "personnel.pdf",
            "acceptance_file": tmp_path / "acceptance.pdf",
            "apply_file": tmp_path / "apply.pdf",
        },
    )
    page = _FakePage()

    drawer_calls: list[tuple[Path, Path]] = []
    apply_calls: list[Path] = []

    async def fake_upload_drawer_required_files(
        target_page: object,
        personnel_file: Path,
        acceptance_file: Path,
    ) -> dict[str, object]:
        assert target_page is page
        drawer_calls.append((personnel_file, acceptance_file))
        return {"ok": True}

    async def fake_upload_submit_dialog_apply_file(
        target_page: object,
        apply_file: Path,
    ) -> dict[str, object]:
        assert target_page is page
        apply_calls.append(apply_file)
        return {"ok": True}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.upload_drawer_required_files",
        fake_upload_drawer_required_files,
    )
    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.upload_submit_dialog_apply_file",
        fake_upload_submit_dialog_apply_file,
    )

    asyncio.run(
        registry.handlers["upload_drawer_required_files"](
            page,
            object(),
            runtime,
            {"args": {"files_ref": "generated_files"}},
        )
    )
    asyncio.run(
        registry.handlers["upload_submit_dialog_apply_file"](
            page,
            object(),
            runtime,
            {"args": {"file_ref": "generated_files.apply_file"}},
        )
    )

    assert drawer_calls == [
        (
            tmp_path / "personnel.pdf",
            tmp_path / "acceptance.pdf",
        )
    ]
    assert apply_calls == [tmp_path / "apply.pdf"]


def test_upload_handlers_fall_back_to_string_paths_when_runtime_generated_files_are_strings(
    monkeypatch, tmp_path: Path
) -> None:
    registry = _FakeRegistry()
    register_suyuan_submit_dialog_actions(registry)
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={
            "personnel_file": str(tmp_path / "personnel.pdf"),
            "acceptance_file": str(tmp_path / "acceptance.pdf"),
        },
    )

    drawer_calls: list[tuple[Path, Path]] = []

    async def fake_upload_drawer_required_files(
        target_page: object,
        personnel_file: Path,
        acceptance_file: Path,
    ) -> dict[str, object]:
        drawer_calls.append((personnel_file, acceptance_file))
        return {"ok": True, "count": 4, "uploads": []}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.upload_drawer_required_files",
        fake_upload_drawer_required_files,
    )

    result = asyncio.run(
        registry.handlers["upload_drawer_required_files"](
            _FakePage(),
            object(),
            runtime,
            {"args": {"files_ref": "generated_files"}},
        )
    )

    assert drawer_calls == [
        (
            tmp_path / "personnel.pdf",
            tmp_path / "acceptance.pdf",
        )
    ]
    assert result == {"ok": True, "count": 4, "uploads": []}


def test_upload_handlers_support_custom_runtime_file_bundle_refs(monkeypatch, tmp_path: Path) -> None:
    registry = _FakeRegistry()
    register_suyuan_submit_dialog_actions(registry)
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={
            "submit_assets": {
                "personnel_file": tmp_path / "personnel.pdf",
                "acceptance_file": tmp_path / "acceptance.pdf",
                "apply_file": tmp_path / "apply.pdf",
            }
        },
        generated_files={},
    )
    drawer_calls: list[tuple[Path, Path]] = []
    apply_calls: list[Path] = []

    async def fake_upload_drawer_required_files(
        target_page: object,
        personnel_file: Path,
        acceptance_file: Path,
    ) -> dict[str, object]:
        drawer_calls.append((personnel_file, acceptance_file))
        return {"ok": True}

    async def fake_upload_submit_dialog_apply_file(
        target_page: object,
        apply_file: Path,
    ) -> dict[str, object]:
        apply_calls.append(apply_file)
        return {"ok": True}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.upload_drawer_required_files",
        fake_upload_drawer_required_files,
    )
    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.upload_submit_dialog_apply_file",
        fake_upload_submit_dialog_apply_file,
    )

    asyncio.run(
        registry.handlers["upload_drawer_required_files"](
            _FakePage(),
            object(),
            runtime,
            {"args": {"files_ref": "submit_assets"}},
        )
    )
    asyncio.run(
        registry.handlers["upload_submit_dialog_apply_file"](
            _FakePage(),
            object(),
            runtime,
            {"args": {"file_ref": "submit_assets.apply_file"}},
        )
    )

    assert drawer_calls == [(tmp_path / "personnel.pdf", tmp_path / "acceptance.pdf")]
    assert apply_calls == [tmp_path / "apply.pdf"]


def test_upload_drawer_required_files_skips_slots_that_already_have_files(tmp_path: Path) -> None:
    inputs = [
        _FakeUploadInput("人员信息表1.xls"),
        _FakeUploadInput(),
        _FakeUploadInput(),
        _FakeUploadInput("验收文件00.pdf"),
    ]
    page = _FakeUploadPage(inputs)

    result = asyncio.run(
        upload_drawer_required_files(
            page,
            tmp_path / "personnel.xls",
            tmp_path / "acceptance.pdf",
        )
    )

    assert result["ok"] is True
    assert [item.get("skipped") for item in result["uploads"]] == ["already_uploaded", "already_uploaded"]
    assert inputs[0].set_files == []
    assert inputs[3].set_files == []
    assert page.waits == []


def test_upload_submit_dialog_apply_file_skips_existing_dialog_file(tmp_path: Path) -> None:
    input_loc = _FakeUploadInput("备案申请表.pdf")
    page = _FakeUploadPage([input_loc])

    result = asyncio.run(upload_submit_dialog_apply_file(page, tmp_path / "备案申请表.pdf"))

    assert result["ok"] is True
    assert result["skipped"] == "already_uploaded"
    assert result["existing_file_name"] == "备案申请表.pdf"
    assert input_loc.set_files == []
    assert page.waits == []


def test_select_and_submit_handlers_support_runtime_ref_and_custom_submit(monkeypatch) -> None:
    registry = _FakeRegistry()
    submit_calls: list[str] = []

    async def custom_submit(page: object, artifacts: object, runtime: object, step: dict[str, object]) -> dict[str, object]:
        submit_calls.append(str(step.get("id") or ""))
        return {"ok": True, "policy_decision": {"status": "allowed"}}

    register_suyuan_submit_dialog_actions(
        registry,
        submit_filing_dialog_handler=custom_submit,
    )
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={"filing_submit": {"dept_label": "市农业农村局"}},
        generated_files={},
    )

    select_calls: list[str] = []

    async def fake_select_submit_dialog_dept(page: object, dept_label: str) -> dict[str, object]:
        select_calls.append(dept_label)
        return {"ok": True, "selected": dept_label}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.select_submit_dialog_dept",
        fake_select_submit_dialog_dept,
    )

    select_result = asyncio.run(
        registry.handlers["select_submit_dialog_dept"](
            _FakePage(),
            object(),
            runtime,
            {"args": {"data_ref": "filing_submit.dept_label"}},
        )
    )
    submit_result = asyncio.run(
        registry.handlers["submit_filing_dialog"](
            _FakePage(),
            object(),
            runtime,
            {"id": "submit_filing_dialog"},
        )
    )

    assert select_calls == ["市农业农村局"]
    assert select_result == {"ok": True, "selected": "市农业农村局"}
    assert submit_calls == ["submit_filing_dialog"]
    assert submit_result["policy_decision"] == {"status": "allowed"}


def test_upload_submit_dialog_apply_file_handler_falls_back_when_ref_missing(monkeypatch) -> None:
    registry = _FakeRegistry()
    register_suyuan_submit_dialog_actions(registry)
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={},
    )

    apply_calls: list[Path] = []

    async def fake_upload_submit_dialog_apply_file(
        target_page: object,
        apply_file: Path,
    ) -> dict[str, object]:
        apply_calls.append(apply_file)
        return {"ok": False, "reason": "file-input-not-found", "file": str(apply_file)}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.upload_submit_dialog_apply_file",
        fake_upload_submit_dialog_apply_file,
    )

    result = asyncio.run(
        registry.handlers["upload_submit_dialog_apply_file"](
            _FakePage(),
            object(),
            runtime,
            {"args": {"file_ref": "generated_files.apply_file"}},
        )
    )

    assert apply_calls == [Path("apply_file_missing.pdf")]
    assert result == {
        "ok": False,
        "reason": "file-input-not-found",
        "file": "apply_file_missing.pdf",
    }


def test_select_submit_dialog_dept_handler_stringifies_missing_runtime_ref(monkeypatch) -> None:
    registry = _FakeRegistry()
    register_suyuan_submit_dialog_actions(registry)
    runtime = TemplateRuntimeData(baseline={}, run_data={}, generated_files={})

    select_calls: list[str] = []

    async def fake_select_submit_dialog_dept(page: object, dept_label: str) -> dict[str, object]:
        select_calls.append(dept_label)
        return {"ok": False, "target": dept_label, "reason": "option-not-found", "candidates": []}

    monkeypatch.setattr(
        "prototype.stage2.app.verification.suyuan_submit_dialog_actions.select_submit_dialog_dept",
        fake_select_submit_dialog_dept,
    )

    result = asyncio.run(
        registry.handlers["select_submit_dialog_dept"](
            _FakePage(),
            object(),
            runtime,
            {"args": {"data_ref": "filing_submit.dept_label"}},
        )
    )

    assert select_calls == [""]
    assert result == {
        "ok": False,
        "target": "",
        "reason": "option-not-found",
        "candidates": [],
    }


def test_resolve_generated_file_prefers_preferred_ref_then_fallback(tmp_path: Path) -> None:
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={
            "preferred": tmp_path / "preferred.pdf",
            "fallback": tmp_path / "fallback.pdf",
        },
    )

    preferred = _resolve_generated_file(
        runtime,
        preferred_ref="generated_files.preferred",
        fallback_ref="generated_files.fallback",
        fallback_value="ignored.pdf",
    )
    fallback = _resolve_generated_file(
        runtime,
        preferred_ref="generated_files.missing",
        fallback_ref="generated_files.fallback",
        fallback_value="ignored.pdf",
    )
    string_fallback = _resolve_generated_file(
        runtime,
        preferred_ref="generated_files.missing",
        fallback_ref="generated_files.also_missing",
        fallback_value="fallback-name.pdf",
    )

    assert preferred == tmp_path / "preferred.pdf"
    assert fallback == tmp_path / "fallback.pdf"
    assert string_fallback == Path("fallback-name.pdf")
