from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import PurePath
from typing import Any


def _to_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if parsed.is_integer():
            return int(parsed)
        return parsed
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Mapping) or is_dataclass(value):
        return [_text(value)] if _text(value) else []
    if isinstance(value, Sequence):
        result: list[str] = []
        for item in value:
            text = _text(item)
            if text:
                result.append(text)
        return result
    text = _text(value)
    return [text] if text else []


def _extra_values(mapping: Mapping[str, Any], known_keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if key not in known_keys}


def _coerce_list(value: Any, factory: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping) or is_dataclass(value) or isinstance(value, str):
        return [factory(value)]
    if isinstance(value, Sequence):
        return [factory(item) for item in value]
    return [factory(value)]


@dataclass(slots=True)
class Fact:
    label: str
    value: Any
    note: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "Fact":
        if isinstance(value, cls):
            return value
        data = _to_mapping(value)
        if data:
            known_keys = {"label", "name", "value", "note"}
            payload = data.get("value")
            extras = _extra_values(data, known_keys)
            if payload is None and extras:
                payload = extras
            return cls(
                label=_text(data.get("label") or data.get("name")) or "value",
                value=payload,
                note=_text(data.get("note")),
            )
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
            return cls(label=_text(value[0]) or "value", value=value[1])
        return cls(label="value", value=value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_facts(value: Any) -> list[Fact]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if "label" in value or "name" in value:
            return [Fact.from_value(value)]
        return [Fact(label=str(key), value=item) for key, item in value.items()]
    return _coerce_list(value, Fact.from_value)


@dataclass(slots=True)
class ArtifactRef:
    label: str
    path: str
    kind: str = "file"
    note: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "ArtifactRef":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            path = value.strip()
            label = PurePath(path).name if path else "artifact"
            return cls(label=label or "artifact", path=path)
        data = _to_mapping(value)
        if not data:
            return cls(label="artifact", path="")
        path = _text(data.get("path") or data.get("uri") or data.get("href")) or ""
        label = _text(data.get("label") or data.get("name")) or (PurePath(path).name if path else "artifact")
        return cls(
            label=label,
            path=path,
            kind=_text(data.get("kind")) or "file",
            note=_text(data.get("note")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProgressCounter:
    label: str
    completed: int | float | None = None
    total: int | float | None = None
    value: int | float | str | None = None
    unit: str | None = None
    note: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "ProgressCounter":
        if isinstance(value, cls):
            return value
        data = _to_mapping(value)
        if data:
            return cls(
                label=_text(data.get("label") or data.get("name")) or "counter",
                completed=_number(data.get("completed")),
                total=_number(data.get("total")),
                value=data.get("value"),
                unit=_text(data.get("unit")),
                note=_text(data.get("note")),
            )
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
            return cls(label=_text(value[0]) or "counter", value=value[1])
        return cls(label="counter", value=value)

    @property
    def ratio(self) -> float | None:
        if self.completed is None or self.total in (None, 0):
            return None
        try:
            return float(self.completed) / float(self.total)
        except (TypeError, ZeroDivisionError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReportItem:
    item_id: str | None = None
    name: str = "item"
    status: str | None = None
    summary: str | None = None
    source: str | None = None
    owner: str | None = None
    facts: list[Fact] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "ReportItem":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(name=value)
        data = _to_mapping(value)
        if not data:
            return cls()
        known_keys = {
            "item_id",
            "id",
            "key",
            "name",
            "title",
            "label",
            "status",
            "state",
            "summary",
            "description",
            "message",
            "source",
            "origin",
            "owner",
            "scope",
            "facts",
            "details",
            "artifacts",
            "evidence",
            "screenshots",
            "tags",
            "notes",
        }
        return cls(
            item_id=_text(data.get("item_id") or data.get("id") or data.get("key")),
            name=_text(data.get("name") or data.get("title") or data.get("label")) or "item",
            status=_text(data.get("status") or data.get("state")),
            summary=_text(data.get("summary") or data.get("description") or data.get("message")),
            source=_text(data.get("source") or data.get("origin")),
            owner=_text(data.get("owner") or data.get("scope")),
            facts=_coerce_facts(data.get("facts") or data.get("details")),
            artifacts=_coerce_list(
                data.get("artifacts") or data.get("evidence") or data.get("screenshots"),
                ArtifactRef.from_value,
            ),
            tags=_string_list(data.get("tags")),
            notes=_string_list(data.get("notes")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FailureCluster:
    cluster_id: str | None = None
    category: str = "unknown"
    status: str | None = None
    summary: str | None = None
    root_cause: str | None = None
    action_level: str | None = None
    recommendation: str | None = None
    related_items: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "FailureCluster":
        if isinstance(value, cls):
            return value
        data = _to_mapping(value)
        if not data:
            return cls()
        known_keys = {
            "cluster_id",
            "id",
            "key",
            "category",
            "type",
            "name",
            "status",
            "state",
            "summary",
            "description",
            "root_cause",
            "cause",
            "action_level",
            "scope",
            "recommendation",
            "next_action",
            "related_items",
            "item_ids",
            "features",
            "facts",
            "details",
            "artifacts",
            "evidence",
            "notes",
        }
        return cls(
            cluster_id=_text(data.get("cluster_id") or data.get("id") or data.get("key")),
            category=_text(data.get("category") or data.get("type") or data.get("name")) or "unknown",
            status=_text(data.get("status") or data.get("state")),
            summary=_text(data.get("summary") or data.get("description")),
            root_cause=_text(data.get("root_cause") or data.get("cause")),
            action_level=_text(data.get("action_level") or data.get("scope")),
            recommendation=_text(data.get("recommendation") or data.get("next_action")),
            related_items=_string_list(
                data.get("related_items") or data.get("item_ids") or data.get("features")
            ),
            facts=_coerce_facts(data.get("facts") or data.get("details")),
            artifacts=_coerce_list(data.get("artifacts") or data.get("evidence"), ArtifactRef.from_value),
            notes=_string_list(data.get("notes")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelEvaluation:
    model_name: str = "model"
    summary: str | None = None
    precheck_tags: list[str] = field(default_factory=list)
    participated_stages: list[str] = field(default_factory=list)
    completion_rate: int | float | str | None = None
    response_stability: str | None = None
    average_latency_ms: int | float | None = None
    structured_output_stability: str | None = None
    recommended_role: str | None = None
    facts: list[Fact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "ModelEvaluation":
        if isinstance(value, cls):
            return value
        data = _to_mapping(value)
        if not data:
            return cls()
        known_keys = {
            "model_name",
            "name",
            "model",
            "summary",
            "description",
            "precheck_tags",
            "capability_tags",
            "participated_stages",
            "roles",
            "completion_rate",
            "response_stability",
            "average_latency_ms",
            "avg_latency_ms",
            "structured_output_stability",
            "recommended_role",
            "facts",
            "metrics",
            "notes",
        }
        return cls(
            model_name=_text(data.get("model_name") or data.get("name") or data.get("model")) or "model",
            summary=_text(data.get("summary") or data.get("description")),
            precheck_tags=_string_list(data.get("precheck_tags") or data.get("capability_tags")),
            participated_stages=_string_list(data.get("participated_stages") or data.get("roles")),
            completion_rate=data.get("completion_rate"),
            response_stability=_text(data.get("response_stability")),
            average_latency_ms=_number(data.get("average_latency_ms") or data.get("avg_latency_ms")),
            structured_output_stability=_text(data.get("structured_output_stability")),
            recommended_role=_text(data.get("recommended_role")),
            facts=_coerce_facts(data.get("facts") or data.get("metrics")),
            notes=_string_list(data.get("notes")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SectionBlock:
    title: str
    summary: str | None = None
    facts: list[Fact] = field(default_factory=list)
    items: list[ReportItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    markdown: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "SectionBlock":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(title="Additional Section", markdown=value)
        data = _to_mapping(value)
        if not data:
            return cls(title="Additional Section")
        known_keys = {"title", "name", "summary", "facts", "items", "notes", "markdown"}
        return cls(
            title=_text(data.get("title") or data.get("name")) or "Additional Section",
            summary=_text(data.get("summary")),
            facts=_coerce_facts(data.get("facts")),
            items=_coerce_list(data.get("items"), ReportItem.from_value),
            notes=_string_list(data.get("notes")),
            markdown=_text(data.get("markdown")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunSummary:
    run_id: str
    status: str = "unknown"
    project_name: str | None = None
    template_name: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: int | float | None = None
    current_round: int | None = None
    discovery_round: int | None = None
    verification_round: int | None = None
    attribution_round: int | None = None
    stop_reason: str | None = None
    next_action: str | None = None
    counts: list[ProgressCounter] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "RunSummary":
        if isinstance(value, cls):
            return value
        data = _to_mapping(value)
        if not data:
            return cls(run_id="unknown-run")
        known_keys = {
            "run_id",
            "id",
            "status",
            "state",
            "project_name",
            "project",
            "template_name",
            "template",
            "started_at",
            "finished_at",
            "duration_seconds",
            "duration",
            "current_round",
            "round",
            "discovery_round",
            "verification_round",
            "attribution_round",
            "stop_reason",
            "next_action",
            "counts",
            "counters",
            "facts",
            "details",
            "notes",
        }
        return cls(
            run_id=_text(data.get("run_id") or data.get("id")) or "unknown-run",
            status=_text(data.get("status") or data.get("state")) or "unknown",
            project_name=_text(data.get("project_name") or data.get("project")),
            template_name=_text(data.get("template_name") or data.get("template")),
            started_at=_text(data.get("started_at")),
            finished_at=_text(data.get("finished_at")),
            duration_seconds=_number(data.get("duration_seconds") or data.get("duration")),
            current_round=_number(data.get("current_round") or data.get("round")),
            discovery_round=_number(data.get("discovery_round")),
            verification_round=_number(data.get("verification_round")),
            attribution_round=_number(data.get("attribution_round")),
            stop_reason=_text(data.get("stop_reason")),
            next_action=_text(data.get("next_action")),
            counts=_coerce_list(data.get("counts") or data.get("counters"), ProgressCounter.from_value),
            facts=_coerce_facts(data.get("facts") or data.get("details")),
            notes=_string_list(data.get("notes")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunReport:
    summary: RunSummary
    page_entries: list[ReportItem] = field(default_factory=list)
    feature_points: list[ReportItem] = field(default_factory=list)
    success_items: list[ReportItem] = field(default_factory=list)
    failure_items: list[ReportItem] = field(default_factory=list)
    failure_clusters: list[FailureCluster] = field(default_factory=list)
    key_artifacts: list[ArtifactRef] = field(default_factory=list)
    network_highlights: list[ReportItem] = field(default_factory=list)
    data_observations: list[Fact] = field(default_factory=list)
    efficiency_observations: list[Fact] = field(default_factory=list)
    project_assets: list[ReportItem] = field(default_factory=list)
    promotion_candidates: list[ReportItem] = field(default_factory=list)
    model_evaluations: list[ModelEvaluation] = field(default_factory=list)
    extra_sections: list[SectionBlock] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "RunReport":
        if isinstance(value, cls):
            return value
        data = _to_mapping(value)
        if not data:
            return cls(summary=RunSummary(run_id="unknown-run"))
        summary_source = data.get("summary", data)
        known_keys = {
            "summary",
            "page_entries",
            "pages",
            "feature_points",
            "features",
            "success_items",
            "successes",
            "failure_items",
            "failures",
            "failure_clusters",
            "clusters",
            "key_artifacts",
            "artifacts",
            "screenshots",
            "network_highlights",
            "network_events",
            "data_observations",
            "data_sources",
            "efficiency_observations",
            "efficiency_metrics",
            "project_assets",
            "assets",
            "promotion_candidates",
            "candidates",
            "model_evaluations",
            "models",
            "extra_sections",
            "sections",
            "notes",
            "run_id",
            "id",
            "status",
            "state",
            "project_name",
            "project",
            "template_name",
            "template",
            "started_at",
            "finished_at",
            "duration_seconds",
            "duration",
            "current_round",
            "round",
            "discovery_round",
            "verification_round",
            "attribution_round",
            "stop_reason",
            "next_action",
            "counts",
            "counters",
            "facts",
            "details",
        }
        return cls(
            summary=RunSummary.from_value(summary_source),
            page_entries=_coerce_list(data.get("page_entries") or data.get("pages"), ReportItem.from_value),
            feature_points=_coerce_list(
                data.get("feature_points") or data.get("features"), ReportItem.from_value
            ),
            success_items=_coerce_list(
                data.get("success_items") or data.get("successes"), ReportItem.from_value
            ),
            failure_items=_coerce_list(
                data.get("failure_items") or data.get("failures"), ReportItem.from_value
            ),
            failure_clusters=_coerce_list(
                data.get("failure_clusters") or data.get("clusters"), FailureCluster.from_value
            ),
            key_artifacts=_coerce_list(
                data.get("key_artifacts") or data.get("artifacts") or data.get("screenshots"),
                ArtifactRef.from_value,
            ),
            network_highlights=_coerce_list(
                data.get("network_highlights") or data.get("network_events"),
                ReportItem.from_value,
            ),
            data_observations=_coerce_facts(data.get("data_observations") or data.get("data_sources")),
            efficiency_observations=_coerce_facts(
                data.get("efficiency_observations") or data.get("efficiency_metrics")
            ),
            project_assets=_coerce_list(
                data.get("project_assets") or data.get("assets"), ReportItem.from_value
            ),
            promotion_candidates=_coerce_list(
                data.get("promotion_candidates") or data.get("candidates"),
                ReportItem.from_value,
            ),
            model_evaluations=_coerce_list(
                data.get("model_evaluations") or data.get("models"), ModelEvaluation.from_value
            ),
            extra_sections=_coerce_list(
                data.get("extra_sections") or data.get("sections"), SectionBlock.from_value
            ),
            notes=_string_list(data.get("notes")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProgressEvent:
    event_id: str | None = None
    occurred_at: str | None = None
    stage: str = "unknown"
    step: str | None = None
    status: str = "unknown"
    message: str | None = None
    target_type: str | None = None
    target_name: str | None = None
    current_round: int | None = None
    discovery_round: int | None = None
    verification_round: int | None = None
    attribution_round: int | None = None
    next_action: str | None = None
    facts: list[Fact] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "ProgressEvent":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(message=value)
        data = _to_mapping(value)
        if not data:
            return cls()
        known_keys = {
            "event_id",
            "id",
            "occurred_at",
            "timestamp",
            "created_at",
            "stage",
            "phase",
            "step",
            "status",
            "state",
            "message",
            "summary",
            "description",
            "target_type",
            "object_type",
            "target_name",
            "object_name",
            "target",
            "current_round",
            "round",
            "discovery_round",
            "verification_round",
            "attribution_round",
            "next_action",
            "facts",
            "details",
        }
        return cls(
            event_id=_text(data.get("event_id") or data.get("id")),
            occurred_at=_text(data.get("occurred_at") or data.get("timestamp") or data.get("created_at")),
            stage=_text(data.get("stage") or data.get("phase")) or "unknown",
            step=_text(data.get("step")),
            status=_text(data.get("status") or data.get("state")) or "unknown",
            message=_text(data.get("message") or data.get("summary") or data.get("description")),
            target_type=_text(data.get("target_type") or data.get("object_type")),
            target_name=_text(data.get("target_name") or data.get("object_name") or data.get("target")),
            current_round=_number(data.get("current_round") or data.get("round")),
            discovery_round=_number(data.get("discovery_round")),
            verification_round=_number(data.get("verification_round")),
            attribution_round=_number(data.get("attribution_round")),
            next_action=_text(data.get("next_action")),
            facts=_coerce_facts(data.get("facts") or data.get("details")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProgressSnapshot:
    run_id: str
    status: str = "unknown"
    stage: str = "unknown"
    step: str | None = None
    project_name: str | None = None
    template_name: str | None = None
    current_round: int | None = None
    discovery_round: int | None = None
    verification_round: int | None = None
    attribution_round: int | None = None
    target_type: str | None = None
    target_name: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    elapsed_seconds: int | float | None = None
    heartbeat_at: str | None = None
    blocked_reason: str | None = None
    next_action: str | None = None
    counters: list[ProgressCounter] = field(default_factory=list)
    recent_events: list[ProgressEvent] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "ProgressSnapshot":
        if isinstance(value, cls):
            return value
        data = _to_mapping(value)
        if not data:
            return cls(run_id="unknown-run")
        known_keys = {
            "run_id",
            "id",
            "status",
            "state",
            "stage",
            "phase",
            "step",
            "project_name",
            "project",
            "template_name",
            "template",
            "current_round",
            "round",
            "discovery_round",
            "verification_round",
            "attribution_round",
            "target_type",
            "object_type",
            "target_name",
            "object_name",
            "target",
            "started_at",
            "updated_at",
            "elapsed_seconds",
            "duration",
            "heartbeat_at",
            "blocked_reason",
            "waiting_reason",
            "next_action",
            "counters",
            "counts",
            "recent_events",
            "events",
            "notes",
        }
        return cls(
            run_id=_text(data.get("run_id") or data.get("id")) or "unknown-run",
            status=_text(data.get("status") or data.get("state")) or "unknown",
            stage=_text(data.get("stage") or data.get("phase")) or "unknown",
            step=_text(data.get("step")),
            project_name=_text(data.get("project_name") or data.get("project")),
            template_name=_text(data.get("template_name") or data.get("template")),
            current_round=_number(data.get("current_round") or data.get("round")),
            discovery_round=_number(data.get("discovery_round")),
            verification_round=_number(data.get("verification_round")),
            attribution_round=_number(data.get("attribution_round")),
            target_type=_text(data.get("target_type") or data.get("object_type")),
            target_name=_text(data.get("target_name") or data.get("object_name") or data.get("target")),
            started_at=_text(data.get("started_at")),
            updated_at=_text(data.get("updated_at")),
            elapsed_seconds=_number(data.get("elapsed_seconds") or data.get("duration")),
            heartbeat_at=_text(data.get("heartbeat_at")),
            blocked_reason=_text(data.get("blocked_reason") or data.get("waiting_reason")),
            next_action=_text(data.get("next_action")),
            counters=_coerce_list(data.get("counters") or data.get("counts"), ProgressCounter.from_value),
            recent_events=_coerce_list(
                data.get("recent_events") or data.get("events"), ProgressEvent.from_value
            ),
            notes=_string_list(data.get("notes")),
            extra=_extra_values(data, known_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def coerce_run_summary(value: Any) -> RunSummary:
    return RunSummary.from_value(value)


def coerce_run_report(value: Any) -> RunReport:
    return RunReport.from_value(value)


def coerce_progress_event(value: Any) -> ProgressEvent:
    return ProgressEvent.from_value(value)


def coerce_progress_snapshot(value: Any) -> ProgressSnapshot:
    return ProgressSnapshot.from_value(value)
