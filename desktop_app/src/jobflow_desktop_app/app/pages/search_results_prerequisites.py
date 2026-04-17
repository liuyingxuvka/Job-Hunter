from __future__ import annotations

from typing import Callable

from ...db.repositories.profiles import SearchProfileRecord
from ..widgets.common import _t


def search_owner_name(candidate_id: int | None, *, resolve_candidate_name: Callable[[int], str | None]) -> str:
    if candidate_id is None:
        return ""
    return str(resolve_candidate_name(int(candidate_id)) or "").strip()


def active_profiles(profiles: list[SearchProfileRecord]) -> list[SearchProfileRecord]:
    return [profile for profile in profiles if profile.is_active]


def blocked_ai_issue(
    ui_language: str,
    *,
    ai_validation_level: str,
    ai_validation_message: str,
    blocked_ai_levels: set[str],
) -> str:
    if ai_validation_level not in blocked_ai_levels:
        return ""
    return ai_validation_message or _t(
        ui_language,
        "当前 AI 状态未通过验证。请先在右上角“设置 / Settings”里修复后再开始岗位搜索。",
        "The current AI status has not passed validation. Fix it in the top-right Settings / 设置 before starting job search.",
    )


def search_prerequisite_issue(
    ui_language: str,
    *,
    target_candidate_id: int | None,
    target_ai_busy: bool,
    target_ai_busy_message: str,
    ai_issue: str,
    current_candidate_running: bool,
    any_candidate_running: bool,
    owner_name: str,
    has_active_profiles: bool,
) -> str:
    if target_candidate_id is None:
        return ""
    if target_ai_busy:
        return target_ai_busy_message or _t(
            ui_language,
            "第二步 AI 仍在处理岗位方向。请先等待它完成，再开始第三步搜索。",
            "Step 2 AI is still processing target roles. Wait for it to finish before starting Step 3 search.",
        )
    if ai_issue:
        return ai_issue
    if current_candidate_running:
        return ""
    if any_candidate_running:
        if owner_name:
            return _t(
                ui_language,
                f"当前还有另一位求职者（{owner_name}）的岗位搜索正在运行。请先切回那位求职者查看状态，或等待它结束后再开始。",
                f"Another candidate ({owner_name}) still has a job search running. Switch back to that candidate to view its status, or wait for it to finish before starting search here.",
            )
        return _t(
            ui_language,
            "当前还有另一位求职者的岗位搜索正在运行。请先切回那位求职者查看状态，或等待它结束后再开始。",
            "Another candidate still has a job search running. Switch back to that candidate to view its status, or wait for it to finish before starting search here.",
        )
    if has_active_profiles:
        return ""
    return _t(
        ui_language,
        "当前还没有任何已启用的目标岗位。请先在第二步至少勾选或创建一个岗位，再开始第三步搜索。",
        "There are no enabled target roles yet. In Step 2, create or check at least one role before starting Step 3 search.",
    )


__all__ = [
    "active_profiles",
    "blocked_ai_issue",
    "search_owner_name",
    "search_prerequisite_issue",
]
