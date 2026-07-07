"""
Feature point classifier for Stage D.

Classifies UI elements into feature types (query, reset, detail, etc.)
and assigns risk levels (low, medium, high, none).
"""

from dataclasses import dataclass
from typing import Literal

# Feature type definitions with keywords and risk levels
FEATURE_TYPE_DEFINITIONS = {
    "query": {
        "keywords": ["查询", "搜索", "检索", "query", "search", "find"],
        "risk": "low",
        "description": "查询/搜索功能",
    },
    "reset": {
        "keywords": ["重置", "清空", "清除", "reset", "clear"],
        "risk": "low",
        "description": "重置/清空功能",
    },
    "detail": {
        "keywords": ["详情", "查看", "明细", "detail", "view", "info"],
        "risk": "low",
        "description": "详情查看功能",
    },
    "export": {
        "keywords": ["导出", "下载", "输出", "export", "download"],
        "risk": "medium",
        "description": "导出/下载功能",
    },
    "tab": {
        "keywords": ["tab", "页签", "选项卡", "标签页"],
        "risk": "low",
        "description": "Tab切换功能",
    },
    "dialog": {
        "keywords": ["弹窗", "对话框", "模态框", "dialog", "modal", "popup"],
        "risk": "medium",
        "description": "弹窗/对话框功能",
    },
    "row_action_edit": {
        "keywords": ["编辑", "修改", "更新", "edit", "update", "modify"],
        "risk": "high",
        "description": "行级编辑操作",
    },
    "row_action_delete": {
        "keywords": ["删除", "移除", "delete", "remove"],
        "risk": "high",
        "description": "行级删除操作",
    },
    "submit": {
        "keywords": ["提交", "保存", "确认", "submit", "save", "confirm"],
        "risk": "high",
        "description": "提交/保存功能",
    },
    "view": {
        "keywords": [],  # Default fallback
        "risk": "none",
        "description": "视图/可见性功能",
    },
}

RiskLevel = Literal["low", "medium", "high", "none"]
Confidence = Literal["high", "medium", "low"]


@dataclass
class FeatureClassification:
    """Feature classification result."""

    feature_type: str
    risk_level: RiskLevel
    confidence: Confidence
    matched_keywords: list[str]
    reasoning: str


def classify_feature_type(
    element_text: str,
    element_role: str | None = None,
    element_context: dict | None = None,
) -> FeatureClassification:
    """
    Classify a UI element into a feature type.

    Args:
        element_text: Text content of the element (button label, link text, etc.)
        element_role: Element role (button, link, tab, etc.)
        element_context: Additional context (parent elements, siblings, etc.)

    Returns:
        FeatureClassification with type, risk level, and confidence

    Examples:
        >>> classify_feature_type("查询")
        FeatureClassification(feature_type='query', risk_level='low', confidence='high', ...)

        >>> classify_feature_type("删除")
        FeatureClassification(feature_type='row_action_delete', risk_level='high', confidence='high', ...)
    """
    element_text_lower = element_text.lower()
    matched_types = []

    # Match keywords against all feature types
    for feature_type, definition in FEATURE_TYPE_DEFINITIONS.items():
        if feature_type == "view":
            continue  # Skip default type in first pass

        matched_keywords = []
        for keyword in definition["keywords"]:
            if keyword.lower() in element_text_lower:
                matched_keywords.append(keyword)

        if matched_keywords:
            matched_types.append({
                "feature_type": feature_type,
                "risk_level": definition["risk"],
                "matched_keywords": matched_keywords,
                "keyword_count": len(matched_keywords),
            })

    # Sort by (keyword_count, risk severity) descending. A tie on keyword_count
    # MUST resolve to the more dangerous risk_level, not insertion order —
    # otherwise a label matching both a low-risk and a high-risk keyword
    # (e.g. "查询并删除") silently downgrades to the safer interpretation and
    # gets an unapproved executable test generated for a delete-capable
    # control. See Stage D adversarial review Finding #1 (critical).
    risk_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    matched_types.sort(
        key=lambda x: (x["keyword_count"], risk_rank.get(x["risk_level"], 0)),
        reverse=True,
    )

    # Determine confidence based on matches
    if matched_types:
        best_match = matched_types[0]

        # High confidence: multiple keywords or exact match
        if best_match["keyword_count"] > 1 or element_text in best_match["matched_keywords"]:
            confidence: Confidence = "high"
        else:
            confidence = "medium"

        return FeatureClassification(
            feature_type=best_match["feature_type"],
            risk_level=best_match["risk_level"],
            confidence=confidence,
            matched_keywords=best_match["matched_keywords"],
            reasoning=f"Matched keywords: {', '.join(best_match['matched_keywords'])}",
        )

    # No keywords matched - default to view with low confidence
    return FeatureClassification(
        feature_type="view",
        risk_level="none",
        confidence="low",
        matched_keywords=[],
        reasoning="No specific feature keywords found, defaulting to view",
    )


