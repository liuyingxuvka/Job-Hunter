from __future__ import annotations

from typing import Any

from ..widgets.common import _t


def candidate_status_text(
    ui_language: str,
    zh_message: str,
    en_message: str,
    *,
    candidate_name: str = "",
) -> str:
    name = str(candidate_name or "").strip()
    if not name:
        return _t(ui_language, zh_message, en_message)
    return _t(
        ui_language,
        f"当前求职者：{name}。{zh_message}",
        f"Current candidate: {name}. {en_message}",
    )


def search_runtime_messages(
    ui_language: str,
    state: str,
    *,
    candidate_name: str = "",
    pending_before_run: int | None = None,
    duration_label: str = "",
    stage_label: str = "",
    elapsed_text: str = "",
) -> tuple[str, str, str]:
    normalized_state = str(state or "").strip().lower()
    normalized_duration = str(duration_label or "").strip()
    has_live_progress = bool(stage_label and elapsed_text)

    if normalized_state == "running":
        if pending_before_run and pending_before_run > 0:
            main_message = candidate_status_text(
                ui_language,
                f"正在后台搜索岗位；会先补完上次未完成的 {pending_before_run} 条岗位，再继续寻找新的岗位。",
                f"Background job search is running; {pending_before_run} unfinished job(s) from the last run will be completed first, then discovery will continue.",
                candidate_name=candidate_name,
            )
        else:
            main_message = candidate_status_text(
                ui_language,
                "正在后台搜索岗位；列表只会在出现新增或结果变化时刷新。",
                "Background job search is running; the list refreshes only when new or changed results appear.",
                candidate_name=candidate_name,
            )
        if has_live_progress:
            if ui_language == "en":
                detail_message = f"Background progress: {stage_label} | elapsed {elapsed_text}"
            else:
                detail_message = f"后台进度：{stage_label} · 已运行 {elapsed_text}"
            dialog_message = _t(
                ui_language,
                f"系统正在后台搜索岗位，当前阶段：{stage_label}，已运行 {elapsed_text}。你可以继续操作，搜索会在后台持续运行。",
                f"Searching jobs in the background. Current stage: {stage_label}, elapsed {elapsed_text}. You can keep working while search continues.",
            )
        else:
            detail_message = _t(
                ui_language,
                "后台进度：正在启动本轮搜索。",
                "Background progress: starting this search round.",
            )
            dialog_message = ""
        return main_message, detail_message, dialog_message

    if normalized_state == "stop_requested":
        main_message = candidate_status_text(
            ui_language,
            "已请求停止；系统会在当前阶段安全结束后停止。现在可以先调整下一次搜索时长。",
            "Stop requested. The system will stop after the current stage ends safely. You can already adjust the next search duration.",
            candidate_name=candidate_name,
        )
        if has_live_progress:
            if ui_language == "en":
                detail_message = f"Finishing: {stage_label} | elapsed {elapsed_text}"
            else:
                detail_message = f"后台收尾：{stage_label} · 已运行 {elapsed_text}"
            dialog_message = _t(
                ui_language,
                f"系统正在等待当前阶段安全结束，当前阶段：{stage_label}，已运行 {elapsed_text}。下一次搜索时长现在可以先行调整。",
                f"Waiting for the current stage to end safely. Current stage: {stage_label}, elapsed {elapsed_text}. The next search duration can already be adjusted.",
            )
        else:
            detail_message = _t(
                ui_language,
                "后台收尾：正在等待当前阶段安全结束。",
                "Finishing: waiting for the current stage to end safely.",
            )
            dialog_message = ""
        return main_message, detail_message, dialog_message

    if normalized_state == "queued":
        main_message = candidate_status_text(
            ui_language,
            f"下一轮搜索已排队；当前收尾完成后会自动开始。计划时长：{normalized_duration}。",
            f"The next search round is queued and will start automatically after the current shutdown finishes. Planned duration: {normalized_duration}.",
            candidate_name=candidate_name,
        )
        if has_live_progress:
            if ui_language == "en":
                detail_message = f"Finishing: {stage_label} | elapsed {elapsed_text} | next round queued: {normalized_duration}"
            else:
                detail_message = f"后台收尾：{stage_label} · 已运行 {elapsed_text} · 下一轮已排队：{normalized_duration}"
            dialog_message = _t(
                ui_language,
                f"系统正在等待当前阶段安全结束，当前阶段：{stage_label}，已运行 {elapsed_text}。下一轮搜索已经排队，时长为 {normalized_duration}。",
                f"Waiting for the current stage to end safely. Current stage: {stage_label}, elapsed {elapsed_text}. The next search round is already queued for {normalized_duration}.",
            )
        else:
            detail_message = _t(
                ui_language,
                f"后台收尾：正在等待当前阶段安全结束；下一轮已排队（{normalized_duration}）。",
                f"Finishing: waiting for the current stage to end safely; the next round is queued ({normalized_duration}).",
            )
            dialog_message = ""
        return main_message, detail_message, dialog_message

    return "", "", ""


