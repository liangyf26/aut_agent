from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse


V3_SCHEMA_VERSION = "stage2_v3_run.v1"
DiscoveryProvider = Callable[[], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class V3RunConfig:
    target_name: str = "第二阶段演示系统"
    start_url: str = ""
    artifact_root: Path = Path("artifacts/stage2/v3_runs")
    run_id: str = ""
    cdp_url: str = ""
    model_name: str | None = None
    use_live_discovery: bool = False
    max_pages: int = 5
    max_features_per_page: int = 6
    risk_whitelist: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


async def run_v3_assessment(
    config: V3RunConfig,
    *,
    discovery_provider: DiscoveryProvider | None = None,
) -> dict[str, Any]:
    """Run the v3 minimum closed loop and persist all contract artifacts."""

    writer = V3RunArtifactWriter(config)
    writer.update_state("initializing", "running", "v3 run initialized")

    discovery_payload: dict[str, Any] = {}
    if config.use_live_discovery and discovery_provider is not None:
        writer.update_state("discovery", "running", "running live discovery provider")
        try:
            discovery_payload = await discovery_provider()
            writer.write_json("source_discovery.json", discovery_payload)
        except Exception as exc:  # keep the run inspectable even when live discovery is unavailable
            discovery_payload = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "fallback": "demo_safe_discovery",
            }
            writer.write_json("source_discovery.json", discovery_payload)

    pages = _build_pages(config, discovery_payload)
    writer.write_json("pages.json", {"schema_version": V3_SCHEMA_VERSION, "pages": pages})
    writer.update_state("feature_identification", "running", f"identified {len(pages)} page entries")

    features = _build_features(config, discovery_payload, pages)
    writer.write_json("features.json", {"schema_version": V3_SCHEMA_VERSION, "features": features})
    writer.update_state("case_generation", "running", f"identified {len(features)} feature points")

    cases = _build_cases(features, risk_whitelist=set(config.risk_whitelist))
    writer.write_json("cases.json", {"schema_version": V3_SCHEMA_VERSION, "cases": cases})
    writer.update_state("safe_execution", "running", f"generated {len(cases)} executable cases")

    execution_results = _execute_cases(cases, config=config)
    writer.write_json(
        "execution_results.json",
        {"schema_version": V3_SCHEMA_VERSION, "results": execution_results},
    )

    round_analysis = _build_round_analysis(
        config=config,
        discovery_payload=discovery_payload,
        pages=pages,
        features=features,
        cases=cases,
        execution_results=execution_results,
    )
    writer.write_json("round_analysis.json", round_analysis)
    writer.update_state("ai_review", "running", "round analysis generated")

    human_tasks = _build_human_tasks(
        config=config,
        pages=pages,
        features=features,
        cases=cases,
        execution_results=execution_results,
        round_analysis=round_analysis,
    )
    writer.write_json("human_tasks.json", human_tasks)

    next_round_plan = _build_next_round_plan(
        config=config,
        round_analysis=round_analysis,
        human_tasks=human_tasks,
    )
    writer.write_json("next_round_plan.json", next_round_plan)

    report = _render_report(
        config=config,
        run_id=writer.run_id,
        run_dir=writer.run_dir,
        pages=pages,
        features=features,
        cases=cases,
        execution_results=execution_results,
        round_analysis=round_analysis,
        human_tasks=human_tasks,
        next_round_plan=next_round_plan,
    )
    report_path = writer.write_text("report.md", report)

    final_status = "waiting_human" if human_tasks["open_task_count"] else "completed"
    writer.update_state(
        "completed",
        final_status,
        "v3 run completed; human review required"
        if human_tasks["open_task_count"]
        else "v3 run completed",
    )

    return {
        "schema_version": V3_SCHEMA_VERSION,
        "run_id": writer.run_id,
        "run_dir": str(writer.run_dir),
        "status": final_status,
        "artifact_paths": {
            name: str(writer.run_dir / name)
            for name in (
                "run_state.json",
                "pages.json",
                "features.json",
                "cases.json",
                "execution_results.json",
                "round_analysis.json",
                "next_round_plan.json",
                "human_tasks.json",
                "report.md",
            )
        },
        "summary": {
            "target_name": config.target_name,
            "page_count": len(pages),
            "feature_count": len(features),
            "case_count": len(cases),
            "executed_count": sum(
                1 for item in execution_results if item["status"] == "passed_safe_placeholder"
            ),
            "blocked_count": sum(1 for item in execution_results if item["status"].startswith("blocked")),
            "open_human_task_count": human_tasks["open_task_count"],
            "next_round_status": next_round_plan["status"],
        },
        "report_path": str(report_path),
    }


