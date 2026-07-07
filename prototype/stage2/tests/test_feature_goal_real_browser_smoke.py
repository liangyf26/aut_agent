"""
Local headless smoke test for the real-browser Stage D feature classifier.

Boots a static HTML fixture with a page containing a real query input +
search button, and a real high-risk delete button, with Python's stdlib
http.server, drives it with a real headless Playwright Chromium page via
``feature_goal.real_browser_classifier.classify_features_with_playwright``,
and asserts:

- the search control is classified feature_type="query", risk_level="low";
- the delete control is classified feature_type="delete", risk_level="high";
- a real screenshot is produced;
- generated test cases mirror the fixture path's shape (executable for the
  low-risk query control, entry_confirmation for the high-risk delete
  control) — this is the safety-relevant distinction Stage D's own
  classifier enforces, and this real DOM path must preserve it too.

No network access or credentials required — everything runs against
127.0.0.1. Independent of test_feature_goal_integration.py's hand-written
title/URL-classification tests.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from prototype.stage2.app.feature_goal.feature_adapter import FeatureAdapter
from prototype.stage2.app.feature_goal.real_browser_classifier import classify_features_with_playwright
from prototype.stage2.app.goal_loop.state_machine import GoalLoopEngine

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")

_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><title>用户管理</title></head>
<body>
  <input type="search" name="keyword" placeholder="查询" />
  <button type="button">查询</button>
  <button type="button">删除</button>
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


def test_real_browser_feature_classification_distinguishes_risk_levels(
    fixture_server, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(fixture_server, wait_until="domcontentloaded")

                engine = GoalLoopEngine(run_id="feature_real_browser_smoke")
                adapter = FeatureAdapter(engine)
                page_goal = engine.register_goal(goal_type="feature", goal_name="page scan root")

                feature_goal_ids, test_cases = await classify_features_with_playwright(
                    page,
                    adapter,
                    page_goal.goal_id,
                    page_id="page_users",
                    screenshots_dir=tmp_path,
                )

                assert len(feature_goal_ids) >= 2
                contexts = [adapter.get_feature_context(gid) for gid in feature_goal_ids]
                by_type = {ctx["feature_type"]: ctx for ctx in contexts}

                assert "query" in by_type
                assert by_type["query"]["risk_level"] == "low"

                assert "delete" in by_type
                assert by_type["delete"]["risk_level"] == "high"

                by_type_case = {tc["metadata"]["feature_type"]: tc for tc in test_cases}
                assert by_type_case["query"]["type"] == "executable"
                assert by_type_case["delete"]["type"] == "entry_confirmation"
                assert by_type_case["delete"]["requires_approval"] is True

                assert any(tmp_path.glob("*.png")), "real screenshot must have been written"
            finally:
                await browser.close()

    asyncio.run(scenario())
