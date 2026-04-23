from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QWidget

from ...db.repositories.candidates import CandidateRecord
from ...ai.role_recommendations import OpenAIRoleRecommendationService, RoleRecommendationError
from ..context import AppContext
from ..widgets.common import _t
from . import target_direction_recommendations


def start_role_suggestion_flow(
    *,
    owner: QWidget,
    context: AppContext,
    ui_language: str,
    current_candidate: CandidateRecord | None,
    role_recommender: OpenAIRoleRecommendationService,
    generate_button,
    reload_profiles: Callable[[int | None], None],
    on_data_changed: Callable[[], None] | None,
    set_ai_busy_state: Callable[[bool, str, int | None], None],
    set_generation_feedback: Callable[[str], None],
    canonical_role_name: Callable[[str, str], str],
    ai_validation_issue: Callable[[], str],
    candidate_still_current: Callable[[], bool],
    run_busy_task_fn: Callable[..., bool],
    show_information: Callable[[str, str], None],
    show_warning: Callable[[str, str], None],
) -> None:
    if current_candidate is None or current_candidate.candidate_id is None:
        show_information(
            _t(ui_language, "AI 推荐岗位", "AI Recommend Roles"),
            _t(ui_language, "请先选择一个求职者。", "Please select a candidate first."),
        )
        return

    settings = context.settings.get_effective_openai_settings()
    if not settings.api_key.strip():
        show_warning(
            _t(ui_language, "AI 推荐岗位", "AI Recommend Roles"),
            _t(
                ui_language,
                "请先在右上角“设置 / Settings”里填写并保存 OpenAI API Key。",
                "Please fill and save OpenAI API Key in the top-right Settings / 设置 first.",
            ),
        )
        return

    ai_issue = str(ai_validation_issue() or "").strip()
    if ai_issue:
        show_warning(
            _t(ui_language, "AI 推荐岗位", "AI Recommend Roles"),
            ai_issue,
        )
        return

    candidate_id = int(current_candidate.candidate_id)
    existing_profiles = context.profiles.list_for_candidate(candidate_id)
    existing_role_context = target_direction_recommendations.build_existing_role_context(
        existing_profiles,
        canonical_role_name=canonical_role_name,
    )
    mix_plan = target_direction_recommendations.build_role_recommendation_mix_plan(existing_profiles)
    if mix_plan.request_total <= 0:
        set_generation_feedback(
            _t(
                ui_language,
                f"当前目标岗位已达到建议上限 {mix_plan.total_cap} 个，请先删除一些岗位再继续推荐。",
                f"Target roles already reached the suggested cap of {mix_plan.total_cap}. Delete some roles before asking for more.",
            )
        )
        return

    generate_button.setEnabled(False)
    dialog_title = _t(ui_language, "AI 推荐岗位", "AI Recommend Roles")
    dialog_message = _t(
        ui_language,
        "AI 正在生成岗位推荐，请稍候...",
        "AI is generating role recommendations, please wait...",
    )
    set_generation_feedback("")
    set_ai_busy_state(
        True,
        _t(
            ui_language,
            "第二步 AI 正在生成岗位方向，完成前不能开始第三步搜索。",
            "Step 2 AI is generating target roles. Step 3 search is blocked until it finishes.",
        ),
        candidate_id,
    )

    def _task() -> Any:
        return role_recommender.recommend_roles(
            current_candidate,
            settings,
            api_base_url=context.settings.get_openai_base_url(),
            max_items=mix_plan.request_total,
            existing_roles=existing_role_context,
            mix_plan=mix_plan,
        )

    def _on_success(result: Any) -> None:
        if not candidate_still_current():
            return
        suggestions = list(result) if isinstance(result, list) else []
        applied = target_direction_recommendations.apply_role_suggestions(
            suggestions,
            candidate=current_candidate,
            existing_profiles=context.profiles.list_for_candidate(candidate_id),
            save_profile=context.profiles.save,
            canonical_role_name=canonical_role_name,
            ui_language=ui_language,
        )

        reload_profiles(applied.last_profile_id)
        if on_data_changed:
            on_data_changed()

        if applied.added_names:
            set_generation_feedback(
                _t(
                    ui_language,
                    f"AI 推荐已新增 {len(applied.added_names)} 个岗位。",
                    f"AI recommendation added {len(applied.added_names)} roles.",
                )
            )
            return

        set_generation_feedback(
            _t(
                ui_language,
                "这次返回的岗位和现有列表重复，没有新增内容。",
                "Returned roles duplicate existing ones, so nothing new was added. Try again or add one manually.",
            )
        )

    def _on_error(exc: Exception) -> None:
        if isinstance(exc, RoleRecommendationError):
            show_warning(dialog_title, str(exc))
            return
        show_warning(
            dialog_title,
            _t(
                ui_language,
                f"AI 推荐失败：{exc}",
                f"AI recommendation failed: {exc}",
            ),
        )

    def _on_finally() -> None:
        set_ai_busy_state(False, "")

    started = run_busy_task_fn(
        owner,
        title=dialog_title,
        message=dialog_message,
        task=_task,
        on_success=_on_success,
        on_error=_on_error,
        on_finally=_on_finally,
    )
    if not started:
        set_ai_busy_state(False, "")


__all__ = ["start_role_suggestion_flow"]