class V3RunArtifactWriter:
    def __init__(self, config: V3RunConfig) -> None:
        self.started_at = _now()
        self.run_id = config.run_id.strip() or _default_run_id(config.target_name)
        self.run_dir = _unique_run_dir(config.artifact_root, self.run_id)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._config = config
        self._state: dict[str, Any] = {
            "schema_version": V3_SCHEMA_VERSION,
            "run_id": self.run_id,
            "target_name": config.target_name,
            "start_url": config.start_url,
            "cdp_url": config.cdp_url,
            "model_name": config.model_name,
            "use_live_discovery": config.use_live_discovery,
            "started_at": self.started_at,
            "updated_at": self.started_at,
            "current_phase": "initializing",
            "overall_status": "running",
            "latest_message": "",
            "metadata": dict(config.metadata),
            "artifact_contract": [
                "run_state.json",
                "pages.json",
                "features.json",
                "cases.json",
                "execution_results.json",
                "round_analysis.json",
                "next_round_plan.json",
                "human_tasks.json",
                "report.md",
            ],
        }

    def write_json(self, relative_path: str, payload: Any) -> Path:
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_text(self, relative_path: str, text: str) -> Path:
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def update_state(self, phase: str, status: str, message: str) -> None:
        self._state.update(
            {
                "current_phase": phase,
                "overall_status": status,
                "latest_message": message,
                "updated_at": _now(),
            }
        )
        self.write_json("run_state.json", self._state)


def _build_pages(config: V3RunConfig, discovery_payload: dict[str, Any]) -> list[dict[str, Any]]:
    discovered = _load_discovery_list(discovery_payload, "page_entries", "page_entries_path")
    pages: list[dict[str, Any]] = []
    for index, item in enumerate(discovered[: max(1, config.max_pages)], start=1):
        if not isinstance(item, dict):
            continue
        page_id = _text(item.get("page_entry_id")) or f"page_{index:03d}"
        pages.append(
            {
                "page_id": page_id,
                "name": _text(item.get("name")) or f"页面入口 {index}",
                "url": _text(item.get("url")) or config.start_url,
                "source": _text(item.get("source")) or "live_discovery",
                "confidence": _text(item.get("confidence")) or "discovered",
                "semantic_page_type": _text(item.get("semantic_page_type"))
                or _infer_page_type(_text(item.get("name")), _text(item.get("url"))),
                "priority": "normal",
                "requires_human_review": False,
                "evidence": _mapping(item.get("evidence")),
            }
        )
    if pages:
        return pages
    return _demo_pages(config)


