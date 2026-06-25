from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prototype.stage2.app.v3_orchestrator import V3RunConfig, run_v3_assessment  # noqa: E402


REQUIRED_ARTIFACTS = {
    "run_state.json",
    "pages.json",
    "features.json",
    "cases.json",
    "execution_results.json",
    "round_analysis.json",
    "next_round_plan.json",
    "human_tasks.json",
    "report.md",
}


def test_v3_demo_run_writes_required_contract_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="追本溯源管理系统",
                    artifact_root=Path(tmpdir),
                    run_id="demo_contract",
                    max_pages=3,
                )
            )
        )

        run_dir = Path(result["run_dir"])
        assert {path.name for path in run_dir.iterdir()} >= REQUIRED_ARTIFACTS
        assert result["summary"]["page_count"] == 3
        assert result["summary"]["feature_count"] > 0
        assert result["summary"]["case_count"] == result["summary"]["feature_count"]
        assert result["summary"]["open_human_task_count"] >= 1

        execution_results = _read_json(run_dir / "execution_results.json")["results"]
        assert any(item["status"] == "passed_safe_placeholder" for item in execution_results)
        assert any(item["status"] == "blocked_by_policy" for item in execution_results)

        human_tasks = _read_json(run_dir / "human_tasks.json")
        assert "不能要求用户直接修改 JSON" in "\n".join(human_tasks["notes"])


def test_v3_run_can_consume_existing_discovery_paths() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "source"
        source_dir.mkdir()
        page_entries_path = source_dir / "page_entries.json"
        feature_points_path = source_dir / "feature_points.json"
        page_entries_path.write_text(
            json.dumps(
                [
                    {
                        "page_entry_id": "page_live_home",
                        "name": "系统首页",
                        "url": "https://example.test/home",
                        "source": "fake_live",
                        "confidence": "high",
                        "semantic_page_type": "dashboard",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        feature_points_path.write_text(
            json.dumps(
                [
                    {
                        "feature_point_id": "feature_live_query",
                        "page_entry_id": "page_live_home",
                        "name": "查询业务记录",
                        "feature_type": "查询",
                        "source": "fake_live",
                        "confidence": "high",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        async def fake_discovery_provider() -> dict[str, str]:
            return {
                "status": "completed",
                "page_entries_path": str(page_entries_path),
                "feature_points_path": str(feature_points_path),
            }

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="真实系统样本",
                    start_url="https://example.test/home",
                    artifact_root=root / "runs",
                    run_id="live_contract",
                    use_live_discovery=True,
                ),
                discovery_provider=fake_discovery_provider,
            )
        )

        run_dir = Path(result["run_dir"])
        pages = _read_json(run_dir / "pages.json")["pages"]
        features = _read_json(run_dir / "features.json")["features"]
        cases = _read_json(run_dir / "cases.json")["cases"]
        next_round_plan = _read_json(run_dir / "next_round_plan.json")

        assert pages[0]["page_id"] == "page_live_home"
        assert features[0]["feature_type"] == "query"
        assert cases[0]["auto_allowed"] is True
        assert next_round_plan["status"] == "ready"


def test_v3_run_records_missing_scope_target_without_marking_goal_complete() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="目标页面未命中系统",
                    start_url="https://example.test/home",
                    artifact_root=root / "runs",
                    run_id="scope_missing_contract",
                    max_pages=1,
                    metadata={"scope": "优先完成“线上备案申请”页面"},
                )
            )
        )

        run_dir = Path(result["run_dir"])
        round_analysis = _read_json(run_dir / "round_analysis.json")
        human_tasks = _read_json(run_dir / "human_tasks.json")
        next_round_plan = _read_json(run_dir / "next_round_plan.json")
        report = (run_dir / "report.md").read_text(encoding="utf-8")

        assert round_analysis["analysis_mode"] == "deterministic_rule_review"
        assert round_analysis["ai_provider_status"] == "not_connected"
        assert round_analysis["missing_scope_targets"] == ["线上备案申请"]
        assert any(
            cluster["cluster_id"] == "scope_target_not_found"
            for cluster in round_analysis["failure_clusters"]
        )
        assert any(
            task["task_id"] == "human_task_scope_target_not_found"
            for task in human_tasks["tasks"]
        )
        assert next_round_plan["status"] == "ready"
        assert "## 规则复盘" in report


def test_v3_test_env_full_access_allows_side_effect_cases_without_human_review() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "source"
        source_dir.mkdir()
        page_entries_path = source_dir / "page_entries.json"
        feature_points_path = source_dir / "feature_points.json"
        page_entries_path.write_text(
            json.dumps(
                [
                    {
                        "page_entry_id": "page_records",
                        "name": "记录管理",
                        "url": "https://example.test/records",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        feature_points_path.write_text(
            json.dumps(
                [
                    {
                        "feature_point_id": "feature_submit",
                        "page_entry_id": "page_records",
                        "name": "提交记录",
                        "feature_type": "提交",
                    },
                    {
                        "feature_point_id": "feature_delete",
                        "page_entry_id": "page_records",
                        "name": "删除记录",
                        "feature_type": "删除",
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        async def fake_discovery_provider() -> dict[str, str]:
            return {
                "status": "completed",
                "page_entries_path": str(page_entries_path),
                "feature_points_path": str(feature_points_path),
            }

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="测试环境系统",
                    start_url="https://example.test/records",
                    artifact_root=root / "runs",
                    run_id="full_access_contract",
                    use_live_discovery=True,
                    safety_policy="test_env_full_access",
                    allowed_side_effect_actions=("submit", "delete"),
                ),
                discovery_provider=fake_discovery_provider,
            )
        )

        run_dir = Path(result["run_dir"])
        cases = _read_json(run_dir / "cases.json")["cases"]
        execution_results = _read_json(run_dir / "execution_results.json")["results"]
        human_tasks = _read_json(run_dir / "human_tasks.json")
        next_round_plan = _read_json(run_dir / "next_round_plan.json")

        assert [case["case_type"] for case in cases] == ["submit", "delete"]
        assert all(case["auto_allowed"] is True for case in cases)
        assert {case["policy_evidence"]["safety_policy"] for case in cases} == {
            "test_env_full_access"
        }
        assert all(
            result["status"] == "authorized_by_policy_placeholder"
            for result in execution_results
        )
        assert human_tasks["open_task_count"] == 0
        assert next_round_plan["status"] == "ready"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
