"""Fixed failure classifier (the decision interface).

Two layers of failure handling exist in v4 (see 技术方案 §5.3):

1. **Fixed classifier** — this module. It maps a single failure to exactly one
   of the ``FIXED_FAILURE_CLASSES`` labels plus a confidence. Its only job is to
   produce a *stable input* so a playbook can be selected deterministically.
2. **Emergent aggregator** — the existing ``iteration.FailureClusterRecord``,
   which clusters coarse categories across attempts to detect recurrence.

The bridge between the two is :func:`to_iteration_category`, which projects a
fixed label onto the coarse category vocabulary that the existing
``iteration.builder._classify_failure`` already emits (ui / network /
verification / data / environment / stability / permission / runtime ...). This
is what keeps the goal loop *reusing* the iteration layer rather than building a
parallel one.

``unknown`` is the overflow bucket: when nothing matches, we fall back to it and
let the aggregator count how often it recurs so a new fixed label can be
proposed for human / programming-model review.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

# --- The 18 fixed failure classes (需求 §6.1 / 技术方案 §13) -----------------

MENU_NOT_FOUND = "menu_not_found"
MENU_EXPAND_FAILED = "menu_expand_failed"
MENU_CLICK_FAILED = "menu_click_failed"
PAGE_BLANK = "page_blank"
PAGE_LOAD_TIMEOUT = "page_load_timeout"
PERMISSION_BLOCKED = "permission_blocked"
LOGIN_REQUIRED = "login_required"
TARGET_DISCOVERED_BUT_UNCOVERED = "target_discovered_but_uncovered"
FEATURE_NOT_IDENTIFIED = "feature_not_identified"
LOCATOR_UNSTABLE = "locator_unstable"
ACTION_NOT_OBSERVED = "action_not_observed"
ASSERTION_FAILED = "assertion_failed"
MISSING_PREREQUISITE_DATA = "missing_prerequisite_data"
BLOCKED_BY_SAFETY_POLICY = "blocked_by_safety_policy"
BROWSER_USE_UNAVAILABLE = "browser_use_unavailable"
EVIDENCE_INCOMPLETE = "evidence_incomplete"
NO_PROGRESS_REPEATED = "no_progress_repeated"
UNKNOWN = "unknown"

FIXED_FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        MENU_NOT_FOUND,
        MENU_EXPAND_FAILED,
        MENU_CLICK_FAILED,
        PAGE_BLANK,
        PAGE_LOAD_TIMEOUT,
        PERMISSION_BLOCKED,
        LOGIN_REQUIRED,
        TARGET_DISCOVERED_BUT_UNCOVERED,
        FEATURE_NOT_IDENTIFIED,
        LOCATOR_UNSTABLE,
        ACTION_NOT_OBSERVED,
        ASSERTION_FAILED,
        MISSING_PREREQUISITE_DATA,
        BLOCKED_BY_SAFETY_POLICY,
        BROWSER_USE_UNAVAILABLE,
        EVIDENCE_INCOMPLETE,
        NO_PROGRESS_REPEATED,
        UNKNOWN,
    }
)

# Confidence levels the classifier can report.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# --- Projection onto the existing coarse aggregator categories ---------------
# These target values match iteration.builder._classify_failure's vocabulary so
# a goal-loop failure can be folded into the existing FailureClusterRecord.

_CLASS_TO_ITERATION_CATEGORY: dict[str, str] = {
    MENU_NOT_FOUND: "ui",
    MENU_EXPAND_FAILED: "ui",
    MENU_CLICK_FAILED: "ui",
    PAGE_BLANK: "ui",
    PAGE_LOAD_TIMEOUT: "stability",
    PERMISSION_BLOCKED: "permission",
    LOGIN_REQUIRED: "permission",
    TARGET_DISCOVERED_BUT_UNCOVERED: "verification",
    FEATURE_NOT_IDENTIFIED: "verification",
    LOCATOR_UNSTABLE: "ui",
    ACTION_NOT_OBSERVED: "verification",
    ASSERTION_FAILED: "verification",
    MISSING_PREREQUISITE_DATA: "data",
    BLOCKED_BY_SAFETY_POLICY: "policy",
    BROWSER_USE_UNAVAILABLE: "environment",
    EVIDENCE_INCOMPLETE: "verification",
    NO_PROGRESS_REPEATED: "stability",
    UNKNOWN: "runtime",
}

# Keyword hints for the fallback path when no explicit class is supplied. Order
# matters: more specific / more structural buckets are tested first. ASCII
# keywords are matched on word boundaries (so "403" does not fire inside "1403"
# and "expect" does not fire inside "unexpected"); CJK keywords fall back to
# substring matching since they have no word boundaries.
_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (LOGIN_REQUIRED, ("login required", "not logged in", "session expired", "需要登录", "重新登录")),
    (PERMISSION_BLOCKED, ("permission", "forbidden", "http 403", "status 403", "无权限", "没有权限")),
    (BLOCKED_BY_SAFETY_POLICY, ("safety policy", "high risk", "risky submit", "policy blocked", "高风险")),
    (BROWSER_USE_UNAVAILABLE, ("browser use unavailable", "browser-use unavailable", "semantic takeover unavailable")),
    (MENU_EXPAND_FAILED, ("expand menu", "menu expand", "collapse menu", "展开菜单")),
    (MENU_CLICK_FAILED, ("click menu", "menu click", "not clickable", "点击菜单")),
    (MENU_NOT_FOUND, ("menu not found", "no menu", "menu missing", "未找到菜单")),
    # locator is tested before page_blank so "selector matched a blank element"
    # is treated as a locator problem, not a blank page.
    (LOCATOR_UNSTABLE, ("locator", "selector", "strict mode", "multiple elements", "定位")),
    (PAGE_BLANK, ("blank page", "white screen", "empty page", "白屏", "空白")),
    (PAGE_LOAD_TIMEOUT, ("timeout", "timed out", "load timeout", "超时")),
    (ACTION_NOT_OBSERVED, ("no feedback", "not observed", "no response", "无反馈")),
    (ASSERTION_FAILED, ("assertion", "expected", "mismatch", "断言")),
    (MISSING_PREREQUISITE_DATA, ("prerequisite", "missing data", "no test data", "前置数据")),
    (FEATURE_NOT_IDENTIFIED, ("no feature", "feature not identified", "unrecognized", "未识别功能")),
    (EVIDENCE_INCOMPLETE, ("evidence incomplete", "missing screenshot", "no step evidence", "证据缺")),
    (TARGET_DISCOVERED_BUT_UNCOVERED, ("discovered but uncovered", "uncovered target", "未覆盖")),
    (NO_PROGRESS_REPEATED, ("no progress", "no improvement", "repeated failure", "无进展")),
)

_ASCII_KEYWORD = re.compile(r"^[\x00-\x7f]+$")


def _keyword_matches(keyword: str, text: str) -> bool:
    """Match ASCII keywords on word boundaries; CJK keywords by substring.

    The boundary assertions use ``re.ASCII`` so ``\\w`` is ASCII-only. Without it,
    Python's Unicode ``\\w`` treats CJK ideographs as word chars, which would make
    an ASCII keyword glued to Chinese text (e.g. ``请求timeout``) fail to match —
    a real regression in this Chinese-heavy deployment. ``re.ASCII`` still blocks
    ``403`` inside ``1403`` and ``expect`` inside ``unexpected``.
    """

    if _ASCII_KEYWORD.match(keyword):
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, re.ASCII) is not None
    return keyword in text


def is_fixed_class(value: Any) -> bool:
    return isinstance(value, str) and value in FIXED_FAILURE_CLASSES


def to_iteration_category(failure_class: str) -> str:
    """Project a fixed failure class onto the emergent aggregator vocabulary."""

    return _CLASS_TO_ITERATION_CATEGORY.get(failure_class, "runtime")


def _normalize_signals(signals: Any) -> str:
    if signals is None:
        return ""
    if isinstance(signals, str):
        return signals.lower()
    # Mappings must be handled before the generic Iterable branch: iterating a
    # dict yields its KEYS, which would drop the actual failure text (values).
    if isinstance(signals, Mapping):
        return " ".join(str(value) for value in signals.values()).lower()
    if isinstance(signals, Iterable):
        return " ".join(str(part) for part in signals).lower()
    return str(signals).lower()


def classify_failure(
    *,
    explicit_class: str | None = None,
    signals: Any = None,
) -> tuple[str, str]:
    """Return ``(failure_class, confidence)`` for a single failure.

    - An explicit, valid fixed class wins with ``high`` confidence. This is the
      normal path: the caller (test model) picks the label from the fixed set.
    - Otherwise keyword hints produce a ``medium`` confidence guess.
    - When nothing matches, ``unknown`` / ``low`` is returned so the aggregator
      can track overflow.
    """

    if explicit_class is not None:
        if is_fixed_class(explicit_class):
            return explicit_class, CONFIDENCE_HIGH
        # An explicit but unrecognized label is itself an overflow signal.
        return UNKNOWN, CONFIDENCE_LOW

    text = _normalize_signals(signals)
    if text:
        for failure_class, keywords in _KEYWORD_RULES:
            if any(_keyword_matches(keyword, text) for keyword in keywords):
                return failure_class, CONFIDENCE_MEDIUM

    return UNKNOWN, CONFIDENCE_LOW


__all__ = [
    "FIXED_FAILURE_CLASSES",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_LOW",
    "classify_failure",
    "is_fixed_class",
    "to_iteration_category",
    # individual labels
    "MENU_NOT_FOUND",
    "MENU_EXPAND_FAILED",
    "MENU_CLICK_FAILED",
    "PAGE_BLANK",
    "PAGE_LOAD_TIMEOUT",
    "PERMISSION_BLOCKED",
    "LOGIN_REQUIRED",
    "TARGET_DISCOVERED_BUT_UNCOVERED",
    "FEATURE_NOT_IDENTIFIED",
    "LOCATOR_UNSTABLE",
    "ACTION_NOT_OBSERVED",
    "ASSERTION_FAILED",
    "MISSING_PREREQUISITE_DATA",
    "BLOCKED_BY_SAFETY_POLICY",
    "BROWSER_USE_UNAVAILABLE",
    "EVIDENCE_INCOMPLETE",
    "NO_PROGRESS_REPEATED",
    "UNKNOWN",
]
