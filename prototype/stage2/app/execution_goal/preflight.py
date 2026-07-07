"""
Stage E execution preflight (P0-3).

Runs a capability preflight before Stage E execution starts, determines
which L1-L4 detection layers are available for this run, and writes a
``routing_summary.json`` to the output directory.

The preflight is only meaningful for ``real_browser`` mode — fixture_simulated
needs no model at all.  Without a ``model_name``, L4 (Browser Use) is
declared unavailable and the routing summary records a "skipped" status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ExecutionLayerAvailability:
    """Which layers of the L1 → L4 detection pyramid are available."""

    l1_static_snapshot: bool = True
    l2_locator_candidates: bool = True
    l3_aria_semantic: bool = True
    l4_browser_use: bool = False
    l4_blocked_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "l1_static_snapshot": self.l1_static_snapshot,
            "l2_locator_candidates": self.l2_locator_candidates,
            "l3_aria_semantic": self.l3_aria_semantic,
            "l4_browser_use": self.l4_browser_use,
            "l4_blocked_reason": self.l4_blocked_reason,
            "notes": list(self.notes),
        }


def run_execution_preflight(
    output_dir: Path,
    *,
    model_name: str | None = None,
    mode: str = "fixture_simulated",
) -> tuple[ExecutionLayerAvailability, Path | None]:
    """Run capability preflight and write ``routing_summary.json``.

    Returns ``(layer_availability, routing_summary_path)``.
    ``routing_summary_path`` is the on-disk file path.
    """
    if mode != "real_browser" or not model_name:
        skip_reason = "fixture_mode_no_preflight_required" if mode != "real_browser" else "no_model_provided"
        layers = ExecutionLayerAvailability(
            l4_blocked_reason=f"preflight_skipped: {skip_reason}",
            notes=[
                f"Preflight skipped ({skip_reason}).",
                "L1 (static snapshot), L2 (locator candidate pool), L3 (ARIA semantic) are "
                "always available — they require only Playwright, no LLM model.",
                "L4 (Browser Use) requires a capability probe and model_name in real_browser mode.",
            ],
        )
        routing_path = _write_routing_summary_json(
            output_dir, layers.to_dict(), preflight_status="skipped", skip_reason=skip_reason
        )
        return layers, routing_path

    return _run_full_preflight(output_dir, model_name)


def _run_full_preflight(
    output_dir: Path,
    model_name: str,
) -> tuple[ExecutionLayerAvailability, Path]:
    from prototype.stage2.app.config import (
        build_capability_routing,
        load_model_profiles,
        validate_model_capabilities,
    )
    from prototype.stage2.app.orchestration.routing_summary import build_routing_summary

    # Inlined from verification.constants to avoid importing the verification
    # package (which requires playwright — unavailable in pure test/CI envs).
    _ROOT_DIR = Path(__file__).resolve().parents[4]
    DEFAULT_ENV_FILES = [
        _ROOT_DIR / "demo" / ".env",
        _ROOT_DIR / "demo" / "local_qwen.env",
    ]

    profiles = load_model_profiles(DEFAULT_ENV_FILES)
    profile = next((p for p in profiles if p.name == model_name), None)

    if profile is None:
        layers = ExecutionLayerAvailability(
            l4_browser_use=False,
            l4_blocked_reason=f"profile_not_found: {model_name}",
            notes=[
                f"Model profile '{model_name}' not found among configured profiles "
                f"({', '.join(p.name for p in profiles)}).",
                "L4 Browser Use is unavailable without a recognized model profile.",
            ],
        )
        routing_path = _write_routing_summary_json(
            output_dir,
            layers.to_dict(),
            preflight_status="skipped",
            skip_reason=f"profile_not_found:{model_name}",
            profiles_checked=[p.name for p in profiles],
        )
        return layers, routing_path

    capability_gate = validate_model_capabilities(profile, mode="stage2_run_sample")
    capability_routing = build_capability_routing(profile, gate=capability_gate)
    routing_summary = build_routing_summary(
        profile,
        capability_gate=capability_gate,
        capability_routing=capability_routing,
    )

    verification_route = capability_routing.verification
    verification_ok = verification_route is not None and verification_route.allowed

    l4_blocked = (
        "P0-4 Browser Use executor and P0-5 cascade (L3 ARIA + L4 Browser Use) "
        "are implemented. L4 is available when capability preflight confirms "
        "Browser Use readiness and a model profile is provided."
    )
    notes: list[str] = [
        "Stage E capability preflight complete.",
        f"Verification route: {verification_route.recommended_mode if verification_route else 'blocked'} "
        f"(allowed={verification_ok}).",
        "L1 (static snapshot), L2 (locator candidate pool), L3 (ARIA semantic) "
        "are always available — Playwright-native, no LLM required.",
        l4_blocked,
    ]
    if verification_ok:
        notes.append(
            "This profile's verification route allows L4 Browser Use fallback "
            "in the L1→L2→L3→L4 cascade."
        )

    layers = ExecutionLayerAvailability(
        l4_browser_use=False,
        l4_blocked_reason=l4_blocked,
        notes=notes,
    )

    full_payload = layers.to_dict()
    full_payload["routing_summary"] = routing_summary.to_dict()
    routing_path = _write_routing_summary_json(
        output_dir,
        full_payload,
        preflight_status="full",
        model=profile.model,
        profile_name=profile.name,
        gate_status=capability_gate.status,
        gate_reason_code=capability_gate.reason_code,
    )
    return layers, routing_path


def _write_routing_summary_json(
    output_dir: Path,
    layers_payload: dict[str, Any],
    **extra: Any,
) -> Path:
    import json
    from datetime import datetime, timezone

    output_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"layers": layers_payload, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    payload.update(extra)
    path = output_dir / "routing_summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = [
    "ExecutionLayerAvailability",
    "run_execution_preflight",
]