def _build_features(
    config: V3RunConfig,
    discovery_payload: dict[str, Any],
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    discovered = _load_discovery_list(discovery_payload, "feature_points", "feature_points_path")
    page_ids = {page["page_id"] for page in pages}
    features: list[dict[str, Any]] = []
    for index, item in enumerate(discovered, start=1):
        if not isinstance(item, dict):
            continue
        page_id = _text(item.get("page_entry_id")) or (pages[0]["page_id"] if pages else "page_001")
        if page_id not in page_ids and pages:
            page_id = pages[0]["page_id"]
        feature_type = _normalize_feature_type(
            _text(item.get("feature_type")) or _text(item.get("action_type"))
        )
        features.append(
            {
                "feature_id": _text(item.get("feature_point_id")) or f"feature_{index:03d}",
                "page_id": page_id,
                "name": _text(item.get("name")) or f"功能点 {index}",
                "feature_type": feature_type,
                "source": _text(item.get("source")) or "live_discovery",
                "confidence": _text(item.get("confidence")) or "discovered",
                "risk_level": _risk_level(feature_type),
                "requires_test_data": feature_type in {"create", "edit", "submit"},
                "evidence": _mapping(item.get("evidence")),
            }
        )
    if features:
        return _limit_features_per_page(features, config.max_features_per_page)
    return _demo_features(pages, config.max_features_per_page)


def _build_cases(features: list[dict[str, Any]], *, risk_whitelist: set[str]) -> list[dict[str, Any]]:
    cases = []
    for index, feature in enumerate(features, start=1):
        feature_type = _text(feature.get("feature_type")) or "navigation"
        risk_level = _text(feature.get("risk_level")) or _risk_level(feature_type)
        auto_allowed = risk_level == "low" or feature_type in risk_whitelist
        cases.append(
            {
                "case_id": f"case_{index:03d}",
                "feature_id": feature["feature_id"],
                "page_id": feature["page_id"],
                "name": f"{feature['name']}基础路径验证",
                "case_type": feature_type,
                "risk_level": risk_level,
                "auto_allowed": auto_allowed,
                "steps": _case_steps(feature_type),
                "expected_result": _expected_result(feature_type),
                "data_requirements": _data_requirements(feature_type),
            }
        )
    return cases


def _execute_cases(cases: list[dict[str, Any]], *, config: V3RunConfig) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        if not case["auto_allowed"]:
            results.append(
                {
                    "case_id": case["case_id"],
                    "feature_id": case["feature_id"],
                    "status": "blocked_by_policy",
                    "execution_mode": "safe_placeholder",
                    "started_at": _now(),
                    "finished_at": _now(),
                    "evidence": [],
                    "message": "高风险或需要业务数据的动作未自动执行，已转成人工任务。",
                }
            )
            continue
        results.append(
            {
                "case_id": case["case_id"],
                "feature_id": case["feature_id"],
                "status": "passed_safe_placeholder",
                "execution_mode": "safe_placeholder",
                "started_at": _now(),
                "finished_at": _now(),
                "evidence": [],
                "message": _safe_execution_message(config),
            }
        )
    return results


def _build_round_analysis(
    *,
    config: V3RunConfig,
    discovery_payload: dict[str, Any],
    pages: list[dict[str, Any]],
    features: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
) -> dict[str, Any]:
    blocked = [item for item in execution_results if item["status"].startswith("blocked")]
    passed = [item for item in execution_results if item["status"] == "passed_safe_placeholder"]
    source_status = _text(discovery_payload.get("status")) if discovery_payload else "demo_safe"
    failure_clusters = []
    if blocked:
        failure_clusters.append(
            {
                "cluster_id": "blocked_by_policy",
                "title": "高风险或缺少数据的操作未自动执行",
                "severity": "medium",
                "case_ids": [item["case_id"] for item in blocked],
                "suggestion": "在运行中心用界面确认白名单、补充测试数据或录制人工样本。",
            }
        )
    if not config.use_live_discovery:
        failure_clusters.append(
            {
                "cluster_id": "demo_discovery_only",
                "title": "本轮使用 demo/safe 发现数据",
                "severity": "low",
                "case_ids": [],
                "suggestion": "连接已登录浏览器并启用 live discovery 后再扩大覆盖范围。",
            }
        )
    if source_status == "failed":
        failure_clusters.append(
            {
                "cluster_id": "live_discovery_failed",
                "title": "真实浏览器发现失败并回退到安全演示数据",
                "severity": "high",
                "case_ids": [],
                "suggestion": "检查 CDP 地址、登录状态和目标页面可达性。",
            }
        )
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "generated_at": _now(),
        "analysis_mode": "deterministic_ai_review_stub",
        "target_name": config.target_name,
        "coverage": {
            "page_count": len(pages),
            "feature_count": len(features),
            "case_count": len(cases),
            "passed_count": len(passed),
            "blocked_count": len(blocked),
        },
        "quality_judgement": _quality_judgement(pages, features, blocked),
        "failure_clusters": failure_clusters,
        "asset_learning": [
            {
                "asset_type": "feature_type_template",
                "status": "candidate",
                "description": "本轮功能点已被归一为导航、查询、详情、新增/编辑/提交等类型，可供运行中心继续审核。",
            }
        ],
        "limitations": [
            "当前 v3 最小闭环不执行真实提交、删除、导出等高风险动作。",
            "safe_placeholder 表示链路和产物契约已跑通，不等价于真实业务断言通过。",
        ],
    }


