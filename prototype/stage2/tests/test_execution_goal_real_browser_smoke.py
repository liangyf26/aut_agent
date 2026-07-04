"""
Local headless smoke test for the real-browser Stage E runner.

Boots a static HTML fixture (query form + reset button + a "high-risk"
button) with Python's stdlib http.server, drives it with a real headless
Playwright Chromium page via ``real_browser_runner.execute_test_case_with_playwright``,
and asserts:

- an ``executable`` case fills/clicks/observes the real DOM and clears the
  query field on reset;
- an ``entry_confirmation`` case confirms the high-risk button is visible but
  NEVER clicks it (tracked via an in-page click counter) — this is the
  safety boundary the real-browser runner must preserve unchanged from the
  fixture-simulated runner;
- the ``executable`` + ``risk_level=="high"`` defense-in-depth refusal still
  fires before any Playwright call.

No network access or credentials required — everything runs against
127.0.0.1. This is independent of the fixture-simulated integration tests in
test_execution_goal_integration.py and does not touch any real system.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from prototype.stage2.app.execution_goal.execution_runner import STATUS_FAILED, STATUS_PASSED
from prototype.stage2.app.execution_goal.real_browser_runner import (
    EXECUTION_MODE_REAL_BROWSER,
    execute_test_case_with_playwright,
)

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")

_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><title>Stage E real-browser fixture</title></head>
<body>
  <input name="keyword" placeholder="keyword" />
  <button id="query-btn" onclick="document.getElementById('result').textContent='queried'">query</button>
  <button id="reset-btn" onclick="document.querySelector('input[name=keyword]').value=''">reset</button>
  <div id="result"></div>
  <button id="high-risk-btn" onclick="window.__highRiskClicks = (window.__highRiskClicks || 0) + 1;">
    submit-final-application
  </button>
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


def _run(coro):
    return asyncio.run(coro)


def test_executable_case_fills_clicks_and_verifies_reset(fixture_server, tmp_path: Path) -> None:
    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(fixture_server, wait_until="domcontentloaded")

                test_case = {
                    "test_case_id": "tc_real_browser_query",
                    "feature_id": "feat_query",
                    "page_id": "page_001",
                    "type": "executable",
                    "risk_level": "low",
                    "steps": [
                        {"step": 1, "action": "fill", "target": "input[name='keyword']", "value": "hello"},
                        {"step": 2, "action": "click", "target": "#query-btn"},
                        {"step": 3, "action": "wait_for", "target": "#result"},
                        {"step": 4, "action": "click", "target": "#reset-btn"},
                        {"step": 5, "action": "verify", "target": "input[name='keyword']", "expected": ""},
                    ],
                    "expected_result": "查询后重置成功",
                }

                outcome = await execute_test_case_with_playwright(
                    page, test_case, goal_id="goal_1", screenshots_dir=tmp_path
                )

                assert outcome.status == STATUS_PASSED
                assert outcome.execution_mode == EXECUTION_MODE_REAL_BROWSER
                assert len(outcome.actions) == 5
                assert all(a["status"] == "completed" for a in outcome.actions)
                assert outcome.screenshot_refs
                screenshot_path = Path(outcome.screenshot_refs[0]["path"])
                assert screenshot_path.exists()

                final_value = await page.input_value("input[name='keyword']")
                assert final_value == ""
            finally:
                await browser.close()

    _run(scenario())


def test_entry_confirmation_never_clicks_high_risk_control(fixture_server, tmp_path: Path) -> None:
    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(fixture_server, wait_until="domcontentloaded")

                test_case = {
                    "test_case_id": "tc_real_browser_entry_confirm",
                    "feature_id": "feat_submit",
                    "page_id": "page_001",
                    "type": "entry_confirmation",
                    "risk_level": "high",
                    "metadata": {"element_locator": "#high-risk-btn"},
                }

                outcome = await execute_test_case_with_playwright(
                    page, test_case, goal_id="goal_2", screenshots_dir=tmp_path
                )

                assert outcome.status == STATUS_PASSED
                assert outcome.requires_human_authorization is True
                assert outcome.execution_mode == EXECUTION_MODE_REAL_BROWSER

                click_count = await page.evaluate("window.__highRiskClicks || 0")
                assert click_count == 0, "entry_confirmation must never click the real high-risk control"
            finally:
                await browser.close()

    _run(scenario())


def test_executable_high_risk_case_refused_before_touching_browser(fixture_server, tmp_path: Path) -> None:
    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(fixture_server, wait_until="domcontentloaded")

                test_case = {
                    "test_case_id": "tc_real_browser_mislabeled",
                    "feature_id": "feat_submit",
                    "page_id": "page_001",
                    "type": "executable",
                    "risk_level": "high",
                    "steps": [{"step": 1, "action": "click", "target": "#high-risk-btn"}],
                }

                outcome = await execute_test_case_with_playwright(
                    page, test_case, goal_id="goal_3", screenshots_dir=tmp_path
                )

                assert outcome.status == STATUS_FAILED
                assert outcome.failure_reason == "blocked_by_safety_policy"
                assert outcome.actions == []

                click_count = await page.evaluate("window.__highRiskClicks || 0")
                assert click_count == 0
            finally:
                await browser.close()

    _run(scenario())
