from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.config.capability_preflight import validate_model_capabilities
from prototype.stage2.app.config.capability_routing import build_capability_routing
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


def _build_profile(root: Path, *, model: str = "AI-tester", base_url: str = "http://localhost:30000/v1") -> ModelProfile:
    env_file = root / f"{model}.env"
    env_file.write_text(f"LOCAL_LLM_MODEL={model}\n", encoding="utf-8")
    return ModelProfile(
        name=model,
        env_file=env_file,
        base_url=base_url,
        api_key="test",
        model=model,
    )


def test_routing_blocks_discovery_and_verification_when_probe_is_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root)
        gate = validate_model_capabilities(profile, mode="stage2_run_sample", probe_output_dir=root)

        routing = build_capability_routing(profile, gate=gate)

        assert routing.gate_reason_code == "capability_probe_missing"
        assert routing.discovery is not None
        assert routing.discovery.allowed is False
        assert routing.verification is not None
        assert routing.verification.allowed is False
        assert routing.reporting is not None
        assert routing.reporting.allowed is True
        assert routing.reporting.recommended_mode == "preflight_failure_report"
        assert "probe_missing" in routing.routing_tags


def test_routing_blocks_discovery_and_verification_when_probe_is_stale() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root)
        _write_probe(
            root,
            filename="20250101_000000_AI-tester.json",
            generated_at="2025-01-01T00:00:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={"chat_completion": True},
        )
        gate = validate_model_capabilities(
            profile,
            mode="stage2_run_sample",
            probe_output_dir=root,
            max_age_hours=1,
        )

        routing = build_capability_routing(profile, gate=gate)

        assert routing.gate_reason_code == "capability_probe_stale"
        assert routing.discovery is not None
        assert routing.discovery.allowed is False
        assert routing.verification is not None
        assert routing.verification.allowed is False
        assert routing.reporting is not None
        assert routing.reporting.allowed is True
        assert routing.reporting.reason_code == "preflight_failure_report_only"
        assert "probe_stale" in routing.routing_tags


def test_routing_keeps_chat_only_profile_on_template_seed_discovery_and_minimal_verification() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root)
        _write_probe(
            root,
            filename="20260620_120000_AI-tester.json",
            generated_at="2026-06-20T12:00:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={"chat_completion": True},
        )
        gate = validate_model_capabilities(
            profile,
            mode="stage2_run_sample",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        routing = build_capability_routing(profile, gate=gate)

        assert routing.gate_reason_code == "capability_probe_ok"
        assert routing.discovery is not None
        assert routing.discovery.allowed is True
        assert routing.discovery.recommended_mode == "template_seed_discovery"
        assert routing.discovery.reason_code == "template_seed_discovery_only"
        assert routing.verification is not None
        assert routing.verification.allowed is True
        assert routing.verification.recommended_mode == "playwright_deterministic_verification"
        assert "minimal chat-completion gate" in routing.verification.reason
        assert routing.reporting is not None
        assert routing.reporting.recommended_mode == "llm_assisted_reporting"


def test_routing_marks_json_schema_profile_as_structured_candidate_without_wrapper_proof() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="Qwen")
        _write_probe(
            root,
            filename="20260620_120500_Qwen.json",
            generated_at="2026-06-20T12:05:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={
                "chat_completion": True,
                "json_schema_response_format": True,
            },
        )
        gate = validate_model_capabilities(
            profile,
            mode="stage2_run_sample",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        routing = build_capability_routing(profile, gate=gate)

        assert routing.discovery is not None
        assert routing.discovery.allowed is True
        assert routing.discovery.recommended_mode == "browser_use_structured_candidate"
        assert routing.discovery.reason_code == "browser_use_structured_candidate"
        assert "structured_candidate" in routing.discovery.routing_tags
        assert "json_schema_ready" in routing.routing_tags


def test_routing_uses_openai_browser_use_wrapper_when_tag_is_present() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="AI-tester")
        _write_probe(
            root,
            filename="20260620_121000_AI-tester.json",
            generated_at="2026-06-20T12:10:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={
                "chat_completion": True,
                "json_schema_response_format": True,
                "browser_use_chatopenai_structured": True,
            },
        )
        gate = validate_model_capabilities(
            profile,
            mode="browser_use_chatopenai_structured",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        routing = build_capability_routing(profile, gate=gate)

        assert routing.gate_reason_code == "capability_probe_ok"
        assert routing.discovery is not None
        assert routing.discovery.allowed is True
        assert routing.discovery.recommended_mode == "browser_use_chatopenai_structured"
        assert routing.discovery.reason_code == "browser_use_openai_structured_ready"
        assert "openai_wrapper" in routing.discovery.routing_tags


def test_routing_uses_deepseek_browser_use_wrapper_when_tag_is_present() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="deepseek-v4-flash", base_url="https://api.deepseek.com/v1")
        _write_probe(
            root,
            filename="20260620_121500_deepseek-v4-flash.json",
            generated_at="2026-06-20T12:15:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={
                "chat_completion": True,
                "browser_use_chatdeepseek_structured": True,
            },
        )
        gate = validate_model_capabilities(
            profile,
            mode="browser_use_chatdeepseek_structured",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        routing = build_capability_routing(profile, gate=gate)

        assert routing.gate_reason_code == "capability_probe_ok"
        assert routing.discovery is not None
        assert routing.discovery.allowed is True
        assert routing.discovery.recommended_mode == "browser_use_chatdeepseek_structured"
        assert routing.discovery.reason_code == "browser_use_deepseek_structured_ready"
        assert "deepseek_wrapper" in routing.discovery.routing_tags


def test_routing_downgrades_incompatible_strict_openai_mode_to_supported_stage_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="Qwen")
        _write_probe(
            root,
            filename="20260620_122000_Qwen.json",
            generated_at="2026-06-20T12:20:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={"chat_completion": True},
        )
        gate = validate_model_capabilities(
            profile,
            mode="browser_use_chatopenai_structured",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        routing = build_capability_routing(profile, gate=gate)

        assert gate.reason_code == "capability_probe_incompatible"
        assert routing.gate_reason_code == "capability_probe_incompatible"
        assert routing.discovery is not None
        assert routing.discovery.allowed is True
        assert routing.discovery.recommended_mode == "template_seed_discovery"
        assert routing.verification is not None
        assert routing.verification.allowed is True
        assert "strict_mode_downgraded" in routing.routing_tags
        assert routing.notes


def test_routing_can_be_built_from_snapshot_only_when_freshness_is_checked_elsewhere() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="AI-tester")
        _write_probe(
            root,
            filename="20260620_122500_AI-tester.json",
            generated_at="2026-06-20T12:25:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={
                "chat_completion": True,
                "json_schema_response_format": True,
                "browser_use_chatopenai_structured": True,
            },
        )
        gate = validate_model_capabilities(
            profile,
            mode="stage2_run_sample",
            probe_output_dir=root,
            max_age_hours=9999,
        )

        routing = build_capability_routing(profile, snapshot=gate.snapshot)

        assert routing.gate_status == "snapshot_only"
        assert routing.gate_reason_code == "routing_snapshot_only"
        assert routing.discovery is not None
        assert routing.discovery.recommended_mode == "browser_use_chatopenai_structured"
        assert routing.notes