def _build_human_tasks(
    *,
    config: V3RunConfig,
    pages: list[dict[str, Any]],
    features: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    round_analysis: dict[str, Any],
) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    if not config.use_live_discovery:
        tasks.append(
            {
                "task_id": "human_task_connect_browser",
                "type": "login_handoff",
                "status": "open",
                "title": "连接已登录浏览器后运行真实发现",
                "ui_action": "在运行中心填写首页 URL 与 CDP 地址，点击开始发现。",
                "blocks_next_round": False,
            }
        )
    blocked_case_ids = [
        item["case_id"] for item in execution_results if item["status"] == "blocked_by_policy"
    ]
    if blocked_case_ids:
        tasks.append(
            {
                "task_id": "human_task_review_policy",
                "type": "risk_review",
                "status": "open",
                "title": "审核高风险动作与测试数据",
                "ui_action": "在运行中心勾选可自动执行白名单，或为新增/编辑/提交类动作补充测试数据。",
                "case_ids": blocked_case_ids,
                "blocks_next_round": True,
            }
        )
    low_confidence_pages = [page["page_id"] for page in pages if page.get("requires_human_review")]
    if low_confidence_pages:
        tasks.append(
            {
                "task_id": "human_task_prioritize_pages",
                "type": "page_priority",
                "status": "open",
                "title": "确认优先测试页面",
                "ui_action": "在运行中心页面清单中调整优先级并排除误识别入口。",
                "page_ids": low_confidence_pages,
                "blocks_next_round": False,
            }
        )
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "generated_at": _now(),
        "open_task_count": sum(1 for item in tasks if item["status"] == "open"),
        "tasks": tasks,
        "notes": [
            "人工介入任务必须通过运行中心界面处理，不能要求用户直接修改 JSON。",
            f"本轮候选页面 {len(pages)} 个，候选功能点 {len(features)} 个。",
            f"质量判断：{round_analysis['quality_judgement']}",
        ],
    }


def _build_next_round_plan(
    *,
    config: V3RunConfig,
    round_analysis: dict[str, Any],
    human_tasks: dict[str, Any],
) -> dict[str, Any]:
    blocking_tasks = [
        item for item in human_tasks["tasks"] if item.get("blocks_next_round") and item["status"] == "open"
    ]
    should_continue = not blocking_tasks
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "generated_at": _now(),
        "status": "blocked_waiting_human" if blocking_tasks else "ready",
        "should_start_next_round": should_continue,
        "target_stage": "safe_execution" if blocking_tasks else "live_discovery",
        "primary_reason": "存在需要界面确认的高风险动作或测试数据。"
        if blocking_tasks
        else "当前最小闭环已完成，可进入下一轮扩大页面覆盖。",
        "recommended_actions": [
            "扩大页面入口发现数量",
            "对查询、详情等低风险功能点执行真实 Playwright 验证",
            "把人工确认后的白名单和测试数据沉淀为项目级资产",
        ]
        if should_continue
        else [
            "在运行中心处理 human_tasks.json 中的阻塞任务",
            "完成后从当前 run 继续下一轮，不直接编辑内部 JSON 文件",
        ],
        "human_task_ids": [item["task_id"] for item in blocking_tasks],
        "analysis_refs": {
            "failure_cluster_count": len(round_analysis["failure_clusters"]),
            "quality_judgement": round_analysis["quality_judgement"],
            "target_name": config.target_name,
        },
    }


