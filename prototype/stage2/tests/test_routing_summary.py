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
from prototype.stage2.app.config.run_policy_loader import load_run_policy, resolve_run_policy_payload
from prototype.stage2.app.orchestration.routing_summary import build_routing_summary


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


def test_routing_summary_flattens_chat_only_profile_for_cli_output() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root)
        _write_probe(
            root,
            filename="20260620_130000_AI-tester.json",
            generated_at="2026-06-20T13:00:00+00:00",
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
        policy = load_run_policy(root / "run_policy.json", project_name="demo-project", template_name="online-apply")

        summary = build_routing_summary(
            profile,
            capability_gate=gate,
            capability_routing=routing,
            run_policy=policy,
        )

        payload = summary.to_dict()
        assert payload["model"] == "AI-tester"
        assert payload["preflight_status"] == "allowed"
        assert payload["discovery_mode"] == "template_seed_discovery"
        assert payload["verification_mode"] == "playwright_deterministic_verification"
        assert payload["reporting_mode"] == "llm_assisted_reporting"
        assert payload["run_policy_load_status"] == "missing"
        assert payload["enabled_capability_tags"] == ["chat_completion"]
        assert any("built-in blocked default" in note for note in summary.notes)


def test_routing_summary_marks_structured_candidate_without_wrapper_proof() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="Qwen")
        _write_probe(
            root,
            filename="20260620_130500_Qwen.json",
            generated_at="2026-06-20T13:05:00+00:00",
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
        policy = resolve_run_policy_payload(
            {
                "run_policy": {
                    "projects": {
                        "Demo Project": {
                            "allowlist": [{"action_id": "submit_case"}],
                        }
                    }
                }
            },
            project_name="demo project",
            template_name="online apply",
            policy_path=Path("fixture-run-policy.json"),
            source_name="fixture",
        )

        summary = build_routing_summary(
            profile,
            capability_gate=gate,
            capability_routing=routing,
            run_policy=policy,
        )

        assert summary.discovery_mode == "browser_use_structured_candidate"
        assert summary.run_policy_load_status == "loaded"
        assert summary.run_policy_rule_count == 1
        assert "json_schema_response_format" in summary.enabled_capability_tags
        assert any("candidate path" in note for note in summary.notes)


def test_routing_summary_keeps_template_seed_mode_for_chat_only_profile() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="AI-tester")
        _write_probe(
            root,
            filename="20260620_130700_AI-tester.json",
            generated_at="2026-06-20T13:07:00+00:00",
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
        summary = build_routing_summary(
            profile,
            capability_gate=gate,
            capability_routing=routing,
        )

        assert summary.discovery_mode == "template_seed_discovery"
        assert summary.discovery_allowed is True
        assert summary.discovery_reason_code == "template_seed_discovery_only"
        assert any("template-seeded path" in note for note in summary.notes)


def test_routing_summary_records_strict_incompatible_mode_as_downgraded() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="Qwen")
        _write_probe(
            root,
            filename="20260620_131000_Qwen.json",
            generated_at="2026-06-20T13:10:00+00:00",
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
        policy = resolve_run_policy_payload(
            {"run_policy": {"risky_submit_default_decision": "needs_review"}},
            project_name="demo project",
            template_name="online apply",
            policy_path=Path("fixture-run-policy.json"),
            source_name="fixture",
        )

        summary = build_routing_summary(
            profile,
            capability_gate=gate,
            capability_routing=routing,
            run_policy=policy,
        )

        assert summary.preflight_reason_code == "capability_probe_incompatible"
        assert summary.requested_mode == "browser_use_chatopenai_structured"
        assert summary.discovery_mode == "template_seed_discovery"
        assert any("downgraded" in note for note in summary.notes)


def test_routing_summary_surfaces_missing_preflight_as_reporting_only() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root)
        gate = validate_model_capabilities(profile, mode="stage2_run_sample", probe_output_dir=root)
        routing = build_capability_routing(profile, gate=gate)
        policy = load_run_policy(root / "run_policy.json")

        summary = build_routing_summary(
            profile,
            capability_gate=gate,
            capability_routing=routing,
            run_policy=policy,
        )

        assert summary.preflight_status == "blocked"
        assert summary.discovery_mode == "blocked_missing_capability_probe"
        assert summary.verification_mode == "blocked_missing_capability_probe"
        assert summary.reporting_mode == "preflight_failure_report"
        assert any("probe is refreshed" in note for note in summary.notes)


def test_routing_summary_surfaces_stale_preflight_and_loaded_policy() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        profile = _build_profile(root, model="deepseek-v4-flash", base_url="https://api.deepseek.com/v1")
        _write_probe(
            root,
            filename="20250101_000000_deepseek-v4-flash.json",
            generated_at="2025-01-01T00:00:00+00:00",
            env_file=profile.env_file,
            model=profile.model,
            base_url=profile.base_url,
            tags={"chat_completion": True, "browser_use_chatdeepseek_structured": True},
        )
        gate = validate_model_capabilities(
            profile,
            mode="browser_use_chatdeepseek_structured",
            probe_output_dir=root,
            max_age_hours=1,
        )
        routing = build_capability_routing(profile, gate=gate)
        policy = resolve_run_policy_payload(
            {
                "run_policy": {
                    "projects": {
                        "Demo Project": {
                            "templates": {
                                "Online Apply": {
                                    "allowlist": [{"action_id": "submit_case"}],
                                }
                            }
                        }
                    }
                }
            },
            project_name="demo project",
            template_name="online apply",
            policy_path=Path("fixture-run-policy.json"),
            source_name="fixture",
        )

        summary = build_routing_summary(
            profile,
            capability_gate=gate,
            capability_routing=routing,
            run_policy=policy,
        )

        assert summary.preflight_reason_code == "capability_probe_stale"
        assert summary.discovery_allowed is False
        assert summary.run_policy_load_status == "loaded"
        assert summary.applied_policy_sources[-1].endswith("templates.online_apply")
        assert any("resolved from" in note for note in summary.notes)
