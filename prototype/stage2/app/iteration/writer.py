from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .builder import build_iteration_outputs
from .models import IterationArtifacts


def write_iteration_artifacts(
    output_dir: str | Path,
    *,
    run_report: Any = None,
    status_snapshot: Any = None,
    attempts: list[Any] | None = None,
    previous_iteration: Any = None,
    max_attempts: int | None = None,
    round_input: Any = None,
) -> IterationArtifacts:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if round_input:
        _write_json(root / "round_input.json", round_input)
    previous_iteration = previous_iteration or _load_previous_iteration(root)
    artifacts = build_iteration_outputs(
        run_report=run_report,
        status_snapshot=status_snapshot,
        attempts=attempts,
        previous_iteration=previous_iteration,
        max_attempts=max_attempts,
        round_input=round_input,
    )
    _write_json(
        root / "round_input.json",
        artifacts.round_input.to_dict() if artifacts.round_input else {},
    )
    _write_json(
        root / "failure_clusters.json",
        {
            "summary": artifacts.summary.to_dict(),
            "clusters": [cluster.to_dict() for cluster in artifacts.failure_clusters],
        },
    )
    _write_json(
        root / "retry_plan.json",
        artifacts.retry_plan.to_dict() if artifacts.retry_plan else {},
    )
    _write_json(
        root / "promotion_candidates.json",
        {
            "summary": artifacts.summary.to_dict(),
            "candidates": [candidate.to_dict() for candidate in artifacts.promotion_candidates],
        },
    )
    _write_json(
        root / "stop_conditions.json",
        artifacts.stop_conditions.to_dict() if artifacts.stop_conditions else {},
    )
    _write_json(
        root / "iteration_comparison.json",
        artifacts.iteration_comparison.to_dict() if artifacts.iteration_comparison else {},
    )
    _write_json(
        root / "next_round_decision.json",
        artifacts.next_round_decision.to_dict() if artifacts.next_round_decision else {},
    )
    return artifacts


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_previous_iteration(root: Path) -> dict[str, Any] | None:
    previous_root = _find_previous_iteration_root(root)
    if previous_root is not None:
        payload = _load_iteration_payload(previous_root)
        if payload:
            return payload

    payload = _load_iteration_payload(root)
    if payload:
        return payload
    return None


def _load_iteration_payload(root: Path) -> dict[str, Any] | None:
    round_input = _read_json(root / "round_input.json")
    failure_payload = _read_json(root / "failure_clusters.json")
    retry_plan = _read_json(root / "retry_plan.json")
    promotion_payload = _read_json(root / "promotion_candidates.json")
    stop_conditions = _read_json(root / "stop_conditions.json")
    iteration_comparison = _read_json(root / "iteration_comparison.json")
    next_round_decision = _read_json(root / "next_round_decision.json")

    if not any(
        payload
        for payload in (
            failure_payload,
            retry_plan,
            promotion_payload,
            stop_conditions,
            iteration_comparison,
            next_round_decision,
            round_input,
        )
    ):
        return None

    summary = (
        failure_payload.get("summary")
        or promotion_payload.get("summary")
        or {}
    )
    return {
        "summary": summary,
        "round_input": round_input,
        "orchestration_stream_id": round_input.get("orchestration_stream_id"),
        "template_name": round_input.get("template_name") or summary.get("template_name"),
        "model_name": round_input.get("model_name"),
        "project_name": round_input.get("project_name") or summary.get("project_name"),
        "failure_clusters": failure_payload.get("clusters", []),
        "retry_plan": retry_plan,
        "promotion_candidates": promotion_payload.get("candidates", []),
        "stop_conditions": stop_conditions,
        "iteration_comparison": iteration_comparison,
        "next_round_decision": next_round_decision,
    }


def _find_previous_iteration_root(root: Path) -> Path | None:
    parent = root.parent
    if not parent.exists():
        return None

    current_payload = _load_iteration_payload(root)
    candidates = [
        path
        for path in parent.iterdir()
        if path.is_dir() and path != root and _has_iteration_artifacts(path)
    ]
    if not candidates:
        return None

    if current_payload:
        candidates = _filter_matching_candidates(candidates, current_payload)
        if not candidates:
            return None

    root_suffix = _run_name_suffix(root.name)
    if root_suffix:
        candidates = [
            path for path in candidates if _run_name_suffix(path.name) == root_suffix
        ] or candidates

    earlier = sorted((path for path in candidates if path.name < root.name), reverse=True)
    if earlier:
        return earlier[0]

    return sorted(candidates, reverse=True)[0]


def _has_iteration_artifacts(root: Path) -> bool:
    return any(
        (root / filename).exists()
        for filename in (
            "round_input.json",
            "iteration_comparison.json",
            "next_round_decision.json",
            "failure_clusters.json",
            "retry_plan.json",
            "promotion_candidates.json",
        )
    )


def _run_name_suffix(name: str) -> str | None:
    match = re.match(r"^\d{8}_\d{6}_(.+)$", name)
    if match:
        return match.group(1)
    return None


def _filter_matching_candidates(candidates: list[Path], current_payload: dict[str, Any]) -> list[Path]:
    current_stream = _normalize_text(current_payload.get("orchestration_stream_id"))
    current_template = _normalize_text(current_payload.get("template_name"))
    current_model = _normalize_text(current_payload.get("model_name"))
    current_project = _normalize_text(current_payload.get("project_name"))
    matched: list[Path] = []
    for path in candidates:
        payload = _load_iteration_payload(path)
        if not payload:
            continue
        candidate_stream = _normalize_text(payload.get("orchestration_stream_id"))
        candidate_template = _normalize_text(payload.get("template_name"))
        candidate_model = _normalize_text(payload.get("model_name"))
        candidate_project = _normalize_text(payload.get("project_name"))
        if current_stream and candidate_stream != current_stream:
            continue
        if current_template and candidate_template != current_template:
            continue
        if current_model and candidate_model != current_model:
            continue
        if current_project and candidate_project != current_project:
            continue
        matched.append(path)
    return matched


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
