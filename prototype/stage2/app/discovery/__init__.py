"""Minimal discovery subsystem for the stage 2 prototype."""

from .identity import build_feature_point_identity, build_page_entry_identity, generalize_url_for_identity
from .live import LiveDiscoveryConfig, plan_live_discovery, run_live_discovery_session
from .models import DiscoveryResult
from .planner import DiscoveryPlanner
from .writer import DiscoveryArtifactWriter

__all__ = [
    "build_feature_point_identity",
    "build_page_entry_identity",
    "DiscoveryArtifactWriter",
    "DiscoveryPlanner",
    "DiscoveryResult",
    "generalize_url_for_identity",
    "LiveDiscoveryConfig",
    "plan_live_discovery",
    "run_live_discovery_session",
]
