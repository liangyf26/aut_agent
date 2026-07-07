"""Minimal discovery subsystem for the stage 2 prototype."""

from .identity import build_feature_point_identity, build_page_entry_identity, generalize_url_for_identity
from .live import LiveDiscoveryConfig, plan_live_discovery, run_live_discovery_session
from .models import DiscoveryResult
from .planner import DiscoveryPlanner
from .review import apply_discovery_review_patch, load_discovery_review_patch
from .summary import build_discovery_views, build_navigation_tree, build_page_semantic_summary
from .writer import DiscoveryArtifactWriter

__all__ = [
    "apply_discovery_review_patch",
    "build_feature_point_identity",
    "build_discovery_views",
    "build_navigation_tree",
    "build_page_semantic_summary",
    "build_page_entry_identity",
    "DiscoveryArtifactWriter",
    "DiscoveryPlanner",
    "DiscoveryResult",
    "generalize_url_for_identity",
    "LiveDiscoveryConfig",
    "load_discovery_review_patch",
    "plan_live_discovery",
    "run_live_discovery_session",
]
