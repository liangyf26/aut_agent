"""
L2 + L3 locator resolution for Stage E execution (P0-2, P0-5).

L2: iterates ``locator_candidates`` from P0-1 in descending-confidence
order, returning the first element found in the live DOM.

L3: when L2 candidates all fail, constructs Playwright-native ARIA /
role-based locators (``page.getByRole()``, ``page.getByText()``) as a
pure semantic fallback — no LLM involved.

L4 (Browser Use) is invoked by ``real_browser_runner`` directly via
``browser_use_executor.execute_with_browser_use()``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page


class AllCandidatesFailed(RuntimeError):
    """All L2 locator candidates were tried and none matched the live page."""


# ── Role → Playwright getByRole mapping ────────────────────────────────

_ARIA_ROLE_FOR_TAG: dict[str, str] = {
    "button": "button",
    "a": "link",
    "input": "textbox",
    "textarea": "textbox",
    "select": "combobox",
    "li": "listitem",
    "td": "cell",
    "th": "columnheader",
    "nav": "navigation",
    "main": "main",
    "header": "banner",
    "footer": "contentinfo",
    "form": "form",
    "table": "table",
    "img": "img",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
}


async def _try_locator_candidates(
    page: "Page",
    candidates: list[dict[str, Any]],
    *,
    timeout_ms: int = 5000,
) -> tuple["Locator", str, dict[str, Any]]:
    """Try each L2 locator candidate in descending-confidence order.

    For each candidate, calls ``page.locator(selector).first.wait_for(
    state="attached")`` to verify the element is present in the DOM.
    On first success, returns ``(locator, winning_selector, meta)``
    where *meta* contains:

    - ``strategy``: the winning strategy label (id/text/role/css_path/class/fallback)
    - ``confidence``: the winning candidate's confidence score
    - ``selector``: the winning selector string
    - ``duration_ms``: time spent finding the element
    - ``attempts``: list of failed attempts

    Raises :exc:`AllCandidatesFailed` if none of the candidates
    match — the caller should fall through to L3 (ARIA) then L4
    (Browser Use).
    """
    attempts: list[dict[str, Any]] = []
    for cand in candidates:
        selector = str(cand["selector"])
        strategy = str(cand.get("strategy", "unknown"))
        confidence = cand.get("confidence")
        started = time.perf_counter()
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="attached", timeout=timeout_ms)
            duration_ms = int((time.perf_counter() - started) * 1000)
            meta: dict[str, Any] = {
                "strategy": strategy,
                "confidence": confidence,
                "selector": selector,
                "duration_ms": duration_ms,
                "attempts": attempts,
            }
            return locator, selector, meta
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            attempts.append(
                {
                    "strategy": strategy,
                    "confidence": confidence,
                    "selector": selector,
                    "duration_ms": duration_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    attempted = ", ".join(a["selector"] for a in attempts[:5])
    suffix = f" (+ {len(attempts) - 5} more)" if len(attempts) > 5 else ""
    raise AllCandidatesFailed(
        f"All {len(candidates)} locator candidates failed: {attempted}{suffix}"
    )


# ── L3: ARIA semantic matching ─────────────────────────────────────────


async def _try_l3_aria(
    page: "Page",
    *,
    element_text: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
    target_tag: str | None = None,
    timeout_ms: int = 5000,
) -> tuple["Locator", str, dict[str, Any]] | None:
    """L3 fallback: ARIA / role-based element matching.

    Tries a sequence of Playwright-native semantic locators:

    1. ``page.getByRole(role, {name: text})`` — when a tag→role mapping
       exists AND non-empty text is available.
    2. ``page.getByText(text)`` — broad text-content match.
    3. ``page.getByLabel(text)`` — labelled form fields.

    Args:
        page: live Playwright page.
        element_text: the element's visible text (from the test-case
            metadata or step description).
        candidates: the failed L2 candidates — inspected to extract a
            tag hint (when the first candidate's selector starts with a
            known HTML tag).
        target_tag: explicit tag override (e.g. ``"button"``).
        timeout_ms: max wait per strategy.

    Returns:
        ``(locator, selector, meta)`` on first hit, or ``None`` when
        all three strategies fail.
    """
    text = _normalize_text(element_text)
    if not text:
        return None

    tag = target_tag or _guess_tag_from_candidates(candidates)

    started = time.perf_counter()
    attempts: list[dict[str, Any]] = []

    # Strategy 1: getByRole(name)
    role = _ARIA_ROLE_FOR_TAG.get(tag or "")
    if role and text:
        result = await _try_get_by_role(page, role, text, timeout_ms, started, attempts)
        if result:
            return result

    # Strategy 2: getByText
    try:
        locator = page.get_by_text(text, exact=False).first
        await locator.wait_for(state="attached", timeout=timeout_ms)
        duration_ms = int((time.perf_counter() - started) * 1000)
        meta: dict[str, Any] = {
            "strategy": "aria_text",
            "confidence": 0.50,
            "selector": f"getByText({text!r})",
            "duration_ms": duration_ms,
            "attempts": attempts,
        }
        return locator, f"getByText({text!r})", meta
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        attempts.append({
            "strategy": "aria_text",
            "selector": f"getByText({text!r})",
            "duration_ms": duration_ms,
            "error": f"{type(exc).__name__}: {exc}",
        })

    # Strategy 3: getByLabel
    try:
        locator = page.get_by_label(text, exact=False).first
        await locator.wait_for(state="attached", timeout=timeout_ms)
        duration_ms = int((time.perf_counter() - started) * 1000)
        meta = {
            "strategy": "aria_label",
            "confidence": 0.40,
            "selector": f"getByLabel({text!r})",
            "duration_ms": duration_ms,
            "attempts": attempts,
        }
        return locator, f"getByLabel({text!r})", meta
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        attempts.append({
            "strategy": "aria_label",
            "selector": f"getByLabel({text!r})",
            "duration_ms": duration_ms,
            "error": f"{type(exc).__name__}: {exc}",
        })

    return None


async def _try_get_by_role(
    page: "Page",
    role: str,
    text: str,
    timeout_ms: int,
    started: float,
    attempts: list[dict[str, Any]],
) -> tuple["Locator", str, dict[str, Any]] | None:
    try:
        locator = page.get_by_role(role, name=text, exact=False).first
        await locator.wait_for(state="attached", timeout=timeout_ms)
        duration_ms = int((time.perf_counter() - started) * 1000)
        meta: dict[str, Any] = {
            "strategy": "aria_role",
            "confidence": 0.55,
            "selector": f"getByRole({role}, name={text!r})",
            "duration_ms": duration_ms,
            "attempts": attempts,
        }
        return locator, f"getByRole({role}, name={text!r})", meta
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        attempts.append({
            "strategy": "aria_role",
            "selector": f"getByRole({role}, name={text!r})",
            "duration_ms": duration_ms,
            "error": f"{type(exc).__name__}: {exc}",
        })
        return None


# ── helpers ─────────────────────────────────────────────────────────────


def _normalize_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None
    return cleaned[:80]


def _guess_tag_from_candidates(candidates: list[dict[str, Any]] | None) -> str | None:
    """Extract an HTML tag from the first candidate's selector."""
    if not candidates:
        return None
    first = candidates[0].get("selector", "")
    import re
    m = re.match(r"^(\w+)", str(first))
    return m.group(1) if m else None


__all__ = [
    "AllCandidatesFailed",
    "_try_l3_aria",
    "_try_locator_candidates",
]
