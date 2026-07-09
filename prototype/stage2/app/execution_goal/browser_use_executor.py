"""
Unified Browser Use executor for the Stage E L4 fallback layer (P0-4).

Provides a single entrypoint ``execute_with_browser_use()`` that any stage
(B/C/D/E) can call with stage-appropriate ``BrowserUseSafety`` constraints.
When L1 (static snapshot), L2 (candidate pool), and L3 (ARIA semantic) all
fail to locate or interact with an element, L4 invokes an LLM-driven
Browser Use agent for semantic takeover.

Safety constraints
------------------
- ``write_allowed=False`` (default, stages B/C/D) → agent may navigate,
  observe, and read page content but must never click, fill, or submit.
- ``write_allowed=True`` (stage E) → agent may click elements but must
  never fill form fields or trigger form submission (§4.3: Browser Use
  ``[仅用于定位和点击，不用于填表/提交]``).
- ``risk_level=="high"`` blocks Browser Use unconditionally — such
  operations must go through ``entry_confirmation``, not auto-execution.
- ``max_steps`` caps the agent's total action count.
- Every execution is stamped ``execution_mode="browser_use_fallback"``.

The ``browser_use`` package is imported lazily; if unavailable the executor
returns a degraded ``BrowserUseResult(ok=False, failure_reason="browser_use_unavailable")``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page


# ── dataclasses ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class BrowserUseSafety:
    """Safety profile controlling what a Browser Use agent may do."""

    write_allowed: bool = False
    max_steps: int = 8
    timeout_ms: int = 15000


@dataclass(slots=True)
class BrowserUseResult:
    """Structured outcome from a Browser Use agent session.

    Designed to slot into Stage E's existing evidence pipeline alongside
    :class:`ExecutionOutcome`, with agent-specific metadata.
    """

    ok: bool
    model: str
    instruction: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    duration_ms: int = 0


# ── stage profiles ──────────────────────────────────────────────────────

_RISK_WARNING_READ_ONLY = (
    "SAFETY CONSTRAINT: You are in READ-ONLY mode.  You may navigate, observe "
    "and read page content, but you MUST NOT click any element, fill any form "
    "field, or trigger any submit action.  Violating this may corrupt production data."
)

_RISK_WARNING_CLICK_ONLY = (
    "SAFETY CONSTRAINT: You may click elements to navigate or reveal information, "
    "but you MUST NOT fill form fields or submit forms.  The only allowed "
    "interaction is clicking — no typing, no form submission."
)

_STAGE_SAFETY: dict[str, BrowserUseSafety] = {
    "menu_discovery": BrowserUseSafety(write_allowed=False, max_steps=3),
    "page_discovery": BrowserUseSafety(write_allowed=False, max_steps=5),
    "feature_discovery": BrowserUseSafety(write_allowed=False, max_steps=5),
    "execution_verification": BrowserUseSafety(write_allowed=True, max_steps=8),
}

BROWSER_USE_FALLBACK_MODE = "browser_use_fallback"


def safety_for_stage(stage: str) -> BrowserUseSafety:
    """Return the recommended safety profile for *stage*.

    Recognised keys: ``"menu_discovery"`` / ``"page_discovery"`` /
    ``"feature_discovery"`` / ``"execution_verification"``.

    Unknown stages receive the most conservative profile
    (``write_allowed=False, max_steps=1``).
    """
    return _STAGE_SAFETY.get(stage, BrowserUseSafety(write_allowed=False, max_steps=1))


# ── main executor ───────────────────────────────────────────────────────


async def execute_with_browser_use(
    page: "Page",
    instruction: str,
    *,
    context: dict[str, Any] | None = None,
    safety: BrowserUseSafety | None = None,
    model_name: str | None = None,
    screenshots_dir: Path | None = None,
) -> BrowserUseResult:
    """Run a Browser Use agent on *page*.

    Args:
        page: A live Playwright ``Page`` (used for pre/post screenshots;
            the agent itself drives the browser via ``Browser(cdp_url=...)``
            when a CDP URL is provided, or starts a fresh browser otherwise).
        instruction: The natural-language task for the agent.  May be
            extended with automatic safety warnings (see
            :class:`BrowserUseSafety`).
        context: Optional metadata dict — recognised keys include
            ``stage``, ``goal_id``, ``risk_level``, ``cdp_url``,
            ``tried_strategies``.
        safety: Override the default safety profile.  When ``None`` the
            executor uses the ``"execution_verification"`` profile, which
            allows clicking but forbids text entry and submission.
        model_name: Profile id / label from ``stage2-model-profiles.json``.
            When ``None`` the first profile is used.
        screenshots_dir: Directory for before/after screenshots.

    Returns:
        :class:`BrowserUseResult` — ``ok=True`` when the agent ran to
        completion and took at least one action.  ``ok=False`` with a
        populated ``failure_reason`` when the agent could not start or
        failed at runtime.
    """
    safety = safety or safety_for_stage("execution_verification")
    context = context or {}
    started = time.perf_counter()

    # ── risk gate (BEFORE any import or I/O) ───────────────────────
    risk_level = str(context.get("risk_level") or "")
    if risk_level == "high":
        return _fail(
            instruction=instruction,
            failure_reason="blocked_by_safety_policy",
            notes=[
                "risk_level='high' is permanently blocked from Browser Use — "
                "these operations must go through entry_confirmation, not "
                "automatic execution.",
            ],
            started=started,
        )

    before_shot: dict[str, Any] = {}
    if screenshots_dir and page is not None:
        before_shot = await _capture(page, screenshots_dir, "browser_use_before")

    # ── lazy-import gate ───────────────────────────────────────────
    try:
        from browser_use import Agent, Browser, ChatOpenAI  # type: ignore[import-untyped]
    except ImportError:
        return _fail(
            instruction=instruction,
            failure_reason="browser_use_unavailable",
            notes=["Browser Use package is not installed in this environment."],
            started=started,
        )

    # ── resolve profile ────────────────────────────────────────────
    profile = _resolve_profile(model_name)
    if not profile:
        return _fail(
            instruction=instruction,
            failure_reason="model_profile_unavailable",
            notes=["No matching model profile found in stage2-model-profiles.json."],
            started=started,
        )

    model = str(profile.get("model") or model_name or "unknown")

    # ── build agent instruction ────────────────────────────────────
    task = _build_task(instruction, safety)

    # ── run ────────────────────────────────────────────────────────
    try:
        return await _run_agent(
            page=page,
            task=task,
            instruction=instruction,
            profile=profile,
            model=model,
            safety=safety,
            context=context,
            started=started,
            screenshots_dir=screenshots_dir,
            before_shot=before_shot,
            AgentCls=Agent,
            BrowserCls=Browser,
            ChatOpenAICls=ChatOpenAI,
        )
    except Exception as exc:
        screenshots: list[dict[str, Any]] = [before_shot] if before_shot else []
        return _fail(
            instruction=instruction,
            failure_reason="browser_use_handover_failed",
            model=model,
            screenshots=screenshots,
            notes=[f"Agent execution raised {type(exc).__name__}: {exc}"],
            started=started,
        )


# ── internal helpers ────────────────────────────────────────────────────


def _build_task(instruction: str, safety: BrowserUseSafety) -> str:
    warning = _RISK_WARNING_CLICK_ONLY if safety.write_allowed else _RISK_WARNING_READ_ONLY
    return f"{instruction}\n\n{warning}"


async def _run_agent(
    *,
    page: "Page",
    task: str,
    instruction: str,
    profile: dict[str, Any],
    model: str,
    safety: BrowserUseSafety,
    context: dict[str, Any],
    started: float,
    screenshots_dir: Path | None,
    before_shot: dict[str, Any],
    AgentCls: Any,
    BrowserCls: Any,
    ChatOpenAICls: Any,
) -> BrowserUseResult:
    api_key = profile.get("apiKey") or profile.get("api_key") or "EMPTY"
    base_url = profile.get("baseUrl") or profile.get("base_url")

    llm = ChatOpenAICls(model=model, api_key=api_key, base_url=base_url, request_timeout=60)

    cdp_url = str(context.get("cdp_url") or "")
    browser = BrowserCls(cdp_url=cdp_url) if cdp_url else BrowserCls()

    agent = AgentCls(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=True,
        max_actions_per_step=1,
    )

    agent_timeout = max(safety.timeout_ms / 1000.0, 5.0)
    try:
        history = await asyncio.wait_for(agent.run(max_steps=safety.max_steps), timeout=agent_timeout)
    except asyncio.TimeoutError:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return BrowserUseResult(
            ok=False,
            model=model,
            instruction=instruction,
            failure_reason="browser_use_timeout",
            notes=[f"Agent timed out after {agent_timeout}s"],
            duration_ms=duration_ms,
        )

    if screenshots_dir and page is not None:
        await _capture(page, screenshots_dir, "browser_use_after")

    duration_ms = int((time.perf_counter() - started) * 1000)

    agent_actions: list[dict[str, Any]] = []
    if history is not None and hasattr(history, "history"):
        for entry in history.history:
            agent_actions.append(
                entry if isinstance(entry, dict)
                else {"entry": str(entry)} if entry is not None
                else {}
            )

    screenshots: list[dict[str, Any]] = [before_shot] if before_shot else []
    ok = len(agent_actions) > 0

    return BrowserUseResult(
        ok=ok,
        model=model,
        instruction=instruction,
        actions=agent_actions,
        screenshots=screenshots,
        notes=[
            f"Browser Use fallback stage={context.get('stage', 'unknown')}, "
            f"max_steps={safety.max_steps}, write_allowed={safety.write_allowed}.",
            f"Execution mode: {BROWSER_USE_FALLBACK_MODE}.",
        ],
        duration_ms=duration_ms,
    )


def _resolve_profile(model_name: str | None) -> dict[str, Any] | None:
    import json
    import os
    from contextlib import suppress

    default_path = Path(__file__).resolve().parents[4] / "config" / "stage2-model-profiles.json"
    path = Path(os.environ.get("STAGE2_MODEL_PROFILES_PATH", str(default_path)))
    if not path.exists():
        return None
    with suppress(Exception):
        payload = json.loads(path.read_text(encoding="utf-8"))
        profiles: list[dict[str, Any]] = payload.get("profiles", [])
        if not model_name and profiles:
            return profiles[0]
        for p in profiles:
            if str(p.get("id")) == model_name or str(p.get("label")) == model_name:
                return p
    return None


def _fail(
    *,
    instruction: str,
    failure_reason: str,
    model: str = "unknown",
    notes: list[str] | None = None,
    screenshots: list[dict[str, Any]] | None = None,
    started: float = 0,
) -> BrowserUseResult:
    duration_ms = int((time.perf_counter() - started) * 1000) if started else 0
    return BrowserUseResult(
        ok=False,
        model=model,
        instruction=instruction,
        failure_reason=failure_reason,
        screenshots=list(screenshots or []),
        notes=list(notes or []),
        duration_ms=duration_ms,
    )


async def _capture(page: "Page", screenshots_dir: Path, name: str) -> dict[str, Any]:
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    path = screenshots_dir / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    return {"path": str(path), "kind": "screenshot", "name": name}


async def classify_with_vision(
    page: "Page",
    instruction: str,
    *,
    model_name: str,
) -> dict[str, Any] | None:
    """Use a screenshot + LLM to classify page content.  No Browser(), no CDP
    — the Playwright *page* that the caller already owns is the only browser
    connection.  Works for Stage D feature re-classification where Browser Use
    would conflict with an existing Playwright CDP session.
    """
    import base64
    import sys

    profile = _resolve_profile(model_name)
    if not profile:
        print(f"[vision] profile not found: {model_name}", file=sys.stderr)
        return None
    try:
        data = await page.screenshot(type="png", full_page=False)
        b64 = base64.b64encode(data).decode()
    except Exception as exc:
        print(f"[vision] screenshot failed: {exc}", file=sys.stderr)
        return None

    model = profile.get("model") or "unknown"
    api_key = profile.get("apiKey") or profile.get("api_key") or "EMPTY"
    base_url = profile.get("baseUrl") or profile.get("base_url")

    prompt = instruction + "\n只返回 JSON，不要其他文字。"

    try:
        import openai
        client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
            max_tokens=1024,
        )
        text = resp.choices[0].message.content or ""
    except ImportError:
        try:
            from browser_use import ChatOpenAI as _ChatOpenAI
            llm = _ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
            resp = await llm.ainvoke(prompt)
            text = str(resp.content if hasattr(resp, "content") else resp)
        except Exception as exc:
            print(f"[vision] LLM call failed: {exc}", file=sys.stderr)
            return None
    except Exception as exc:
        print(f"[vision] LLM call failed: {exc}", file=sys.stderr)
        return None

    print(f"[vision] LLM response ({len(text)} chars): {text[:200]}", file=sys.stderr)
    import json, re
    m = re.search(r'\{[^{}]*"feature_type"[^{}]*|[^{}]*"controls"[^{}]*\}', text, re.DOTALL)
    if not m:
        m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            print(f"[vision] JSON parse failed: {exc}", file=sys.stderr)
            return None
    print(f"[vision] no JSON found in response", file=sys.stderr)
    return None


__all__ = [
    "BROWSER_USE_FALLBACK_MODE",
    "BrowserUseResult",
    "BrowserUseSafety",
    "classify_with_vision",
    "execute_with_browser_use",
    "safety_for_stage",
]
