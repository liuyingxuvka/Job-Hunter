from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem

from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord
from ...ai.role_recommendations import select_bilingual_description
from ..widgets.common import _t
from . import target_direction_profile_sync
from . import target_direction_profile_ui


def set_candidate(page: Any, candidate: CandidateRecord | int | None, preserve_profile_id: int | None = None) -> None:
    current_candidate = (
        page.context.candidates.get(int(candidate))
        if isinstance(candidate, int)
        else candidate
    )
    page._current_candidate = current_candidate if isinstance(current_candidate, CandidateRecord) else None
    page.current_profile_id = None
    page._auto_translated_profile_ids = set()
    if page._current_candidate is None or page._current_candidate.candidate_id is None:
        page.profile_records = []
        page.direction_list.clear()
        clear_profile_form(page)
        page.profile_meta_label.setText(
            _t(page.ui_language, "请先选择一个求职者，再进入工作台。", "Select a candidate first, then open the workspace.")
        )
        page._set_enabled(False)
        return

    page.current_profile_id = preserve_profile_id if preserve_profile_id is not None else None
    page._set_enabled(True)
    page.profile_meta_label.setText(
        _t(
            page.ui_language,
            f"当前求职者：{page._current_candidate.name}。先确定要重点投的目标岗位，后面系统才会按这些岗位去找公司和岗位。",
            f"Current candidate: {page._current_candidate.name}. Confirm target roles first; downstream company/job search uses these roles.",
        )
    )
    reload_profiles(page, preserve_profile_id=page.current_profile_id)


def reload_profiles(page: Any, preserve_profile_id: int | None = None) -> None:
    result = target_direction_profile_sync.reload_profiles(
        current_candidate=page._current_candidate,
        current_profile_id=page.current_profile_id,
        preserve_profile_id=preserve_profile_id,
        direction_list=page.direction_list,
        list_for_candidate=page.context.profiles.list_for_candidate,
        prepare_profile=page._ensure_profile_bilingual_for_ui,
        display_role_name=page._display_role_name,
        untitled_label=_t(page.ui_language, "未命名岗位", "Untitled Role"),
    )
    page.profile_records = result.profile_records
    if result.target_row is not None:
        page.direction_list.setCurrentRow(result.target_row)
        return
    if result.should_clear_form:
        clear_profile_form(page)


def load_profile(page: Any, profile: SearchProfileRecord) -> None:
    profile = page._ensure_profile_bilingual_for_ui(profile)
    page.current_profile_id = profile.profile_id
    display_name = page._display_role_name(profile) or _t(page.ui_language, "未命名岗位", "Untitled Role")
    target_direction_profile_ui.load_profile_form(
        page.ui_language,
        display_name=display_name,
        description_text=select_bilingual_description(profile.keyword_focus, page.ui_language),
        direction_name_input=page.direction_name_input,
        direction_reason_input=page.direction_reason_input,
        profile_meta_label=page.profile_meta_label,
    )


def clear_profile_form(page: Any) -> None:
    page.current_profile_id = None
    target_direction_profile_ui.clear_profile_form(
        page.ui_language,
        has_candidate=page._current_candidate is not None,
        direction_name_input=page.direction_name_input,
        direction_reason_input=page.direction_reason_input,
        profile_meta_label=page.profile_meta_label,
    )


def on_profile_selected(page: Any, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
    del previous
    page.current_profile_id = target_direction_profile_sync.apply_profile_selection(
        current,
        clear_form=lambda: clear_profile_form(page),
        find_profile=page._find_profile,
        load_profile=lambda profile: load_profile(page, profile),
    )


def on_item_checked_changed(page: Any, item: QListWidgetItem) -> None:
    profile_id = item.data(Qt.UserRole)
    if profile_id is None:
        return
    profile = page._find_profile(int(profile_id))
    if profile is None:
        return
    page.context.profiles.save(
        SearchProfileRecord(
            profile_id=profile.profile_id,
            candidate_id=profile.candidate_id,
            name=profile.name,
            scope_profile=profile.scope_profile,
            target_role=profile.target_role,
            location_preference=profile.location_preference,
            company_focus=profile.company_focus,
            company_keyword_focus=profile.company_keyword_focus,
            role_name_i18n=profile.role_name_i18n,
            keyword_focus=profile.keyword_focus,
            is_active=item.checkState() == Qt.Checked,
            queries=profile.queries,
        )
    )
    candidate_id = getattr(page._current_candidate, "candidate_id", None)
    page.profile_records = sorted(
        page.context.profiles.list_for_candidate(int(candidate_id) if candidate_id is not None else 0),
        key=lambda current: (page._display_role_name(current).casefold(), current.profile_id or 0),
    )
    if page.on_data_changed:
        page.on_data_changed()


__all__ = [
    "clear_profile_form",
    "load_profile",
    "on_item_checked_changed",
    "on_profile_selected",
    "reload_profiles",
    "set_candidate",
]
