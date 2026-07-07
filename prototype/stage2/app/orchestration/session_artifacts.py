from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

RUN_DIR_PATTERN = re.compile(r"^\d{8}_\d{6}_.+")
SESSION_SUMMARY_SCHEMA_VERSION = "stage2_orchestration_session_summary.v1"
SESSION_TIMELINE_SCHEMA_VERSION = "stage2_orchestration_session_timeline.v1"
SESSION_INDEX_SCHEMA_VERSION = "stage2_orchestration_session_index.v1"


def sync_orchestration_session_artifacts(
    stage2_root: str | Path,
    *,
    run_dirs: Iterable[str | Path] | None = None,
) -> list[dict[str, Any]]:
    root = Path(stage2_root)
    sessions_root = root / "sessions"
    grouped_runs: dict[str, list[dict[str, Any]]] = {}

    for run_dir in _iter_run_dirs(root, run_dirs):
        run_record = _load_run_session_record(run_dir)
        session_id = _text(run_record.get("session_id"))
        if not session_id:
            continue
        grouped_runs.setdefault(session_id, []).append(run_record)

    session_summaries = [
        _build_session_summary(session_id, runs)
        for session_id, runs in grouped_runs.items()
    ]
    session_summaries.sort(
        key=lambda item: (
            _text(item.get("updatedAt")) or "",
            _text(item.get("sessionId")) or "",
        ),
        reverse=True,
    )

    sessions_root.mkdir(parents=True, exist_ok=True)
    for session in session_summaries:
        session_dir = sessions_root / str(session["directoryName"])
        session_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            session_dir / "session_summary.json",
            {
                "schema_version": SESSION_SUMMARY_SCHEMA_VERSION,
                "session_id": session["sessionId"],
                "session_directory": session["directoryName"],
                "template_name": session["templateName"],
                "model_name": session["modelName"],
                "project_name": session["projectName"],
                "run_ids": session["runIds"],
                "run_count": session["runCount"],
                "latest_run_id": session["latestRunId"],
                "latest_run_status": session["latestRunStatus"],
                "latest_next_round_status": session["latestNextRoundStatus"],
                "latest_message": session["latestMessage"],
                "waiting_human": session["waitingHuman"],
                "unresolved_human_run_id": session["unresolvedHumanRunId"],
                "latest_resume_command": session["latestResumeCommand"],
                "started_at": session["startedAt"],
                "updated_at": session["updatedAt"],
                "stats": session["stats"],
            },
        )
        _write_json(
            session_dir / "session_timeline.json",
            {
                "schema_version": SESSION_TIMELINE_SCHEMA_VERSION,
                "session_id": session["sessionId"],
                "generated_at": datetime.now().isoformat(),
                "runs": session["timeline"],
            },
        )

    _write_json(
        sessions_root / "index.json",
        {
            "schema_version": SESSION_INDEX_SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(),
            "session_count": len(session_summaries),
            "sessions": [
                {
                    "session_id": session["sessionId"],
                    "session_directory": session["directoryName"],
                    "template_name": session["templateName"],
                    "model_name": session["modelName"],
                    "project_name": session["projectName"],
                    "run_count": session["runCount"],
                    "latest_run_id": session["latestRunId"],
                    "latest_run_status": session["latestRunStatus"],
                    "latest_next_round_status": session["latestNextRoundStatus"],
                    "waiting_human": session["waitingHuman"],
                    "updated_at": session["updatedAt"],
                }
                for session in session_summaries
            ],
        },
    )
    return session_summaries


def _iter_run_dirs(root: Path, run_dirs: Iterable[str | Path] | None) -> list[Path]:
    if run_dirs is not None:
        return [
            Path(item)
            for item in run_dirs
            if Path(item).is_dir()
        ]
    if not root.exists():
        return []
    return [
        path
        for path in root.iterdir()
        if path.is_dir() and RUN_DIR_PATTERN.match(path.name)
    ]


