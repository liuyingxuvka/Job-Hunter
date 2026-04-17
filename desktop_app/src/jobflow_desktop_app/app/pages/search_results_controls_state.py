from __future__ import annotations

import time
from typing import Callable

from ..widgets.common import _t


def selected_search_duration_seconds(
    value: object,
    *,
    minimum_seconds: int = 300,
    default_seconds: int = 3600,
) -> int:
    try:
        return max(int(minimum_seconds), int(value))
    except (TypeError, ValueError):
        return int(default_seconds)


def selected_search_duration_label(ui_language: str, text: str) -> str:
    normalized = str(text or "").strip()
    if normalized:
        return normalized
    return _t(
        ui_language,
        "1 小时",
        "1 hour",
    )


def remaining_countdown_seconds(
    *,
    owns_running_search: bool,
    owns_queued_restart: bool,
    started_monotonic: float | None,
    duration_seconds: int,
    now_monotonic: Callable[[], float] | None = None,
) -> int:
    if not ((owns_running_search or owns_queued_restart) and started_monotonic is not None):
        return 0
    monotonic_now = now_monotonic or time.monotonic
    elapsed = max(0, int(monotonic_now() - started_monotonic))
    return max(0, int(duration_seconds) - elapsed)


def search_button_text(ui_language: str, *, running: bool, queued: bool = False) -> str:
    if running or queued:
        return _t(ui_language, "停止搜索", "Stop Search")
    return _t(ui_language, "开始搜索", "Start Search")


__all__ = [
    "remaining_countdown_seconds",
    "search_button_text",
    "selected_search_duration_label",
    "selected_search_duration_seconds",
]
