from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.verification.generic_templates import execute_generic_template, fill_field_by_locator
from prototype.stage2.app.verification.locator_resolution import resolve_step_locator_candidates
from prototype.stage2.app.verification.template_runtime import TemplateRuntimeData


class _FakeLocator:
    def __init__(self, selector: str, *, wait_error: Exception | None = None) -> None:
        self.selector = selector
        self.wait_error = wait_error
        self.filled_values: list[str] = []

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def wait_for(self, timeout: int) -> None:
        if self.wait_error is not None:
            raise self.wait_error

    async def fill(self, value: str) -> None:
        self.filled_values.append(value)


class _FakePage:
    def __init__(self, locators: dict[str, _FakeLocator]) -> None:
        self._locators = locators

    def locator(self, selector: str) -> _FakeLocator:
        try:
            return self._locators[selector]
        except KeyError as exc:
            raise RuntimeError(f"unexpected selector: {selector}") from exc


def test_template_runtime_resolve_ref_exposes_locator_hints() -> None:
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={},
        locator_hints={"query_input": {"preferred": "#queryInput"}},
    )

    assert runtime.resolve_ref("locator_hints.query_input.preferred") == "#queryInput"


def test_resolve_step_locator_candidates_reads_structured_locator_hints_in_order() -> None:
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={},
        locator_hints={
            "query_input": {
                "preferred": "#primaryQueryInput",
                "fallback": ["#backupQueryInput", "#legacyQueryInput"],
                "candidates": [
                    {"value": "#candidateQueryInput"},
                    "#finalCandidateQueryInput",
                ],
            }
        },
    )
    step = {
        "args": {
            "locator_hint_key": "query_input",
        }
    }

    assert resolve_step_locator_candidates(runtime, step) == [
        "#primaryQueryInput",
        "#backupQueryInput",
        "#legacyQueryInput",
        "#candidateQueryInput",
        "#finalCandidateQueryInput",
    ]


def test_resolve_step_locator_candidates_treats_locator_value_as_hint_key_when_available() -> None:
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={},
        generated_files={},
        locator_hints={
            "search_button": {
                "preferred": "button:has-text('搜索')",
                "fallback": ["button.search"],
            }
        },
    )
    step = {
        "args": {
            "locator": "search_button",
        }
    }

    assert resolve_step_locator_candidates(runtime, step) == [
        "button:has-text('搜索')",
        "button.search",
    ]


def test_fill_field_by_locator_falls_back_to_next_locator_hint_candidate() -> None:
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={"query_form": {"keyword": "stage2-keyword"}},
        generated_files={},
        locator_hints={
            "query_input": {
                "preferred": "#missingQueryInput",
                "fallback": ["#workingQueryInput"],
            }
        },
    )
    page = _FakePage(
        {
            "#missingQueryInput": _FakeLocator(
                "#missingQueryInput",
                wait_error=RuntimeError("preferred locator not found"),
            ),
            "#workingQueryInput": _FakeLocator("#workingQueryInput"),
        }
    )
    step = {
        "id": "fill_query_keyword",
        "action": "fill_field_by_locator",
        "args": {
            "locator_hint_key": "query_input",
            "data_ref": "query_form.keyword",
            "timeout_ms": 50,
        },
    }

    result = asyncio.run(fill_field_by_locator(page, object(), runtime, step))

    assert result["ok"] is True
    assert result["locator"] == "#workingQueryInput"
    assert result["locator_candidates"] == ["#missingQueryInput", "#workingQueryInput"]
    assert result["locator_attempts"] == [
        {
            "locator": "#missingQueryInput",
            "error": "RuntimeError: preferred locator not found",
        }
    ]
    assert page.locator("#workingQueryInput").filled_values == ["stage2-keyword"]


def test_execute_generic_template_uses_locator_hints_during_step_execution() -> None:
    runtime = TemplateRuntimeData(
        baseline={},
        run_data={"query_form": {"keyword": "executor-keyword"}},
        generated_files={},
        locator_hints={
            "query_input": {
                "preferred": "#staleQueryInput",
                "fallback": ["#activeQueryInput"],
            }
        },
    )
    page = _FakePage(
        {
            "#staleQueryInput": _FakeLocator(
                "#staleQueryInput",
                wait_error=RuntimeError("stale locator"),
            ),
            "#activeQueryInput": _FakeLocator("#activeQueryInput"),
        }
    )
    template = {
        "template_name": "locator_hint_executor_smoke",
        "steps": [
            {
                "id": "fill_query_keyword",
                "action": "fill_field_by_locator",
                "args": {
                    "locator": "query_input",
                    "data_ref": "query_form.keyword",
                    "timeout_ms": 50,
                },
            }
        ],
    }

    executions = asyncio.run(
        execute_generic_template(
            page=page,
            artifacts=object(),
            runtime=runtime,
            template=template,
        )
    )

    assert len(executions) == 1
    assert executions[0].status == "completed"
    assert executions[0].result == {
        "ok": True,
        "locator": "#activeQueryInput",
        "locator_candidates": ["#staleQueryInput", "#activeQueryInput"],
        "locator_attempts": [
            {
                "locator": "#staleQueryInput",
                "error": "RuntimeError: stale locator",
            }
        ],
        "value": "executor-keyword",
    }
