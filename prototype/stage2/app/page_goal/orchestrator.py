"""
Page goal orchestrator for session lifecycle.

Coordinates menu_entries.json loading, page discovery execution,
and page_entries.json export with run-level aggregation.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..goal_loop.state_machine import GoalLoopEngine
from .page_adapter import PageAdapter
from .loader import load_page_goals_from_menu_fixture
from .page_fixture_writer import (
    write_page_fixture,
    collect_page_screenshots,
    map_goal_status_to_entry_status,
    safe_json_write,
)

if TYPE_CHECKING:
    from ..goal_loop.models import Goal


class PageGoalOrchestrator:
    """
    Session lifecycle manager for page goal loop.

    Coordinates:
    - menu_entries.json loading from Stage B
    - Page goal registration and discovery
    - page_entries.json export for Stage D
    - Run-level aggregation and summaries
    """

    def __init__(self, *, output_dir: str | Path, run_id: str | None = None):
        """
        Initialize page goal orchestrator.

        Creates output_dir if needed. Initializes GoalLoopEngine with run_id
        (auto-generated if None). Creates PageAdapter for page context tracking.

        Args:
            output_dir: Directory for output artifacts
            run_id: Optional run identifier (auto-generated if None)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Auto-generate run_id if not provided
        if run_id is None:
            run_id = f"page_run_{uuid.uuid4().hex[:8]}"

        self.run_id = run_id
        self.engine = GoalLoopEngine(run_id=run_id)
        self.adapter = PageAdapter(self.engine)
        self.root_goal_id: str | None = None

    def create_root_goal(
        self, *, description: str = "Discover all reachable pages"
    ) -> str:
        """
        Create root page discovery goal.

        Uses engine.register_goal(goal_type='page', goal_name, origin='page_discovery::root').
        Stores root_goal_id for aggregation.

        Args:
            description: Root goal description

        Returns:
            Root goal ID
        """
        goal = self.engine.register_goal(
            goal_type="page",
            goal_name=description,
            origin="page_discovery::root",
        )
        self.root_goal_id = goal.goal_id
        return goal.goal_id

    def get_root_goal(self) -> "Goal | None":
        """
        Get root goal object if it exists.

        Returns:
            Root Goal instance or None
        """
        if self.root_goal_id:
            return self.engine.goals.get(self.root_goal_id)
        return None

    def load_menu_entries(self, menu_entries_path: str | Path) -> list[str]:
        """
        Load page goals from menu_entries.json.

        Uses loader.load_page_goals_from_menu_fixture(engine, adapter, menu_entries_path,
        parent_goal_id=root_goal_id). Returns list of registered page goal_ids.

        New for Stage C - loads Stage B output as input fixture.

        Args:
            menu_entries_path: Path to menu_entries.json from Stage B

        Returns:
            List of registered page goal IDs
        """
        goal_ids = load_page_goals_from_menu_fixture(
            self.engine,
            self.adapter,
            menu_entries_path,
            parent_goal_id=self.root_goal_id,
        )

        # Mitigation for Finding #5: deduplicate after loading
        # Note: deduplication requires final URLs, so this is placeholder
        # Real deduplication happens after page discoveries populate final URLs
        # self.adapter.deduplicate_pages()

        return goal_ids

    def export_fixture(self, fixture_path: str | Path | None = None) -> Path:
        """
        Export page_entries.json fixture for Stage D.

        Uses page_fixture_writer.write_page_fixture(adapter, fixture_path).
        Defaults to output_dir/page_entries.json.

        Args:
            fixture_path: Optional output path (default: output_dir/page_entries.json)

        Returns:
            Path to exported fixture
        """
        if fixture_path is None:
            fixture_path = self.output_dir / "page_entries.json"

        write_page_fixture(self.adapter, fixture_path)
        return Path(fixture_path)

    def export_exploration_log(self, log_path: str | Path | None = None) -> Path:
        """
        Export page_exploration_log.jsonl from goal loop attempts/steps.

        Iterates through all page goals' attempts, writes JSONL entries:
        {
            timestamp: str,
            page_id: str,
            goal_id: str,
            attempt_index: int,
            step_index: int,
            action: str,
            status: str,
            evidence_ids: list[str],
            failure_class: str | null,
            note: str | null
        }

        Mitigation for Finding #7: UTF-8 encoding.

        Args:
            log_path: Optional output path (default: output_dir/page_exploration_log.jsonl)

        Returns:
            Path to exported log
        """
        if log_path is None:
            log_path = self.output_dir / "page_exploration_log.jsonl"

        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Mitigation for Finding #7: explicit UTF-8 encoding
        with open(log_path, "w", encoding="utf-8") as f:
            for attempt in self.engine.attempts:
                # Find goal
                goal = self.engine.goals.get(attempt.goal_id)
                if not goal or not goal.origin or not goal.origin.startswith("page_entry::"):
                    continue

                # Get page context
                context = self.adapter.get_page_context(attempt.goal_id)
                page_id = context.get("page_id") if context else None

                # Write attempt entry
                for step_idx, step in enumerate(attempt.steps):
                    entry = {
                        "timestamp": attempt.started_at,
                        "page_id": page_id,
                        "goal_id": attempt.goal_id,
                        "attempt_index": attempt.index,
                        "step_index": step_idx,
                        "action": step.kind,
                        "status": attempt.status,
                        "evidence_ids": step.evidence_ids,
                        "failure_class": attempt.failure_class,
                        "note": step.action or "",
                    }
                    # Mitigation for Finding #7: ensure_ascii=False for CJK
                    json.dump(entry, f, ensure_ascii=False)
                    f.write("\n")

        return log_path

    def export_screenshots_index(
        self, index_path: str | Path | None = None
    ) -> Path:
        """
        Export screenshots_index.json mapping page_id -> screenshot_path.

        Uses page_fixture_writer.collect_page_screenshots(adapter).
        Defaults to output_dir/screenshots_index.json.

        Args:
            index_path: Optional output path (default: output_dir/screenshots_index.json)

        Returns:
            Path to exported index
        """
        if index_path is None:
            index_path = self.output_dir / "screenshots_index.json"

        screenshot_index = collect_page_screenshots(self.adapter)

        # Mitigation for Finding #7: use safe_json_write
        safe_json_write(index_path, screenshot_index)
        return Path(index_path)

    def export_goal_summary(self, summary_path: str | Path | None = None) -> Path:
        """
        Export run_summary.json with page goal aggregation.

        Schema:
        {
            run_id: str,
            domain: 'page_discovery',
            total_goals: int,
            succeeded: int,
            failed: int,
            pending: int,
            blocked: int,
            deduplicated: int,
            reachable_count: int,
            blocked_count: int,
            blank_count: int,
            timeout_count: int,
            root_goal_id: str,
            root_conclusion: str | null,
            generated_at: str (ISO 8601 UTC)
        }

        Renamed from goal_summary.json to run_summary.json (仪表盘可见性 gap
        fix) so the dashboard's goal-loop run scanner can read the same
        filename across all four goal-loop packages (menu/page/feature/
        execution) without per-package special-casing.

        Args:
            summary_path: Optional output path (default: output_dir/run_summary.json)

        Returns:
            Path to exported summary
        """
        if summary_path is None:
            summary_path = self.output_dir / "run_summary.json"

        summary = self.get_summary()
        summary["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Mitigation for Finding #7: use safe_json_write
        safe_json_write(summary_path, summary)
        return Path(summary_path)

    def get_summary(self) -> dict:
        """
        Get page discovery session summary dict.

        Aggregates metrics from engine.goals:
        - total_goals, succeeded, failed, pending, blocked, deduplicated
        - reachable_pages, blocked_pages, blank_pages
        - root_goal_id, root_conclusion

        Mitigation for Finding #1: Uses map_goal_status_to_entry_status for correct counts.
        Mitigation for Finding #6: Counts blocked separately from failed.

        Returns:
            Summary dict
        """
        # Count by mapped entry status
        status_counts = {
            "reachable": 0,
            "failed": 0,
            "pending": 0,
            "blocked": 0,
            "deduplicated": 0,
        }

        # Count by failure class for page state breakdown
        blank_count = 0
        timeout_count = 0

        for goal_id, goal in self.engine.goals.items():
            # Only count page goals (not root)
            if not goal.origin or not goal.origin.startswith("page_entry::"):
                continue

            # Map goal status to entry status
            entry_status = map_goal_status_to_entry_status(goal, self.adapter)
            status_counts[entry_status] = status_counts.get(entry_status, 0) + 1

            # Count specific page state failures from attempts
            for attempt in self.engine.attempts:
                if attempt.goal_id == goal_id and attempt.failure_class:
                    if attempt.failure_class == "page_blank":
                        blank_count += 1
                        break
                    elif attempt.failure_class == "page_load_timeout":
                        timeout_count += 1
                        break

        # Root goal info
        root = self.get_root_goal()
        root_conclusion = root.stop_reason if root else None

        return {
            "run_id": self.run_id,
            "domain": "page_discovery",
            "total_goals": sum(status_counts.values()),
            "succeeded": status_counts["reachable"],
            "failed": status_counts["failed"],
            "pending": status_counts["pending"],
            "blocked": status_counts["blocked"],  # Mitigation for Finding #6
            "deduplicated": status_counts["deduplicated"],
            "reachable_count": status_counts["reachable"],
            "blocked_count": status_counts["blocked"],
            "blank_count": blank_count,
            "timeout_count": timeout_count,
            "root_goal_id": self.root_goal_id,
            "root_conclusion": root_conclusion,
        }
