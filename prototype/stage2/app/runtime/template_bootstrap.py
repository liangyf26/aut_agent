from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BOOTSTRAP_VERSION = "0.2.0-draft"
DEFAULT_SCENARIO_KIND = "navigation"

SCENARIO_FEATURE_TYPES = {
    "generic": "导航",
    "navigation": "导航",
    "query": "查询",
    "detail": "查看",
    "create": "新增",
    "edit": "编辑",
}


@dataclass(frozen=True)
class BootstrapTemplateResult:
    template_name: str
    template_dir: Path
    page_name: str
    feature_name: str
    feature_type: str
    scenario_kind: str
    files: dict[str, Path]


def bootstrap_template_bundle(
    template_root: Path,
    *,
    template_name: str,
    page_url: str,
    page_name: str = "",
    feature_name: str = "",
    feature_type: str = "",
    scenario_kind: str = DEFAULT_SCENARIO_KIND,
    overwrite: bool = False,
) -> BootstrapTemplateResult:
    normalized_template_name = str(template_name or "").strip()
    normalized_page_url = str(page_url or "").strip()
    normalized_scenario_kind = str(scenario_kind or DEFAULT_SCENARIO_KIND).strip().lower()
    if not normalized_template_name:
        raise ValueError("template_name 不能为空。")
    if not normalized_page_url:
        raise ValueError("page_url 不能为空。")
    if normalized_scenario_kind not in SCENARIO_FEATURE_TYPES:
        supported = ", ".join(sorted(SCENARIO_FEATURE_TYPES.keys()))
        raise ValueError(f"不支持的 scenario_kind：{scenario_kind}。可选值：{supported}")

    resolved_page_name = str(page_name or "").strip() or _humanize_template_name(normalized_template_name)
    resolved_feature_type = str(feature_type or "").strip() or SCENARIO_FEATURE_TYPES[normalized_scenario_kind]
    resolved_feature_name = str(feature_name or "").strip() or _default_feature_name(
        resolved_page_name,
        resolved_feature_type,
        normalized_scenario_kind,
    )

    template_dir = template_root / normalized_template_name
    if template_dir.exists() and not overwrite:
        raise FileExistsError(
            f"模板目录已存在：{template_dir}。如需覆盖，请使用 bootstrap_overwrite=True。"
        )
    template_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now().isoformat(timespec="seconds")
    files = {
        "template": template_dir / "template.json",
        "baseline": template_dir / "baseline.json",
        "data_schema": template_dir / "data_schema.json",
        "locator_hints": template_dir / "locator_hints.json",
    }

    template_payload = _build_template_payload(
        template_name=normalized_template_name,
        page_name=resolved_page_name,
        page_url=normalized_page_url,
        feature_name=resolved_feature_name,
        feature_type=resolved_feature_type,
        scenario_kind=normalized_scenario_kind,
        created_at=created_at,
    )
    baseline_payload = _build_baseline_payload(
        page_name=resolved_page_name,
        page_url=normalized_page_url,
        feature_name=resolved_feature_name,
        feature_type=resolved_feature_type,
        scenario_kind=normalized_scenario_kind,
        created_at=created_at,
    )
    data_schema_payload = _build_data_schema_payload(
        scenario_kind=normalized_scenario_kind,
        created_at=created_at,
    )
    locator_hints_payload = _build_locator_hints_payload(page_name=resolved_page_name)

    _write_json(files["template"], template_payload)
    _write_json(files["baseline"], baseline_payload)
    _write_json(files["data_schema"], data_schema_payload)
    _write_json(files["locator_hints"], locator_hints_payload)

    return BootstrapTemplateResult(
        template_name=normalized_template_name,
        template_dir=template_dir,
        page_name=resolved_page_name,
        feature_name=resolved_feature_name,
        feature_type=resolved_feature_type,
        scenario_kind=normalized_scenario_kind,
        files=files,
    )


def _build_template_payload(
    *,
    template_name: str,
    page_name: str,
    page_url: str,
    feature_name: str,
    feature_type: str,
    scenario_kind: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "template_name": template_name,
        "version": BOOTSTRAP_VERSION,
        "page_entry": {
            "name": page_name,
            "url": page_url,
        },
        "feature_point": {
            "name": feature_name,
            "type": feature_type,
        },
        "execution_path": f"bootstrap_{scenario_kind}",
        "steps": [
            {
                "id": "open_bootstrap_page",
                "kind": "navigation",
                "action": "navigate_to_url",
                "args": {
                    "url_ref": "page_entry.url",
                    "wait_ms": 1500,
                },
            },
            {
                "id": "capture_bootstrap_landing",
                "kind": "artifact",
                "action": "capture_named_screenshot",
                "args": {
                    "file_name": "bootstrap_landing.png",
                    "full_page": True,
                },
            },
        ],
        "success_rules": {
            "notes": [
                "Bootstrap scaffold does not declare executable success rules yet.",
                "As long as the page can be opened and the landing screenshot can be captured, the first smoke run is treated as reachable.",
            ]
        },
        "notes": [
            "This scaffold is intentionally minimal. Replace the placeholder steps with real interaction steps after live discovery or human recording review.",
            "Recommended next actions: run --routing-summary, then --live-discovery, then revise template.json and locator_hints.json before deeper connected validation.",
        ],
        "bootstrap": {
            "status": "draft_needs_discovery_review",
            "scenario_kind": scenario_kind,
            "created_at": created_at,
        },
    }


def _build_baseline_payload(
    *,
    page_name: str,
    page_url: str,
    feature_name: str,
    feature_type: str,
    scenario_kind: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "page_entry": {
            "name": page_name,
            "url": page_url,
        },
        "bootstrap": {
            "status": "draft_needs_manual_refinement",
            "scenario_kind": scenario_kind,
            "feature_name": feature_name,
            "feature_type": feature_type,
            "created_at": created_at,
        },
    }


def _build_data_schema_payload(*, scenario_kind: str, created_at: str) -> dict[str, object]:
    return {
        "schema_version": BOOTSTRAP_VERSION,
        "strategy": "bootstrap_placeholder",
        "field_rules": {},
        "notes": [
            "No data-generation rules are declared in the bootstrap scaffold.",
            "Add field rules only after the first discovery or recording round confirms which inputs are actually needed.",
            f"bootstrap_scenario={scenario_kind}",
            f"created_at={created_at}",
        ],
    }


def _build_locator_hints_payload(*, page_name: str) -> dict[str, object]:
    page_keywords = [page_name] if page_name else []
    return {
        "bootstrap_hints": {
            "page_keywords": page_keywords,
            "candidate_button_texts": [],
            "candidate_field_labels": [],
            "candidate_dialog_titles": [],
            "notes": [
                "Keep this file minimal in round 1.",
                "Use live discovery artifacts or candidate_template_review.json to add stable locators in round 2.",
            ],
        }
    }


def _default_feature_name(page_name: str, feature_type: str, scenario_kind: str) -> str:
    if scenario_kind == "query":
        return f"{page_name}初始查询草稿"
    if scenario_kind == "detail":
        return f"{page_name}详情查看草稿"
    if scenario_kind == "create":
        return f"{page_name}新增流程草稿"
    if scenario_kind == "edit":
        return f"{page_name}编辑流程草稿"
    if feature_type == "导航":
        return f"{page_name}页面可达性检查"
    return f"{page_name}{feature_type}草稿"


def _humanize_template_name(template_name: str) -> str:
    parts = [part for part in template_name.replace("-", "_").split("_") if part]
    if not parts:
        return "新系统页面"
    return " ".join(part.capitalize() for part in parts)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
