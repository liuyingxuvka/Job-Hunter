from __future__ import annotations

from typing import Any

from ..widgets.common import _t
from ...search.orchestration.job_result_i18n import select_display_text


def display_job_title(ui_language: str, job: Any) -> str:
    return select_display_text(
        ui_language=ui_language,
        raw_text=str(getattr(job, "title", "") or "").strip(),
        zh_text=str(getattr(job, "title_zh", "") or "").strip(),
        en_text=str(getattr(job, "title_en", "") or "").strip(),
    )


def display_job_location(ui_language: str, job: Any) -> str:
    return select_display_text(
        ui_language=ui_language,
        raw_text=str(getattr(job, "location", "") or "").strip(),
        zh_text=str(getattr(job, "location_zh", "") or "").strip(),
        en_text=str(getattr(job, "location_en", "") or "").strip(),
    )


def display_target_role(ui_language: str, job: Any) -> str:
    if ui_language == "en":
        localized = (
            str(getattr(job, "bound_target_role_name_en", "") or "").strip()
            or str(getattr(job, "bound_target_role_display_name", "") or "").strip()
            or str(getattr(job, "bound_target_role_name_zh", "") or "").strip()
        )
    else:
        localized = (
            str(getattr(job, "bound_target_role_name_zh", "") or "").strip()
            or str(getattr(job, "bound_target_role_display_name", "") or "").strip()
            or str(getattr(job, "bound_target_role_name_en", "") or "").strip()
        )
    status = str(getattr(job, "current_target_role_status", "") or "").strip()
    suffix = ""
    if status == "needs_rescore":
        suffix = _t(ui_language, "（历史/待重算）", " (historical / rescore)")
    elif status == "not_current_fit":
        suffix = _t(ui_language, "（历史，当前不匹配）", " (historical, not current fit)")
    elif status == "historical_only":
        suffix = _t(ui_language, "（历史推荐）", " (historical)")
    if localized:
        return f"{localized}{suffix}"
    return _t(ui_language, "未绑定", "Unbound")


def format_score(ui_language: str, job: Any) -> str:
    score = getattr(job, "bound_target_role_score", None)
    if score is None:
        score = getattr(job, "match_score", None)
    if score is None:
        return _t(ui_language, "无评分", "No score")
    if score >= 85:
        level = _t(ui_language, "高推荐", "High")
    elif score >= 70:
        level = _t(ui_language, "中推荐", "Medium")
    else:
        level = _t(ui_language, "低推荐", "Low")
    score_text = f"{score} / 100"
    if ui_language == "en":
        return f"{score_text} ({level})"
    return f"{score_text}（{level}）"


__all__ = [
    "display_job_location",
    "display_job_title",
    "display_target_role",
    "format_score",
]
