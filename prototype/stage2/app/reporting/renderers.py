from __future__ import annotations

from typing import Any

from .progress_view import render_progress_markdown
from .report_markdown import (
    render_platform_daily_report_markdown as _render_platform_daily_report_markdown,
    render_run_report_markdown as _render_run_report_markdown,
)


def render_run_report_markdown(report: Any) -> str:
    return _render_run_report_markdown(report)


def render_platform_daily_report_markdown(report: Any) -> str:
    return _render_platform_daily_report_markdown(report)


def render_progress_view_markdown(snapshot: Any, recent_events: list[Any] | None = None) -> str:
    return render_progress_markdown(snapshot, recent_events=recent_events)
