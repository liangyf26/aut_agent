"""DiscoveryAdapter: Maps v3 discovery results to goal loop primitives.

Bridges the gap between:
- v3 discovery output: menu_tree.json, screenshots, traversal logs
- Goal loop primitives: goals, attempts, steps, evidence

Design:
- One adapter per menu discovery operation
- Creates goal/attempt/step structure from discovery session
- Registers evidence for screenshots and menu metadata
- Maps discovery failures to goal loop failure classification
- Maintains menu context registry for fixture export
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine


class DiscoveryAdapter:
    """Adapts v3 discovery results to goal loop primitives."""

    def __init__(self, engine: GoalLoopEngine):
        """Initialize discovery adapter.

        Args:
            engine: Goal loop engine to register primitives with
        """
        self.engine = engine
        # Registry mapping goal_id -> menu context
        self._menu_context: dict[str, dict[str, Any]] = {}

    def register_menu_goal(
        self,
        *,
        menu_id: str,
        menu_path: list[str],
        parent_goal_id: str | None = None,
    ) -> str:
        """Register a menu discovery goal.

        Args:
            menu_id: Unique menu identifier
            menu_path: Path components to this menu (e.g., ["系统管理", "用户管理"])
            parent_goal_id: Optional parent goal ID for hierarchical discovery

        Returns:
            Goal ID
        """
        goal_name = f"Discover menu: {' > '.join(menu_path)}"
        origin = f"menu_entry::{menu_id}"

        goal = self.engine.register_goal(
            goal_type="menu",
            goal_name=goal_name,
            parent_goal_id=parent_goal_id,
            origin=origin,
        )

        # Store menu context in adapter registry
        self._menu_context[goal.goal_id] = {
            "menu_id": menu_id,
            "menu_path": menu_path,
            "menu_depth": len(menu_path),
        }

        return goal.goal_id

    def get_menu_context(self, goal_id: str) -> dict[str, Any] | None:
        """Get menu context for a goal.

        Args:
            goal_id: Goal ID

        Returns:
            Menu context dict or None if not found
        """
        return self._menu_context.get(goal_id)

    def record_discovery_attempt(
        self,
        *,
        goal_id: str,
        route_hint: str | None = None,
    ) -> str:
        """Record a menu discovery attempt.

        Args:
            goal_id: Goal ID for this menu
            route_hint: Optional route hint for navigation

        Returns:
            Attempt ID
        """
        # Ensure goal is running (activate if needed)
        goal = self.engine.goals[goal_id]
        if goal.status == "planned":
            self.engine.resume_goal(goal_id)

        attempt = self.engine.start_attempt(goal_id=goal_id)
        attempt_id = attempt.attempt_id

        # Store route hint in menu context if provided
        if route_hint:
            if goal_id in self._menu_context:
                self._menu_context[goal_id]["route_hint"] = route_hint

        return attempt_id

    def record_navigation_step(
        self,
        *,
        attempt_id: str,
        action: str,
        target: str | None = None,
    ) -> str:
        """Record a navigation step (click, expand, scroll, etc).

        Args:
            attempt_id: Attempt ID for this discovery session
            action: Action type (click_menu, expand_submenu, scroll, wait)
            target: Optional target selector or description

        Returns:
            Step ID
        """
        description = f"{action}"
        if target:
            description += f": {target}"

        step_id = self.engine.record_step(
            attempt_id=attempt_id,
            description=description,
        )

        return step_id

    def attach_screenshot_evidence(
        self,
        *,
        step_id: str,
        screenshot_path: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Attach screenshot evidence to a step.

        Args:
            step_id: Step ID to attach evidence to
            screenshot_path: Path to screenshot file
            metadata: Optional metadata (timestamp, viewport, etc)

        Returns:
            Evidence ID
        """
        evidence_id = self.engine.record_evidence(
            step_id=step_id,
            evidence_type="screenshot",
            content=str(screenshot_path),
            metadata=metadata or {},
        )

        return evidence_id

    def attach_menu_metadata_evidence(
        self,
        *,
        step_id: str,
        menu_text: str,
        menu_html: str | None = None,
        bounding_box: dict | None = None,
    ) -> str:
        """Attach menu metadata evidence to a step.

        Args:
            step_id: Step ID to attach evidence to
            menu_text: Extracted menu text
            menu_html: Optional raw HTML
            bounding_box: Optional bounding box coordinates

        Returns:
            Evidence ID
        """
        metadata = {
            "menu_text": menu_text,
        }
        if menu_html:
            metadata["menu_html"] = menu_html
        if bounding_box:
            metadata["bounding_box"] = bounding_box

        evidence_id = self.engine.record_evidence(
            step_id=step_id,
            evidence_type="menu_metadata",
            content=menu_text,
            metadata=metadata,
        )

        return evidence_id

    def record_discovery_failure(
        self,
        *,
        attempt_id: str,
        failure_class: str,
        confidence: str,
        evidence_refs: list[str] | None = None,
        note: str | None = None,
    ) -> None:
        """Record a discovery failure.

        Args:
            attempt_id: Attempt ID for this discovery session
            failure_class: Failure classification (menu_not_found, menu_expand_failed, etc)
            confidence: Classification confidence (high, medium, low)
            evidence_refs: Optional evidence IDs supporting this classification
            note: Optional diagnostic note
        """
        self.engine.record_failure(
            attempt_id=attempt_id,
            failure_class=failure_class,
            confidence=confidence,
            evidence_refs=evidence_refs,
            note=note,
        )

    def record_discovery_success(
        self,
        *,
        attempt_id: str,
        evidence_refs: list[str],
        note: str | None = None,
    ) -> None:
        """Record successful menu discovery.

        Args:
            attempt_id: Attempt ID for this discovery session
            evidence_refs: Evidence IDs proving success (must satisfy menu success predicate:
                          has_menu_text AND has_path AND has_screenshot)
            note: Optional success note
        """
        self.engine.record_success(
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
            note=note,
        )
