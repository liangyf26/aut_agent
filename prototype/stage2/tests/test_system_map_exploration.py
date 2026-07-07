from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import prototype.stage2.main as stage2_main  # noqa: E402


def test_explore_system_map_returns_bootstrap_and_discovery_payload(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        template_root = Path(tmpdir)
        monkeypatch.setattr(stage2_main, "TEMPLATE_ROOT", template_root)

        async def fake_run_live_discovery(
            template_name: str,
            *,
            cdp_url: str,
            model_name: str | None = None,
            reuse_completed_discovery: bool = False,
        ) -> dict[str, object]:
            return {
                "template": template_name,
                "model": model_name or "default-model",
                "output_dir": str(template_root / "artifacts"),
                "navigation_node_count": 3,
                "page_semantic_count": 2,
                "semantic_page_type_breakdown": {"查询列表页": 1, "导航页": 1},
                "navigation_tree_path": str(template_root / "artifacts" / "navigation_tree.json"),
                "page_semantic_summary_path": str(template_root / "artifacts" / "page_semantic_summary.json"),
            }

        monkeypatch.setattr(stage2_main, "run_live_discovery", fake_run_live_discovery)
        payload = asyncio.run(
            stage2_main.explore_system_map(
                target_name="公交业务系统",
                start_url="https://example.com/home",
                cdp_url="http://localhost:9222",
                model_name="demo-model",
            )
        )

        assert payload["mode"] == "system_map_exploration"
        assert payload["bootstrap"]["mode"] == "system_map_bootstrap"
        assert payload["discovery"]["navigation_node_count"] == 3
        assert "navigation_tree.json" in payload["discovery"]["navigation_tree_path"]


def test_explore_system_map_reuses_existing_template_when_same_start_url(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        template_root = Path(tmpdir)
        monkeypatch.setattr(stage2_main, "TEMPLATE_ROOT", template_root)

        stage2_main.bootstrap_system_exploration_template(
            target_name="公交业务系统",
            start_url="https://example.com/home",
            template_name="bus_system_map",
        )

        async def fake_run_live_discovery(
            template_name: str,
            *,
            cdp_url: str,
            model_name: str | None = None,
            reuse_completed_discovery: bool = False,
        ) -> dict[str, object]:
            return {
                "template": template_name,
                "model": model_name or "default-model",
                "output_dir": str(template_root / "artifacts"),
                "navigation_node_count": 1,
                "page_semantic_count": 1,
                "semantic_page_type_breakdown": {"导航页": 1},
                "navigation_tree_path": str(template_root / "artifacts" / "navigation_tree.json"),
                "page_semantic_summary_path": str(template_root / "artifacts" / "page_semantic_summary.json"),
            }

        monkeypatch.setattr(stage2_main, "run_live_discovery", fake_run_live_discovery)
        payload = asyncio.run(
            stage2_main.explore_system_map(
                target_name="公交业务系统",
                start_url="https://example.com/home",
                cdp_url="http://localhost:9222",
                model_name="demo-model",
                template_name="bus_system_map",
            )
        )

        assert payload["bootstrap"]["reused_existing_template"] is True
        assert payload["template"] == "bus_system_map"
