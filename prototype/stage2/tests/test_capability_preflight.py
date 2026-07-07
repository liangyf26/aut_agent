from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.config.capability_preflight import (
    find_latest_capability_snapshot,
    required_capability_tags_for_mode,
    validate_model_capabilities,
)
from prototype.stage2.app.config.models import ModelProfile


def _write_probe(
    root: Path,
    *,
    filename: str,
    generated_at: str,
    env_file: Path,
    model: str,
    base_url: str,
    tags: dict[str, bool],
) -> Path:
    path = root / filename
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "env_file": str(env_file),
                "base_url": base_url,
                "model": model,
                "capability_tags": tags,
                "results": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_required_capability_tags_for_stage2_run_sample_is_minimal_chat_gate() -> None:
    assert required_capability_tags_for_mode("stage2_run_sample") == ["chat_completion"]


def test_find_latest_capability_snapshot_prefers_matching_env_and_latest_timestamp() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_file = root / "demo.env"
        env_file.write_text("LOCAL_LLM_MODEL=AI-tester\n", encoding="utf-8")
        profile = ModelProfile(
            name="AI-tester",
            env_file=env_file,
            base_url="http://localhost:30000/v1",
            api_key="test",
            model="AI-tester",
        )
        older = _write_probe(
            root,
            filename="20260617_214200_AI-tester.json",
            generated_at="2026-06-17T13:42:00+00:00",
            env_file=env_file,
            model="AI-tester",
            base_url="http://localhost:30000/v1",
            tags={"chat_completion": True},
        )
        newer = _write_probe(
            root,
            filename="20260617_214900_AI-tester.json",
            generated_at="2026-06-17T13:49:00+00:00",
            env_file=env_file,
            model="AI-tester",
            base_url="http://localhost:30000/v1",
            tags={"chat_completion": True, "json_schema_response_format": True},
        )

        snapshot = find_latest_capability_snapshot(profile, probe_output_dir=root)

        assert snapshot is not None
        assert snapshot.report_path == newer
        assert snapshot.report_path != older
        assert snapshot.capability_tags["json_schema_response_format"] is True


def test_validate_model_capabilities_blocks_when_probe_is_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_file = root / "demo.env"
        env_file.write_text("LOCAL_LLM_MODEL=AI-tester\n", encoding="utf-8")
        profile = ModelProfile(
            name="AI-tester",
            env_file=env_file,
            base_url="http://localhost:30000/v1",
            api_key="test",
            model="AI-tester",
        )

        decision = validate_model_capabilities(profile, mode="stage2_run_sample", probe_output_dir=root)

        assert decision.status == "blocked"
        assert decision.reason_code == "capability_probe_missing"
        assert decision.snapshot is None


def test_validate_model_capabilities_blocks_when_required_tag_is_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_file = root / "demo.env"
        env_file.write_text("LOCAL_LLM_MODEL=AI-tester\n", encoding="utf-8")
        profile = ModelProfile(
            name="AI-tester",
            env_file=env_file,
            base_url="http://localhost:30000/v1",
            api_key="test",
            model="AI-tester",
        )
        _write_probe(
            root,
            filename="20260617_214900_AI-tester.json",
            generated_at="2026-06-17T13:49:00+00:00",
            env_file=env_file,
            model="AI-tester",
            base_url="http://localhost:30000/v1",
            tags={"chat_completion": True, "json_schema_response_format": False},
        )

        decision = validate_model_capabilities(
            profile,
            mode="browser_use_chatopenai_structured",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        assert decision.status == "blocked"
        assert decision.reason_code == "capability_probe_incompatible"
        assert "json_schema_response_format" in decision.missing_tags


def test_validate_model_capabilities_allows_matching_snapshot_with_required_tags() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        env_file = root / "demo.env"
        env_file.write_text("LOCAL_LLM_MODEL=AI-tester\n", encoding="utf-8")
        profile = ModelProfile(
            name="AI-tester",
            env_file=env_file,
            base_url="http://localhost:30000/v1",
            api_key="test",
            model="AI-tester",
        )
        _write_probe(
            root,
            filename="20260617_214900_AI-tester.json",
            generated_at="2026-06-17T13:49:00+00:00",
            env_file=env_file,
            model="AI-tester",
            base_url="http://localhost:30000/v1",
            tags={
                "chat_completion": True,
                "json_schema_response_format": True,
                "browser_use_chatopenai_structured": True,
            },
        )

        decision = validate_model_capabilities(
            profile,
            mode="browser_use_chatopenai_structured",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        assert decision.status == "allowed"
        assert decision.reason_code == "capability_probe_ok"
        assert decision.snapshot is not None
