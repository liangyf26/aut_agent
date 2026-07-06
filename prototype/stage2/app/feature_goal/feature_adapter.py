"""
Feature goal adapter for Stage D.

Adapts GoalLoopEngine API for feature point discovery workflow.
Follows PageAdapter pattern from Stage C.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine


class FeatureAdapter:
    """
    Adapter for feature point discovery using GoalLoopEngine.

    Provides simplified API for feature discovery operations and maintains
    internal context registry for feature metadata.
    """

    def __init__(self, engine: "GoalLoopEngine"):
        """
        Initialize adapter with GoalLoopEngine instance.

        Args:
            engine: GoalLoopEngine instance
        """
        self.engine = engine
        # Internal context registry: goal_id → feature context
        self._feature_context: dict[str, dict] = {}

    def register_feature_goal(
        self,
        *,
        feature_id: str,
        page_id: str,
        feature_type: str,
        risk_level: str,
        parent_goal_id: str,
        element_text: str | None = None,
        element_locator: str | None = None,
        locator_candidates: list[dict] | None = None,
    ) -> str:
        """
        Register a feature discovery goal.

        Args:
            feature_id: Unique feature identifier
            page_id: Parent page identifier
            feature_type: Feature type (query, reset, detail, etc.)
            risk_level: Risk level (low, medium, high, none)
            parent_goal_id: Parent goal ID (page discovery goal)
            element_text: Element text content
            element_locator: Element selector/locator
            locator_candidates: Ranked locator candidates (L2 pool, P0-1)

        Returns:
            Goal ID string
        """
        goal = self.engine.register_goal(
            goal_type="feature",
            goal_name=f"Discover {feature_type} feature: {element_text or feature_id}",
            parent_goal_id=parent_goal_id,
            origin=f"feature_entry::{feature_id}",
        )

        # Store feature context in internal registry
        self._feature_context[goal.goal_id] = {
            "feature_id": feature_id,
            "page_id": page_id,
            "feature_type": feature_type,
            "risk_level": risk_level,
            "element_text": element_text,
            "element_locator": element_locator,
            "locator_candidates": locator_candidates,
            "parent_page_id": page_id,
        }

        return goal.goal_id

    def get_feature_context(self, goal_id: str) -> dict | None:
        """
        Retrieve feature context from internal registry.

        Args:
            goal_id: Goal ID

        Returns:
            Feature context dict or None if not found
        """
        return self._feature_context.get(goal_id)

    def record_feature_attempt(
        self,
        *,
        goal_id: str,
    ) -> str:
        """
        Record a feature discovery attempt.

        Args:
            goal_id: Feature goal ID

        Returns:
            Attempt ID string
        """
        attempt = self.engine.start_attempt(goal_id=goal_id)
        return attempt.attempt_id

    def record_scan_step(
        self,
        *,
        attempt_id: str,
        action: str,
        target: str | None = None,
        observed: bool = True,
    ) -> str:
        """
        Record a feature scan step.

        Args:
            attempt_id: Attempt ID
            action: Action description (scan_page, identify_element, etc.)
            target: Target element or selector
            observed: Whether step has evidence attached

        Returns:
            Step ID string
        """
        step = self.engine.add_step(
            attempt_id=attempt_id,
            kind=action,
            action=target,
            observed=observed,
        )
        return step.step_id

    def attach_dom_evidence(
        self,
        *,
        step_id: str,
        dom_snapshot: dict,
    ) -> str:
        """
        Attach DOM snapshot evidence.

        Args:
            step_id: Step ID
            dom_snapshot: DOM snapshot data (element counts, text, etc.)

        Returns:
            Evidence ID string
        """
        import json

        evidence = self.engine.attach_evidence(
            step_id=step_id,
            kind="dom_snapshot",
            uri="dom://snapshot",
            note=json.dumps(dom_snapshot, ensure_ascii=False),
        )
        return evidence.evidence_id

    def attach_feature_metadata_evidence(
        self,
        *,
        step_id: str,
        feature_type: str,
        risk_level: str,
        confidence: str,
        element_text: str | None = None,
        element_locator: str | None = None,
        locator_candidates: list[dict] | None = None,
    ) -> str:
        """
        Attach feature metadata evidence.

        Args:
            step_id: Step ID
            feature_type: Classified feature type
            risk_level: Risk level
            confidence: Classification confidence
            element_text: Element text
            element_locator: Element locator
            locator_candidates: Ranked locator candidates (L2 pool, P0-1)

        Returns:
            Evidence ID string
        """
        import json

        metadata = {
            "feature_type": feature_type,
            "risk_level": risk_level,
            "confidence": confidence,
            "element_text": element_text,
            "element_locator": element_locator,
            "locator_candidates": locator_candidates,
        }

        evidence = self.engine.attach_evidence(
            step_id=step_id,
            kind="feature_metadata",
            uri="feature://metadata",
            note=json.dumps(metadata, ensure_ascii=False),
        )
        return evidence.evidence_id

    def record_feature_identified(
        self,
        *,
        attempt_id: str,
        feature_type: str,
        risk_level: str,
        confidence: str,
        evidence_refs: list[str],
    ) -> None:
        """
        Record that a feature was identified and a test case was generated.

        IMPORTANT: This does NOT call engine.record_success(). The 'feature'
        goal_type's success predicate requires
        feature_identified AND case_generated AND basic_path_executed AND has_feedback
        (see predicates.py) — the last two conditions belong to Stage E
        (execution), which Stage D does not perform.

        Instead, Stage D records TARGET_DISCOVERED_BUT_UNCOVERED (or
        FEATURE_NOT_IDENTIFIED for degraded/low-confidence classifications).
        Both resolve via EXIT_CONTINUE / EXIT_RETRY in the playbook table —
        the goal is NOT marked as a hard failure, it is kept in the tracking
        loop for Stage E to conclude later with real execution signals.

        Args:
            attempt_id: Attempt ID
            feature_type: Feature type
            risk_level: Risk level
            confidence: Classification confidence
            evidence_refs: List of evidence IDs
        """
        # Feature degraded: the entire page produced only the baseline 'view'
        # entry (confidence='low', risk_level='none'). Checking confidence=='low'
        # alone is now reliable because classify_feature_from_page_context emits
        # view with confidence='low' (Stage D adversarial review Fix #2) so that
        # a page producing NOTHING beyond the baseline view is correctly flagged.
        is_degraded = feature_type == "view" and confidence == "low"

        # Confidence is stored in the adapter's own registry, NOT threaded
        # through engine signals/notes: when explicit_class is supplied,
        # classify_failure() ignores `signals` entirely and always resolves
        # to CONFIDENCE_HIGH (verified against classification.py), and
        # record_failure's own `note` local only gets populated on evidence
        # chain-break paths (missing/invalid refs) — a valid explicit_class
        # with valid refs leaves attempt.notes empty. Mirrors Stage C
        # Finding #4 (parent_menu_id must live in adapter context, not be
        # smuggled through engine fields that don't carry it).
        goal_id = self._goal_id_of_attempt(attempt_id)
        if goal_id and goal_id in self._feature_context:
            self._feature_context[goal_id]["confidence"] = confidence

        if is_degraded:
            self.engine.record_failure(
                attempt_id=attempt_id,
                explicit_class="feature_not_identified",
                evidence_refs=evidence_refs,
                made_progress=False,
            )
            return

        # Real feature identified + test case generated, but not yet executed
        # (execution is Stage E's job). This is "discovered but not covered",
        # not a failure — EXIT_CONTINUE keeps tracking it without penalizing
        # no_improvement_streak.
        self.engine.record_failure(
            attempt_id=attempt_id,
            explicit_class="target_discovered_but_uncovered",
            evidence_refs=evidence_refs,
            made_progress=True,
        )

    def _goal_id_of_attempt(self, attempt_id: str) -> str | None:
        """Look up the goal_id owning an attempt_id (engine.attempts is a list)."""
        for attempt in self.engine.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt.goal_id
        return None

    def record_feature_failure(
        self,
        *,
        attempt_id: str,
        failure_class: str,
        confidence: str,
        evidence_refs: list[str],
        note: str | None = None,
    ) -> None:
        """
        Record feature discovery failure using a fixed failure class.

        Args:
            attempt_id: Attempt ID
            failure_class: One of the 18 fixed failure classes
                (see goal_loop.classification for the enumeration)
            confidence: Classification confidence ('high'|'medium'|'low')
            evidence_refs: List of evidence IDs
            note: Additional diagnostic notes
        """
        # Confidence is stored in the adapter's own registry — see the
        # comment in record_feature_identified for why signals/notes cannot
        # carry it when explicit_class is used.
        goal_id = self._goal_id_of_attempt(attempt_id)
        if goal_id and goal_id in self._feature_context:
            self._feature_context[goal_id]["confidence"] = confidence
            if note:
                self._feature_context[goal_id]["failure_note"] = note

        self.engine.record_failure(
            attempt_id=attempt_id,
            explicit_class=failure_class,
            evidence_refs=evidence_refs,
            made_progress=False,
        )
