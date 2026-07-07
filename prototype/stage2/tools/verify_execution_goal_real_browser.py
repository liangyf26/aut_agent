"""
One-off verification driver: run Stage E's real-browser runner against the
actual 苏源 (suyuan) system, connected via an already-logged-in Chrome CDP
session.

This is NOT part of the automated test suite (no network/credentials in
CI) — it's a manual verification spike per docs/第二阶段实施计划v4.md §7's
"端到端真跑" escape hatch (实施计划 §2.6). Run it once a human has:

  1. Started Chrome with a remote debugging port, e.g.:
       chrome.exe --remote-debugging-port=9222 --user-data-dir=<temp dir>
  2. Manually logged into https://www.zbsykj.com:19096/record/online with a
     test account in that Chrome window.
  3. Confirmed http://localhost:9222/json/version is reachable.

Usage:
    .venv\\Scripts\\python.exe -m prototype.stage2.tools.verify_execution_goal_real_browser \\
        --cdp-url http://localhost:9222

Test cases are hand-built from the SAME real locators already proven safe by
the existing connected-template pipeline (templates/suyuan_online_query_reset,
templates/suyuan_online_detail_view) — this script does not invent new
selectors against the production page.

Safety: the entry_confirmation case only checks visibility of the "详情"
button; it does NOT open the detail drawer or click "去支付". The
executable cases only fill/search/reset a date-range filter, which the
existing templates already proved does not submit any data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from playwright.async_api import Browser, Page, async_playwright

from prototype.stage2.app.execution_goal.execution_runner import EXECUTION_MODE_FIXTURE_SIMULATED
from prototype.stage2.app.execution_goal.orchestrator import ExecutionGoalOrchestrator
from prototype.stage2.app.execution_goal.real_browser_runner import execute_test_case_with_playwright

TARGET_URL = "https://www.zbsykj.com:19096/record/online"

TEST_CASES: list[dict] = [
    {
        "test_case_id": "tc_verify_query_reset",
        "feature_id": "page_online_record_feat_query_reset",
        "page_id": "page_online_record",
        "type": "executable",
        "risk_level": "low",
        "steps": [
            {"step": 1, "action": "fill", "target": "input[placeholder='开始日期']", "value": "2026-06-01"},
            {"step": 2, "action": "fill", "target": "input[placeholder='结束日期']", "value": "2026-06-30"},
            {"step": 3, "action": "click", "target": "button:has-text('搜索')"},
            {"step": 4, "action": "click", "target": "button:has-text('重置')"},
            {"step": 5, "action": "verify", "target": "input[placeholder='开始日期']", "expected": ""},
            {"step": 6, "action": "verify", "target": "input[placeholder='结束日期']", "expected": ""},
        ],
        "expected_result": "查询后重置成功，起止日期均已清空",
    },
    {
        "test_case_id": "tc_verify_detail_entry",
        "feature_id": "page_online_record_feat_detail",
        "page_id": "page_online_record",
        "type": "entry_confirmation",
        "risk_level": "high",
        "metadata": {
            "element_locator": "button.el-button.el-button--text:has-text('详情')",
        },
    },
]


async def _resolve_target_page(browser: Browser, target_url: str) -> Page:
    pages: list[Page] = []
    for context in browser.contexts:
        pages.extend(context.pages)
    if not pages:
        raise RuntimeError("未发现可用页面，请先在已连接的 Chrome 中打开目标系统并登录")

    page = next((item for item in pages if target_url and target_url in item.url), pages[0])
    await page.bring_to_front()
    await page.wait_for_load_state("domcontentloaded")
    if target_url and target_url not in page.url:
        await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
    return page


async def run(cdp_url: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = output_dir / "screenshots"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        try:
            page = await _resolve_target_page(browser, TARGET_URL)

            async def runner(test_case: dict, *, goal_id: str | None, injected_failure: str | None):
                return await execute_test_case_with_playwright(
                    page,
                    test_case,
                    goal_id=goal_id,
                    screenshots_dir=screenshots_dir,
                    injected_failure=injected_failure,
                )

            orchestrator = ExecutionGoalOrchestrator(output_dir=output_dir, run_id="verify_real_browser_001")
            orchestrator.create_root_goal("Verify Stage E real-browser runner against 苏源 online-record page")
            orchestrator.load_test_cases_from_list(TEST_CASES)

            outcomes = await orchestrator.execute_all_async(runner=runner)

            orchestrator.export_execution_results()
            orchestrator.export_action_log()
            orchestrator.export_network_events()
            orchestrator.export_screenshots_index()
            orchestrator.export_human_tasks()
            orchestrator.export_human_takeover()
            orchestrator.export_run_report()

            summary = orchestrator.get_summary()
            summary["any_fixture_simulated"] = any(
                o.execution_mode == EXECUTION_MODE_FIXTURE_SIMULATED for o in outcomes
            )
            summary["outcomes"] = [o.to_dict() for o in outcomes]
            return summary
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument(
        "--output-dir",
        default=str(Path(tempfile.gettempdir()) / "aut_agent_stage_e_real_browser_verify"),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    summary = asyncio.run(run(args.cdp_url, output_dir))

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n产物目录: {output_dir}")


if __name__ == "__main__":
    main()
