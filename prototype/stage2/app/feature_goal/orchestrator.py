"""
Feature goal orchestrator for Stage D.

Manages feature discovery session lifecycle.
"""

from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine

from .feature_adapter import FeatureAdapter
from .loader import load_feature_goals_from_page_fixture, get_page_context_from_feature_goal
from .feature_classifier import classify_feature_from_page_context
from .test_case_generator import generate_test_case
from .feature_fixture_writer import (
    write_feature_fixture,
    write_test_cases_fixture,
    write_discovery_review,
)


class FeatureGoalOrchestrator:
    """
    Orchestrator for feature discovery session.

    Manages:
    - Loading reachable pages from page_entries.json
    - Scanning pages for features
    - Generating test cases
    - Exporting fixtures

    IMPORTANT — engine reuse warning: scan_page_features() activates goals by
    setting goal.status = "running" directly, NOT via engine.activate_next().
    This satisfies start_attempt()'s RUNNING guard for Stage D's one-shot
    fixture-generation flow, but it never sets engine.active_goal_id and
    never pops the goal from engine.frontier. After a scan, every page-scan
    and feature goal is simultaneously STATUS_RUNNING while active_goal_id
    is None and frontier still lists them all — the single-active-goal
    invariant is violated by design for this stage.

    Do NOT hand self.engine to a caller that will invoke activate_next() or
    evaluate_stop() on it (e.g. a Stage E execution driver): activate_next()
    only promotes goals whose status is STATUS_PLANNED (state_machine.py),
    so it would skip every already-"running" goal from this scan and
    activate an unrelated STATUS_PLANNED goal instead — silently dropping
    all identified features from the frontier. Construct a fresh
    GoalLoopEngine for any stage that needs proper activate_next() semantics.
    """

    def __init__(
        self,
        engine: "GoalLoopEngine | None" = None,
        output_dir: str | Path = "output",
        run_id: str = "feature_run_001",
    ):
        """
        Initialize orchestrator.

        Args:
            engine: Optional GoalLoopEngine instance (creates new if None)
            output_dir: Output directory for fixtures
            run_id: Run identifier
        """
        from ..goal_loop.state_machine import GoalLoopEngine

        self.engine = engine or GoalLoopEngine(run_id=run_id)
        self.adapter = FeatureAdapter(self.engine)
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Track generated test cases
        self._test_cases: list[dict] = []

    def create_root_goal(self, description: str = "Discover all features") -> str:
        """
        Create root goal for feature discovery session.

        Args:
            description: Goal description

        Returns:
            Root goal ID
        """
        goal = self.engine.register_goal(
            goal_type="feature",
            goal_name=description,
            parent_goal_id=None,
            origin="root::feature_discovery",
        )
        return goal.goal_id

    def load_page_entries(self, page_entries_path: str | Path) -> list[str]:
        """
        Load reachable pages from page_entries.json and create feature discovery goals.

        Args:
            page_entries_path: Path to page_entries.json from Stage C

        Returns:
            List of created page discovery goal IDs
        """
        root_goals = [g for g in self.engine.goals.values() if g.origin == "root::feature_discovery"]
        if not root_goals:
            raise RuntimeError("No root goal found. Call create_root_goal() first.")

        root_id = root_goals[0].goal_id

        return load_feature_goals_from_page_fixture(
            self.engine,
            self.adapter,
            page_entries_path,
            parent_goal_id=root_id,
        )

    def scan_page_features(self, page_goal_id: str) -> list[str]:
        """
        Scan a page for features and create feature goals.

        Uses simplified rule-based classification (no real browser needed).
        Starts exactly ONE attempt on page_goal_id for the scan itself, then
        registers one child feature-goal (with its own attempt) per
        classified feature.

        Args:
            page_goal_id: Page discovery goal ID

        Returns:
            List of created feature goal IDs
        """
        # Get page context
        page_context = get_page_context_from_feature_goal(self.adapter, page_goal_id)
        if not page_context:
            return []

        page_id = page_context["page_id"]
        page_url = page_context["page_url"]
        page_title = page_context["page_title"]

        # Classify features from page context
        feature_classifications = classify_feature_from_page_context(
            page_title=page_title or "",
            page_url=page_url or "",
        )

        # --- Page-level scan attempt (started ONCE, not per feature) ---
        page_goal = self.engine.goals[page_goal_id]
        page_goal.status = "running"  # Simulate activation (fixture-test mode)
        scan_attempt_id = self.adapter.record_feature_attempt(goal_id=page_goal_id)

        scan_step_id = self.adapter.record_scan_step(
            attempt_id=scan_attempt_id,
            action="scan_page",
            target=page_url,
            observed=True,
        )

        scan_dom_evidence = self.adapter.attach_dom_evidence(
            step_id=scan_step_id,
            dom_snapshot={
                "page_title": page_title,
                "page_url": page_url,
                "scan_method": "rule_based_classification",
                "features_found": len(feature_classifications),
            },
        )

        feature_goal_ids = []

        for idx, classification in enumerate(feature_classifications):
            feature_id = f"{page_id}_feat_{idx+1:03d}"
            element_text = f"{classification.feature_type} on {page_title}"

            # Register feature goal (child of the page scan goal)
            feature_goal_id = self.adapter.register_feature_goal(
                feature_id=feature_id,
                page_id=page_id,
                feature_type=classification.feature_type,
                risk_level=classification.risk_level,
                parent_goal_id=page_goal_id,
                element_text=element_text,
                element_locator=None,
            )

            # Activate and record its own, independent attempt
            feature_goal = self.engine.goals[feature_goal_id]
            feature_goal.status = "running"

            feature_attempt_id = self.adapter.record_feature_attempt(goal_id=feature_goal_id)
            feature_step_id = self.adapter.record_scan_step(
                attempt_id=feature_attempt_id,
                action="classify_feature",
                target=classification.feature_type,
                observed=True,
            )

            feature_metadata_evidence = self.adapter.attach_feature_metadata_evidence(
                step_id=feature_step_id,
                feature_type=classification.feature_type,
                risk_level=classification.risk_level,
                confidence=classification.confidence,
                element_text=element_text,
                element_locator=None,
            )

            # Record as identified (target_discovered_but_uncovered / feature_not_identified —
            # never engine.record_success(), which requires Stage E execution signals)
            self.adapter.record_feature_identified(
                attempt_id=feature_attempt_id,
                feature_type=classification.feature_type,
                risk_level=classification.risk_level,
                confidence=classification.confidence,
                evidence_refs=[feature_metadata_evidence],
            )

            feature_goal_ids.append(feature_goal_id)

            # Generate test case
            test_case = generate_test_case(
                feature_id=feature_id,
                page_id=page_id,
                feature_type=classification.feature_type,
                risk_level=classification.risk_level,
                confidence=classification.confidence,
                element_text=element_text,
                element_locator=None,
                page_url=page_url,
            )
            self._test_cases.append(test_case)

        # Record the page-level scan itself as identified/uncovered too — it
        # is registered with goal_type="feature" (via loader.py) and is
        # subject to the same success predicate as any feature sub-goal.
        self.adapter.record_feature_identified(
            attempt_id=scan_attempt_id,
            feature_type="page_scan",
            risk_level="none",
            confidence="high",
            evidence_refs=[scan_dom_evidence],
        )

        return feature_goal_ids

    def export_fixture(self, filename: str = "feature_points.json") -> Path:
        """
        Export feature_points.json fixture.

        Returns:
            Path to exported file
        """
        output_path = self.output_dir / filename
        write_feature_fixture(self.adapter, output_path)
        return output_path

    def export_test_cases(self, filename: str = "generated_test_cases.json") -> Path:
        """
        Export generated_test_cases.json fixture.

        Returns:
            Path to exported file
        """
        output_path = self.output_dir / filename
        write_test_cases_fixture(self._test_cases, output_path)
        return output_path

    def export_discovery_review(self, filename: str = "discovery_review.json") -> Path:
        """
        Export discovery_review.json with feature summary.

        Returns:
            Path to exported file
        """
        output_path = self.output_dir / filename
        write_discovery_review(self.adapter, output_path)
        return output_path

    def export_goal_summary(self, filename: str = "goal_summary.json") -> Path:
        """
        Export goal_summary.json with goal-level statistics.

        Returns:
            Path to exported file
        """
        import json

        summary = self.get_summary()
        output_path = self.output_dir / filename

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        return output_path

    def get_summary(self) -> dict:
        """
        Get summary statistics for feature discovery session.

        Bucketing uses map_goal_status_to_feature_status() — the SAME
        resolver write_feature_fixture() uses — rather than reading
        goal.status directly. Stage D never calls evaluate_stop() or
        record_success(), so every feature goal stays STATUS_RUNNING
        forever; bucketing on raw goal.status would put everything in
        'pending' while feature_points.json reports the same goals as
        'identified', which is exactly the inconsistency the Stage D
        adversarial review flagged (goal_summary.json vs feature_points.json
        disagreeing on how many features were identified).

        Returns:
            Dict with counts and breakdowns
        """
        from collections import Counter
        from .feature_fixture_writer import map_goal_status_to_feature_status

        total_goals = len(self.engine.goals)

        status_counts = Counter()
        for goal in self.engine.goals.values():
            status_counts[map_goal_status_to_feature_status(goal, self.adapter)] += 1

        # Feature type/risk breakdown — excludes page_scan marker contexts
        # (one per reachable page, injected by loader.py) so it counts only
        # real feature points, not the page-level scan pseudo-goals.
        feature_types = Counter()
        risk_levels = Counter()

        for goal_id, context in self.adapter._feature_context.items():
            if context.get("feature_type") != "page_scan":
                feature_types[context.get("feature_type")] += 1
                risk_levels[context.get("risk_level")] += 1

        # Test case breakdown
        test_case_types = Counter(tc["type"] for tc in self._test_cases)

        return {
            "run_id": self.run_id,
            "total_goals": total_goals,
            "succeeded": status_counts["identified"],
            "failed": status_counts["failed"],
            "pending": status_counts["pending"],
            "blocked": status_counts["blocked"],
            "deduplicated": status_counts["deduplicated"],
            # feature_count must exclude page_scan marker contexts — it is
            # exactly sum(feature_types.values()), computed the same way.
            "feature_count": sum(feature_types.values()),
            "feature_types": dict(feature_types),
            "risk_levels": dict(risk_levels),
            "test_cases_generated": len(self._test_cases),
            "test_case_types": dict(test_case_types),
        }
