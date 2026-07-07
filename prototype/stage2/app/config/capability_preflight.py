from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ModelProfile


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PROBE_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "model_capability_probe"
DEFAULT_CAPABILITY_MAX_AGE_HOURS = int(os.getenv("STAGE2_CAPABILITY_MAX_AGE_HOURS", "168"))

_DEFAULT_REQUIRED_TAGS_BY_MODE: dict[str, tuple[str, ...]] = {
    "stage2_run_sample": ("chat_completion",),
    "resume_human_takeover": ("chat_completion",),
    "template_init": ("chat_completion",),
    "browser_use_chatopenai_structured": (
        "chat_completion",
        "json_schema_response_format",
        "browser_use_chatopenai_structured",
    ),
    "browser_use_chatdeepseek_structured": (
        "chat_completion",
        "browser_use_chatdeepseek_structured",
    ),
}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_identifier(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in text)


def _parse_generated_at(value: Any) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


@dataclass(frozen=True)
class CapabilitySnapshot:
    report_path: Path
    generated_at: str | None = None
    env_file: str | None = None
    base_url: str | None = None
    model: str | None = None
    capability_tags: dict[str, bool] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    age_hours: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["report_path"] = str(self.report_path)
        return payload


@dataclass(frozen=True)
class CapabilityGateDecision:
    status: str
    reason_code: str
    reason: str
    mode: str
    profile_name: str
    required_tags: list[str] = field(default_factory=list)
    missing_tags: list[str] = field(default_factory=list)
    capability_tags: dict[str, bool] = field(default_factory=dict)
    max_age_hours: int = DEFAULT_CAPABILITY_MAX_AGE_HOURS
    snapshot: CapabilitySnapshot | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_allowed(self) -> bool:
        return self.status == "allowed"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["snapshot"] = self.snapshot.to_dict() if self.snapshot else None
        return payload


def required_capability_tags_for_mode(mode: str) -> list[str]:
    normalized = _normalize_identifier(mode)
    return list(_DEFAULT_REQUIRED_TAGS_BY_MODE.get(normalized, ("chat_completion",)))


def find_latest_capability_snapshot(
    profile: ModelProfile,
    *,
    probe_output_dir: Path = DEFAULT_PROBE_OUTPUT_DIR,
) -> CapabilitySnapshot | None:
    if not probe_output_dir.exists():
        return None

    resolved_env = profile.env_file.resolve()
    resolved_base_url = _normalize_text(profile.base_url).rstrip("/")
    normalized_model = _normalize_identifier(profile.model)
    candidates: list[tuple[int, datetime, CapabilitySnapshot]] = []

    for path in sorted(probe_output_dir.glob("*.json"), reverse=True):
        payload = _read_json_file(path)
        if not payload:
            continue

        payload_model = _normalize_text(payload.get("model"))
        payload_env = _normalize_text(payload.get("env_file"))
        payload_base_url = _normalize_text(payload.get("base_url")).rstrip("/")
        generated_at = _parse_generated_at(payload.get("generated_at"))
        if generated_at is None:
            generated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        payload_results = payload.get("results")
        capability_tags = payload.get("capability_tags") or {}
        if not isinstance(capability_tags, dict):
            capability_tags = {}

        score = 0
        if payload_model and _normalize_identifier(payload_model) == normalized_model:
            score += 4
        if payload_env:
            try:
                if Path(payload_env).resolve() == resolved_env:
                    score += 8
            except OSError:
                pass
        if payload_base_url and payload_base_url == resolved_base_url:
            score += 2
        if score <= 0:
            continue

        age_hours = max(
            0.0,
            (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600,
        )
        snapshot = CapabilitySnapshot(
            report_path=path,
            generated_at=_normalize_text(payload.get("generated_at")) or generated_at.isoformat(),
            env_file=payload_env or None,
            base_url=payload_base_url or None,
            model=payload_model or None,
            capability_tags={key: bool(value) for key, value in capability_tags.items()},
            results=[item for item in payload_results if isinstance(item, dict)] if isinstance(payload_results, list) else [],
            age_hours=round(age_hours, 3),
        )
        candidates.append((score, generated_at, snapshot))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def validate_model_capabilities(
    profile: ModelProfile,
    *,
    mode: str,
    required_tags: list[str] | None = None,
    max_age_hours: int = DEFAULT_CAPABILITY_MAX_AGE_HOURS,
    probe_output_dir: Path = DEFAULT_PROBE_OUTPUT_DIR,
) -> CapabilityGateDecision:
    required = required_tags or required_capability_tags_for_mode(mode)
    snapshot = find_latest_capability_snapshot(profile, probe_output_dir=probe_output_dir)
    if snapshot is None:
        return CapabilityGateDecision(
            status="blocked",
            reason_code="capability_probe_missing",
            reason=(
                f"No capability probe artifact matched model {profile.model} "
                f"({profile.env_file}). Run tools/probe_llm_capabilities.py before starting mode {mode}."
            ),
            mode=mode,
            profile_name=profile.name,
            required_tags=required,
            max_age_hours=max_age_hours,
            notes=[
                "Stage 2 now treats capability probe artifacts as a hard preflight dependency.",
            ],
        )

    if snapshot.age_hours is not None and snapshot.age_hours > max_age_hours:
        return CapabilityGateDecision(
            status="blocked",
            reason_code="capability_probe_stale",
            reason=(
                f"Capability probe for model {profile.model} was generated at {snapshot.generated_at} "
                f"and is older than the {max_age_hours}-hour freshness window."
            ),
            mode=mode,
            profile_name=profile.name,
            required_tags=required,
            capability_tags=dict(snapshot.capability_tags),
            max_age_hours=max_age_hours,
            snapshot=snapshot,
            notes=[
                "Refresh the capability probe before another stage-2 run.",
            ],
        )

    missing_tags = [tag for tag in required if snapshot.capability_tags.get(tag) is not True]
    if missing_tags:
        return CapabilityGateDecision(
            status="blocked",
            reason_code="capability_probe_incompatible",
            reason=(
                f"Capability probe for model {profile.model} does not satisfy mode {mode}; "
                f"missing required tags: {', '.join(missing_tags)}."
            ),
            mode=mode,
            profile_name=profile.name,
            required_tags=required,
            missing_tags=missing_tags,
            capability_tags=dict(snapshot.capability_tags),
            max_age_hours=max_age_hours,
            snapshot=snapshot,
            notes=[
                "Task routing must respect the recorded capability tags instead of assuming all wrappers are interchangeable.",
            ],
        )

    return CapabilityGateDecision(
        status="allowed",
        reason_code="capability_probe_ok",
        reason=(
            f"Capability probe for model {profile.model} satisfies mode {mode} "
            f"with required tags: {', '.join(required)}."
        ),
        mode=mode,
        profile_name=profile.name,
        required_tags=required,
        capability_tags=dict(snapshot.capability_tags),
        max_age_hours=max_age_hours,
        snapshot=snapshot,
        notes=[
            "Current routing mode only requires the tags listed in required_tags.",
        ],
    )


__all__ = [
    "CapabilityGateDecision",
    "CapabilitySnapshot",
    "DEFAULT_CAPABILITY_MAX_AGE_HOURS",
    "DEFAULT_PROBE_OUTPUT_DIR",
    "find_latest_capability_snapshot",
    "required_capability_tags_for_mode",
    "validate_model_capabilities",
]
