from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.config.capability_routing import CapabilityRoutingDecision, CapabilityStageRoute
from prototype.stage2.app.discovery.strategy import select_discovery_strategy


def _build_stage_route(
    *,
    stage: str,
    allowed: bool,
    recommended_mode: str,
    reason_code: str,
    reason: str,
    routing_tags: list[str] | None = None,
) -> CapabilityStageRoute:
    return CapabilityStageRoute(
        stage=stage,
        allowed=allowed,
        recommended_mode=recommended_mode,
        reason_code=reason_code,
        reason=reason,
        routing_tags=list(routing_tags or []),
        capability_tags={"chat_completion": True},
    )


def _build_routing(
    *,
    discovery_route: CapabilityStageRoute,
    reporting_route: CapabilityStageRoute | None = None,
) -> CapabilityRoutingDecision:
    return CapabilityRoutingDecision(
        profile_name="AI-tester",
        model="AI-tester",
        gate_status="ok",
        gate_reason_code="capability_probe_ok",
        gate_reason="Capability preflight passed.",
        capability_tags={"chat_completion": True},
        routing_tags=list(discovery_route.routing_tags),
        discovery=discovery_route,
        verification=_build_stage_route(
            stage="verification",
            allowed=True,
            recommended_mode="playwright_deterministic_verification",
            reason_code="playwright_verification_ready",
            reason="Verification is allowed.",
            routing_tags=["verification_enabled"],
        ),
        reporting=reporting_route
        or _build_stage_route(
            stage="reporting",
            allowed=True,
            recommended_mode="llm_assisted_reporting",
            reason_code="llm_reporting_ready",
            reason="Reporting is allowed.",
            routing_tags=["reporting_enabled"],
        ),
    )


def test_strategy_keeps_template_seed_only_for_conservative_route() -> None:
    routing = _build_routing(
        discovery_route=_build_stage_route(
            stage="discovery",
            allowed=True,
            recommended_mode="template_seed_discovery",
            reason_code="template_seed_discovery_only",
            reason="Only conservative discovery is available.",
            routing_tags=["discovery_enabled", "template_seed_only"],
        )
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={},
        has_completed_discovery=False,
        allow_live_enrichment=True,
    )

    assert decision.selected_strategy == "template_seed_only"
    assert decision.should_seed_discovery is True
    assert decision.should_run_live_discovery is False
    assert decision.route_mode == "template_seed_discovery"
    assert "conservative template-seeded discovery" in " ".join(decision.notes)


def test_strategy_enables_live_enrichment_when_route_and_run_allow_it() -> None:
    routing = _build_routing(
        discovery_route=_build_stage_route(
            stage="discovery",
            allowed=True,
            recommended_mode="browser_use_structured_candidate",
            reason_code="browser_use_structured_candidate",
            reason="Structured discovery may be possible.",
            routing_tags=["discovery_enabled", "structured_candidate"],
        )
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={},
        has_completed_discovery=False,
        allow_live_enrichment=True,
    )

    assert decision.selected_strategy == "live_enrich"
    assert decision.should_seed_discovery is True
    assert decision.should_run_live_discovery is True
    assert decision.route_mode == "browser_use_structured_candidate"
    assert "Playwright-controlled enrichment" in " ".join(decision.notes)


def test_strategy_reuses_completed_discovery_when_requested() -> None:
    routing = _build_routing(
        discovery_route=_build_stage_route(
            stage="discovery",
            allowed=True,
            recommended_mode="browser_use_chatopenai_structured",
            reason_code="browser_use_openai_structured_ready",
            reason="A stronger discovery route is available.",
            routing_tags=["discovery_enabled", "browser_use_structured", "openai_wrapper"],
        )
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={"skip_completed_discovery": True},
        has_completed_discovery=True,
        allow_live_enrichment=True,
    )

    assert decision.selected_strategy == "skip_completed_discovery"
    assert decision.should_seed_discovery is False
    assert decision.should_run_live_discovery is False
    assert decision.reuse_completed_discovery is True
    assert "reuse was explicitly requested" in " ".join(decision.notes)


