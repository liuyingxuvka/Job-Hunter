from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def format_company_source_diagnostic_summary(
    company: Mapping[str, Any],
    *,
    ui_language: str = "zh",
) -> str:
    diagnostics = company.get("sourceDiagnostics")
    if not isinstance(diagnostics, Mapping):
        return ""
    reason = str(diagnostics.get("reason") or "").strip()
    if not reason:
        return ""
    company_name = str(company.get("name") or "").strip() or _t(
        ui_language,
        "未命名公司",
        "Unnamed company",
    )
    source_path = str(diagnostics.get("sourcePath") or "unknown").strip() or "unknown"
    raw_jobs = _to_int(diagnostics.get("rawJobsFetched"))
    snapshot_jobs = _to_int(diagnostics.get("snapshotJobs"))
    selected_jobs = _to_int(diagnostics.get("selectedJobs"))
    queued_jobs = _to_int(diagnostics.get("queuedJobs"))
    followed_links = _to_int(diagnostics.get("followedBoardLinks"))
    reason_text = _reason_text(reason, ui_language=ui_language)
    if str(ui_language or "").strip().lower().startswith("zh"):
        follow_text = f" 跟进={followed_links}" if followed_links > 0 else ""
        return (
            f"{company_name}：{reason_text} | 来源={source_path} | "
            f"抓取={raw_jobs} 快照={snapshot_jobs} 选中={selected_jobs} 排队={queued_jobs}{follow_text}"
        )
    follow_text = f" followed={followed_links}" if followed_links > 0 else ""
    return (
        f"{company_name}: {reason_text} | source={source_path} | "
        f"fetched={raw_jobs} snapshot={snapshot_jobs} selected={selected_jobs} queued={queued_jobs}{follow_text}"
    )


def select_recent_company_source_diagnostic_summary(
    companies: list[Mapping[str, Any]],
    *,
    ui_language: str = "zh",
) -> str:
    candidates: list[tuple[bool, datetime, str]] = []
    for company in companies:
        if not isinstance(company, Mapping):
            continue
        diagnostics = company.get("sourceDiagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        summary = format_company_source_diagnostic_summary(
            company,
            ui_language=ui_language,
        )
        if not summary:
            continue
        reason = str(diagnostics.get("reason") or "").strip()
        searched_at = _parse_datetime(company.get("lastSearchedAt") or "")
        candidates.append(
            (
                reason != "queued_jobs",
                searched_at or datetime.min.replace(tzinfo=timezone.utc),
                summary,
            )
        )
    if not candidates:
        return ""
    interesting = [item for item in candidates if item[0]]
    pool = interesting or candidates
    pool.sort(key=lambda item: item[1], reverse=True)
    return pool[0][2]


def _reason_text(reason: str, *, ui_language: str) -> str:
    normalized = str(reason or "").strip()
    reason_map = {
        "all_jobs_filtered": _t(ui_language, "抓到了岗位，但全部被过滤", "Jobs were fetched, but all were filtered out"),
        "all_snapshot_jobs_already_analyzed": _t(ui_language, "快照岗位都已分析过", "All snapshot jobs were already analyzed"),
        "detail_budget_reached": _t(ui_language, "详情抓取预算已用完，保留部分结果", "Detail-fetch budget reached; partial results were kept"),
        "no_careers_page": _t(ui_language, "未找到 careers 页面", "No careers page found"),
        "no_jobs_fetched": _t(ui_language, "没有抓到任何岗位", "No jobs were fetched"),
        "no_jobs_queued": _t(ui_language, "已选中岗位，但没有进入分析队列", "Jobs were selected, but none entered the analysis queue"),
        "no_jobs_selected": _t(ui_language, "有快照岗位，但这轮没有选中任何岗位", "Snapshot jobs exist, but none were selected this round"),
        "queued_jobs": _t(ui_language, "已进入分析队列", "Jobs entered the analysis queue"),
        "transient_fetch_error": _t(ui_language, "临时查找失败，可重试", "Transient lookup failure; retry later"),
    }
    return reason_map.get(normalized, normalized or _t(ui_language, "未知原因", "Unknown reason"))


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        text = str(value).strip()
        return int(float(text)) if text else 0
    except Exception:
        return 0


def _t(ui_language: str, zh_text: str, en_text: str) -> str:
    return zh_text if str(ui_language or "").strip().lower().startswith("zh") else en_text


__all__ = [
    "format_company_source_diagnostic_summary",
    "select_recent_company_source_diagnostic_summary",
]
