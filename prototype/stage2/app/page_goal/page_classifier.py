"""
Page discovery failure classification.

Maps page access failures to fixed failure classes with confidence levels.
Extends goal_loop.classification with page-specific domain knowledge.
"""

from typing import Any

from ..goal_loop.classification import classify_failure
from ..goal_loop.predicates import DEFAULT_THRESHOLDS, Thresholds


# Page-specific failure classes (subset of 18 fixed classes)
_PAGE_FAILURE_CLASSES = {
    "page_blank",
    "page_load_timeout",
    "permission_blocked",
    "login_required",
}

# Failure classes that should trigger retry
_RETRYABLE_CLASSES = {
    "page_load_timeout",
    "locator_unstable",
    "page_blank",  # Only with medium/low confidence
    "menu_not_found",
    "menu_expand_failed",
    "menu_click_failed",
    "evidence_incomplete",
    "unknown",
}

# Failure classes that require human intervention
_HUMAN_REQUIRED_CLASSES = {
    "permission_blocked",
    "login_required",
    "blocked_by_safety_policy",
    "missing_prerequisite_data",
    "assertion_failed",
}


def classify_page_discovery_failure(
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    http_status: int | None = None,
    page_signals: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Classify a page discovery operation failure.

    Maps error codes (PAGE_BLANK, PAGE_TIMEOUT, PERMISSION_DENIED, LOGIN_REQUIRED)
    to fixed failure classes. Uses http_status (403 -> permission_blocked,
    4xx/5xx -> unknown). Analyzes page_signals (visible_text_len, dom_nodes,
    blank_screenshot_ratio) to detect page_blank.

    Returns (failure_class, confidence).
    Falls back to classification.classify_failure for unknown patterns.

    Args:
        error_code: Structured error code (PAGE_BLANK, PAGE_TIMEOUT, etc.)
        error_message: Free-form error description
        http_status: HTTP status code from navigation
        page_signals: Dict containing visible_text_len, dom_nodes,
                     blank_screenshot_ratio, has_main_content

    Returns:
        Tuple of (failure_class: str, confidence: str)
        confidence is one of: 'high', 'medium', 'low'
    """
    # Check page_signals first for structural page state detection
    if page_signals:
        failure_class, confidence = classify_from_page_state(page_signals, DEFAULT_THRESHOLDS)
        if failure_class != "unknown":
            return (failure_class, confidence)

    # Map known error codes
    if error_code:
        code_upper = error_code.upper()
        if "PAGE_BLANK" in code_upper or "BLANK" in code_upper:
            return ("page_blank", "high")
        if "PAGE_TIMEOUT" in code_upper or "TIMEOUT" in code_upper:
            return ("page_load_timeout", "high")
        if "PERMISSION" in code_upper or "FORBIDDEN" in code_upper:
            return ("permission_blocked", "high")
        if "LOGIN_REQUIRED" in code_upper or "AUTH" in code_upper:
            return ("login_required", "high")
        if "MENU_NOT_FOUND" in code_upper:
            return ("menu_not_found", "high")
        if "MENU_EXPAND" in code_upper:
            return ("menu_expand_failed", "high")
        if "MENU_CLICK" in code_upper:
            return ("menu_click_failed", "high")

    # Map HTTP status codes
    if http_status:
        if http_status == 403:
            return ("permission_blocked", "high")
        # Mitigation for Finding #9: map non-403 4xx/5xx to 'unknown'
        # not 'locator_unstable' to prevent semantic pollution
        if 400 <= http_status < 600:
            return ("unknown", "medium")

    # Check error message for keywords
    if error_message:
        msg_lower = error_message.lower()

        # Permission/auth keywords
        if any(
            kw in msg_lower
            for kw in ["permission", "forbidden", "无权限", "权限"]
        ):
            return ("permission_blocked", "medium")
        if any(kw in msg_lower for kw in ["login required", "需要登录", "未登录"]):
            return ("login_required", "medium")

        # Timeout keywords
        if any(kw in msg_lower for kw in ["timeout", "timed out", "超时"]):
            return ("page_load_timeout", "medium")

        # Blank/empty keywords
        if any(kw in msg_lower for kw in ["blank", "empty", "white screen", "空白"]):
            return ("page_blank", "low")

    # Fall back to generic classification
    # Build signals dict from available information
    fallback_signals = {}
    if error_code:
        fallback_signals["error_code"] = error_code
    if error_message:
        fallback_signals["error_message"] = error_message
    if http_status:
        fallback_signals["http_status"] = http_status
    if page_signals:
        fallback_signals.update(page_signals)

    return classify_failure(signals=fallback_signals)


def classify_from_page_state(
    page_state: dict[str, Any], thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> tuple[str, str]:
    """
    Classify failure from structured page state.

    Applies page_blank detection using thresholds:
    - min_visible_text_len (default: 20)
    - min_dom_nodes (default: 5)
    - blank_screenshot_ratio (default: 0.98)

    Args:
        page_state: Dict containing:
            - http_ok: bool (HTTP success)
            - http_status: int (HTTP status code)
            - visible_text_len: int (visible text length)
            - dom_nodes: int (DOM node count)
            - blank_screenshot_ratio: float (0.0-1.0)
            - has_main_content: bool (main content identified)
            - error_code: str (optional error code)
        thresholds: Thresholds instance for blank detection

    Returns:
        Tuple of (failure_class: str, confidence: str)
    """
    # Extract signals
    http_ok = page_state.get("http_ok", False)
    http_status = page_state.get("http_status")
    visible_text_len = page_state.get("visible_text_len", 0)
    dom_nodes = page_state.get("dom_nodes", 0)
    blank_ratio = page_state.get("blank_screenshot_ratio", 0.0)
    has_main_content = page_state.get("has_main_content", False)
    error_code = page_state.get("error_code")

    # Detect blank page using thresholds
    # Mitigation for Finding #10: use explicit thresholds not is_blank signal
    is_blank = (
        visible_text_len < thresholds.min_visible_text_len
        or dom_nodes < thresholds.min_dom_nodes
        or blank_ratio >= thresholds.blank_screenshot_threshold
    )

    if http_ok and is_blank:
        # HTTP 200 but page is blank
        # Confidence based on how many thresholds violated
        violations = sum([
            visible_text_len < thresholds.min_visible_text_len,
            dom_nodes < thresholds.min_dom_nodes,
            blank_ratio >= thresholds.blank_screenshot_threshold,
        ])
        if violations >= 2:
            return ("page_blank", "high")
        return ("page_blank", "medium")

    # Non-blank page but no main content
    if http_ok and not has_main_content:
        return ("page_blank", "low")

    # HTTP failures handled by classify_page_discovery_failure
    if http_status:
        return classify_page_discovery_failure(
            http_status=http_status, error_code=error_code
        )

    return ("unknown", "low")


def is_page_discovery_failure(failure_class: str) -> bool:
    """
    Check if failure class is page discovery specific.

    Returns True for page-domain failures (page_blank, page_load_timeout).
    Used by orchestrator to distinguish page failures from menu/feature failures.

    Args:
        failure_class: Failure class name

    Returns:
        True if page-specific failure class
    """
    return failure_class in _PAGE_FAILURE_CLASSES


def should_retry_page_discovery(
    failure_class: str,
    attempt_count: int,
    confidence: str = "high",
    max_retries: int = 3,
) -> bool:
    """
    Determine if page discovery should be retried.

    Retries transient failures (page_load_timeout, locator_unstable) up to max_retries.
    Does not retry structural failures (permission_blocked, login_required,
    page_blank with high confidence).

    Mitigation for Finding #8: accepts confidence parameter for page_blank gating.
    page_blank with high confidence -> no retry
    page_blank with medium/low confidence -> retry

    Args:
        failure_class: Classified failure class
        attempt_count: Number of attempts made so far
        confidence: Classification confidence ('high', 'medium', 'low')
        max_retries: Maximum retry attempts allowed

    Returns:
        True if retry recommended
    """
    # Never retry if max attempts reached
    if attempt_count >= max_retries:
        return False

    # Never retry human-required failures
    if failure_class in _HUMAN_REQUIRED_CLASSES:
        return False

    # Special handling for page_blank: confidence-gated retry
    if failure_class == "page_blank":
        # High confidence blank (multiple violations) -> structural, no retry
        if confidence == "high":
            return False
        # Medium/low confidence blank (transient/loading) -> retry
        return True

    # Retry if failure class is in retryable set
    return failure_class in _RETRYABLE_CLASSES
