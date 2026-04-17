from __future__ import annotations

from typing import Any

from ..widgets.common import _t


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
    if localized:
        return localized
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
    "display_target_role",
    "format_score",
]
