"""Menu-specific failure classification helpers.

Extends the fixed classifier with menu discovery domain knowledge to produce
higher-confidence classifications from discovery operation results.

Design:
- Wraps classification.classify_failure with menu context
- Provides helper functions for common menu discovery failure patterns
- Maps v3 discovery error codes to fixed failure classes
"""

from __future__ import annotations

from typing import Any

from ..goal_loop import classification as fc


def classify_menu_discovery_failure(
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    operation: str | None = None,
    signals: Any = None,
) -> tuple[str, str]:
    """Classify a menu discovery operation failure.

    Args:
        error_code: Optional v3 error code (MENU_NOT_FOUND, EXPAND_TIMEOUT, etc)
        error_message: Optional error message text
        operation: Optional operation type (expand, click, scroll, wait)
        signals: Additional signals for classification

    Returns:
        Tuple of (failure_class, confidence)
    """
    # Map v3 error codes to fixed failure classes
    if error_code:
        error_code_map = {
            "MENU_NOT_FOUND": fc.MENU_NOT_FOUND,
            "MENU_MISSING": fc.MENU_NOT_FOUND,
            "EXPAND_FAILED": fc.MENU_EXPAND_FAILED,
            "EXPAND_TIMEOUT": fc.MENU_EXPAND_FAILED,
            "CLICK_FAILED": fc.MENU_CLICK_FAILED,
            "CLICK_TIMEOUT": fc.MENU_CLICK_FAILED,
            "NOT_CLICKABLE": fc.MENU_CLICK_FAILED,
            "PAGE_BLANK": fc.PAGE_BLANK,
            "PAGE_TIMEOUT": fc.PAGE_LOAD_TIMEOUT,
            "LOAD_TIMEOUT": fc.PAGE_LOAD_TIMEOUT,
            "PERMISSION_DENIED": fc.PERMISSION_BLOCKED,
            "LOGIN_REQUIRED": fc.LOGIN_REQUIRED,
            "LOCATOR_AMBIGUOUS": fc.LOCATOR_UNSTABLE,
            "LOCATOR_FAILED": fc.LOCATOR_UNSTABLE,
            "NO_SCREENSHOT": fc.EVIDENCE_INCOMPLETE,
            "MISSING_EVIDENCE": fc.EVIDENCE_INCOMPLETE,
        }

        mapped_class = error_code_map.get(error_code)
        if mapped_class:
            return mapped_class, fc.CONFIDENCE_HIGH

    # Operation-specific classification
    if operation:
        op_lower = operation.lower()
        if "expand" in op_lower:
            # Expand operations that fail are menu_expand_failed
            return fc.MENU_EXPAND_FAILED, fc.CONFIDENCE_HIGH
        elif "click" in op_lower:
            # Click operations that fail are menu_click_failed
            return fc.MENU_CLICK_FAILED, fc.CONFIDENCE_HIGH

    # Fallback to keyword-based classification
    combined_signals = []
    if error_message:
        combined_signals.append(error_message)
    if signals:
        combined_signals.append(signals)

    return fc.classify_failure(signals=combined_signals if combined_signals else None)


def classify_from_discovery_log(
    log_entry: dict[str, Any],
) -> tuple[str, str]:
    """Classify failure from v3 discovery traversal log entry.

    Args:
        log_entry: Log entry dict with keys like operation, status, error, menu_id

    Returns:
        Tuple of (failure_class, confidence)
    """
    status = log_entry.get("status")
    if status == "success":
        # Not a failure
        return fc.UNKNOWN, fc.CONFIDENCE_LOW

    operation = log_entry.get("operation")
    error = log_entry.get("error", {})
    error_code = error.get("code") if isinstance(error, dict) else None
    error_message = error.get("message") if isinstance(error, dict) else str(error)

    return classify_menu_discovery_failure(
        error_code=error_code,
        error_message=error_message,
        operation=operation,
        signals=log_entry,
    )


def is_menu_discovery_failure(failure_class: str) -> bool:
    """Check if failure class is menu discovery related.

    Args:
        failure_class: Fixed failure class

    Returns:
        True if this is a menu-specific failure class
    """
    menu_classes = {
        fc.MENU_NOT_FOUND,
        fc.MENU_EXPAND_FAILED,
        fc.MENU_CLICK_FAILED,
    }
    return failure_class in menu_classes


def should_retry_menu_discovery(
    failure_class: str,
    attempt_count: int,
    max_retries: int = 3,
) -> bool:
    """Determine if menu discovery should be retried based on failure class.

    Args:
        failure_class: Fixed failure class
        attempt_count: Number of attempts so far
        max_retries: Maximum retry attempts allowed

    Returns:
        True if retry is recommended
    """
    if attempt_count >= max_retries:
        return False

    # Retry transient failures
    transient_classes = {
        fc.PAGE_LOAD_TIMEOUT,
        fc.LOCATOR_UNSTABLE,
        fc.ACTION_NOT_OBSERVED,
        fc.NO_PROGRESS_REPEATED,
    }
    if failure_class in transient_classes:
        return True

    # Don't retry structural failures
    structural_classes = {
        fc.MENU_NOT_FOUND,
        fc.PERMISSION_BLOCKED,
        fc.LOGIN_REQUIRED,
        fc.BLOCKED_BY_SAFETY_POLICY,
        fc.BROWSER_USE_UNAVAILABLE,
        fc.MISSING_PREREQUISITE_DATA,
    }
    if failure_class in structural_classes:
        return False

    # Retry menu operation failures once
    menu_classes = {
        fc.MENU_EXPAND_FAILED,
        fc.MENU_CLICK_FAILED,
    }
    if failure_class in menu_classes:
        return attempt_count < 2

    # Unknown failures: try once more
    return attempt_count < 2


__all__ = [
    "classify_menu_discovery_failure",
    "classify_from_discovery_log",
    "is_menu_discovery_failure",
    "should_retry_menu_discovery",
]
