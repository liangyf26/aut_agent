from __future__ import annotations

import re
from contextlib import suppress
from typing import Any


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


async def native_control_selected_value(input_locator: Any) -> str | None:
    """Return the committed value for select/date/cascader-like inputs."""
    with suppress(Exception):
        value = _clean_text(await input_locator.input_value(timeout=500))
        placeholder = _clean_text(await input_locator.get_attribute("placeholder") or "")
        if value and value != placeholder and not re.search(r"请选择|全部", value):
            return value
    return None


async def native_upload_existing_file_name(input_locator: Any) -> str:
    """Detect an already uploaded file name near a native file input."""
    with suppress(Exception):
        uploaded_name = await input_locator.evaluate(
            r"""(input) => {
              const upload = input.closest('.el-upload,.el-upload-dragger,[class*=upload]');
              const item = input.closest('.el-form-item') || (upload && upload.closest('.el-form-item'));
              const parent = item || upload || input.parentElement;
              const text = ((parent && (parent.innerText || parent.textContent)) || '').trim();
              const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
              const fileName = lines.find((line) => /\.[A-Za-z0-9]{2,5}(?:_\d+)?$/.test(line));
              if (fileName) return fileName;
              if (parent && parent.querySelector('img[src],.el-upload-list__item-thumbnail,.el-image,img')) {
                return "__stage2_existing_upload_image__";
              }
              return "";
            }"""
        )
        return _clean_text(uploaded_name)
    return ""


async def native_button_is_disabled(button_locator: Any) -> bool:
    """Return true for native, aria, or Element UI disabled buttons."""
    with suppress(Exception):
        return bool(await button_locator.evaluate(
            "(button) => Boolean(button.disabled || button.getAttribute('aria-disabled') === 'true' || button.classList.contains('is-disabled'))"
        ))
    return False