def _load_run_session_record(run_dir: Path) -> dict[str, Any]:
    current_status = _read_json(run_dir / "current_status.json")
    run_report = _read_json(run_dir / "reports" / "run_report.json")
    round_input = _read_json(run_dir / "round_input.json")
    next_round_decision = _read_json(run_dir / "next_round_decision.json")
    human_takeover = _read_json(run_dir / "human_takeover.json")
    human_takeover_resolution = _read_json(run_dir / "human_takeover_resolution.json")

    if not current_status and not run_report:
        return {}

    report_summary = _mapping(run_report.get("summary"))
    template_name = _first_text(
        current_status.get("template_name"),
        report_summary.get("template_name"),
        round_input.get("template_name"),
        "unknown-template",
    )
    model_name = _first_text(
        current_status.get("model_name"),
        report_summary.get("model_name"),
        round_input.get("model_name"),
        "unknown-model",
    )
    session_id = _first_text(
        round_input.get("orchestration_stream_id"),
        report_summary.get("orchestration_stream_id"),
        current_status.get("orchestration_stream_id"),
        f"{template_name}::{model_name}",
    )
    effective_takeover = _effective_human_takeover(
        human_takeover=human_takeover,
        next_round_decision=next_round_decision,
    )
    resolution_status = _text(human_takeover_resolution.get("status"))
    waiting_human = bool(
        effective_takeover
        and _first_text(effective_takeover.get("status"), "needs_review") != "none"
        and resolution_status != "resolved"
    )
    pending_actions = _list_value(effective_takeover.get("pending_actions"))
    scheduled_action_ids = _list_value(next_round_decision.get("scheduled_action_ids"))
    notes = _unique_texts(run_report.get("notes"), effective_takeover.get("notes"))
    run_id = _first_text(current_status.get("run_id"), run_dir.name)
    latest_message = _first_text(
        current_status.get("latest_message"),
        report_summary.get("stop_reason"),
        report_summary.get("summary"),
    )
    next_action = _first_text(
        current_status.get("next_action"),
        report_summary.get("next_action"),
    )
    updated_at = _first_text(
        current_status.get("updated_at"),
        report_summary.get("finished_at"),
        report_summary.get("started_at"),
        datetime.now().isoformat(),
    )
    return {
        "runId": run_id,
        "session_id": session_id,
        "templateName": template_name,
        "modelName": model_name,
        "projectName": _first_text(
            current_status.get("project_name"),
            report_summary.get("project_name"),
            round_input.get("project_name"),
            "第二阶段原型",
        ),
        "orchestrationRound": _first_present(
            round_input.get("round_index"),
            report_summary.get("orchestration_round"),
        ),
        "previousRunId": _first_text(
            round_input.get("previous_run_id"),
            report_summary.get("previous_run_id"),
        ),
        "overallStatus": _first_text(
            current_status.get("overall_status"),
            report_summary.get("status"),
            "unknown",
        ),
        "currentPhase": _first_text(current_status.get("current_phase"), ""),
        "currentPhaseLabel": _first_text(current_status.get("current_phase_label"), "未知阶段"),
        "currentStepLabel": _first_text(
            _mapping(current_status.get("current_step")).get("label"),
            "",
        ),
        "currentTargetLabel": _first_text(
            _mapping(current_status.get("current_target")).get("label"),
            "",
        ),
        "latestMessage": latest_message,
        "nextAction": next_action,
        "nextRound": {
            "status": _first_text(next_round_decision.get("status")),
            "shouldStart": _first_present(next_round_decision.get("should_start_next_round")),
            "targetStage": _first_text(next_round_decision.get("target_stage")),
        },
        "waitingReason": _first_text(
            current_status.get("waiting_reason"),
            effective_takeover.get("reason"),
            effective_takeover.get("waiting_reason"),
            next_round_decision.get("primary_reason"),
        ),
        "humanTakeover": {
            "status": _first_text(effective_takeover.get("status"), "none"),
            "targetStage": _first_text(effective_takeover.get("target_stage")),
            "waitingReason": _first_text(
                effective_takeover.get("waiting_reason"),
                effective_takeover.get("reason"),
                next_round_decision.get("primary_reason"),
            ),
            "resumeCommand": _first_text(effective_takeover.get("resume_command")),
            "pendingActionCount": len(pending_actions),
            "resolutionStatus": resolution_status,
            "resolvedAt": _first_text(human_takeover_resolution.get("resolved_at")),
            "resolutionOperator": _first_text(human_takeover_resolution.get("operator_id")),
            "resolutionNote": _first_text(human_takeover_resolution.get("note")),
            "readyToResume": _first_present(human_takeover_resolution.get("ready_to_resume")),
            "notes": _unique_texts(effective_takeover.get("notes"), human_takeover_resolution.get("note")),
        },
        "scheduledActionCount": len(scheduled_action_ids),
        "waitingHuman": waiting_human,
        "resumeCommand": _first_text(effective_takeover.get("resume_command")),
        "elapsedMs": _first_present(current_status.get("elapsed_ms")),
        "updatedAt": updated_at,
        "stats": {
            "promotionCandidates": _first_present(
                report_summary.get("promotion_candidate_count"),
                len(_list_value(run_report.get("promotion_candidates"))),
                0,
            ),
        },
        "notes": notes,
    }


def _effective_human_takeover(
    *,
    human_takeover: dict[str, Any],
    next_round_decision: dict[str, Any],
) -> dict[str, Any]:
    if human_takeover:
        return human_takeover
    if _text(next_round_decision.get("status")) != "needs_review":
        return {}
    return {
        "status": "needs_review",
        "target_stage": next_round_decision.get("target_stage"),
        "waiting_reason": next_round_decision.get("primary_reason"),
        "pending_actions": [],
        "resume_command": None,
        "notes": _list_value(next_round_decision.get("notes")),
    }


