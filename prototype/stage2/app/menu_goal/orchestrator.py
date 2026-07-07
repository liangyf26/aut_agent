"""MenuGoalOrchestrator: Session lifecycle manager for menu goal loop.

Responsibilities:
- Initialize GoalLoopEngine with menu domain configuration
- Coordinate discovery → goal registration → execution flow
- Export menu_entries.json fixture for Stage C independent testing
- Aggregate menu-level conclusions to run-level summary

Design:
- One orchestrator per discovery session
- Wraps GoalLoopEngine with menu-specific initialization
- Uses DiscoveryAdapter to map discovery results to goal primitives
- Uses MenuFixtureWriter to serialize menu_entries.json
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..goal_loop.state_machine import GoalLoopEngine

if TYPE_CHECKING:
    from ..goal_loop.types import Goal


class MenuGoalOrchestrator:
    """Orchestrates menu discovery as goal loop session."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        run_id: str | None = None,
    ):
        """Initialize menu goal orchestrator.

        Args:
            output_dir: Directory for all Stage B outputs (menu_tree.json, menu_entries.json,
                       menu_traversal_log.jsonl, screenshots_index.json, goal_summary.json,
                       progress_events.jsonl)
            run_id: Optional run ID for tracking (auto-generated if not provided)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize goal loop engine
        if run_id is None:
            import uuid
            run_id = f"menu_run_{uuid.uuid4().hex[:8]}"

        self.engine = GoalLoopEngine(run_id=run_id)
        self.run_id = run_id

        # Initialize discovery adapter for menu context tracking
        from .discovery_adapter import DiscoveryAdapter

        self.adapter = DiscoveryAdapter(self.engine)

        # Track root goal for aggregation
        self._root_goal_id: str | None = None

    def create_root_goal(
        self,
        *,
        description: str = "Discover all L1 menus",
    ) -> str:
        """Create root menu discovery goal.

        Returns:
            Root goal ID
        """
        goal = self.engine.register_goal(
            goal_type="menu",
            goal_name=description,
            origin="menu_discovery::root",
        )
        self._root_goal_id = goal.goal_id
        return goal.goal_id

    def get_root_goal(self) -> Goal | None:
        """Get root goal if it exists."""
        if self._root_goal_id is None:
            return None
        return self.engine.goals.get(self._root_goal_id)

    def export_fixture(self, fixture_path: str | Path | None = None) -> Path:
        """Export menu_entries.json fixture for Stage C independent testing.

        Args:
            fixture_path: Optional custom path (defaults to output_dir/menu_entries.json)

        Returns:
            Path to exported fixture
        """
        if fixture_path is None:
            fixture_path = self.output_dir / "menu_entries.json"
        else:
            fixture_path = Path(fixture_path)

        # Use MenuFixtureWriter to serialize
        from .fixture_writer import write_menu_fixture

        write_menu_fixture(
            adapter=self.adapter,
            output_path=fixture_path,
        )

        return fixture_path

    def get_summary(self) -> dict:
        """Get menu discovery session summary.

        Returns:
            Dict with session metrics and conclusions
        """
        # Aggregate metrics from all menu goals
        total_goals = len(self.engine.goals)
        succeeded = sum(1 for g in self.engine.goals.values() if g.status == "succeeded")
        failed = sum(1 for g in self.engine.goals.values() if g.status == "failed")
        # pending includes both "pending" and "planned" states
        pending = sum(
            1
            for g in self.engine.goals.values()
            if g.status in ("pending", "planned", "running")
        )

        # Get root goal stop_reason if available
        root_conclusion = None
        if self._root_goal_id:
            root_goal = self.engine.goals.get(self._root_goal_id)
            if root_goal and root_goal.stop_reason:
                root_conclusion = root_goal.stop_reason

        return {
            "run_id": self.run_id,
            "domain": "menu_discovery",
            "total_goals": total_goals,
            "succeeded": succeeded,
            "failed": failed,
            "pending": pending,
            "root_goal_id": self._root_goal_id,
            "root_conclusion": root_conclusion,
        }

    def export_goal_summary(self, filename: str = "run_summary.json") -> Path:
        """Export run_summary.json with menu discovery session statistics.

        Sibling to page_goal/feature_goal's own ``export_goal_summary`` and
        execution_goal's ``export_run_summary`` — all four goal packages
        write the SAME filename so the dashboard's read side doesn't need
        per-package special-casing (方案: 运行中心可见性).

        Returns:
            Path to exported file
        """
        import json
        from datetime import datetime, timezone

        summary = self.get_summary()
        summary["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        output_path = self.output_dir / filename

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        return output_path
