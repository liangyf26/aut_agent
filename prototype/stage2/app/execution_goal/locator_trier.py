"""
L2 locator candidate trier for Stage E execution (P0-2).

Consumes the ``locator_candidates`` array produced by Stage D's
``_build_locator_candidates()`` (P0-1) and tries each candidate in
descending confidence order, returning the first one that matches
the live page.

The trier is used inside ``real_browser_runner._run_executable_steps()``
for ``click`` and ``fill`` actions. For ``view_only`` and
``entry_confirmation`` visibility checks, the runner itself iterates
candidates via ``_any_visible`` because the check is purely visual
(no side-effect) and does not need ``wait_for`` semantics.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page


class AllCandidatesFailed(RuntimeError):
    """All locator candidates were tried and none matched the live page."""


async def _try_locator_candidates(
    page: "Page",
    candidates: list[dict[str, Any]],
    *,
    timeout_ms: int = 5000,
) -> tuple["Locator", str, dict[str, Any]]:
    """Try each locator candidate in descending-confidence order.

    For each candidate, calls ``page.locator(selector).first.wait_for(
    state="attached")`` to verify the element is present in the DOM.
    On first success, returns ``(locator, winning_selector, meta)``
    where *meta* contains:

    - ``strategy``: the winning strategy label (id/text/role/css_path/class/fallback)
    - ``confidence``: the winning candidate's confidence score
    - ``selector``: the winning selector string
    - ``duration_ms``: time spent finding the element
    - ``attempts``: list of failed attempts, each with strategy/confidence/selector/
      duration_ms/error

    Raises :exc:`AllCandidatesFailed` if none of the candidates
    match — the caller should record this as ``locator_unstable``.
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


__all__ = [
    "AllCandidatesFailed",
    "_try_locator_candidates",
]
