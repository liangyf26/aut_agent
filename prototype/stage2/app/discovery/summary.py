from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Iterable

from .models import FeaturePointRecord, PageEntryRecord

SEMANTIC_TYPE_NAVIGATION = "导航页"
SEMANTIC_TYPE_QUERY = "查询列表页"
SEMANTIC_TYPE_DETAIL = "详情展示页"
SEMANTIC_TYPE_CREATE = "新增录入页"
SEMANTIC_TYPE_EDIT = "编辑处理页"
SEMANTIC_TYPE_SUBMIT = "提交流程页"
SEMANTIC_TYPE_MIXED = "混合工作台页"


def build_discovery_views(
    *,
    page_entries: list[PageEntryRecord],
    feature_points: list[FeaturePointRecord],
    navigation_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    page_semantic_summary = build_page_semantic_summary(
        page_entries=page_entries,
        feature_points=feature_points,
    )
    summary_by_page_id = {
        str(item.get("page_entry_id") or ""): item
        for item in page_semantic_summary
        if str(item.get("page_entry_id") or "").strip()
    }
    annotated_page_entries: list[PageEntryRecord] = []
    for item in page_entries:
        semantic = summary_by_page_id.get(item.page_entry_id, {})
        annotated_page_entries.append(
            replace(
                item,
                entry_role=str(semantic.get("entry_role") or item.entry_role or _infer_entry_role(item)),
                semantic_page_type=str(semantic.get("semantic_page_type") or item.semantic_page_type or ""),
                semantic_page_type_confidence=str(
                    semantic.get("confidence") or item.semantic_page_type_confidence or "unknown"
                ),
                semantic_subtypes=[str(value) for value in semantic.get("semantic_subtypes", []) if str(value).strip()],
                review_reasons=[str(value) for value in semantic.get("review_reasons", []) if str(value).strip()],
            )
        )

    annotated_summary = build_page_semantic_summary(
        page_entries=annotated_page_entries,
        feature_points=feature_points,
    )
    navigation_tree = build_navigation_tree(
        page_entries=annotated_page_entries,
        navigation_nodes=navigation_nodes,
    )
    return {
        "page_entries": annotated_page_entries,
        "page_semantic_summary": annotated_summary,
        "navigation_tree": navigation_tree,
        "stats": _build_summary_stats(
            page_entries=annotated_page_entries,
            navigation_nodes=navigation_nodes,
            page_semantic_summary=annotated_summary,
        ),
    }


def build_page_semantic_summary(
    *,
    page_entries: Iterable[PageEntryRecord],
    feature_points: Iterable[FeaturePointRecord],
) -> list[dict[str, Any]]:
    feature_points_by_page: dict[str, list[FeaturePointRecord]] = defaultdict(list)
    for item in feature_points:
        feature_points_by_page[item.page_entry_id].append(item)

    child_entry_count_by_page: Counter[str] = Counter(
        item.source_page_entry_id
        for item in page_entries
        if item.source_page_entry_id and item.source_page_entry_id != item.page_entry_id
    )
    summaries: list[dict[str, Any]] = []
    for page_entry in page_entries:
        page_features = feature_points_by_page.get(page_entry.page_entry_id, [])
        feature_type_breakdown = Counter(item.feature_type for item in page_features)
        feature_scope_breakdown = Counter(item.feature_scope for item in page_features)
        action_type_breakdown = Counter(item.action_type for item in page_features)
        container_type_breakdown = Counter(
            str(item.evidence.get("container_type") or "")
            for item in page_features
            if str(item.evidence.get("container_type") or "").strip()
        )
        primary_action_count = sum(1 for item in page_features if bool(item.evidence.get("is_primary_action")))
        input_like_action_count = sum(
            1 for item in page_features if item.action_type in {"input", "select", "create", "edit"}
        )
        row_action_count = int(feature_scope_breakdown.get("row_action") or 0)
        modal_action_count = int(feature_scope_breakdown.get("modal_action") or 0)
        linked_entry_count = int(child_entry_count_by_page.get(page_entry.page_entry_id) or 0)
        high_occurrence_row_action_count = sum(
            1
            for item in page_features
            if item.feature_scope == "row_action" and int(item.evidence.get("occurrence_count") or 0) > 1
        )
        title_text = str((page_entry.evidence or {}).get("title") or "")
        url_text = str(page_entry.url or "")
        scores = _score_page_semantics(
            feature_type_breakdown=feature_type_breakdown,
            feature_scope_breakdown=feature_scope_breakdown,
            action_type_breakdown=action_type_breakdown,
            container_type_breakdown=container_type_breakdown,
            primary_action_count=primary_action_count,
            input_like_action_count=input_like_action_count,
            linked_entry_count=linked_entry_count,
            high_occurrence_row_action_count=high_occurrence_row_action_count,
        )
        semantic_page_type, confidence, review_reasons, semantic_subtypes = _finalize_semantic_classification(
            page_entry=page_entry,
            page_features=page_features,
            scores=scores,
            modal_action_count=modal_action_count,
            row_action_count=row_action_count,
            linked_entry_count=linked_entry_count,
            title_text=title_text,
            url_text=url_text,
        )
        if str(page_entry.semantic_page_type or "").strip():
            semantic_page_type = str(page_entry.semantic_page_type)
        if str(page_entry.semantic_page_type_confidence or "").strip() and page_entry.semantic_page_type_confidence != "unknown":
            confidence = str(page_entry.semantic_page_type_confidence)
        if page_entry.review_reasons:
            review_reasons = _dedupe_texts([*review_reasons, *page_entry.review_reasons])
        if page_entry.semantic_subtypes:
            semantic_subtypes = _dedupe_texts([*semantic_subtypes, *page_entry.semantic_subtypes])
        sorted_candidates = sorted(
            (
                {"type": key, "score": int(value)}
                for key, value in scores.items()
                if int(value) > 0
            ),
            key=lambda item: (-int(item["score"]), str(item["type"])),
        )
        summaries.append(
            {
                "page_entry_id": page_entry.page_entry_id,
                "name": page_entry.name,
                "url": page_entry.url,
                "entry_role": page_entry.entry_role or _infer_entry_role(page_entry),
                "semantic_page_type": semantic_page_type,
                "confidence": confidence,
                "score": int(scores.get(semantic_page_type, 0)),
                "runner_up": sorted_candidates[1] if len(sorted_candidates) > 1 else None,
                "semantic_subtypes": semantic_subtypes,
                "review_required": bool(review_reasons),
                "review_reasons": review_reasons,
                "signals": {
                    "feature_type_breakdown": dict(feature_type_breakdown),
                    "feature_scope_breakdown": dict(feature_scope_breakdown),
                    "action_type_breakdown": dict(action_type_breakdown),
                    "container_type_breakdown": dict(container_type_breakdown),
                    "primary_action_count": primary_action_count,
                    "input_like_action_count": input_like_action_count,
                    "row_action_count": row_action_count,
                    "modal_action_count": modal_action_count,
                    "linked_entry_count": linked_entry_count,
                    "high_occurrence_row_action_count": high_occurrence_row_action_count,
                    "feature_count": len(page_features),
                    "title_text": title_text,
                    "url_text": url_text,
                    "source_action_type": page_entry.source_action_type,
                },
                "semantic_page_type_candidates": sorted_candidates[:3],
            }
        )
    return summaries


def build_navigation_tree(
    *,
    page_entries: Iterable[PageEntryRecord],
    navigation_nodes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    page_entries_by_id = {
        item.page_entry_id: item
        for item in page_entries
    }
    nodes_by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in navigation_nodes:
        owner_page_entry_id = str(item.get("owner_page_entry_id") or "").strip()
        if owner_page_entry_id:
            nodes_by_owner[owner_page_entry_id].append(dict(item))

    roots: list[dict[str, Any]] = []
    for page_entry in page_entries:
        if page_entry.source_page_entry_id and page_entry.source_page_entry_id != page_entry.page_entry_id:
            continue
        children = _build_navigation_tree_for_page(
            page_entry=page_entry,
            navigation_nodes=nodes_by_owner.get(page_entry.page_entry_id, []),
            page_entries_by_id=page_entries_by_id,
        )
        roots.append(
            {
                "node_id": f"page::{page_entry.page_entry_id}",
                "label": page_entry.name,
                "kind": "page_entry_root",
                "page_entry_id": page_entry.page_entry_id,
                "entry_role": page_entry.entry_role or _infer_entry_role(page_entry),
                "semantic_page_type": page_entry.semantic_page_type,
                "confidence": page_entry.semantic_page_type_confidence,
                "url": page_entry.url,
                "children": children,
            }
        )
    return roots


def _build_navigation_tree_for_page(
    *,
    page_entry: PageEntryRecord,
    navigation_nodes: list[dict[str, Any]],
    page_entries_by_id: dict[str, PageEntryRecord],
) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    group_index: dict[tuple[str, ...], dict[str, Any]] = {}

    for item in navigation_nodes:
        path_labels = [
            str(value).strip()
            for value in item.get("menu_path_labels", [])
            if str(value).strip()
        ]
        leaf_label = str(item.get("label") or "").strip()
        if not path_labels:
            path_labels = [leaf_label] if leaf_label else []
        if not path_labels:
            continue

        parent_children = roots
        for depth, label in enumerate(path_labels[:-1], start=1):
            prefix = tuple(path_labels[:depth])
            group = group_index.get(prefix)
            if group is None:
                group = {
                    "node_id": f"{page_entry.page_entry_id}::group::{'__'.join(prefix)}",
                    "label": label,
                    "kind": "menu_group",
                    "menu_level": depth - 1,
                    "children": [],
                }
                group_index[prefix] = group
                parent_children.append(group)
            parent_children = group["children"]

        target_page_entry_id = str(item.get("target_page_entry_id") or "").strip()
        target_page = page_entries_by_id.get(target_page_entry_id)
        parent_children.append(
            {
                "node_id": str(item.get("navigation_node_id") or ""),
                "label": path_labels[-1],
                "kind": str(item.get("node_kind") or "menu_item"),
                "menu_level": max(len(path_labels) - 1, 0),
                "status": str(item.get("status") or ""),
                "status_reason": str(item.get("status_reason") or ""),
                "href": str(item.get("href") or ""),
                "locator": str(item.get("locator") or ""),
                "target_page_entry_id": target_page_entry_id or None,
                "target_page_name": target_page.name if target_page is not None else "",
                "target_page_type": target_page.semantic_page_type if target_page is not None else "",
                "children": [],
            }
        )
    return roots


def _build_summary_stats(
    *,
    page_entries: list[PageEntryRecord],
    navigation_nodes: list[dict[str, Any]],
    page_semantic_summary: list[dict[str, Any]],
) -> dict[str, Any]:
    semantic_type_breakdown = Counter(
        item.semantic_page_type
        for item in page_entries
        if str(item.semantic_page_type or "").strip()
    )
    navigation_status_breakdown = Counter(
        str(item.get("status") or "")
        for item in navigation_nodes
        if str(item.get("status") or "").strip()
    )
    navigation_kind_breakdown = Counter(
        str(item.get("node_kind") or "")
        for item in navigation_nodes
        if str(item.get("node_kind") or "").strip()
    )
    low_confidence_count = sum(
        1 for item in page_semantic_summary if str(item.get("confidence") or "") == "low"
    )
    review_required_count = sum(
        1 for item in page_semantic_summary if bool(item.get("review_required"))
    )
    return {
        "semantic_page_type_breakdown": dict(semantic_type_breakdown),
        "navigation_node_count": len(navigation_nodes),
        "navigation_status_breakdown": dict(navigation_status_breakdown),
        "navigation_kind_breakdown": dict(navigation_kind_breakdown),
        "page_semantic_count": len(page_semantic_summary),
        "low_confidence_page_count": low_confidence_count,
        "semantic_review_required_count": review_required_count,
    }


def _score_page_semantics(
    *,
    feature_type_breakdown: Counter[str],
    feature_scope_breakdown: Counter[str],
    action_type_breakdown: Counter[str],
    container_type_breakdown: Counter[str],
    primary_action_count: int,
    input_like_action_count: int,
    linked_entry_count: int,
    high_occurrence_row_action_count: int,
) -> dict[str, int]:
    scores: Counter[str] = Counter()
    scores[SEMANTIC_TYPE_NAVIGATION] += int(action_type_breakdown.get("navigate") or 0) * 2
    scores[SEMANTIC_TYPE_NAVIGATION] += int(action_type_breakdown.get("switch_tab") or 0) * 2
    scores[SEMANTIC_TYPE_NAVIGATION] += int(container_type_breakdown.get("navigation") or 0)
    if input_like_action_count == 0 and linked_entry_count > 0:
        scores[SEMANTIC_TYPE_NAVIGATION] += 2

    scores[SEMANTIC_TYPE_QUERY] += int(feature_type_breakdown.get("查询") or 0) * 2
    scores[SEMANTIC_TYPE_QUERY] += int(action_type_breakdown.get("filter") or 0) * 2
    scores[SEMANTIC_TYPE_QUERY] += int(action_type_breakdown.get("input") or 0)
    scores[SEMANTIC_TYPE_QUERY] += int(action_type_breakdown.get("select") or 0)
    if high_occurrence_row_action_count > 0:
        scores[SEMANTIC_TYPE_QUERY] += 1

    scores[SEMANTIC_TYPE_DETAIL] += int(feature_type_breakdown.get("查看") or 0) * 2
    scores[SEMANTIC_TYPE_DETAIL] += int(action_type_breakdown.get("inspect") or 0) * 2
    if int(feature_scope_breakdown.get("modal_action") or 0) > 0:
        scores[SEMANTIC_TYPE_DETAIL] += 1

    scores[SEMANTIC_TYPE_CREATE] += int(feature_type_breakdown.get("新增") or 0) * 3
    scores[SEMANTIC_TYPE_CREATE] += int(action_type_breakdown.get("create") or 0) * 3
    if input_like_action_count >= 2:
        scores[SEMANTIC_TYPE_CREATE] += 1

    scores[SEMANTIC_TYPE_EDIT] += int(feature_type_breakdown.get("编辑") or 0) * 3
    scores[SEMANTIC_TYPE_EDIT] += int(action_type_breakdown.get("edit") or 0) * 3

    scores[SEMANTIC_TYPE_SUBMIT] += int(feature_type_breakdown.get("提交") or 0) * 2
    scores[SEMANTIC_TYPE_SUBMIT] += int(action_type_breakdown.get("submit") or 0) * 2
    scores[SEMANTIC_TYPE_SUBMIT] += int(action_type_breakdown.get("confirm_modal") or 0) * 2
    scores[SEMANTIC_TYPE_SUBMIT] += primary_action_count
    return dict(scores)


def _finalize_semantic_classification(
    *,
    page_entry: PageEntryRecord,
    page_features: list[FeaturePointRecord],
    scores: dict[str, int],
    modal_action_count: int,
    row_action_count: int,
    linked_entry_count: int,
    title_text: str,
    url_text: str,
) -> tuple[str, str, list[str], list[str]]:
    sorted_candidates = sorted(
        ((key, int(value)) for key, value in scores.items() if int(value) > 0),
        key=lambda item: (-item[1], item[0]),
    )
    review_reasons: list[str] = []
    semantic_subtypes: list[str] = []

    if not page_features:
        review_reasons.append("too_few_signals")
    if page_entry.discovery_depth > 0:
        review_reasons.append("deep_linked_entry")
    if page_entry.source_action_type == "switch_tab":
        review_reasons.append("tab_derived_view")
    if modal_action_count > 0:
        semantic_subtypes.append("弹层驱动")
    if row_action_count > 1:
        semantic_subtypes.append("行操作密集")
    if linked_entry_count > 1:
        semantic_subtypes.append("多入口导航")

    semantic_page_type = SEMANTIC_TYPE_NAVIGATION if linked_entry_count > 0 and not page_features else SEMANTIC_TYPE_MIXED
    top_score = 0
    if sorted_candidates:
        semantic_page_type, top_score = sorted_candidates[0]
        if len(sorted_candidates) > 1 and top_score - sorted_candidates[1][1] < 2:
            review_reasons.append("close_score_competition")
            semantic_page_type = SEMANTIC_TYPE_MIXED
        strong_candidates = [item for item in sorted_candidates if item[1] >= 3]
        if len(strong_candidates) >= 3:
            review_reasons.append("mixed_signals")
            semantic_page_type = SEMANTIC_TYPE_MIXED
    elif linked_entry_count > 0:
        semantic_page_type = SEMANTIC_TYPE_NAVIGATION
        top_score = 1

    if modal_action_count >= 2:
        review_reasons.append("modal_heavy")
    if row_action_count >= 2:
        review_reasons.append("row_action_heavy")
    if len(page_features) <= 1:
        review_reasons.append("too_few_signals")
    if _looks_like_generic_button_only(page_features):
        review_reasons.append("generic_button_text_only")
    if _title_action_conflict(title_text=title_text, url_text=url_text, semantic_page_type=semantic_page_type):
        review_reasons.append("title_action_conflict")
    if not page_features and linked_entry_count <= 0:
        review_reasons.append("insufficient_context")

    review_reasons = _dedupe_texts(review_reasons)
    semantic_subtypes = _dedupe_texts(semantic_subtypes)
    if top_score >= 6 and not review_reasons:
        confidence = "high"
    elif top_score >= 3 and len(review_reasons) <= 2:
        confidence = "medium"
    else:
        confidence = "low"
    return semantic_page_type, confidence, review_reasons, semantic_subtypes


def _infer_entry_role(page_entry: PageEntryRecord) -> str:
    if page_entry.entry_role:
        return page_entry.entry_role
    if page_entry.page_type == "linked_page_entry":
        return "linked_entry"
    if page_entry.source_action_type in {"inspect", "switch_tab"}:
        return "derived_view"
    return "seed_entry"


def _looks_like_generic_button_only(page_features: Iterable[FeaturePointRecord]) -> bool:
    names = [
        str(item.name or "").strip()
        for item in page_features
        if str(item.name or "").strip()
    ]
    if not names:
        return False
    generic = {"确定", "确认", "保存"}
    return all(name in generic for name in names)


def _title_action_conflict(*, title_text: str, url_text: str, semantic_page_type: str) -> bool:
    title_lower = title_text.lower()
    url_lower = url_text.lower()
    query_hint = any(keyword in title_lower or keyword in url_lower for keyword in ("query", "search", "list", "查询"))
    detail_hint = any(keyword in title_lower or keyword in url_lower for keyword in ("detail", "view", "详情"))
    create_hint = any(keyword in title_lower or keyword in url_lower for keyword in ("create", "new", "add", "新增"))
    if semantic_page_type == SEMANTIC_TYPE_QUERY and detail_hint and not query_hint:
        return True
    if semantic_page_type == SEMANTIC_TYPE_DETAIL and query_hint and not detail_hint:
        return True
    if semantic_page_type == SEMANTIC_TYPE_CREATE and query_hint and not create_hint:
        return True
    return False


def _dedupe_texts(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results
