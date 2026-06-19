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
            "review_queue": list(self.review_queue),
            "review_hints": dict(self.review_hints),
            "stats": dict(self.stats),
            "notes": list(self.notes),
        }