def test_strategy_does_not_reuse_completed_discovery_without_available_artifacts() -> None:
    routing = _build_routing(
        discovery_route=_build_stage_route(
            stage="discovery",
            allowed=True,
            recommended_mode="browser_use_structured_candidate",
            reason_code="browser_use_structured_candidate",
            reason="Structured discovery may be possible.",
            routing_tags=["discovery_enabled", "structured_candidate"],
        )
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={"skip_completed_discovery": True},
        has_completed_discovery=False,
        allow_live_enrichment=True,
    )

    assert decision.selected_strategy == "live_enrich"
    assert decision.should_seed_discovery is True
    assert decision.should_run_live_discovery is True
    assert decision.reuse_completed_discovery is False
    assert "no completed discovery artifacts are available" in " ".join(decision.notes)
    assert decision.route_summary["recommended_mode"] == "browser_use_structured_candidate"


def test_strategy_falls_back_to_template_seed_when_reuse_was_requested_but_live_enrichment_is_disabled() -> None:
    routing = _build_routing(
        discovery_route=_build_stage_route(
            stage="discovery",
            allowed=True,
            recommended_mode="browser_use_structured_candidate",
            reason_code="browser_use_structured_candidate",
            reason="Structured discovery may be possible.",
            routing_tags=["discovery_enabled", "structured_candidate"],
        )
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={"skip_completed_discovery": True},
        has_completed_discovery=False,
        allow_live_enrichment=False,
    )

    assert decision.selected_strategy == "template_seed_only"
    assert decision.should_seed_discovery is True
    assert decision.should_run_live_discovery is False
    assert decision.reuse_completed_discovery is False
    assert decision.reason_code == "live_enrichment_disabled"
    notes = " ".join(decision.notes)
    assert "no completed discovery artifacts are available" in notes
    assert "live enrichment is disabled for this run" in notes


def test_strategy_blocks_discovery_for_reporting_only_preflight_failure() -> None:
    routing = _build_routing(
        discovery_route=_build_stage_route(
            stage="discovery",
            allowed=False,
            recommended_mode="blocked_missing_capability_probe",
            reason_code="capability_probe_missing",
            reason="Capability probe is missing.",
            routing_tags=["probe_missing", "reporting_only"],
        ),
        reporting_route=_build_stage_route(
            stage="reporting",
            allowed=True,
            recommended_mode="preflight_failure_report",
            reason_code="preflight_failure_report_only",
            reason="Only failure reporting is allowed.",
            routing_tags=["reporting_enabled", "reporting_only"],
        ),
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={},
        has_completed_discovery=False,
        allow_live_enrichment=True,
    )

    assert decision.selected_strategy == "blocked"
    assert decision.should_seed_discovery is False
    assert decision.should_run_live_discovery is False
    assert decision.reporting_only is True
    assert decision.route_mode == "blocked_missing_capability_probe"


def test_strategy_falls_back_to_template_seed_when_live_enrichment_is_disabled() -> None:
    routing = _build_routing(
        discovery_route=_build_stage_route(
            stage="discovery",
            allowed=True,
            recommended_mode="browser_use_chatdeepseek_structured",
            reason_code="browser_use_deepseek_structured_ready",
            reason="DeepSeek structured discovery is available.",
            routing_tags=["discovery_enabled", "browser_use_structured", "deepseek_wrapper"],
        )
    )

    decision = select_discovery_strategy(
        capability_routing=routing,
        execution_hints={"skip_completed_discovery": False},
        has_completed_discovery=False,
        allow_live_enrichment=False,
    )

    assert decision.selected_strategy == "template_seed_only"
    assert decision.should_seed_discovery is True
    assert decision.should_run_live_discovery is False
    assert decision.reason_code == "live_enrichment_disabled"
    assert "Browser Use readiness remains a routing hint" in " ".join(decision.notes)
