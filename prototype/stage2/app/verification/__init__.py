"""Verification modules for the stage 2 prototype."""

from .generic_templates import (
    build_generic_template_registry,
    execute_generic_template,
    navigate_to_url,
    fill_field_by_locator,
    select_option_by_locator,
    click_by_locator,
    assert_locator_value,
    assert_text_visible,
    capture_named_screenshot,
)
from .template_executor import TemplateActionRegistry, TemplateFlowExecutor, TemplateStepExecution
from .template_runtime import TemplateRuntimeData

__all__ = [
    "build_generic_template_registry",
    "navigate_to_url",
    "fill_field_by_locator",
    "select_option_by_locator",
    "click_by_locator",
    "assert_locator_value",
    "assert_text_visible",
    "capture_named_screenshot",
    "execute_generic_template",
    "TemplateActionRegistry",
    "TemplateFlowExecutor",
    "TemplateRuntimeData",
    "TemplateStepExecution",
]
