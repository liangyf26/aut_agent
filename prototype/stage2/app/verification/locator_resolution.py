from __future__ import annotations

from typing import Any, Awaitable, Callable

from playwright.async_api import Locator, Page

from .template_runtime import TemplateRuntimeData

LocatorAction = Callable[[Locator, str], Awaitable[dict[str, Any]]]

_LOCATOR_REF_KEYS = (
    "locator_ref",
    "locator_hint_ref",
    "locator_hints_ref",
)
_LOCATOR_HINT_KEY_KEYS = (
    "locator_hint_key",
    "locator_key",
)
_STRUCTURED_HINT_KEYS = (
    "preferred",
    "primary",
    "preferred_locator",
    "fallback",
    "secondary",
    "backup",
    "alternate",
    "alternates",
    "candidate",
    "candidates",
    "candidate_locators",
    "locator_candidates",
    "locators",
)
_RANKED_SCALAR_KEYS = (
    "preferred",
    "primary",
    "preferred_locator",
    "selector",
    "locator",
    "value",
    "test_id",
    "id",
    "name",
    "aria_label",
    "placeholder",
    "label_text",
    "css_path",
    "css",
    "role",
)


def resolve_step_locator_candidates(
    runtime: TemplateRuntimeData,
    step: dict[str, Any],
) -> list[str]:
    args = step.get("args", {}) if isinstance(step, dict) else {}
    if not isinstance(args, dict):
        return []

    locator = _clean_text(args.get("locator"))
    hint_refs = [_clean_text(args.get(key)) for key in _LOCATOR_REF_KEYS]
    hint_refs = [item for item in hint_refs if item]
    hint_keys = [_clean_text(args.get(key)) for key in _LOCATOR_HINT_KEY_KEYS]
    hint_keys = [item for item in hint_keys if item]

    candidates: list[str] = []
    explicit_hint_source = bool(hint_refs or hint_keys)
    if locator:
        if not explicit_hint_source and runtime.has_locator_hint(locator):
            candidates.extend(_flatten_locator_candidates(runtime.locator_hint(locator)))
        else:
            candidates.append(locator)

    for ref in hint_refs:
        candidates.extend(_flatten_locator_candidates(runtime.resolve_ref(ref)))

    for key in hint_keys:
        candidates.extend(_flatten_locator_candidates(runtime.locator_hint(key)))

    return _dedupe_candidates(candidates)


async def run_action_with_locator_candidates(
    page: Page,
    *,
    locator_candidates: list[str],
    timeout_ms: int,
    action: LocatorAction,
) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    if not locator_candidates:
        raise RuntimeError("未提供可用定位器候选。")

    attempts: list[dict[str, str]] = []
    last_error: Exception | None = None
    for candidate in locator_candidates:
        locator = page.locator(candidate).first
        try:
            await locator.wait_for(timeout=timeout_ms)
            result = await action(locator, candidate)
            return result, candidate, attempts
        except Exception as exc:
            last_error = exc
            attempts.append(
                {
                    "locator": candidate,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    attempted = ", ".join(item["locator"] for item in attempts) or "<none>"
    if last_error is None:
        raise RuntimeError(f"未能解析任何可用定位器，候选: {attempted}")
    raise RuntimeError(f"未能解析任何可用定位器，候选: {attempted}") from last_error


def _flatten_locator_candidates(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        candidate = payload.strip()
        return [candidate] if candidate else []
    if isinstance(payload, (list, tuple)):
        result: list[str] = []
        for item in payload:
            result.extend(_flatten_locator_candidates(item))
        return result
    if isinstance(payload, dict):
        result: list[str] = []
        for key in _STRUCTURED_HINT_KEYS:
            if key in payload:
                result.extend(_flatten_locator_candidates(payload.get(key)))
        for key in _RANKED_SCALAR_KEYS:
            if key in payload:
                result.extend(_flatten_locator_candidates(payload.get(key)))
        return _dedupe_candidates(result)
    return []


def _dedupe_candidates(candidates: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _clean_text(candidate)
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
