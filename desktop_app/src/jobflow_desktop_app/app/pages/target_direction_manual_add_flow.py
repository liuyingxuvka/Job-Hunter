from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from ...db.repositories.candidates import CandidateRecord
from ...ai.role_recommendations import OpenAIRoleRecommendationService, RoleRecommendationError
from ..context import AppContext
from ..widgets.common import _t
from . import target_direction_profile_completion
from . import target_direction_profile_records


def start_manual_add_flow(
    *,
    owner: QWidget,
    context: AppContext,
    ui_language: str,
    current_candidate: CandidateRecord | None,
    role_recommender: OpenAIRoleRecommendationService,
    add_button,
    on_data_changed: Callable[[], None] | None,
    reload_profiles: Callable[[int | None], None],
    set_ai_busy_state: Callable[[bool, str, int | None], None],
    complete_role_name_pair: Callable[..., tuple[str, str]],
    complete_description_pair: Callable[..., tuple[str, str]],
    canonical_role_name: Callable[[str, str], str],
    is_generic_role_name: Callable[[str], bool],
    dialog_factory: Callable[..., Any],
    candidate_still_current: Callable[[], bool],
    allow_ai_actions: Callable[[], bool],
    run_busy_task_fn: Callable[..., bool],
) -> None:
    if current_candidate is None or current_candidate.candidate_id is None:
        QMessageBox.information(
            owner,
            _t(ui_language, "手动添加岗位", "Add Role Manually"),
            _t(ui_language, "请先选择一个求职者。", "Please select a candidate first."),
        )
        return

    dialog = dialog_factory(ui_language=ui_language, parent=owner)
    if dialog.exec() != QDialog.Accepted:
        return

    direction_name, rough_description = dialog.values()
    if not direction_name:
        QMessageBox.warning(
            owner,
            _t(ui_language, "手动添加岗位", "Add Role Manually"),
            _t(ui_language, "岗位名称不能为空。", "Role name cannot be empty."),
        )
        return

    candidate_id = int(current_candidate.candidate_id)
    settings = context.settings.get_effective_openai_settings()
    api_base_url = context.settings.get_openai_base_url()
    use_ai = bool(settings.api_key.strip()) and bool(allow_ai_actions())
    dialog_title = _t(ui_language, "手动添加岗位", "Add Role Manually")

    def _build_payload(enable_ai: bool) -> dict[str, str]:
        suggestion = None
        enrich_error = ""
        if enable_ai:
            try:
                suggestion = role_recommender.enrich_manual_role(
                    candidate=current_candidate,
                    settings=settings,
                    role_name=direction_name,
                    rough_description=rough_description,
                    api_base_url=api_base_url,
                )
            except RoleRecommendationError as exc:
                enrich_error = str(exc)

        if suggestion is not None:
            role_name_zh = suggestion.name_zh.strip()
            role_name_en = (suggestion.name_en or suggestion.name).strip()
            description_zh = suggestion.description_zh.strip()
            description_en = suggestion.description_en.strip()
            scope_profile = suggestion.scope_profile or ""
        else:
            role_name_zh = direction_name if ui_language != "en" else ""
            role_name_en = direction_name if ui_language == "en" else ""
            description_zh = rough_description if ui_language != "en" else ""
            description_en = rough_description if ui_language == "en" else ""
            scope_profile = ""

        completed = target_direction_profile_completion.complete_profile_text(
            role_name_zh=role_name_zh,
            role_name_en=role_name_en,
            description_zh=description_zh,
            description_en=description_en,
            fallback_name=direction_name,
            settings=settings,
            api_base_url=api_base_url,
            use_ai=enable_ai,
            complete_role_name_pair=complete_role_name_pair,
            complete_description_pair=complete_description_pair,
        )
        return {
            "role_name_zh": completed.role_name_zh,
            "role_name_en": completed.role_name_en,
            "description_zh": completed.description_zh,
            "description_en": completed.description_en,
            "scope_profile": scope_profile,
            "enrich_error": enrich_error,
        }

    def _persist_payload(payload: dict[str, str]) -> None:
        role_name_zh = str(payload.get("role_name_zh") or "").strip()
        role_name_en = str(payload.get("role_name_en") or "").strip()
        description_zh = str(payload.get("description_zh") or "").strip()
        description_en = str(payload.get("description_en") or "").strip()
        scope_profile = str(payload.get("scope_profile") or "").strip()
        enrich_error = str(payload.get("enrich_error") or "").strip()

        prepared = target_direction_profile_records.prepare_profile_content(
            role_name_zh=role_name_zh,
            role_name_en=role_name_en,
            description_zh=description_zh,
            description_en=description_en,
            fallback_name=direction_name,
            untitled_label=_t(ui_language, "未命名岗位", "Untitled Role"),
            canonical_role_name=canonical_role_name,
            is_generic_role_name=is_generic_role_name,
        )
        if prepared.is_generic:
            QMessageBox.warning(
                owner,
                dialog_title,
                _t(
                    ui_language,
                    "岗位名称还是过于泛化（例如仅 Engineer/Manager）。请补充更具体方向后再提交。",
                    "Role name is still too generic (for example Engineer/Manager only). "
                    "Please add a more specific direction and submit again.",
                ),
            )
            return

        profile_id = context.profiles.save(
            target_direction_profile_records.build_new_profile_record(
                candidate=current_candidate,
                scope_profile=scope_profile,
                prepared=prepared,
                is_active=True,
            )
        )
        reload_profiles(profile_id)
        if on_data_changed:
            on_data_changed()

        if enrich_error:
            QMessageBox.information(
                owner,
                dialog_title,
                _t(
                    ui_language,
                    "AI 自动补全失败，已先按输入创建岗位。你可以继续编辑该岗位说明。\n\n"
                    f"错误信息：{enrich_error}",
                    "AI enrichment failed, so the role was created from your input first. "
                    "You can keep editing the role details.\n\n"
                    f"Error: {enrich_error}",
                ),
            )

    if not use_ai:
        _persist_payload(_build_payload(False))
        return

    add_button.setEnabled(False)
    busy_message = _t(
        ui_language,
        "AI 正在补全岗位信息，请稍候...",
        "AI is enriching role details, please wait...",
    )
    set_ai_busy_state(
        True,
        _t(
            ui_language,
            "第二步 AI 正在补全岗位信息，完成前不能开始第三步搜索。",
            "Step 2 AI is enriching role details. Step 3 search is blocked until it finishes.",
        ),
        candidate_id,
    )

    def _task() -> Any:
        return _build_payload(True)

    def _on_success(result: Any) -> None:
        if not candidate_still_current():
            return
        if not isinstance(result, dict):
            QMessageBox.warning(
                owner,
                dialog_title,
                _t(ui_language, "返回结果格式异常。", "Unexpected result payload."),
            )
            return
        _persist_payload(result)

    def _on_error(exc: Exception) -> None:
        QMessageBox.warning(
            owner,
            dialog_title,
            _t(
                ui_language,
                f"岗位补全失败：{exc}",
                f"Role enrichment failed: {exc}",
            ),
        )

    def _on_finally() -> None:
        add_button.setEnabled(current_candidate is not None)
        set_ai_busy_state(False, "", candidate_id)

    started = run_busy_task_fn(
        owner,
        title=dialog_title,
        message=busy_message,
        task=_task,
        on_success=_on_success,
        on_error=_on_error,
        on_finally=_on_finally,
    )
    if not started:
        add_button.setEnabled(current_candidate is not None)
        set_ai_busy_state(False, "", candidate_id)


__all__ = ["start_manual_add_flow"]
