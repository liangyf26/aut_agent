"""
Execution goal orchestrator for Stage E.

Drives the goal loop over every test case Stage D generated: activates one
execution goal at a time (enforcing the engine's single-active-goal
invariant, 技术方案 §4.3), simulates its basic path, records step-level
evidence, and concludes success/failure via ``ExecutionAdapter``.

IMPORTANT — batch halts on a paused goal, by design: ``assertion_failed``,
``permission_blocked``, ``login_required``, ``missing_prerequisite_data``
and ``blocked_by_safety_policy`` are all in the fixed playbook's
``HUMAN_REQUIRED_CLASSES`` (Stage A, ``goal_loop/playbook.py``) — a case
that fails with one of these classes pauses its goal as
``waiting_human``/``blocked_by_*`` (a PAUSED, non-terminal status). The
engine refuses to advance the frontier past an unresolved paused goal
(``activate_next`` raises), so ``execute_all`` stops the batch right there
rather than silently skipping ahead. This matches 技术方案 §4.11: "当系统被
安全策略、权限、前置数据或执行器问题阻断时，应输出结构化接管包... 再继续下一轮" —
remaining cases stay queued in ``engine.frontier`` for the next round, not
silently dropped or force-run out of order.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine

RealBrowserRunner = Callable[..., Awaitable["ExecutionOutcome"]]

from ..goal_loop.models import PAUSED_STATUSES
from ..goal_loop.resolution_writer import write_human_takeover_resolution
from .execution_adapter import ExecutionAdapter
from .execution_fixture_writer import (
    write_action_log,
    write_execution_results,
    write_network_events,
    write_screenshots_index,
)
from .execution_runner import ExecutionOutcome, simulate_test_case_execution
from .human_tasks_writer import write_human_takeover, write_human_tasks
from .loader import load_execution_goals_from_test_case_list, load_execution_goals_from_test_cases
from .round_writer import resolve_retryable_test_cases, write_next_round_plan, write_round_analysis
from .run_report_writer import write_run_report


class ExecutionGoalOrchestrator:
    """Orchestrator for Stage E's execution / evidence / retrospective session."""

    def __init__(
        self,
        engine: "GoalLoopEngine | None" = None,
        *,
        output_dir: str | Path = "output",
        run_id: str = "execution_run_001",
    ):
        from ..goal_loop.state_machine import GoalLoopEngine

        self.engine = engine or GoalLoopEngine(run_id=run_id)
        self.adapter = ExecutionAdapter(self.engine)
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._outcomes: list[ExecutionOutcome] = []
        self.halted_early = False

    def create_root_goal(self, description: str = "Execute discovered feature test cases") -> str:
        """Register a bookkeeping root goal for lineage only.

        Unlike a real execution goal, the root has no test case and no
        satisfiable success predicate (feature success requires
        basic_path_executed/has_feedback, which do not apply to an empty
        container). It is removed from ``engine.frontier`` immediately so
        ``activate_next()`` — which this orchestrator uses for real,
        single-active-goal semantics (see module docstring) — never picks it
        up and stalls waiting for it to be resolved. It stays in
        ``engine.goals`` so child goals still carry a valid
        ``parent_goal_id`` lineage.
        """

        goal = self.engine.register_goal(
            goal_type="feature",
            goal_name=description,
            parent_goal_id=None,
            origin="root::execution",
        )
        if goal.goal_id in self.engine.frontier:
            self.engine.frontier.remove(goal.goal_id)
        return goal.goal_id

    def load_test_cases(self, test_cases_path: str | Path) -> list[str]:
        root_goals = [g for g in self.engine.goals.values() if g.origin == "root::execution"]
        if not root_goals:
            raise RuntimeError("No root goal found. Call create_root_goal() first.")
        return load_execution_goals_from_test_cases(
            self.engine,
            self.adapter,
            test_cases_path,
            parent_goal_id=root_goals[0].goal_id,
        )

    def load_test_cases_from_list(self, test_cases: list[dict], *, round_index: int = 1) -> list[str]:
        """Same registration as :meth:`load_test_cases`, from an in-memory list.

        For callers (e.g. a real-browser verification driver, or
        :meth:`run_until_stable` re-registering a retried test_case) that
        build cases directly in Python rather than reading Stage D's
        ``generated_test_cases.json`` off disk.
        """

        root_goals = [g for g in self.engine.goals.values() if g.origin == "root::execution"]
        if not root_goals:
            raise RuntimeError("No root goal found. Call create_root_goal() first.")
        return load_execution_goals_from_test_case_list(
            self.engine,
            self.adapter,
            test_cases,
            parent_goal_id=root_goals[0].goal_id,
            round_index=round_index,
        )

    def execute_all(self, *, injected_failures: dict[str, str] | None = None) -> list[ExecutionOutcome]:
        """Run every queued execution goal's basic path until the frontier is
        exhausted or a goal pauses for human resolution.

        Args:
            injected_failures: optional ``test_case_id -> fixed_failure_class``
                overrides, for tests / future real-runner integration.

        Returns:
            The list of :class:`ExecutionOutcome` produced this call, in
            execution order. ``self.halted_early`` is set if the batch
            stopped on a paused goal with cases still left in the frontier.

        Raises:
            RuntimeError: if ``activate_next()`` promotes a goal this
                orchestrator did not register (no execution context) — this
                can only happen if the ``engine`` passed to the constructor
                is shared with another goal producer whose goals are mixed
                into the same frontier. Such a goal is left permanently
                STATUS_RUNNING (this orchestrator has no basic path to run
                for it and no basis to resolve it), so every SUBSEQUENT call
                to ``execute_all``/``activate_next`` on this engine will also
                raise until the caller resolves it directly. Outcomes already
                recorded before this point are NOT lost: each is appended to
                ``self._outcomes`` immediately after ``_execute_one``
                returns, not batched at the end of the loop.
        """

        injected_failures = injected_failures or {}
        outcomes: list[ExecutionOutcome] = []

        while True:
            goal = self.engine.activate_next()
            if goal is None:
                break

            context = self.adapter.get_execution_context(goal.goal_id)
            if context is None:
                raise RuntimeError(
                    f"activate_next() promoted goal {goal.goal_id!r}, which this "
                    "ExecutionGoalOrchestrator did not register (no execution "
                    "context). This goal is now stuck at STATUS_RUNNING — "
                    "resolve it directly via engine.record_success()/"
                    "record_failure()/supersede_active() before calling "
                    "execute_all() again. This orchestrator's engine must not "
                    "be shared with another goal producer unless their "
                    "frontiers are kept disjoint."
                )

            outcome = self._execute_one(
                goal.goal_id,
                context,
                injected_failure=injected_failures.get(context["test_case_id"]),
            )
            outcomes.append(outcome)
            self._outcomes.append(outcome)

            stop_eval = self.engine.evaluate_stop(goal.goal_id)
            if stop_eval.target_status in PAUSED_STATUSES:
                self.halted_early = True
                break

        return outcomes

    async def execute_all_async(
        self,
        *,
        runner: RealBrowserRunner,
        runner_kwargs: dict[str, Any] | None = None,
        injected_failures: dict[str, str] | None = None,
    ) -> list[ExecutionOutcome]:
        """Real-browser counterpart to :meth:`execute_all`.

        Mirrors the SAME frontier-advancement / single-active-goal / pause-halts-
        the-batch semantics as :meth:`execute_all` (see that method's and this
        class's docstrings) — the only difference is that each case's basic path
        is driven by an awaited ``runner`` (e.g.
        ``real_browser_runner.execute_test_case_with_playwright``) instead of
        the fixture-simulated ``simulate_test_case_execution``. The synchronous
        ``execute_all``/`_execute_one`` path is untouched by this method.

        Args:
            runner: an async callable with the same contract as
                ``execute_test_case_with_playwright`` — called as
                ``runner(test_case, goal_id=..., injected_failure=..., **runner_kwargs)``
                and returning an :class:`ExecutionOutcome`.
            runner_kwargs: extra keyword arguments forwarded to every call
                (e.g. ``page``, ``screenshots_dir``).
            injected_failures: optional ``test_case_id -> fixed_failure_class``
                overrides, same contract as :meth:`execute_all`.
        """

        runner_kwargs = runner_kwargs or {}
        injected_failures = injected_failures or {}
        outcomes: list[ExecutionOutcome] = []

        while True:
            goal = self.engine.activate_next()
            if goal is None:
                break

            context = self.adapter.get_execution_context(goal.goal_id)
            if context is None:
                raise RuntimeError(
                    f"activate_next() promoted goal {goal.goal_id!r}, which this "
                    "ExecutionGoalOrchestrator did not register (no execution "
                    "context). This goal is now stuck at STATUS_RUNNING — "
                    "resolve it directly via engine.record_success()/"
                    "record_failure()/supersede_active() before calling "
                    "execute_all_async() again. This orchestrator's engine must not "
                    "be shared with another goal producer unless their "
                    "frontiers are kept disjoint."
                )

            outcome = await self._execute_one_async(
                goal.goal_id,
                context,
                runner=runner,
                runner_kwargs=runner_kwargs,
                injected_failure=injected_failures.get(context["test_case_id"]),
            )
            outcomes.append(outcome)
            self._outcomes.append(outcome)

            stop_eval = self.engine.evaluate_stop(goal.goal_id)
            if stop_eval.target_status in PAUSED_STATUSES:
                self.halted_early = True
                break

        return outcomes

    async def _execute_one_async(
        self,
        goal_id: str,
        context: dict,
        *,
        runner: RealBrowserRunner,
        runner_kwargs: dict[str, Any],
        injected_failure: str | None,
    ) -> ExecutionOutcome:
        test_case = context["test_case"]
        outcome = await runner(
            test_case,
            goal_id=goal_id,
            injected_failure=injected_failure,
            **runner_kwargs,
        )

        attempt_id = self.adapter.record_execution_attempt(goal_id=goal_id)
        for action_record in outcome.actions:
            self.adapter.record_action(attempt_id=attempt_id, action_record=action_record)
        if outcome.page_feedback:
            self.adapter.record_feedback(attempt_id=attempt_id, feedback=outcome.page_feedback)
        self.adapter.record_network_capture(
            attempt_id=attempt_id,
            network_events=outcome.network_events,
            capture_status="not_applicable_fixture_mode" if outcome.execution_mode == "fixture_simulated" else "captured",
        )
        for screenshot_ref in outcome.screenshot_refs:
            self.adapter.record_screenshot(attempt_id=attempt_id, screenshot_ref=screenshot_ref)

        self.adapter.conclude_execution(attempt_id=attempt_id, outcome=outcome)
        return outcome

    async def run(
        self,
        *,
        mode: str = "fixture_simulated",
        page: Any = None,
        screenshots_dir: Path | None = None,
        injected_failures: dict[str, str] | None = None,
    ) -> list[ExecutionOutcome]:
        """Run every queued execution goal via either execution mode.

        This is a convenience dispatcher over :meth:`execute_all` (default,
        fixture-simulated, unchanged) and :meth:`execute_all_async` (real
        browser) so a caller (CLI entrypoint, verification script) can pick
        the mode via one string argument instead of knowing which of the two
        underlying methods to call and how to build the runner closure
        itself. Neither underlying method is modified by this wrapper.

        Args:
            mode: ``"fixture_simulated"`` (default) or ``"real_browser"``.
            page: a live Playwright ``Page``, required when
                ``mode == "real_browser"``.
            screenshots_dir: directory real screenshots are written under,
                required when ``mode == "real_browser"``.
            injected_failures: forwarded to whichever underlying method runs.

        Raises:
            ValueError: for an unrecognized ``mode``, or a missing
                ``page``/``screenshots_dir`` when ``mode == "real_browser"``.
        """

        if mode == "fixture_simulated":
            return self.execute_all(injected_failures=injected_failures)

        if mode == "real_browser":
            if page is None or screenshots_dir is None:
                raise ValueError(
                    "mode='real_browser' requires both page and screenshots_dir"
                )
            from .real_browser_runner import execute_test_case_with_playwright

            async def runner(test_case: dict, *, goal_id: str | None, injected_failure: str | None):
                return await execute_test_case_with_playwright(
                    page,
                    test_case,
                    goal_id=goal_id,
                    screenshots_dir=screenshots_dir,
                    injected_failure=injected_failure,
                )

            return await self.execute_all_async(runner=runner, injected_failures=injected_failures)

        raise ValueError(f"unrecognized execution mode: {mode!r}; expected 'fixture_simulated' or 'real_browser'")

    async def run_until_stable(
        self,
        initial_test_cases: list[dict],
        *,
        mode: str = "fixture_simulated",
        max_rounds: int = 1,
        page: Any = None,
        screenshots_dir: Path | None = None,
        injected_failures: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run round 1, then keep auto-advancing through retryable failures
        (方案 §13 exit=retry) within THIS process, up to ``max_rounds``.

        This is the round-to-round closing of the loop that
        ``round_analysis.json``/``next_round_plan.json`` alone never
        provided (those files were write-only — nothing consumed them to
        actually start a next round). It works by calling :meth:`run` /
        :meth:`load_test_cases_from_list` repeatedly on the SAME in-memory
        ``GoalLoopEngine`` instance within this one call, never by
        serializing/reconstructing engine state across process boundaries
        (``GoalLoopEngine`` has no such serialization — see
        ``goal_loop/resolution_writer.py``'s docstring for the related
        human-takeover-resolve design).

        Auto-advance stops (before starting a NEW round) as soon as any of:
          - a goal paused (``self.halted_early``): the engine's active goal
            is now stuck non-terminal, and ``activate_next()`` would raise
            on any further call — this is a hard stop, not a choice. A human
            must resolve the takeover first (see
            ``main.resolve_goal_loop_takeover_entrypoint``) and then re-invoke
            this command fresh.
          - ``mode == "real_browser"``: real-browser rounds ALWAYS stop after
            round 1 regardless of ``max_rounds`` or retryable failures — this
            is a deliberate safety boundary (a retryable failure_class like
            LOCATOR_UNSTABLE means "try a different locator against the real
            production system", which must not happen unattended).
          - no retryable failures remain (converged).
          - ``round_index >= max_rounds`` (budget exhausted).

        Args:
            initial_test_cases: round 1's test cases (same shape as
                ``generated_test_cases.json``).
            mode: forwarded to :meth:`run` each round.
            max_rounds: upper bound on rounds attempted (default 1 — no
                auto-advance, matching the pre-existing single-round
                behavior exactly).
            page, screenshots_dir: forwarded to :meth:`run` when
                ``mode == "real_browser"``.

        Returns:
            One summary dict per round actually run: ``{"round_index":,
            "outcome_count":, "retryable_count":, "blocked_reasons":,
            "stopped_reason": <set only on the LAST round's entry>}``.
        """

        rounds: list[dict[str, Any]] = []
        round_index = 1
        round_goal_ids = set(self.load_test_cases_from_list(initial_test_cases, round_index=round_index))

        while True:
            round_injected_failures = injected_failures if round_index == 1 else None
            outcomes = await self.run(
                mode=mode,
                page=page,
                screenshots_dir=screenshots_dir,
                injected_failures=round_injected_failures,
            )
            # Scoped to THIS round's goal_ids only: the engine never deletes
            # a prior round's terminal goal, it accumulates a new one
            # alongside it — an unscoped scan would keep re-surfacing round
            # 1's already-superseded failure after round 2 already passed
            # the same test_case.
            decision = resolve_retryable_test_cases(self.engine, self.adapter, goal_ids=round_goal_ids)
            retryable = decision["retryable"]
            blocked_reasons = decision["blocked_reasons"]

            round_summary: dict[str, Any] = {
                "round_index": round_index,
                "outcome_count": len(outcomes),
                "retryable_count": len(retryable),
                "blocked_reasons": blocked_reasons,
            }

            if self.halted_early:
                round_summary["stopped_reason"] = (
                    "paused_on_human_takeover: a goal is waiting_human/blocked_* — "
                    "resolve it (--resolve-goal-loop-takeover) before re-invoking "
                    "this command; auto-advance cannot continue past an unresolved "
                    "active goal."
                )
                rounds.append(round_summary)
                break

            if mode == "real_browser":
                round_summary["stopped_reason"] = (
                    "real_browser_round_limit: real-browser rounds always stop after "
                    "one round regardless of max_rounds; resolve/verify manually, then "
                    "re-invoke with the retryable test_case(s) if you want to retry."
                    if retryable
                    else "converged: no retryable failures after round 1."
                )
                rounds.append(round_summary)
                break

            if not retryable:
                round_summary["stopped_reason"] = "converged: no retryable failures remain."
                rounds.append(round_summary)
                break

            if round_index >= max_rounds:
                round_summary["stopped_reason"] = (
                    f"max_rounds_reached: {len(retryable)} retryable failure(s) remain "
                    f"but max_rounds={max_rounds} was hit."
                )
                rounds.append(round_summary)
                break

            rounds.append(round_summary)
            round_index += 1
            round_goal_ids = set(
                self.load_test_cases_from_list(
                    [item["test_case"] for item in retryable], round_index=round_index
                )
            )

        return rounds

    def _execute_one(self, goal_id: str, context: dict, *, injected_failure: str | None) -> ExecutionOutcome:
        test_case = context["test_case"]
        outcome = simulate_test_case_execution(test_case, goal_id=goal_id, injected_failure=injected_failure)

        attempt_id = self.adapter.record_execution_attempt(goal_id=goal_id)
        for action_record in outcome.actions:
            self.adapter.record_action(attempt_id=attempt_id, action_record=action_record)
        if outcome.page_feedback:
            self.adapter.record_feedback(attempt_id=attempt_id, feedback=outcome.page_feedback)
        self.adapter.record_network_capture(
            attempt_id=attempt_id,
            network_events=outcome.network_events,
            capture_status="not_applicable_fixture_mode" if outcome.execution_mode == "fixture_simulated" else "captured",
        )
        for screenshot_ref in outcome.screenshot_refs:
            self.adapter.record_screenshot(attempt_id=attempt_id, screenshot_ref=screenshot_ref)

        self.adapter.conclude_execution(attempt_id=attempt_id, outcome=outcome)
        return outcome

    # --- exports -------------------------------------------------------

    def export_execution_results(self, filename: str = "execution_results.json") -> Path:
        return write_execution_results(self._outcomes, self.output_dir / filename)

    def export_action_log(self, filename: str = "action_log.jsonl") -> Path:
        return write_action_log(self.engine, self.adapter, self.output_dir / filename)

    def export_network_events(self, filename: str = "network_events.json") -> Path:
        return write_network_events(self._outcomes, self.output_dir / filename)

    def export_screenshots_index(self, filename: str = "screenshots_index.json") -> Path:
        return write_screenshots_index(self._outcomes, self.output_dir / filename)

    def export_human_tasks(self, filename: str = "human_tasks.json") -> Path:
        return write_human_tasks(
            self.engine, self.adapter, self._outcomes, self.run_id, self.output_dir / filename
        )

    def export_human_takeover(self, filename: str = "human_takeover.json") -> Path | None:
        return write_human_takeover(
            self.engine,
            self.adapter,
            self._outcomes,
            self.run_id,
            self.output_dir,
            self.output_dir / filename,
        )

    def export_human_takeover_resolution(
        self,
        *,
        status: str,
        operator_id: str | None = None,
        note: str | None = None,
        ready_to_resume: bool = False,
        resolved_at: str,
        filename: str = "human_takeover_resolution.json",
    ) -> Path:
        return write_human_takeover_resolution(
            self.output_dir,
            status=status,
            operator_id=operator_id,
            note=note,
            ready_to_resume=ready_to_resume,
            resolved_at=resolved_at,
            filename=filename,
        )

    def export_round_analysis(self, filename: str = "round_analysis.json") -> Path:
        return write_round_analysis(self.engine, self.run_id, self.output_dir / filename)

    def export_next_round_plan(self, filename: str = "next_round_plan.json") -> Path:
        return write_next_round_plan(
            self.engine,
            self.run_id,
            self.output_dir / filename,
            decision_alias_path=self.output_dir / "next_round_decision.json",
        )

    def export_run_report(self) -> tuple[Path, Path]:
        return write_run_report(self.engine, self._outcomes, self.run_id, self.output_dir)

    def get_summary(self) -> dict:
        from collections import Counter

        status_counts = Counter()
        for goal in self.engine.goals.values():
            if not goal.origin or not goal.origin.startswith("feature_execution::"):
                continue
            status_counts[goal.status] += 1

        case_kind_counts = Counter(outcome.case_kind for outcome in self._outcomes)
        outcome_status_counts = Counter(outcome.status for outcome in self._outcomes)
        pending_authorization = sum(1 for outcome in self._outcomes if outcome.requires_human_authorization)

        return {
            "run_id": self.run_id,
            "total_execution_goals": sum(status_counts.values()),
            "goal_status_breakdown": dict(status_counts),
            "executed_case_count": len(self._outcomes),
            "case_kind_breakdown": dict(case_kind_counts),
            "outcome_status_breakdown": dict(outcome_status_counts),
            "pending_human_authorization_count": pending_authorization,
            "halted_early": self.halted_early,
            "remaining_in_frontier": len(self.engine.frontier),
        }

    def export_run_summary(self, filename: str = "run_summary.json", *, extra: dict | None = None) -> Path:
        """Export run_summary.json with execution session statistics.

        Sibling to menu_goal/page_goal/feature_goal's own
        ``export_goal_summary`` — all four goal packages write the SAME
        filename so the dashboard's goal-loop run scanner doesn't need
        per-package special-casing (方案: 运行中心可见性).

        ``extra`` merges caller-known fields ``get_summary()`` itself
        cannot see — e.g. ``rounds_run``/``round_history``/``stopped_reason``,
        which only exist in ``main.py``'s ``run_until_stable`` call site,
        not on the orchestrator/engine (a single orchestrator instance is
        reused across all rounds within one run, so it has no notion of
        "which round produced which outcome").

        Returns:
            Path to exported file
        """
        import json
        from datetime import datetime, timezone

        summary = self.get_summary()
        if extra:
            summary.update(extra)
        summary["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        output_path = self.output_dir / filename

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        return output_path


__all__ = ["ExecutionGoalOrchestrator"]
