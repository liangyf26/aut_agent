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


def test_v3_real_browser_menu_discovery_writes_menu_artifacts_without_counting_cdp_targets() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        async def fake_real_browser_provider(
            config: V3RunConfig,
            run_dir: Path,
        ) -> dict[str, object]:
            return {
                "schema_version": "stage2_v3_run.v1",
                "status": "completed",
                "message": "menu discovery completed",
                "preflight_result": {
                    "schema_version": "stage2_v3_run.v1",
                    "ok": True,
                    "status": "completed",
                    "browser_target_count": 3,
                },
                "browser_targets": [
                    {"url": "https://example.test/index"},
                    {"url": "https://example.test/debug"},
                    {"url": "https://example.test/blank"},
                ],
                "menu_tree": {
                    "schema_version": "stage2_menu_tree.v1",
                    "status": "completed",
                    "root_count": 3,
                    "nodes": [
                        {
                            "menu_id": "menu_001",
                            "text": "业务办理",
                            "level": 1,
                            "is_leaf": False,
                            "expandable": True,
                            "status": "expanded",
                            "children": [
                                {
                                    "menu_id": "menu_002",
                                    "text": "线上备案申请",
                                    "level": 2,
                                    "parent_id": "menu_001",
                                    "is_leaf": True,
                                    "expandable": False,
                                    "status": "discovered",
                                    "route_hint": "/online/apply",
                                    "screenshot_refs": ["menu_001_after_expand"],
                                }
                            ],
                        },
                        {
                            "menu_id": "menu_003",
                            "text": "备案查询",
                            "level": 1,
                            "is_leaf": True,
                            "expandable": False,
                            "status": "discovered",
                        },
                        {
                            "menu_id": "menu_004",
                            "text": "系统管理",
                            "level": 1,
                            "is_leaf": False,
                            "expandable": True,
                            "status": "permission_blocked",
                            "failure_reason": "permission_denied",
                        },
                    ],
                },
                "menu_entries": [
                    {
                        "menu_id": "menu_001",
                        "text": "业务办理",
                        "level": 1,
                        "is_leaf": False,
                        "status": "expanded",
                    },
                    {
                        "menu_id": "menu_002",
                        "text": "线上备案申请",
                        "level": 2,
                        "parent_id": "menu_001",
                        "menu_path": ["业务办理", "线上备案申请"],
                        "is_leaf": True,
                        "status": "discovered",
                        "route_hint": "/online/apply",
                        "locator_candidates": [{"kind": "text", "value": "线上备案申请"}],
                        "screenshot_refs": ["menu_001_after_expand"],
                        "source": "playwright.menu_discovery",
                    },
                    {
                        "menu_id": "menu_003",
                        "text": "备案查询",
                        "level": 1,
                        "menu_path": ["备案查询"],
                        "is_leaf": True,
                        "status": "discovered",
                        "source": "playwright.menu_discovery",
                    },
                    {
                        "menu_id": "menu_004",
                        "text": "系统管理",
                        "level": 1,
                        "menu_path": ["系统管理"],
                        "is_leaf": False,
                        "status": "permission_blocked",
                        "failure_reason": "permission_denied",
                        "source": "playwright.menu_discovery",
                    },
                ],
                "menu_traversal_log": [
                    {
                        "event": "expand",
                        "menu_id": "menu_001",
                        "status": "success",
                        "screenshot_ref": "menu_001_after_expand",
                    },
                    {
                        "event": "expand",
                        "menu_id": "menu_004",
                        "status": "permission_blocked",
                        "failure_reason": "permission_denied",
                    },
                ],
                "screenshots_index": {
                    "schema_version": "stage2_v3_run.v1",
                    "screenshots": [
                        {
                            "screenshot_id": "menu_001_after_expand",
                            "relative_path": "screenshots/menu_001_after_expand.png",
                            "stage": "menu_discovery",
                        }
                    ],
                    "items": [],
                },
                "pages": [
                    {
                        "page_id": "raw_current_page",
                        "name": "当前首页",
                        "url": "https://example.test/index",
                        "source": "real_browser_cdp",
                    }
                ],
                "features": [],
            }

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="追本溯源管理平台",
                    start_url="https://example.test/index",
                    cdp_url="http://localhost:9222",
                    execution_mode="real_browser",
                    artifact_root=root / "runs",
                    run_id="menu_contract",
                    max_pages=30,
                    metadata={"scope": "优先完成“线上备案申请”页面"},
                ),
                real_browser_provider=fake_real_browser_provider,
            )
        )

        run_dir = Path(result["run_dir"])
        menu_tree = _read_json(run_dir / "menu_tree.json")
        menu_entries = _read_json(run_dir / "menu_entries.json")["menu_entries"]
        page_entries = _read_json(run_dir / "page_entries.json")["page_entries"]
        traversal_log = (run_dir / "menu_traversal_log.jsonl").read_text(encoding="utf-8")
        round_analysis = _read_json(run_dir / "round_analysis.json")

        assert menu_tree["root_count"] == 3
        assert any(entry["text"] == "线上备案申请" for entry in menu_entries)
        assert "permission_denied" in traversal_log
        assert result["summary"]["browser_target_count"] == 3
        assert result["summary"]["menu_entry_count"] == 4
        assert result["summary"]["page_count"] == 2
        assert [page["name"] for page in page_entries] == ["线上备案申请", "备案查询"]
        assert all(page["source"] == "playwright.menu_discovery" for page in page_entries)
        assert round_analysis["coverage"]["browser_target_count"] == 3
        assert round_analysis["coverage"]["menu_entry_count"] == 4
        assert round_analysis["missing_scope_targets"] == []


