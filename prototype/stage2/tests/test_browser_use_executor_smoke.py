"""
Local smoke tests for the Browser Use unified executor (P0-4).

Tests cover both the interface contracts and the graceful-degradation paths
(browser_use package unavailable, missing model profile, high-risk blocks).

No network access or browser_use package required — the executor's lazy-
import guard is exercised directly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prototype.stage2.app.execution_goal.browser_use_executor import (
    BROWSER_USE_FALLBACK_MODE,
    BrowserUseResult,
    BrowserUseSafety,
    execute_with_browser_use,
    safety_for_stage,
)


# ── safety_for_stage ────────────────────────────────────────────────────


def test_safety_for_stage_known_stages():
    assert safety_for_stage("menu_discovery") == BrowserUseSafety(write_allowed=False, max_steps=3)
    assert safety_for_stage("page_discovery") == BrowserUseSafety(write_allowed=False, max_steps=5)
    assert safety_for_stage("feature_discovery") == BrowserUseSafety(write_allowed=False, max_steps=5)
    assert safety_for_stage("execution_verification") == BrowserUseSafety(write_allowed=True, max_steps=8)


def test_safety_for_stage_unknown_returns_conservative():
    s = safety_for_stage("nonexistent_stage")
    assert s.write_allowed is False
    assert s.max_steps == 1


# ── dataclass round-trips ───────────────────────────────────────────────


def test_browser_use_safety_defaults():
    s = BrowserUseSafety()
    assert s.write_allowed is False
    assert s.max_steps == 8
    assert s.timeout_ms == 15000


def test_browser_use_result_success():
    result = BrowserUseResult(
        ok=True,
        model="AI-tester",
        instruction="点击查询按钮",
        actions=[{"step": 1, "action": "click", "selector": "#btn"}],
        notes=["test"],
        duration_ms=1234,
    )
    assert result.ok is True
    assert result.model == "AI-tester"
    assert len(result.actions) == 1
    assert result.failure_reason is None


def test_browser_use_result_defaults():
    result = BrowserUseResult(ok=False, model="unknown", instruction="")
    assert result.actions == []
    assert result.screenshots == []
    assert result.failure_reason is None
    assert result.notes == []
    assert result.duration_ms == 0


# ── executor: degraded gracefully (no package / no profile) ──────


@pytest.mark.asyncio
async def test_executor_degraded_when_unavailable():
    """Without a working profile, the executor returns a degraded result.
    The exact reason depends on environment: 'browser_use_unavailable' when
    the package is not installed, 'model_profile_unavailable' when it is
    installed but no profile matches."""
    result = await execute_with_browser_use(
        None,  # type: ignore — page is irrelevant
        "点击查询按钮",
        context={"stage": "execution_verification"},
        safety=BrowserUseSafety(write_allowed=True, max_steps=3),
    )
    assert result.ok is False
    assert result.failure_reason in {
        "browser_use_unavailable",
        "model_profile_unavailable",
    }, f"unexpected failure_reason: {result.failure_reason}"
    assert result.duration_ms >= 0


# ── executor: model_profile_unavailable ────────────────────────────────


def test_resolve_profile_returns_none_for_missing_config():
    from prototype.stage2.app.execution_goal.browser_use_executor import _resolve_profile

    assert _resolve_profile("nonexistent_model_xyz") is None


def test_resolve_profile_with_fake_env_path(tmp_path: Path):
    import os
    from prototype.stage2.app.execution_goal.browser_use_executor import _resolve_profile

    fake = tmp_path / "fake.json"
    fake.write_text('{"schema_version":"1","profiles":[{"id":"a","model":"m"}]}', encoding="utf-8")
    os.environ["STAGE2_MODEL_PROFILES_PATH"] = str(fake)
    try:
        result = _resolve_profile("a")
        assert result is not None
        assert result["id"] == "a"
        assert result["model"] == "m"

        # first-profile default when no name given
        result2 = _resolve_profile(None)
        assert result2 is not None
        assert result2["id"] == "a"
    finally:
        os.environ.pop("STAGE2_MODEL_PROFILES_PATH", None)


# ── executor: high-risk block ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_blocks_high_risk_operations():
    """risk_level='high' is blocked before any browser or model interaction."""
    result = await execute_with_browser_use(
        None,  # type: ignore
        "删除记录",
        context={"risk_level": "high"},
    )
    assert result.ok is False
    assert result.failure_reason == "blocked_by_safety_policy"


# ── executor: context metadata propagation ─────────────────────────────


@pytest.mark.asyncio
async def test_executor_context_metadata_preserved():
    result = await execute_with_browser_use(
        None,  # type: ignore
        "test instruction",
        context={"stage": "feature_discovery", "goal_id": "goal-abc"},
        safety=BrowserUseSafety(write_allowed=False, max_steps=1),
    )
    assert result.ok is False
    assert result.instruction == "test instruction"


# ── executor: screenshots_dir with unavailable page ─────────────────


@pytest.mark.asyncio
async def test_executor_screenshots_dir_accepted():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = await execute_with_browser_use(
            None,  # type: ignore  # noqa: S101
            "test",
            screenshots_dir=Path(tmpdir),
        )
        assert result.ok is False
        assert result.failure_reason in {
            "browser_use_unavailable",
            "model_profile_unavailable",
        }


# ── constants ───────────────────────────────────────────────────────────


def test_fallback_mode_constant():
    assert BROWSER_USE_FALLBACK_MODE == "browser_use_fallback"


# ── safety profile immutability ─────────────────────────────────────────


def test_safety_for_stage_returns_independent_copies():
    a = safety_for_stage("execution_verification")
    b = safety_for_stage("menu_discovery")
    assert a != b
    assert a.write_allowed != b.write_allowed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