def search_completion_detail(
    ui_language: str,
    *,
    discovered_job_count: int,
    scored_job_count: int,
    recommended_job_count: int,
    pending_job_count: int,
    candidate_company_pool_count: int = 0,
    no_qualified_company_stop: bool = False,
) -> str:
    discovered = max(0, int(discovered_job_count or 0))
    scored = max(0, int(scored_job_count or 0))
    recommended = max(0, int(recommended_job_count or 0))
    pending = max(0, int(pending_job_count or 0))
    pool = max(0, int(candidate_company_pool_count or 0))

    if ui_language == "en":
        parts = [
            f"This round found {discovered} job(s)",
            f"analyzed {scored}",
            f"final recommendations {recommended}",
        ]
        if pool > 0:
            parts.append(f"current company pool {pool}")
        summary = ", ".join(parts) + "."
        if no_qualified_company_stop:
            return summary + " No new qualified companies were found; try again in a few days."
        if pending > 0:
            return summary + f" {pending} job(s) still need analysis; starting search again will resume them first."
        return summary + " No pending jobs remain."

    parts = [
        f"本轮找到 {discovered} 条",
        f"已分析 {scored} 条",
        f"最终推荐 {recommended} 条",
    ]
    if pool > 0:
        parts.append(f"当前公司池 {pool} 家")
    summary = "，".join(parts) + "。"
    if no_qualified_company_stop:
        return summary + "当前没有发现新的合格公司，建议过几天再试。"
    if pending > 0:
        return summary + f"当前还有 {pending} 条待补完岗位，建议现在继续搜索。"
    return summary + "当前没有待补完岗位。"


def search_completion_popup_message(
    ui_language: str,
    *,
    detail_text: str,
) -> str:
    header = _t(
        ui_language,
        "本轮搜索已结束。",
        "This search round finished.",
    )
    detail = str(detail_text or "").strip()
    if not detail:
        return header
    return f"{header}\n\n{detail}"


def format_elapsed_text(ui_language: str, seconds: int) -> str:
    total = max(0, int(seconds or 0))
    minutes, remaining_seconds = divmod(total, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    if ui_language != "en":
        if hours > 0:
            return f"{hours} 小时 {remaining_minutes} 分 {remaining_seconds} 秒"
        if minutes > 0:
            return f"{minutes} 分 {remaining_seconds} 秒"
        return f"{remaining_seconds} 秒"
    if hours > 0:
        return f"{hours}h {remaining_minutes}m {remaining_seconds}s"
    if minutes > 0:
        return f"{minutes}m {remaining_seconds}s"
    return f"{remaining_seconds}s"


def format_countdown_text(seconds: int) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


def progress_stage_label(ui_language: str, stage: str) -> str:
    return {
        "preparing": _t(ui_language, "准备环境", "Preparing"),
        "resume": _t(ui_language, "补完待处理岗位", "Resuming pending jobs"),
        "resume_pending": _t(ui_language, "补完待处理岗位", "Resuming pending jobs"),
        "discover": _t(ui_language, "公司池主流程", "Company-first stage"),
        "main": _t(ui_language, "公司池主流程", "Company-first stage"),
        "finalize": _t(ui_language, "收尾补完岗位", "Finalizing discovered jobs"),
        "done": _t(ui_language, "已完成", "Completed"),
        "completed": _t(ui_language, "已完成", "Completed"),
    }.get(stage, _t(ui_language, "后台处理中", "Background work"))


def search_progress_text(
    ui_language: str,
    progress: Any,
    *,
    stop_requested: bool,
    queued_restart: bool,
    queued_duration_label: str = "",
    selected_duration_label: str = "",
) -> tuple[str, str]:
    if str(getattr(progress, "status", "") or "").strip().lower() != "running":
        return "", ""

    stage = str(getattr(progress, "stage", "") or "").strip().lower()
    stage_label = progress_stage_label(ui_language, stage)
    if stop_requested:
        if queued_restart:
            duration_label = queued_duration_label or selected_duration_label
            if ui_language == "en":
                detail_text = f"Finishing: {stage_label} | next round queued: {duration_label}"
            else:
                detail_text = f"后台收尾：{stage_label} · 下一轮已排队：{duration_label}"
            dialog_text = _t(
                ui_language,
                f"系统正在等待当前阶段安全结束，当前阶段：{stage_label}。下一轮搜索已经排队，时长为 {duration_label}。",
                f"Waiting for the current stage to end safely. Current stage: {stage_label}. The next search round is already queued for {duration_label}.",
            )
            return detail_text, dialog_text
        detail_text = _t(
            ui_language,
            f"后台收尾：{stage_label}",
            f"Finishing: {stage_label}",
        )
        dialog_text = _t(
            ui_language,
            f"系统正在等待当前阶段安全结束，当前阶段：{stage_label}。你可以先调整下一次搜索时长。",
            f"Waiting for the current stage to end safely. Current stage: {stage_label}. You can already adjust the next search duration.",
        )
        return detail_text, dialog_text

    detail_text = _t(
        ui_language,
        f"后台进度：{stage_label}",
        f"Background progress: {stage_label}",
    )
    dialog_text = _t(
        ui_language,
        f"系统正在后台搜索岗位，当前阶段：{stage_label}。你可以继续操作，搜索会在后台持续运行。",
        f"Searching jobs in the background. Current stage: {stage_label}. You can keep working while search continues.",
    )
    return detail_text, dialog_text


def status_display(ui_language: str, status_code: str) -> str:
    labels = {
        "pending": _t(ui_language, "待定", "Pending"),
        "focus": _t(ui_language, "重点", "Focus"),
        "applied": _t(ui_language, "已投递", "Applied"),
        "offered": _t(ui_language, "已得到 Offer", "Offer Received"),
        "rejected": _t(ui_language, "已被拒绝", "Rejected"),
        "dropped": _t(ui_language, "已放弃", "Dropped"),
    }
    return labels.get(status_code, labels["pending"])


__all__ = [
    "candidate_status_text",
    "format_countdown_text",
    "format_elapsed_text",
    "progress_stage_label",
    "search_completion_detail",
    "search_completion_popup_message",
    "search_progress_text",
    "search_runtime_messages",
    "status_display",
]