def test_v3_second_round_menu_leaf_page_exploration_persists_attempts_and_count_explanation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        async def fake_real_browser_provider(
            config: V3RunConfig,
            run_dir: Path,
        ) -> dict[str, object]:
            return {
                "schema_version": "stage2_v3_run.v1",
                "status": "completed",
                "message": "second round page exploration completed",
                "browser_targets": [{"url": "https://example.test/index"}],
                "menu_tree": {
                    "schema_version": "stage2_menu_tree.v1",
                    "status": "completed",
                    "root_count": 1,
                    "leaf_count": 2,
                    "nodes": [],
                },
                "menu_entries": [
                    {
                        "menu_id": "menu_apply",
                        "text": "线上备案申请",
                        "menu_path": ["业务办理", "线上备案申请"],
                        "is_leaf": True,
                        "status": "discovered",
                        "route_hint": "/online/apply",
                        "source": "playwright.menu_discovery",
                    },
                    {
                        "menu_id": "menu_archive",
                        "text": "归档查询",
                        "menu_path": ["查询统计", "归档查询"],
                        "is_leaf": True,
                        "status": "discovered",
                        "route_hint": "/archive/query",
                        "source": "playwright.menu_discovery",
                    },
                ],
                "page_exploration_log": [
                    {
                        "event": "enter_menu_leaf",
                        "menu_id": "menu_apply",
                        "status": "reachable",
                        "page_entry_id": "page_apply",
                        "screenshot_ref": "page_apply_visible",
                    },
                    {
                        "event": "enter_menu_leaf",
                        "menu_id": "menu_archive",
                        "status": "failed",
                        "failure_reason": "navigation_timeout",
                    },
                ],
                "pages": [
                    {
                        "page_id": "page_apply",
                        "page_entry_id": "page_apply",
                        "menu_id": "menu_apply",
                        "name": "线上备案申请",
                        "url": "https://example.test/online/apply",
                        "menu_path": ["业务办理", "线上备案申请"],
                        "page_type": "form_entry",
                        "semantic_page_type": "form_entry",
                        "discovery_depth": 1,
                        "status": "reachable",
                        "source": "playwright.menu_page_exploration",
                        "screenshot_refs": ["page_apply_visible"],
                    },
                    {
                        "page_id": "page_archive",
                        "page_entry_id": "page_archive",
                        "menu_id": "menu_archive",
                        "name": "归档查询",
                        "url": "https://example.test/archive/query",
                        "menu_path": ["查询统计", "归档查询"],
                        "page_type": "query_list",
                        "semantic_page_type": "query_list",
                        "discovery_depth": 1,
                        "status": "unreachable",
                        "source": "playwright.menu_page_exploration",
                        "screenshot_refs": [],
                        "failure_reason": "navigation_timeout",
                    },
                ],
                "features": [
                    {
                        "feature_id": "feature_submit",
                        "feature_point_id": "feature_submit",
                        "page_id": "page_apply",
                        "page_entry_id": "page_apply",
                        "name": "提交",
                        "feature_type": "submit",
                        "risk_level": "high",
                        "source": "playwright.light_interaction",
                        "confidence": 0.91,
                    }
                ],
                "screenshots_index": {
                    "schema_version": "stage2_v3_run.v1",
                    "screenshots": [
                        {
                            "screenshot_id": "page_apply_visible",
                            "relative_path": "screenshots/page_apply_visible.png",
                            "stage": "page_exploration",
                        }
                    ],
                    "items": [],
                },
            }

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="追本溯源管理平台",
                    start_url="https://example.test/index",
                    cdp_url="http://localhost:9222",
                    execution_mode="real_browser",
                    artifact_root=root / "runs",
                    run_id="page_exploration",
                    max_pages=30,
                ),
                real_browser_provider=fake_real_browser_provider,
            )
        )

        run_dir = Path(result["run_dir"])
        page_entries = _read_json(run_dir / "page_entries.json")["page_entries"]
        feature_points = _read_json(run_dir / "feature_points.json")["feature_points"]
        round_analysis = _read_json(run_dir / "round_analysis.json")
        exploration_log = (run_dir / "page_exploration_log.jsonl").read_text(encoding="utf-8")

        assert [page["status"] for page in page_entries] == ["reachable", "unreachable"]
        assert page_entries[0]["source"] == "playwright.menu_page_exploration"
        assert page_entries[0]["page_type"] == "form_entry"
        assert page_entries[1]["failure_reason"] == "navigation_timeout"
        assert "menu_archive" in exploration_log
        assert feature_points[0]["auto_verifiable"] is True
        assert feature_points[0]["verification_strategy"] == "side_effect_policy_gate"
        assert feature_points[0]["review_status"] == "pending"
        assert round_analysis["coverage"]["menu_leaf_count"] == 2
        assert round_analysis["coverage"]["page_count"] == 2
        assert round_analysis["coverage"]["feature_count"] == 1
        assert round_analysis["count_explanation"]["menu_leaf_vs_page_entries"] == "2 menu leaves attempted; 2 page entries recorded."
        assert round_analysis["count_explanation"]["browser_targets"] == "1 browser targets are diagnostic CDP targets, not discovered business pages."


