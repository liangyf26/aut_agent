from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.discovery import DiscoveryArtifactWriter, DiscoveryPlanner  # noqa: E402
from prototype.stage2.app.discovery.review import (  # noqa: E402
    REVIEW_PATCH_FILENAME,
    apply_discovery_review_patch,
    load_discovery_review_patch,
)
from prototype.stage2.app.runtime.templates import load_template_bundle  # noqa: E402
from prototype.stage2.app.verification import run_sample as run_sample_module  # noqa: E402


TEMPLATE_ROOT = ROOT_DIR / "prototype" / "stage2" / "templates"


def test_apply_discovery_review_patch_can_ignore_and_rename_records() -> None:
    bundle = load_template_bundle(TEMPLATE_ROOT / "suyuan_online_query_reset")
    result = DiscoveryPlanner().plan(
        template_name=bundle.name,
        template=bundle.template,
        baseline=bundle.baseline,
    )
    feature_id = result.feature_points[0].feature_point_id
    patch = {
        "status": "reviewed",
        "ignore_record_ids": [feature_id],
        "rename_records": {
            result.page_entries[0].page_entry_id: "线上备案申请查询入口",
        },
        "summary": "ignored generated feature point for manual regrouping",
    }

    reviewed = apply_discovery_review_patch(result, patch)

    assert len(reviewed.feature_points) == 0
    assert reviewed.page_entries[0].name == "线上备案申请查询入口"
    assert reviewed.review_hints["status"] == "reviewed"
    assert reviewed.stats["review_patch_applied"] is True
    assert reviewed.stats["review_patch_ignored_count"] == 1


def test_build_run_contexts_prefers_shared_live_discovery_outputs_when_present(monkeypatch) -> None:
    template_name = "suyuan_online_query_reset"
    bundle = load_template_bundle(TEMPLATE_ROOT / template_name)
    seeded = DiscoveryPlanner(strategy_name="test_live_reuse").plan(
        template_name=bundle.name,
        template=bundle.template,
        baseline=bundle.baseline,
    )
    review_patch = {
        "status": "reviewed",
        "rename_records": {seeded.feature_points[0].feature_point_id: "查询条件重置"},
        "summary": "manual review rename",
    }
    original_exists = pathlib.Path.exists

    def fake_exists(path: Path) -> bool:
        if path.name == f"live_discovery_{template_name}":
            return True
        return original_exists(path)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)
    monkeypatch.setattr(run_sample_module.DiscoveryArtifactWriter, "load", staticmethod(lambda _: seeded))
    monkeypatch.setattr(run_sample_module, "load_discovery_review_patch", lambda _: review_patch)

    contexts = run_sample_module.build_run_contexts(template_name=template_name)
    try:
        assert contexts
        for context in contexts:
            assert context.discovery_result is not None
            assert context.discovery_paths is not None
            assert context.discovery_result.strategy == "test_live_reuse"
            assert context.discovery_result.feature_points[0].name == "查询条件重置"
            assert (context.artifacts.run_dir / "discovery_result.json").exists()
    finally:
        for context in contexts:
            if context.artifacts.run_dir.exists():
                for child in sorted(context.artifacts.run_dir.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                context.artifacts.run_dir.rmdir()


def test_load_discovery_review_patch_returns_empty_mapping_for_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        assert load_discovery_review_patch(Path(tmpdir)) == {}