def _render_report(
    *,
    config: V3RunConfig,
    run_id: str,
    run_dir: Path,
    pages: list[dict[str, Any]],
    features: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    round_analysis: dict[str, Any],
    human_tasks: dict[str, Any],
    next_round_plan: dict[str, Any],
) -> str:
    passed = sum(1 for item in execution_results if item["status"] == "passed_safe_placeholder")
    blocked = sum(1 for item in execution_results if item["status"].startswith("blocked"))
    lines = [
        f"# 第二阶段 v3 运行报告 - {config.target_name}",
        "",
        f"- Run ID: `{run_id}`",
        f"- Run Dir: `{run_dir}`",
        f"- Start URL: `{config.start_url or 'demo/safe mode'}`",
        f"- Generated At: `{_now()}`",
        "",
        "## 总览",
        "",
        f"- 页面入口: {len(pages)}",
        f"- 功能点: {len(features)}",
        f"- 执行型用例: {len(cases)}",
        f"- 安全占位通过: {passed}",
        f"- 策略阻断: {blocked}",
        f"- 人工任务: {human_tasks['open_task_count']}",
        "",
        "## AI 复盘",
        "",
        f"- 质量判断: {round_analysis['quality_judgement']}",
        f"- 下一轮状态: {next_round_plan['status']}",
        f"- 下一轮原因: {next_round_plan['primary_reason']}",
        "",
        "## 失败簇 / 风险簇",
        "",
    ]
    for cluster in round_analysis["failure_clusters"]:
        lines.append(f"- {cluster['cluster_id']}: {cluster['title']}（{cluster['severity']}）")
    if not round_analysis["failure_clusters"]:
        lines.append("- 暂无失败簇。")
    lines.extend(["", "## 人工任务", ""])
    for task in human_tasks["tasks"]:
        lines.append(f"- {task['task_id']}: {task['title']} - {task['ui_action']}")
    if not human_tasks["tasks"]:
        lines.append("- 暂无人工任务。")
    lines.extend(
        [
            "",
            "## 产物",
            "",
            "- `run_state.json`",
            "- `pages.json`",
            "- `features.json`",
            "- `cases.json`",
            "- `execution_results.json`",
            "- `round_analysis.json`",
            "- `next_round_plan.json`",
            "- `human_tasks.json`",
            "- `report.md`",
            "",
            "> 注：`passed_safe_placeholder` 仅表示低风险最小执行占位已完成；真实业务正确性仍需后续接入 Playwright 确定性验证或人工确认。",
            "",
        ]
    )
    return "\n".join(lines)


def _demo_pages(config: V3RunConfig) -> list[dict[str, Any]]:
    parsed = urlparse(config.start_url)
    base_url = config.start_url or "https://demo.stage2.local/home"
    host = parsed.netloc or "demo.stage2.local"
    return [
        {
            "page_id": "page_dashboard",
            "name": f"{config.target_name}首页",
            "url": base_url,
            "source": "demo_safe_seed",
            "confidence": "seeded",
            "semantic_page_type": "dashboard",
            "priority": "high",
            "requires_human_review": False,
            "evidence": {"host": host},
        },
        {
            "page_id": "page_list",
            "name": "业务列表页",
            "url": f"{base_url.rstrip('/')}/list",
            "source": "demo_safe_seed",
            "confidence": "seeded",
            "semantic_page_type": "query_list",
            "priority": "high",
            "requires_human_review": False,
            "evidence": {"reason": "v3 demo covers query/list flow"},
        },
        {
            "page_id": "page_detail",
            "name": "业务详情页",
            "url": f"{base_url.rstrip('/')}/detail",
            "source": "demo_safe_seed",
            "confidence": "seeded",
            "semantic_page_type": "detail",
            "priority": "normal",
            "requires_human_review": False,
            "evidence": {"reason": "v3 demo covers read-only detail flow"},
        },
    ][: max(1, config.max_pages)]


def _demo_features(pages: list[dict[str, Any]], max_features_per_page: int) -> list[dict[str, Any]]:
    feature_specs = {
        "dashboard": [("打开首页", "navigation"), ("查看概览卡片", "view")],
        "query_list": [("查询列表", "query"), ("重置查询", "reset"), ("查看详情", "detail")],
        "detail": [("查看详情", "detail"), ("新增记录", "create")],
    }
    features: list[dict[str, Any]] = []
    for page in pages:
        specs = feature_specs.get(page["semantic_page_type"], [("打开页面", "navigation")])
        for name, feature_type in specs[: max(1, max_features_per_page)]:
            feature_id = f"feature_{len(features) + 1:03d}"
            features.append(
                {
                    "feature_id": feature_id,
                    "page_id": page["page_id"],
                    "name": name,
                    "feature_type": feature_type,
                    "source": "demo_safe_seed",
                    "confidence": "seeded",
                    "risk_level": _risk_level(feature_type),
                    "requires_test_data": feature_type in {"create", "edit", "submit"},
                    "evidence": {"page_name": page["name"]},
                }
            )
    return features


def _load_discovery_list(
    discovery_payload: dict[str, Any],
    inline_key: str,
    path_key: str,
) -> list[dict[str, Any]]:
    inline = discovery_payload.get(inline_key)
    if isinstance(inline, list):
        return [item for item in inline if isinstance(item, dict)]
    path_text = _text(discovery_payload.get(path_key))
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    nested = payload.get(inline_key)
    if isinstance(nested, list):
        return [item for item in nested if isinstance(item, dict)]
    return []


