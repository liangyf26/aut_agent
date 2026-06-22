from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from ..config.capability_routing import CapabilityRoutingDecision, CapabilityStageRoute

_STRATEGY_BLOCKED = "blocked"
_STRATEGY_SKIP_COMPLETED = "skip_completed_discovery"
_STRATEGY_TEMPLATE_SEED_ONLY = "template_seed_only"
_STRATEGY_LIVE_ENRICH = "live_enrich"
_TEMPLATE_SEED_MODE = "template_seed_discovery"
_PREFLIGHT_FAILURE_REPORT_MODE = "preflight_failure_report"


def _normalize_execution_hints(execution_hints: Mapping[str, Any] | None) -> dict[str, Any]:
    if not execution_hints:
        return {}
    return {str(key): value for key, value in execution_hints.items()}


def _is_reporting_only(
    capability_routing: CapabilityRoutingDecision | None,
    discovery_route: CapabilityStageRoute | None,
) -> bool:
    if discovery_route and "reporting_only" in discovery_route.routing_tags:
        return True
    reporting_route = capability_routing.reporting if capability_routing else None
    return bool(
        reporting_route
        and reporting_route.allowed
        and reporting_route.recommended_mode == _PREFLIGHT_FAILURE_REPORT_MODE
        and (discovery_route is None or not discovery_route.allowed)
    )


def _route_summary(route: CapabilityStageRoute | None) -> dict[str, Any]:
    return route.to_dict() if route else {}


@dataclass(frozen=True)
class DiscoveryStrategyDecision:
    selected_strategy: str
    should_seed_discovery: bool
    should_run_live_discovery: bool
    reason_code: str
    reason: str
    route_mode: str | None = None
    route_allowed: bool = False
    reuse_completed_discovery: bool = False
    reporting_only: bool = False
    execution_hints: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    route_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_discovery_strategy(
    *,
    capability_routing: CapabilityRoutingDecision | None,
    execution_hints: Mapping[str, Any] | None = None,
    has_completed_discovery: bool = False,
    allow_live_enrichment: bool = True,
) -> DiscoveryStrategyDecision:
    hints = _normalize_execution_hints(execution_hints)
    discovery_route = capability_routing.discovery if capability_routing else None
    reporting_only = _is_reporting_only(capability_routing, discovery_route)

    if discovery_route is None:
        return DiscoveryStrategyDecision(
            selected_strategy=_STRATEGY_BLOCKED,
            should_seed_discovery=False,
            should_run_live_discovery=False,
            reason_code="discovery_route_missing",
            reason="No discovery route was available, so the stage cannot choose a safe discovery strategy.",
            route_mode=None,
            route_allowed=False,
            reuse_completed_discovery=False,
            reporting_only=reporting_only,
            execution_hints=hints,
            notes=["Run capability routing before selecting a discovery strategy."],
            route_summary={},
        )

    if not discovery_route.allowed:
        notes = [discovery_route.reason]
        if reporting_only:
            notes.append("Discovery stays blocked and the run should keep only reportable preflight artifacts.")
        return DiscoveryStrategyDecision(
            selected_strategy=_STRATEGY_BLOCKED,
            should_seed_discovery=False,
            should_run_live_discovery=False,
            reason_code=discovery_route.reason_code,
            reason=discovery_route.reason,
            route_mode=discovery_route.recommended_mode,
            route_allowed=False,
            reuse_completed_discovery=False,
            reporting_only=reporting_only,
            execution_hints=hints,
            notes=notes,
            route_summary=_route_summary(discovery_route),
        )

    skip_completed_requested = bool(hints.get("skip_completed_discovery"))
    notes: list[str] = []

    if skip_completed_requested and has_completed_discovery:
        notes.append("This round reuses an already completed discovery result from a previous run.")
        if allow_live_enrichment and discovery_route.recommended_mode != _TEMPLATE_SEED_MODE:
            notes.append("Live enrichment stays off because reuse was explicitly requested for this round.")
        return DiscoveryStrategyDecision(
            selected_strategy=_STRATEGY_SKIP_COMPLETED,
            should_seed_discovery=False,
            should_run_live_discovery=False,
            reason_code="skip_completed_discovery",
            reason="Execution hints requested that the run should reuse the completed discovery output.",
            route_mode=discovery_route.recommended_mode,
            route_allowed=True,
            reuse_completed_discovery=True,
            reporting_only=reporting_only,
            execution_hints=hints,
            notes=notes,
            route_summary=_route_summary(discovery_route),
        )

    if skip_completed_requested and not has_completed_discovery:
        notes.append(
            "skip_completed_discovery was requested, but no completed discovery artifacts are available, "
            "so the run falls back to fresh discovery."
        )

    if discovery_route.recommended_mode == _TEMPLATE_SEED_MODE:
        notes.append("The current route only supports conservative template-seeded discovery.")
        return DiscoveryStrategyDecision(
            selected_strategy=_STRATEGY_TEMPLATE_SEED_ONLY,
            should_seed_discovery=True,
            should_run_live_discovery=False,
            reason_code=discovery_route.reason_code,
            reason=discovery_route.reason,
            route_mode=discovery_route.recommended_mode,
            route_allowed=True,
            reuse_completed_discovery=False,
            reporting_only=reporting_only,
            execution_hints=hints,
            notes=notes,
            route_summary=_route_summary(discovery_route),
        )

    if not allow_live_enrichment:
        notes.append("The route would allow stronger discovery, but live enrichment is disabled for this run.")
        notes.append(
            "Current Stage C live discovery is still Playwright-controlled enrichment; Browser Use readiness "
            "remains a routing hint, not the main discovery executor."
        )
        return DiscoveryStrategyDecision(
            selected_strategy=_STRATEGY_TEMPLATE_SEED_ONLY,
            should_seed_discovery=True,
            should_run_live_discovery=False,
            reason_code="live_enrichment_disabled",
            reason="Live enrichment was disabled for this run, so discovery stays on template seeding only.",
            route_mode=discovery_route.recommended_mode,
            route_allowed=True,
            reuse_completed_discovery=False,
            reporting_only=reporting_only,
            execution_hints=hints,
            notes=notes,
            route_summary=_route_summary(discovery_route),
        )

    notes.append(
        "Current Stage C live discovery is still Playwright-controlled enrichment layered on top of "
        "template seeding."
    )
    notes.append("Browser Use wrapper readiness remains a routing hint until the discovery mainline is upgraded.")
    return DiscoveryStrategyDecision(
        selected_strategy=_STRATEGY_LIVE_ENRICH,
        should_seed_discovery=True,
        should_run_live_discovery=True,
        reason_code="playwright_live_enrichment_selected",
        reason="The route allows a stronger discovery mode, so the run can seed first and then enrich live.",
        route_mode=discovery_route.recommended_mode,
        route_allowed=True,
        reuse_completed_discovery=False,
        reporting_only=reporting_only,
        execution_hints=hints,
        notes=notes,
        route_summary=_route_summary(discovery_route),
    )


__all__ = [
    "DiscoveryStrategyDecision",
    "select_discovery_strategy",
]
