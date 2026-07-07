"""Goal loop kernel (第二阶段 v4, 阶段A).

The goal loop is the runtime self-evolution unit: a single menu / page / feature
goal advances through ``尝试 -> 失败分类 -> 套路动作 -> 经验沉淀 -> 再尝试`` until it
succeeds or hits a stop condition. This package adds only the two genuinely new
layers (the goal entity layer and the fixed failure classifier); everything else
reuses the existing ``iteration`` layer via :mod:`compat` (技术方案 §2.6).
"""

from __future__ import annotations

from . import classification, compat, playbook, predicates
from .ids import IdAllocator, parent_of
from .models import (
    DefectEscalation,
    EvidenceRef,
    ExperienceUpdate,
    FailureClassification,
    Goal,
    GoalAttempt,
    GoalStep,
    GoalSummary,
    PlaybookAction,
    SuccessCriterion,
)
from .playbook import PLAYBOOK_TABLE, PlaybookSpec, select_playbook
from .predicates import (
    DEFAULT_THRESHOLDS,
    PredicateResult,
    StopEvaluation,
    Thresholds,
    evaluate_stop_conditions,
    evaluate_success,
)
from .state_machine import GoalLoopEngine
from .writer import GoalLoopWriter

__all__ = [
    "classification",
    "compat",
    "playbook",
    "predicates",
    "IdAllocator",
    "parent_of",
    "DefectEscalation",
    "EvidenceRef",
    "ExperienceUpdate",
    "FailureClassification",
    "Goal",
    "GoalAttempt",
    "GoalStep",
    "GoalSummary",
    "PlaybookAction",
    "SuccessCriterion",
    "PLAYBOOK_TABLE",
    "PlaybookSpec",
    "select_playbook",
    "DEFAULT_THRESHOLDS",
    "PredicateResult",
    "StopEvaluation",
    "Thresholds",
    "evaluate_stop_conditions",
    "evaluate_success",
    "GoalLoopEngine",
    "GoalLoopWriter",
]