def _limit_features_per_page(
    features: list[dict[str, Any]],
    max_features_per_page: int,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    limited: list[dict[str, Any]] = []
    limit = max(1, max_features_per_page)
    for feature in features:
        page_id = feature["page_id"]
        counts[page_id] = counts.get(page_id, 0) + 1
        if counts[page_id] <= limit:
            limited.append(feature)
    return limited


def _normalize_feature_type(value: str) -> str:
    normalized = value.lower().strip()
    mapping = {
        "导航": "navigation",
        "查询": "query",
        "搜索": "query",
        "详情": "detail",
        "查看": "view",
        "新增": "create",
        "新建": "create",
        "编辑": "edit",
        "删除": "delete",
        "提交": "submit",
        "导出": "export",
        "重置": "reset",
    }
    return mapping.get(normalized, normalized or "action")


def _risk_level(feature_type: str) -> str:
    if feature_type in {"navigation", "view", "query", "detail", "reset"}:
        return "low"
    if feature_type in {"create", "edit", "submit", "export"}:
        return "medium"
    if feature_type in {"delete", "remove"}:
        return "high"
    return "medium"


def _case_steps(feature_type: str) -> list[dict[str, Any]]:
    steps_by_type = {
        "navigation": [
            {"action": "navigate", "target": "page_url"},
            {"action": "assert_loaded", "target": "page"},
        ],
        "view": [{"action": "assert_visible", "target": "primary_content"}],
        "query": [
            {"action": "fill_optional_filter", "target": "first_filter"},
            {"action": "click", "target": "query_button"},
            {"action": "assert_feedback", "target": "list_or_empty_state"},
        ],
        "reset": [
            {"action": "click", "target": "reset_button"},
            {"action": "assert_reset", "target": "filters"},
        ],
        "detail": [
            {"action": "open_first_record", "target": "list_row"},
            {"action": "assert_visible", "target": "detail_panel_or_page"},
        ],
        "create": [
            {"action": "open_create_form", "target": "create_button"},
            {"action": "fill_required_fields", "target": "ui_provided_test_data"},
            {"action": "dry_run_or_stop_before_submit", "target": "submit_button"},
        ],
    }
    return steps_by_type.get(feature_type, [{"action": "inspect", "target": "feature"}])


def _expected_result(feature_type: str) -> str:
    if feature_type in {"navigation", "view", "detail"}:
        return "页面或目标区域稳定可见，无明显前端错误。"
    if feature_type == "query":
        return "查询动作有明确反馈：列表刷新、空状态、提示或接口响应。"
    if feature_type == "reset":
        return "筛选条件恢复默认状态。"
    return "动作需要人工确认风险和测试数据后再执行。"


def _data_requirements(feature_type: str) -> list[str]:
    if feature_type in {"create", "edit", "submit"}:
        return ["测试数据模板", "提交风险白名单", "回滚或清理策略"]
    if feature_type == "query":
        return ["可选筛选条件"]
    return []


def _quality_judgement(
    pages: list[dict[str, Any]],
    features: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> str:
    if not pages or not features:
        return "needs_discovery"
    if blocked:
        return "partial_loop_waiting_human"
    return "minimum_loop_ready"


def _safe_execution_message(config: V3RunConfig) -> str:
    if config.use_live_discovery:
        return "已按 v3 最小闭环完成低风险安全占位执行；真实 Playwright 断言将在后续迭代接入。"
    return "已在 demo/safe 模式完成低风险占位执行，用于验证 v3 产物链路。"


def _infer_page_type(name: str, url: str) -> str:
    text = f"{name} {url}".lower()
    if any(keyword in text for keyword in ("query", "search", "list", "查询", "列表")):
        return "query_list"
    if any(keyword in text for keyword in ("detail", "详情")):
        return "detail"
    return "page"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _default_run_id(target_name: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slug(target_name or 'stage2_v3')}"


def _unique_run_dir(root: Path, run_id: str) -> Path:
    base = root / _slug(run_id)
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = root / f"{base.name}_{suffix:02d}"
        suffix += 1
    return candidate


def _slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    return safe or "stage2_v3_run"


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()