def test_v3_target_tracking_links_found_second_level_menu_target() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        async def fake_real_browser_provider(
            config: V3RunConfig,
            run_dir: Path,
        ) -> dict[str, object]:
            return {
                "schema_version": "stage2_v3_run.v1",
                "status": "completed",
                "preflight_result": {"ok": True, "status": "completed"},
                "menu_entries": [
                    {
                        "menu_id": "menu_001",
                        "text": "业务办理",
                        "level": 1,
                        "is_leaf": False,
                        "status": "expanded",
                        "source": "playwright.menu_discovery",
                    },
                    {
                        "menu_id": "menu_002",
                        "text": "线上备案申请",
                        "level": 2,
                        "parent_id": "menu_001",
                        "menu_path": ["业务办理", "线上备案申请"],
                        "is_leaf": True,
                        "status": "discovered",
                        "route_hint": "/online/apply",
                        "source": "playwright.menu_discovery",
                        "screenshot_refs": ["menu_001_after_expand"],
                    },
                ],
                "menu_tree": {"schema_version": "stage2_menu_tree.v1", "root_count": 1, "nodes": []},
                "menu_traversal_log": [],
                "screenshots_index": {"schema_version": "stage2_v3_run.v1", "screenshots": []},
                "pages": [],
                "features": [],
            }

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="追本溯源管理平台",
                    start_url="https://example.test/index",
                    cdp_url="http://localhost:9222",
                    execution_mode="real_browser",
                    artifact_root=root / "runs",
                    run_id="target_found",
                    metadata={"prioritized_targets": ["线上备案申请"]},
                ),
                real_browser_provider=fake_real_browser_provider,
            )
        )

        run_dir = Path(result["run_dir"])
        round_analysis = _read_json(run_dir / "round_analysis.json")
        next_round_plan = _read_json(run_dir / "next_round_plan.json")

        assert round_analysis["missing_scope_targets"] == []
        assert round_analysis["target_tracking"][0]["target"] == "线上备案申请"
        assert round_analysis["target_tracking"][0]["status"] == "found"
        assert round_analysis["target_tracking"][0]["matched_menu_entry_ids"] == ["menu_002"]
        assert round_analysis["target_tracking"][0]["evidence_quality"] == "high"
        assert next_round_plan["target_search_goals"] == []


def test_v3_target_tracking_carries_unfound_target_forward() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="追本溯源管理平台",
                    start_url="https://example.test/index",
                    artifact_root=root / "runs",
                    run_id="target_missing",
                    max_pages=1,
                    metadata={"prioritized_targets": ["线上备案申请"]},
                )
            )
        )

        run_dir = Path(result["run_dir"])
        round_analysis = _read_json(run_dir / "round_analysis.json")
        next_round_plan = _read_json(run_dir / "next_round_plan.json")

        assert round_analysis["missing_scope_targets"] == ["线上备案申请"]
        assert round_analysis["target_tracking"][0]["status"] == "missed"
        assert round_analysis["target_tracking"][0]["missed_reason"] == "not_found_in_menu_or_page_artifacts"
        assert round_analysis["target_tracking"][0]["evidence_quality"] == "low"
        assert next_round_plan["target_search_goals"] == ["线上备案申请"]


def test_v3_target_tracking_respects_user_waived_target() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        result = asyncio.run(
            run_v3_assessment(
                V3RunConfig(
                    target_name="追本溯源管理平台",
                    start_url="https://example.test/index",
                    artifact_root=root / "runs",
                    run_id="target_waived",
                    max_pages=1,
                    metadata={
                        "prioritized_targets": ["线上备案申请"],
                        "waived_targets": ["线上备案申请"],
                    },
                )
            )
        )

        run_dir = Path(result["run_dir"])
        round_analysis = _read_json(run_dir / "round_analysis.json")
        next_round_plan = _read_json(run_dir / "next_round_plan.json")

        assert round_analysis["missing_scope_targets"] == []
        assert round_analysis["target_tracking"][0]["status"] == "waived"
        assert round_analysis["target_tracking"][0]["waived"] is True
        assert next_round_plan["target_search_goals"] == []


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