# Vocabulary for classify_feature_from_page_context's title/URL heuristics.
# Deliberately broader than a literal "管理"/"列表" match (Stage D adversarial
# review Finding #3): common English list/admin page titles like "Users",
# "Roles", "Settings", "Reports" must not silently degrade to view-only.
_PAGE_CONTEXT_QUERY_KEYWORDS = (
    "管理", "列表", "list", "manage", "admin", "users", "roles",
    "settings", "report", "audit", "permissions",
)
_PAGE_CONTEXT_DETAIL_KEYWORDS = ("详情", "detail", "profile", "info")
_PAGE_CONTEXT_EXPORT_KEYWORDS = ("导出", "export", "download")


def classify_feature_from_page_context(
    page_title: str,
    page_url: str,
) -> list[FeatureClassification]:
    """
    Infer likely feature points from page context (title and URL).

    This is a simplified classifier that doesn't require DOM analysis.
    Used for MVP implementation.

    IMPORTANT: the baseline 'view' entry carries confidence='low', not
    'high'. It is a placeholder emitted when no substantive feature was
    inferred, and callers (FeatureAdapter.record_feature_identified) rely on
    feature_type=='view' and confidence=='low' together to detect genuine
    degradation (实施计划 §6.5/6.6). Giving the baseline view confidence='high'
    would make that degradation check permanently unreachable — see Stage D
    adversarial review Finding #2.

    Args:
        page_title: Page title
        page_url: Page URL

    Returns:
        List of likely feature classifications. Always contains at least the
        baseline 'view' entry; contains additional entries when the title/URL
        vocabulary suggests query/reset/detail/export functionality.

    Examples:
        >>> classify_feature_from_page_context("用户管理", "/admin/users")
        [
            FeatureClassification(feature_type='view', confidence='low', ...),
            FeatureClassification(feature_type='query', ...),
            FeatureClassification(feature_type='reset', ...),
        ]
    """
    features = []

    # Baseline view — confidence='low' so a page that infers NOTHING beyond
    # this is correctly flagged as degraded by the caller.
    features.append(
        FeatureClassification(
            feature_type="view",
            risk_level="none",
            confidence="low",
            matched_keywords=[],
            reasoning="Base page visibility (no substantive feature inferred)",
        )
    )

    combined_text = f"{page_title} {page_url}".lower()

    def _first_match(keywords: tuple[str, ...]) -> str | None:
        return next((kw for kw in keywords if kw in combined_text), None)

    # Management/list-style pages typically have query and reset
    query_match = _first_match(_PAGE_CONTEXT_QUERY_KEYWORDS)
    if query_match:
        features.append(
            FeatureClassification(
                feature_type="query",
                risk_level="low",
                confidence="medium",
                matched_keywords=[query_match],
                reasoning="Management/list pages typically have query functionality",
            )
        )
        features.append(
            FeatureClassification(
                feature_type="reset",
                risk_level="low",
                confidence="medium",
                matched_keywords=[query_match],
                reasoning="Management/list pages typically have reset functionality",
            )
        )

    # Detail/profile pages have detail view
    detail_match = _first_match(_PAGE_CONTEXT_DETAIL_KEYWORDS)
    if detail_match:
        features.append(
            FeatureClassification(
                feature_type="detail",
                risk_level="low",
                confidence="medium",
                matched_keywords=[detail_match],
                reasoning="Detail/profile page has detail view functionality",
            )
        )

    # Export/download indicators
    export_match = _first_match(_PAGE_CONTEXT_EXPORT_KEYWORDS)
    if export_match:
        features.append(
            FeatureClassification(
                feature_type="export",
                risk_level="medium",
                confidence="medium",
                matched_keywords=[export_match],
                reasoning="Page mentions export/download functionality",
            )
        )

    return features


def should_generate_executable_test(
    risk_level: RiskLevel,
    confidence: Confidence,
) -> bool:
    """
    Determine if a feature should have an executable test case.

    Low and medium risk features with reasonable confidence get executable tests.
    High risk features only get entry confirmation tests.

    Args:
        risk_level: Feature risk level
        confidence: Classification confidence

    Returns:
        True if executable test should be generated
    """
    if risk_level == "high":
        return False  # High risk → entry confirmation only

    if risk_level == "none":
        return False  # View-only → no test needed

    # Low/medium risk with at least medium confidence
    return confidence in {"high", "medium"}


def get_feature_type_description(feature_type: str) -> str:
    """Get human-readable description for a feature type."""
    return FEATURE_TYPE_DEFINITIONS.get(feature_type, {}).get(
        "description", f"Unknown feature type: {feature_type}"
    )
