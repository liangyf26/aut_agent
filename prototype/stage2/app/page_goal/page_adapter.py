"""
Page discovery adapter for goal loop engine.

Bridges page discovery operations to goal loop primitives (goals, attempts, steps, evidence).
Maintains internal page context registry for fixture export.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, parse_qs, urlencode

if TYPE_CHECKING:
    from ..goal_loop.state_machine import GoalLoopEngine
    from ..goal_loop.models import Goal, GoalAttempt


class PageAdapter:
    """
    Adapter for page discovery operations.

    Maintains internal page context registry mapping goal_id -> page context.
    Context includes: page_id, menu_path, route_hint, page_url, page_title, parent_menu_id.

    Mitigation for Finding #4: stores parent_menu_id directly in context,
    independent of goal.parent_goal_id.
    """

    def __init__(self, engine: "GoalLoopEngine"):
        """
        Initialize page discovery adapter.

        Args:
            engine: GoalLoopEngine instance
        """
        self.engine = engine
        # Mitigation for Finding #4: internal registry with parent_menu_id
        self._page_context: dict[str, dict[str, Any]] = {}

    def register_page_goal(
        self,
        *,
        page_id: str,
        menu_path: list[str],
        route_hint: str | None = None,
        parent_goal_id: str | None = None,
        parent_menu_id: str | None = None,
    ) -> str:
        """
        Register a page discovery goal.

        Uses engine.register_goal(goal_type='page', goal_name, parent_goal_id, origin).
        Goal name is 'Discover page: {menu_path_joined}'.
        Origin is 'page_entry::{page_id}'.
        Stores page context in adapter registry.

        Mitigation for Finding #4: accepts and stores parent_menu_id directly.

        Args:
            page_id: Unique page identifier (derived from menu_id)
            menu_path: Menu path leading to page (e.g., ['系统管理', '用户管理'])
            route_hint: Route hint from menu entry (e.g., '/system/users')
            parent_goal_id: Parent goal ID (typically root goal)
            parent_menu_id: Source menu ID (for lineage tracking)

        Returns:
            Registered goal_id
        """
        menu_path_str = " > ".join(menu_path) if menu_path else page_id
        goal_name = f"Discover page: {menu_path_str}"
        origin = f"page_entry::{page_id}"

        goal = self.engine.register_goal(
            goal_type="page",
            goal_name=goal_name,
            parent_goal_id=parent_goal_id,
            origin=origin,
            max_rounds=3,
        )

        goal_id = goal.goal_id

        # Store page context in internal registry
        self._page_context[goal_id] = {
            "page_id": page_id,
            "menu_path": menu_path,
            "route_hint": route_hint,
            "parent_menu_id": parent_menu_id,
            "page_url": None,  # Set after navigation
            "page_title": None,  # Set after navigation
        }

        return goal_id

    def get_page_context(self, goal_id: str) -> dict[str, Any] | None:
        """
        Get page context for a goal from adapter registry.

        Returns dict with page_id, menu_path, route_hint, page_url, page_title,
        parent_menu_id or None if not found.

        Args:
            goal_id: Goal identifier

        Returns:
            Page context dict or None
        """
        return self._page_context.get(goal_id)

    def record_page_attempt(
        self, *, goal_id: str, route_hint: str | None = None
    ) -> str:
        """
        Record a page discovery attempt.

        Mitigation for Finding #2: Uses engine.start_attempt without mutating
        goal.status directly. Status managed by engine to preserve active_goal_id
        invariant and frontier consistency.

        For test scenarios, caller should use engine.activate_next() or
        engine.activate_goal_for_test() to properly activate the goal first.

        Args:
            goal_id: Goal identifier
            route_hint: Optional route hint override

        Returns:
            Attempt ID
        """
        # Update route_hint in context if provided
        if route_hint and goal_id in self._page_context:
            self._page_context[goal_id]["route_hint"] = route_hint

        # Use engine.start_attempt to maintain engine state consistency
        attempt = self.engine.start_attempt(goal_id)
        return attempt.attempt_id

    def record_navigation_step(
        self,
        *,
        attempt_id: str,
        action: str,
        target: str | None = None,
        observed: bool = False,
    ) -> str:
        """
        Record a navigation step.

        Mitigation for Finding #3: defaults observed=False for intermediate steps.
        Only steps with attached evidence should set observed=True to pass
        check_evidence_complete gate in record_success.

        Action types:
        - navigate_to_page: Navigate to target URL
        - wait_for_load: Wait for page stable state
        - extract_content: Extract DOM content
        - capture_state: Capture page snapshot (should have observed=True with evidence)

        Args:
            attempt_id: Attempt identifier
            action: Step action type
            target: Optional action target (e.g., URL)
            observed: Whether step is observed (needs evidence if True)

        Returns:
            Step ID
        """
        step = self.engine.add_step(
            attempt_id=attempt_id,
            kind=action,
            action=target or "",
            observed=observed,
        )
        return step.step_id

    def attach_screenshot_evidence(
        self,
        *,
        step_id: str,
        screenshot_path: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Attach screenshot evidence to step.

        Uses engine.attach_evidence(step_id, kind='screenshot', uri, note).
        Metadata includes timestamp, viewport dimensions, page_url.

        Args:
            step_id: Step identifier
            screenshot_path: Path to screenshot file
            metadata: Optional metadata dict

        Returns:
            Evidence ID
        """
        # Encode metadata as JSON in note field
        # Mitigation for Finding #12: use JSON encoding not pipe-delimited
        note = json.dumps(metadata or {}, ensure_ascii=False) if metadata else None

        evidence = self.engine.attach_evidence(
            step_id=step_id,
            kind="screenshot",
            uri=str(screenshot_path),
            note=note,
        )
        return evidence.evidence_id

    def attach_page_metadata_evidence(
        self,
        *,
        step_id: str,
        page_title: str,
        page_url: str,
        http_status: int,
        dom_snapshot: dict[str, Any] | None = None,
    ) -> str:
        """
        Attach page metadata evidence.

        Mitigation for Finding #12: encodes metadata as JSON not pipe-delimited string.
        Stores page_title, http_status, visible_text_len, dom_nodes, has_main_content.

        Args:
            step_id: Step identifier
            page_title: Page title
            page_url: Page URL
            http_status: HTTP status code
            dom_snapshot: Optional DOM snapshot with visible_text_len, dom_nodes, has_main_content

        Returns:
            Evidence ID
        """
        metadata = {
            "page_title": page_title,
            "http_status": http_status,
        }

        if dom_snapshot:
            metadata.update({
                "visible_text_len": dom_snapshot.get("visible_text_len", 0),
                "dom_nodes": dom_snapshot.get("dom_nodes", 0),
                "has_main_content": dom_snapshot.get("has_main_content", False),
            })

        # JSON encoding for CJK titles with '|' or '=' characters
        note = json.dumps(metadata, ensure_ascii=False)

        evidence = self.engine.attach_evidence(
            step_id=step_id,
            kind="page_metadata",
            uri=page_url,
            note=note,
        )
        return evidence.evidence_id

    def attach_dom_snapshot_evidence(
        self, *, step_id: str, dom_data: dict[str, Any]
    ) -> str:
        """
        Attach DOM snapshot evidence.

        DOM data includes node_count, visible_text_length, has_main_content,
        main_content_selector.

        Args:
            step_id: Step identifier
            dom_data: DOM snapshot data

        Returns:
            Evidence ID
        """
        note = json.dumps(dom_data, ensure_ascii=False)

        evidence = self.engine.attach_evidence(
            step_id=step_id,
            kind="dom_snapshot",
            uri=None,
            note=note,
        )
        return evidence.evidence_id

    def record_page_failure(
        self,
        *,
        attempt_id: str,
        failure_class: str,
        confidence: str,
        evidence_refs: list[str] | None = None,
        note: str | None = None,
    ) -> None:
        """
        Record a page discovery failure.

        Mitigation for Finding #8: encodes confidence in note field since
        engine.record_failure has no confidence parameter.
        Format: 'confidence:{level}|{original_note}'

        Args:
            attempt_id: Attempt identifier
            failure_class: Classified failure class
            confidence: Classification confidence ('high', 'medium', 'low')
            evidence_refs: List of evidence IDs
            note: Optional diagnostic note
        """
        # Encode confidence in note field
        encoded_note = f"confidence:{confidence}"
        if note:
            encoded_note += f"|{note}"

        self.engine.record_failure(
            attempt_id=attempt_id,
            explicit_class=failure_class,
            signals={"note": encoded_note},
            evidence_refs=evidence_refs or [],
            scope="goal",
            made_progress=False,
        )

    def record_page_success(
        self,
        *,
        attempt_id: str,
        page_url: str,
        page_title: str,
        visible_text_len: int,
        dom_nodes: int,
        blank_screenshot_ratio: float,
        evidence_refs: list[str],
        note: str | None = None,
    ) -> None:
        """
        Record successful page discovery.

        Mitigation for Finding #10: requires explicit visible_text_len, dom_nodes,
        blank_screenshot_ratio instead of misleading is_blank boolean.
        Predicate recomputes is_blank from these values.

        Signals must satisfy page success predicate:
        http_ok AND has_main_content AND NOT is_blank
        where is_blank = (visible_text_len < 20) OR (dom_nodes < 5) OR (blank_screenshot_ratio >= 0.98)

        Updates page context with final page_url and page_title.

        Args:
            attempt_id: Attempt identifier
            page_url: Final page URL (after redirects)
            page_title: Page title
            visible_text_len: Length of visible text on page
            dom_nodes: Count of DOM nodes
            blank_screenshot_ratio: Ratio of blank pixels (0.0-1.0)
            evidence_refs: List of evidence IDs (must match observed steps)
            note: Optional note
        """
        # Find goal_id from attempt
        attempt = self._get_attempt(attempt_id)
        if attempt:
            goal_id = attempt.goal_id
            # Update page context with final URL and title
            if goal_id in self._page_context:
                self._page_context[goal_id]["page_url"] = page_url
                self._page_context[goal_id]["page_title"] = page_title

        # Build signals for page success predicate
        # Predicate: http_ok AND has_main_content AND NOT is_blank
        signals = {
            "http_ok": True,
            "has_main_content": True,
            # Don't include is_blank - predicate recomputes from these:
            "visible_text_len": visible_text_len,
            "dom_nodes": dom_nodes,
            "blank_screenshot_ratio": blank_screenshot_ratio,
        }
        if note:
            signals["note"] = note

        self.engine.record_success(
            attempt_id=attempt_id,
            signals=signals,
        )

    def _get_attempt(self, attempt_id: str) -> "GoalAttempt | None":
        """
        Get attempt by ID from engine.attempts list.

        Args:
            attempt_id: Attempt identifier

        Returns:
            GoalAttempt or None if not found
        """
        for attempt in self.engine.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        return None

    def normalize_page_url(self, url: str) -> str:
        """
        Normalize page URL for deduplication.

        Mitigation for Finding #5: standardizes URLs by:
        - Stripping trailing slashes
        - Sorting query parameters
        - Removing hash fragments
        - Lowercasing scheme/netloc

        Args:
            url: Raw URL

        Returns:
            Normalized URL
        """
        if not url:
            return ""

        parsed = urlparse(url)

        # Lowercase scheme and netloc
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Lowercase and strip trailing slash from path (unless it's root '/')
        path = parsed.path.lower()
        path = path.rstrip("/") if path != "/" else "/"

        # Sort query parameters for consistent ordering
        if parsed.query:
            query_dict = parse_qs(parsed.query, keep_blank_values=True)
            # Sort keys and flatten single-value lists
            sorted_query = sorted(
                (k, v[0] if len(v) == 1 else v) for k, v in query_dict.items()
            )
            query = urlencode(sorted_query, doseq=True)
        else:
            query = ""

        # Remove hash fragment (not sent to server)
        # Reconstruct normalized URL
        normalized = f"{scheme}://{netloc}{path}"
        if query:
            normalized += f"?{query}"

        return normalized

    def deduplicate_pages(self) -> None:
        """
        Deduplicate page goals by normalized final URL.

        Mitigation for Finding #5: collapses duplicate pages using engine.supersede_active.
        Groups goals by normalize_page_url(final_page_url), keeps first occurrence,
        supersedes duplicates.

        Should be called after all page goals are loaded and after initial page
        discoveries have populated final URLs.
        """
        # Group page goals by normalized URL
        url_to_goals: dict[str, list[tuple[str, "Goal"]]] = {}

        for goal_id, goal in self.engine.goals.items():
            # Only process page goals with final URL
            if not goal.origin or not goal.origin.startswith("page_entry::"):
                continue

            context = self._page_context.get(goal_id)
            if not context:
                continue

            final_url = context.get("page_url")
            if not final_url:
                # Goal not yet discovered, skip deduplication
                continue

            normalized = self.normalize_page_url(final_url)
            if normalized:
                if normalized not in url_to_goals:
                    url_to_goals[normalized] = []
                url_to_goals[normalized].append((goal_id, goal))

        # Supersede duplicates (keep first, supersede rest)
        for normalized_url, goal_list in url_to_goals.items():
            if len(goal_list) <= 1:
                continue

            # Keep first goal (earliest registration)
            keeper_id, _ = goal_list[0]

            # Supersede duplicates
            for dup_id, dup_goal in goal_list[1:]:
                # Only supersede if not already terminal
                if dup_goal.status not in {"succeeded", "failed_max_rounds", "stopped_no_progress"}:
                    try:
                        self.engine.supersede_active(next_goal_id=keeper_id)
                    except ValueError:
                        # Goal not active, skip
                        pass
