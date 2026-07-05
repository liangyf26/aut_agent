"""
One-off verification driver: run Stage F's real-browser cross-system
validation against BOTH real systems — 追本溯源 (suyuan, CDP :9222) and
订场系统 (booking, CDP :9333) — and print compare_failures()/
review_promotions()/get_summary() output.

This is NOT part of the automated test suite (no network/credentials in
CI) — run once by a human with both Chrome CDP sessions already live:

  1. 苏源: https://www.zbsykj.com:19096/record/online, CDP :9222, logged in.
  2. 订场系统: https://wzdc.chaosen.ltd:19201/index, CDP :9333, logged in.

Safety: this script performs ONLY real, read-only menu discovery on both
systems (bounded nav-expansion clicks already proven safe on 苏源 by Stage
B's existing production use; on 订场系统 — completely unexplored — this is
intentionally the LOWEST-risk real check available, no feature-level
interaction). Do not extend this script to click/fill/submit against
订场系统 without first proving specific locators safe via a dedicated
template, the same discipline menu_goal/page_goal/feature_goal already
follow for 苏源.

Usage:
    .venv\\Scripts\\python.exe -m prototype.stage2.tools.verify_cross_system_goal_real_browser
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from playwright.async_api import Browser, async_playwright

from prototype.stage2.app.cross_system_goal import CrossSystemGoalOrchestrator, SystemProfile

SYSTEMS: list[dict[str, str]] = [
    {
        "system_id": "suyuan",
        "system_name": "追本溯源",
        "cdp_url": "http://localhost:9222",
        "target_url": "https://www.zbsykj.com:19096/record/online",
    },
    {
        "system_id": "wzdc_booking",
        "system_name": "梧州市体育场订场管理后台",
        "cdp_url": "http://localhost:9333",
        "target_url": "https://wzdc.chaosen.ltd:19201/index",
    },
]


async def _resolve_target_page(browser: Browser, target_url: str):
    pages = []
    for context in browser.contexts:
        pages.extend(context.pages)
    if not pages:
        raise RuntimeError(f"未发现可用页面 (target={target_url})，请先在已连接的 Chrome 中打开并登录")
    page = next((item for item in pages if target_url and target_url in item.url), pages[0])
    await page.bring_to_front()
    await page.wait_for_load_state("domcontentloaded")
    if target_url and target_url not in page.url:
        await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
    return page


async def run(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    orchestrator = CrossSystemGoalOrchestrator(
        output_dir=output_dir, run_id="verify_cross_system_real_browser_001"
    )

    per_system: list[dict] = []
    for entry in SYSTEMS:
        system = SystemProfile(
            system_id=entry["system_id"],
            system_name=entry["system_name"],
            run_mode="real_browser",
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(entry["cdp_url"])
            try:
                page = await _resolve_target_page(browser, entry["target_url"])
                screenshots_dir = output_dir / system.system_id / "screenshots"
                record = await orchestrator.run_real_browser_validation(
                    system,
                    page,
                    screenshots_dir=screenshots_dir,
                    max_pages=5,
                )
            finally:
                await browser.close()
        per_system.append({"system_id": system.system_id, "goal_count": len(record.goals)})

    comparisons = [row.to_dict() for row in orchestrator.compare_failures()]
    reviews = [row.to_dict() for row in orchestrator.review_promotions()]
    summary = orchestrator.get_summary()

    orchestrator.export_all()

    return {
        "summary": summary,
        "per_system": per_system,
        "compare_failures": comparisons,
        "review_promotions": reviews,
        "output_dir": str(output_dir),
    }


def main() -> None:
    output_dir = Path(tempfile.gettempdir()) / "aut_agent_stage_f_real_browser_verify"
    result = asyncio.run(run(output_dir))

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n产物目录: {result['output_dir']}")


if __name__ == "__main__":
    main()