def _build_session_summary(session_id: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_runs = sorted(
        runs,
        key=lambda item: (
            _text(item.get("updatedAt")) or "",
            _text(item.get("runId")) or "",
        ),
        reverse=True,
    )
    latest_run = ordered_runs[0]
    oldest_run = sorted(
        runs,
        key=lambda item: (
            _text(item.get("updatedAt")) or "",
            _text(item.get("runId")) or "",
        ),
    )[0]
    unresolved_human_run = next(
        (
            run
            for run in ordered_runs
            if _text(_mapping(run.get("humanTakeover")).get("status")) not in {None, "", "none"}
            and _text(_mapping(run.get("humanTakeover")).get("resolutionStatus")) != "resolved"
        ),
        None,
    )
    return {
        "sessionId": session_id,
        "directoryName": _build_session_directory_name(session_id),
        "templateName": latest_run.get("templateName") or "未知模板",
        "modelName": latest_run.get("modelName") or "未知模型",
        "projectName": latest_run.get("projectName") or "第二阶段原型",
        "runIds": [run.get("runId") for run in ordered_runs if run.get("runId")],
        "runCount": len(ordered_runs),
        "latestRunId": latest_run.get("runId"),
        "latestRunStatus": latest_run.get("overallStatus") or "unknown",
        "latestNextRoundStatus": _mapping(latest_run.get("nextRound")).get("status"),
        "latestMessage": latest_run.get("latestMessage"),
        "waitingHuman": unresolved_human_run is not None,
        "unresolvedHumanRunId": unresolved_human_run.get("runId") if unresolved_human_run else None,
        "latestResumeCommand": _first_text(
            _mapping(unresolved_human_run.get("humanTakeover")).get("resumeCommand")
            if unresolved_human_run
            else None,
            _mapping(latest_run.get("humanTakeover")).get("resumeCommand"),
        ),
        "startedAt": oldest_run.get("updatedAt") or "",
        "updatedAt": latest_run.get("updatedAt") or "",
        "stats": {
            "failedRuns": sum(1 for run in ordered_runs if run.get("overallStatus") == "failed"),
            "waitingHumanRuns": sum(1 for run in ordered_runs if run.get("waitingHuman") is True),
            "scheduledNextRoundRuns": sum(
                1
                for run in ordered_runs
                if _mapping(run.get("nextRound")).get("shouldStart") is True
            ),
            "promotionCandidateTotal": sum(
                int(_mapping(run.get("stats")).get("promotionCandidates") or 0)
                for run in ordered_runs
            ),
        },
        "timeline": [_build_timeline_record(run) for run in ordered_runs],
    }


def _build_timeline_record(run: dict[str, Any]) -> dict[str, Any]:
    human_takeover = _mapping(run.get("humanTakeover"))
    next_round = _mapping(run.get("nextRound"))
    return {
        "runId": run.get("runId"),
        "templateName": run.get("templateName"),
        "modelName": run.get("modelName"),
        "projectName": run.get("projectName"),
        "orchestrationRound": run.get("orchestrationRound"),
        "previousRunId": run.get("previousRunId"),
        "overallStatus": run.get("overallStatus"),
        "currentPhase": run.get("currentPhase"),
        "currentPhaseLabel": run.get("currentPhaseLabel"),
        "currentStepLabel": run.get("currentStepLabel"),
        "currentTargetLabel": run.get("currentTargetLabel"),
        "latestMessage": run.get("latestMessage"),
        "nextAction": run.get("nextAction"),
        "nextRoundStatus": next_round.get("status"),
        "nextRoundShouldStart": next_round.get("shouldStart"),
        "nextRoundTargetStage": next_round.get("targetStage"),
        "waitingHuman": run.get("waitingHuman"),
        "waitingReason": run.get("waitingReason"),
        "humanTakeoverStatus": human_takeover.get("status") or "none",
        "humanTakeoverResolutionStatus": human_takeover.get("resolutionStatus"),
        "humanTakeoverResolvedAt": human_takeover.get("resolvedAt"),
        "humanTakeoverReadyToResume": human_takeover.get("readyToResume"),
        "pendingActionCount": human_takeover.get("pendingActionCount") or 0,
        "scheduledActionCount": run.get("scheduledActionCount") or 0,
        "resumeCommand": human_takeover.get("resumeCommand"),
        "elapsedMs": run.get("elapsedMs"),
        "updatedAt": run.get("updatedAt") or "",
        "notes": _list_value(run.get("notes")),
    }


def _build_session_directory_name(session_id: str) -> str:
    safe_prefix = re.sub(r"[^a-z0-9]+", "_", session_id.lower()).strip("_")[:48] or "session"
    suffix = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe_prefix}_{suffix}"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text(value)
        if text is not None:
            return text
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _unique_texts(*groups: Any) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if isinstance(group, list):
            candidates = group
        else:
            candidates = [group]
        for candidate in candidates:
            text = _text(candidate)
            if text is None or text in seen:
                continue
            seen.add(text)
            values.append(text)
    return values


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
