"""
Local headless smoke test for the real-browser Stage C page discovery driver.

Boots a static HTTP fixture site with two pages — one with real content, one
genuinely blank — with Python's stdlib http.server, drives it with a real
headless Playwright Chromium page via
``page_goal.real_browser_discovery.discover_pages_with_playwright``, and
asserts:

- the content page is registered as a REACHABLE page goal with real
  screenshot evidence;
- the blank page is registered as a FAILED page goal with failure_class
  page_blank (not silently treated as reachable);
- parent lineage (menu_id -> parent_menu_id) is preserved from the real
  menu_entries list into the page context, same guarantee as the fixture
  chain regression test in test_integration_menu_discovery_flow.py.

No network access or credentials required — everything runs against
127.0.0.1. Independent of test_page_goal_integration.py's hand-written-data
tests.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine
from prototype.stage2.app.page_goal.page_adapter import PageAdapter
from prototype.stage2.app.page_goal.page_fixture_writer import write_page_fixture
from prototype.stage2.app.page_goal.real_browser_discovery import discover_pages_with_playwright

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")

_INDEX_HTML = """<!DOCTYPE html>
<html><head><title>首页</title></head><body><h1>首页</h1></body></html>
"""

_CONTENT_HTML = """<!DOCTYPE html>
<html><head><title>用户管理</title></head>
<body><h1>用户管理</h1><p>这里是用户列表内容，足够长以避免被判定为空白页。</p></body></html>
"""

_BLANK_HTML = "<!DOCTYPE html><html><head><title></title></head><body></body></html>"


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    _ROUTES = {
        "/": _INDEX_HTML,
        "/users": _CONTENT_HTML,
        "/blank": _BLANK_HTML,
    }

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = self._ROUTES.get(self.path, "").encode("utf-8")
        self.send_response(200 if body else 404)
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
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_real_browser_page_discovery_distinguishes_reachable_from_blank(
    fixture_server, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(f"{fixture_server}/", wait_until="domcontentloaded")

                # Real menu_entries shape (as menu_goal.real_browser_discovery would
                # produce from build_menu_discovery_artifacts) with two leaf entries.
                menu_entries = [
                    {
                        "menu_id": "menu_users",
                        "text": "用户管理",
                        "menu_path": ["系统管理", "用户管理"],
                        "is_leaf": True,
                        "status": "discovered",
                        "route_hint": f"{fixture_server}/users",
                        "locator_candidates": [{"kind": "css", "value": "#missing"}],
                    },
                    {
                        "menu_id": "menu_blank",
                        "text": "空白页",
                        "menu_path": ["空白页"],
                        "is_leaf": True,
                        "status": "discovered",
                        "route_hint": f"{fixture_server}/blank",
                        "locator_candidates": [{"kind": "css", "value": "#missing"}],
                    },
                ]

                engine = GoalLoopEngine(run_id="page_real_browser_smoke")
                adapter = PageAdapter(engine)
                root_goal = engine.register_goal(goal_type="page", goal_name="root")

                goal_ids = await discover_pages_with_playwright(
                    page,
                    adapter,
                    menu_entries,
                    screenshots_dir=tmp_path,
                    parent_goal_id=root_goal.goal_id,
                )

                assert len(goal_ids) == 2
                contexts = {gid: adapter.get_page_context(gid) for gid in goal_ids}
                # route_hint is set unconditionally at registration time (unlike
                # page_url/page_title, which record_page_success alone fills in) —
                # safe to key off it regardless of whether a page succeeded or failed.
                by_route_suffix = {
                    ctx["route_hint"].rsplit("/", 1)[-1]: (gid, ctx) for gid, ctx in contexts.items()
                }

                users_gid, users_ctx = by_route_suffix["users"]
                blank_gid, blank_ctx = by_route_suffix["blank"]

                assert engine.goals[users_gid].status == "succeeded"
                assert users_ctx["parent_menu_id"] == "menu_users"

                assert engine.goals[blank_gid].status != "succeeded"
                last_attempt = engine.last_attempt_for(blank_gid)
                assert last_attempt is not None
                assert last_attempt.failure_class == "page_blank"

                assert any(tmp_path.glob("*.png")), "real screenshot must have been written"

                # Regression guard: write_page_fixture reads has_main_content
                # back OUT of the SAME dom_snapshot dict passed into
                # attach_page_metadata_evidence — a real reachable page must
                # export has_main_content=True, not silently default to False
                # (found during 2026-07-04 real suyuan-system verification).
                fixture_path = tmp_path / "page_entries.json"
                write_page_fixture(adapter, fixture_path)
                import json as _json

                exported = _json.loads(fixture_path.read_text(encoding="utf-8"))
                users_entry = next(e for e in exported if e["page_id"] == "menu_page_001")
                assert users_entry["status"] == "reachable"
                assert users_entry["has_main_content"] is True
                assert users_entry["is_blank"] is False
            finally:
                await browser.close()

    asyncio.run(scenario())
