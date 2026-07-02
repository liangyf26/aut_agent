"""Tests for menu-specific failure classifier."""

from prototype.stage2.app.goal_loop import classification as fc
from prototype.stage2.app.menu_goal import menu_classifier


def test_classify_menu_discovery_with_error_code():
    """Test classification from v3 error codes."""
    # Menu not found
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_code="MENU_NOT_FOUND",
    )
    assert failure_class == fc.MENU_NOT_FOUND
    assert confidence == fc.CONFIDENCE_HIGH

    # Expand failed
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_code="EXPAND_FAILED",
    )
    assert failure_class == fc.MENU_EXPAND_FAILED
    assert confidence == fc.CONFIDENCE_HIGH

    # Click failed
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_code="CLICK_TIMEOUT",
    )
    assert failure_class == fc.MENU_CLICK_FAILED
    assert confidence == fc.CONFIDENCE_HIGH


def test_classify_menu_discovery_with_operation():
    """Test classification from operation type."""
    # Expand operation
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        operation="expand_submenu",
    )
    assert failure_class == fc.MENU_EXPAND_FAILED
    assert confidence == fc.CONFIDENCE_HIGH

    # Click operation
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        operation="click_menu_item",
    )
    assert failure_class == fc.MENU_CLICK_FAILED
    assert confidence == fc.CONFIDENCE_HIGH


def test_classify_from_discovery_log_success():
    """Test classification treats success as non-failure."""
    log_entry = {
        "operation": "click_menu",
        "status": "success",
        "menu_id": "menu_001",
    }

    failure_class, confidence = menu_classifier.classify_from_discovery_log(log_entry)

    assert failure_class == fc.UNKNOWN
    assert confidence == fc.CONFIDENCE_LOW


def test_classify_from_discovery_log_failure():
    """Test classification from discovery log failure entry."""
    log_entry = {
        "operation": "expand",
        "status": "failed",
        "error": {
            "code": "EXPAND_TIMEOUT",
            "message": "Menu expand timed out after 5s",
        },
        "menu_id": "menu_001",
    }

    failure_class, confidence = menu_classifier.classify_from_discovery_log(log_entry)

    assert failure_class == fc.MENU_EXPAND_FAILED
    assert confidence == fc.CONFIDENCE_HIGH


def test_is_menu_discovery_failure():
    """Test menu discovery failure detection."""
    assert menu_classifier.is_menu_discovery_failure(fc.MENU_NOT_FOUND)
    assert menu_classifier.is_menu_discovery_failure(fc.MENU_EXPAND_FAILED)
    assert menu_classifier.is_menu_discovery_failure(fc.MENU_CLICK_FAILED)

    assert not menu_classifier.is_menu_discovery_failure(fc.PAGE_BLANK)
    assert not menu_classifier.is_menu_discovery_failure(fc.PERMISSION_BLOCKED)
    assert not menu_classifier.is_menu_discovery_failure(fc.UNKNOWN)


def test_should_retry_transient_failures():
    """Test retry logic for transient failures."""
    # Transient failures should retry
    assert menu_classifier.should_retry_menu_discovery(fc.PAGE_LOAD_TIMEOUT, 1)
    assert menu_classifier.should_retry_menu_discovery(fc.LOCATOR_UNSTABLE, 2)

    # But not after max retries
    assert not menu_classifier.should_retry_menu_discovery(fc.PAGE_LOAD_TIMEOUT, 3)


def test_should_not_retry_structural_failures():
    """Test retry logic for structural failures."""
    # Structural failures should not retry
    assert not menu_classifier.should_retry_menu_discovery(fc.MENU_NOT_FOUND, 1)
    assert not menu_classifier.should_retry_menu_discovery(fc.PERMISSION_BLOCKED, 1)
    assert not menu_classifier.should_retry_menu_discovery(fc.LOGIN_REQUIRED, 1)
    assert not menu_classifier.should_retry_menu_discovery(fc.BLOCKED_BY_SAFETY_POLICY, 1)


def test_should_retry_menu_operations_once():
    """Test retry logic for menu operation failures."""
    # Menu operation failures retry once
    assert menu_classifier.should_retry_menu_discovery(fc.MENU_EXPAND_FAILED, 1)
    assert menu_classifier.should_retry_menu_discovery(fc.MENU_CLICK_FAILED, 1)

    # But not twice
    assert not menu_classifier.should_retry_menu_discovery(fc.MENU_EXPAND_FAILED, 2)
    assert not menu_classifier.should_retry_menu_discovery(fc.MENU_CLICK_FAILED, 2)


def test_should_retry_unknown_once():
    """Test retry logic for unknown failures."""
    # Unknown failures retry once
    assert menu_classifier.should_retry_menu_discovery(fc.UNKNOWN, 1)

    # But not twice
    assert not menu_classifier.should_retry_menu_discovery(fc.UNKNOWN, 2)


def test_classify_with_cjk_error_message():
    """Test classification with Chinese error messages."""
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_message="未找到菜单，请检查配置",
    )

    # Should fall back to keyword classification
    assert failure_class == fc.MENU_NOT_FOUND
    assert confidence == fc.CONFIDENCE_MEDIUM


def test_classify_permission_errors():
    """Test classification of permission-related errors."""
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_code="PERMISSION_DENIED",
    )
    assert failure_class == fc.PERMISSION_BLOCKED
    assert confidence == fc.CONFIDENCE_HIGH

    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_code="LOGIN_REQUIRED",
    )
    assert failure_class == fc.LOGIN_REQUIRED
    assert confidence == fc.CONFIDENCE_HIGH


def test_classify_evidence_errors():
    """Test classification of evidence-related errors."""
    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_code="NO_SCREENSHOT",
    )
    assert failure_class == fc.EVIDENCE_INCOMPLETE
    assert confidence == fc.CONFIDENCE_HIGH

    failure_class, confidence = menu_classifier.classify_menu_discovery_failure(
        error_code="MISSING_EVIDENCE",
    )
    assert failure_class == fc.EVIDENCE_INCOMPLETE
    assert confidence == fc.CONFIDENCE_HIGH
