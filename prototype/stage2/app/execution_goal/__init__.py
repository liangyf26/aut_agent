"""
Stage E: execution, evidence and retrospective loop.

Executes the low-risk test cases Stage D generated, records step-level
evidence (actions, network events, screenshots, feedback), concludes each
case's feature goal via the SAME "feature" goal_type success predicate
Stage D left open (feature_identified AND case_generated AND
basic_path_executed AND has_feedback — see goal_loop.predicates), and
projects the run onto the existing iteration layer for failure clusters,
retry planning and the next-round decision (技术方案 §2.6).
"""

from .browser_use_executor import (
    BrowserUseResult,
    BrowserUseSafety,
    execute_with_browser_use,
    safety_for_stage,
)
from .execution_adapter import ExecutionAdapter
from .execution_runner import simulate_test_case_execution, ExecutionOutcome
from .failure_adviser import FailureAdvice, analyze_failure, analyze_round_failures
from .loader import load_execution_goals_from_test_cases
from .orchestrator import ExecutionGoalOrchestrator
from .preflight import ExecutionLayerAvailability, run_execution_preflight

__all__ = [
    "BrowserUseResult",
    "BrowserUseSafety",
    "ExecutionAdapter",
    "ExecutionGoalOrchestrator",
    "ExecutionLayerAvailability",
    "ExecutionOutcome",
    "FailureAdvice",
    "analyze_failure",
    "analyze_round_failures",
    "execute_with_browser_use",
    "load_execution_goals_from_test_cases",
    "run_execution_preflight",
    "safety_for_stage",
    "simulate_test_case_execution",
]
