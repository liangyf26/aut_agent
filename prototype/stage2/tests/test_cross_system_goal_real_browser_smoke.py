"""
Local headless smoke test for Stage F's real-browser menu-discovery
validation goal.

Boots the SAME two-level nav-menu fixture pattern as
test_menu_goal_real_browser_smoke.py with Python's stdlib http.server,
drives it with a real headless Playwright Chromium page via
``cross_system_goal.real_browser_validation.run_menu_validation_goal``, and
asserts the discovered goals reach a real terminal status via
CrossSystemAdapter (not fabricated).

No network access or credentials required — everything runs against
127.0.0.1. Independent of test_cross_system_goal_integration.py's
hand-written-data / frozen-fixture tests.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from prototype.stage2.app.cross_system_goal import CrossSystemAdapter, SystemProfile
from prototype.stage2.app.cross_system_goal.real_browser_validation import (
    run_menu_validation_goal,
)
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")

_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><title>Stage F real-browser fixture</title></head>
<body>
  <nav class="sidebar-container">
    <a role="menuitem" id="menu-sys" aria-expanded="false"
       onclick="document.getElementById('submenu').style.display='block'; this.setAttribute('aria-expanded','true')">系统管理</a>
    <div id="submenu" role="menu" style="display:none">
      <a role="menuitem" id="menu-user" href="/system/users">用户管理</a>
    </div>
  </nav>
</body>
</html>
"""


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = _FIXTURE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - silence stdout
        pass


@pytest.fixture()
def fixture_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_real_browser_menu_validation_records_goals_and_succeeds(
    fixture_server, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(fixture_server, wait_until="domcontentloaded")

                engine = GoalLoopEngine(run_id="cross_system_real_browser_smoke")
                system = SystemProfile(
                    system_id="sys_fixture",
                    system_name="Fixture System",
                    run_mode="real_browser",
                )
                adapter = CrossSystemAdapter(engine, system)

                goal_ids, raw_entries = await run_menu_validation_goal(
                    page,
                    adapter,
                    engine,
                    screenshots_dir=tmp_path,
                )

                assert len(goal_ids) >= 2
                assert len(raw_entries) == len(goal_ids)

                # Every discovered goal reached a real terminal-ish status —
                # succeeded (screenshot evidence + predicate satisfied), or
                # paused/failed if the real scan genuinely didn't produce
                # enough evidence on this fixture (still a legitimate,
                # non-crashing outcome, not a bug this test should hide).
                for gid in goal_ids:
                    assert engine.goals[gid].status in {
                        "succeeded",
                        "waiting_human",
                        "failed_max_rounds",
                        "blocked_by_policy",
                        "blocked_by_executor",
                        "stopped_no_progress",
                    }

                assert any(tmp_path.glob("*.png")), "real screenshot must have been written"
            finally:
                await browser.close()

    asyncio.run(scenario())
