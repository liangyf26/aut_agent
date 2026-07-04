"""
Local headless smoke test for the real-browser Stage B menu discovery driver.

Boots a static HTML fixture with a two-level nav menu (a collapsed parent
item that expands to reveal a child item on click) with Python's stdlib
http.server, drives it with a real headless Playwright Chromium page via
``menu_goal.real_browser_discovery.discover_menus_with_playwright``, and
asserts:

- both the parent and child menu are discovered as real goals via
  DiscoveryAdapter (not fabricated);
- parent lineage survives (child's goal has the parent's goal_id as
  parent_goal_id) — this is the same lineage this session's fixture-chain
  regression test guards for the pure-fixture path;
- a real screenshot file is produced.

No network access or credentials required — everything runs against
127.0.0.1. Independent of test_integration_menu_discovery_flow.py's
hand-written-data tests.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine
from prototype.stage2.app.menu_goal.discovery_adapter import DiscoveryAdapter
from prototype.stage2.app.menu_goal.real_browser_discovery import discover_menus_with_playwright

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")

_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><title>Stage B real-browser menu fixture</title></head>
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


def test_real_browser_menu_discovery_finds_parent_and_child_with_lineage(
    fixture_server, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(fixture_server, wait_until="domcontentloaded")

                engine = GoalLoopEngine(run_id="menu_real_browser_smoke")
                adapter = DiscoveryAdapter(engine)
                root_goal = engine.register_goal(goal_type="menu", goal_name="root")

                goal_ids, raw_entries = await discover_menus_with_playwright(
                    page,
                    adapter,
                    screenshots_dir=tmp_path,
                    parent_goal_id=root_goal.goal_id,
                )

                assert len(goal_ids) >= 2
                assert len(raw_entries) == len(goal_ids)
                assert all("is_leaf" in entry for entry in raw_entries)

                contexts = {gid: adapter.get_menu_context(gid) for gid in goal_ids}
                by_menu_id = {ctx["menu_id"]: (gid, ctx) for gid, ctx in contexts.items()}

                assert "menu_1" in by_menu_id or any(
                    "系统管理" in "".join(ctx["menu_path"]) for _, ctx in contexts.items()
                )

                parent_gid, parent_ctx = next(
                    (gid, ctx) for gid, ctx in contexts.items() if ctx["menu_path"] == ["系统管理"]
                )
                child_gid, child_ctx = next(
                    (gid, ctx)
                    for gid, ctx in contexts.items()
                    if ctx["menu_path"] == ["系统管理", "用户管理"]
                )
                assert engine.goals[child_gid].parent_goal_id == parent_gid

                assert any(tmp_path.glob("*.png")), "real screenshot must have been written"

                for gid in goal_ids:
                    assert engine.goals[gid].status == "succeeded"
            finally:
                await browser.close()

    asyncio.run(scenario())
