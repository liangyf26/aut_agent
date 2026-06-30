from __future__ import annotations

import asyncio

from prototype.stage2.app.verification.native_control_skills import (
    native_button_is_disabled,
    native_control_selected_value,
    native_upload_existing_file_name,
)


class _FakeNativeLocator:
    def __init__(
        self,
        *,
        input_value: str = "",
        placeholder: str = "",
        uploaded_name: str = "",
        has_image_thumbnail: bool = False,
        disabled: bool = False,
    ) -> None:
        self._input_value = input_value
        self._placeholder = placeholder
        self._uploaded_name = uploaded_name
        self._has_image_thumbnail = has_image_thumbnail
        self._disabled = disabled

    async def input_value(self, timeout: int = 0) -> str:
        return self._input_value

    async def get_attribute(self, name: str) -> str:
        if name == "placeholder":
            return self._placeholder
        return ""

    async def evaluate(self, script: str) -> str | bool:
        if "classList.contains('is-disabled')" in script:
            return self._disabled
        if self._has_image_thumbnail and "querySelector('img" in script:
            return "__stage2_existing_upload_image__"
        return self._uploaded_name


def test_native_control_selected_value_skips_empty_placeholder_values() -> None:
    empty = _FakeNativeLocator(input_value="请选择", placeholder="请选择")
    selected = _FakeNativeLocator(input_value="金毛狗脊", placeholder="请选择")

    assert asyncio.run(native_control_selected_value(empty)) is None
    assert asyncio.run(native_control_selected_value(selected)) == "金毛狗脊"


def test_native_upload_existing_file_name_detects_uploaded_file_text() -> None:
    upload = _FakeNativeLocator(uploaded_name="备案图片01.jpg")

    assert asyncio.run(native_upload_existing_file_name(upload)) == "备案图片01.jpg"


def test_native_upload_existing_file_name_detects_image_thumbnail_without_file_name() -> None:
    upload = _FakeNativeLocator(has_image_thumbnail=True)

    assert (
        asyncio.run(native_upload_existing_file_name(upload))
        == "__stage2_existing_upload_image__"
    )


def test_native_button_is_disabled_reads_native_and_element_ui_disabled_state() -> None:
    enabled = _FakeNativeLocator(disabled=False)
    disabled = _FakeNativeLocator(disabled=True)

    assert asyncio.run(native_button_is_disabled(enabled)) is False
    assert asyncio.run(native_button_is_disabled(disabled)) is True
