from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class PageEntryRecord:
    page_entry_id: str
    name: str
    url: str
    template_name: str
    source: str
    confidence: str
    page_type: str = "page_entry"
    stable_key: str | None = None
    dedupe_key: str | None = None
    dedupe_basis: dict[str, Any] = field(default_factory=dict)
    discovery_depth: int = 0
    parent_page_entry_id: str | None = None
    source_page_entry_id: str | None = None
    source_action_type: str | None = None
    entry_role: str = "seed_entry"
    semantic_page_type: str = ""
    semantic_page_type_confidence: str = "unknown"
    semantic_subtypes: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    execution_path: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeaturePointRecord:
    feature_point_id: str
    page_entry_id: str
    name: str
    feature_type: str
    template_name: str
    source: str
    confidence: str
    feature_scope: str = "page_action"
    action_type: str = "trigger"
    stable_key: str | None = None
    dedupe_key: str | None = None
    dedupe_basis: dict[str, Any] = field(default_factory=dict)
    discovery_depth: int = 0
    source_page_entry_id: str | None = None
    execution_path: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScreenshotRecord:
    screenshot_id: str
    page_entry_id: str
    feature_point_id: str | None
    stage: str
    purpose: str
    status: str
    relative_path: str | None
    source: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryResult:
    template_name: str
    generated_at: str
    strategy: str
    page_entries: list[PageEntryRecord]
    feature_points: list[FeaturePointRecord]
    screenshot_records: list[ScreenshotRecord]
    navigation_tree: list[dict[str, Any]] = field(default_factory=list)
    page_semantic_summary: list[dict[str, Any]] = field(default_factory=list)
    navigation_nodes: list[dict[str, Any]] = field(default_factory=list)
    review_queue: list[dict[str, Any]] = field(default_factory=list)
    review_hints: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_name": self.template_name,
            "generated_at": self.generated_at,
            "strategy": self.strategy,
            "page_entries": [item.to_dict() for item in self.page_entries],
            "feature_points": [item.to_dict() for item in self.feature_points],
            "screenshot_records": [item.to_dict() for item in self.screenshot_records],
            "navigation_tree": list(self.navigation_tree),
            "page_semantic_summary": list(self.page_semantic_summary),
            "navigation_nodes": list(self.navigation_nodes),
            "review_queue": list(self.review_queue),
            "review_hints": dict(self.review_hints),
            "stats": dict(self.stats),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscoveryResult":
        return cls(
            template_name=str(payload.get("template_name") or ""),
            generated_at=str(payload.get("generated_at") or utc_now_iso()),
            strategy=str(payload.get("strategy") or ""),
            page_entries=[
                PageEntryRecord(**item)
                for item in payload.get("page_entries", [])
                if isinstance(item, dict)
            ],
            feature_points=[
                FeaturePointRecord(**item)
                for item in payload.get("feature_points", [])
                if isinstance(item, dict)
            ],
            screenshot_records=[
                ScreenshotRecord(**item)
                for item in payload.get("screenshot_records", [])
                if isinstance(item, dict)
            ],
            navigation_tree=[
                item for item in payload.get("navigation_tree", []) if isinstance(item, dict)
            ],
            page_semantic_summary=[
                item for item in payload.get("page_semantic_summary", []) if isinstance(item, dict)
            ],
            navigation_nodes=[
                item for item in payload.get("navigation_nodes", []) if isinstance(item, dict)
            ],
            review_queue=[
                item for item in payload.get("review_queue", []) if isinstance(item, dict)
            ],
            review_hints=dict(payload.get("review_hints", {})),
            stats=dict(payload.get("stats", {})),
            notes=[str(item) for item in payload.get("notes", [])],
        )
