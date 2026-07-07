"""
Stage F: cross-system validation and platform deepening.

Verifies the SAME goal_loop kernel (goal_loop.state_machine.GoalLoopEngine)
generalizes across more than one real business system, and separates
project-local experience from experience that is stable enough to promote to
the platform level (技术方案 §2.3 分层沉淀 / 实施计划 §8).

No new goal types, failure classes, or playbooks are introduced. Stage F adds
exactly two genuinely new things, per 实施计划 §8.1/§8.5-8.6:

- ``SystemProfile`` — a label for "which real system did this run target",
  since nothing upstream enforces a system-scoped identifier (only the loose
  ``project_name``/``template_name`` pair in config.run_policy_loader).
- Promotion-gate logic (``promotion_reviewer``) that only allows an
  ``ExperienceUpdate`` to reach ``promotion_level="platform"`` if it recurs
  across >= 2 distinct systems, never on a single system's repetition alone.
"""

from .system_registry import SystemProfile
from .cross_system_adapter import CrossSystemAdapter
from .comparison import (
    CrossSystemFailureComparison,
    compare_failure_classifications,
)
from .promotion_reviewer import (
    PromotionReview,
    review_experience_updates,
)
from .orchestrator import CrossSystemGoalOrchestrator

__all__ = [
    "SystemProfile",
    "CrossSystemAdapter",
    "CrossSystemFailureComparison",
    "compare_failure_classifications",
    "PromotionReview",
    "review_experience_updates",
    "CrossSystemGoalOrchestrator",
]
