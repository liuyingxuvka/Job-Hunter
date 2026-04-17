from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from . import target_direction_profile_ui


@dataclass(slots=True)
class ReloadProfilesResult:
    profile_records: list[Any]
    target_row: int | None
    should_clear_form: bool


def reload_profiles(
    *,
    current_candidate,
    current_profile_id: int | None,
    preserve_profile_id: int | None,
    direction_list: QListWidget,
    list_for_candidate: Callable[[int], list[Any]],
    prepare_profile: Callable[[Any], Any],
    display_role_name: Callable[[Any], str],
    untitled_label: str,
) -> ReloadProfilesResult:
    current_candidate_id = getattr(current_candidate, "candidate_id", None)
    if current_candidate_id is None:
        direction_list.clear()
        return ReloadProfilesResult([], None, True)

    profile_records = sorted(
        list_for_candidate(current_candidate_id),
        key=lambda profile: (display_role_name(profile).casefold(), getattr(profile, "profile_id", None) or 0),
    )
    target_profile_id = preserve_profile_id if preserve_profile_id is not None else current_profile_id
    profile_records, target_row = target_direction_profile_ui.populate_direction_list(
        direction_list,
        profile_records,
        target_profile_id=target_profile_id,
        prepare_profile=prepare_profile,
        display_role_name=display_role_name,
        untitled_label=untitled_label,
    )
    if target_row is None and profile_records:
        target_row = 0
    return ReloadProfilesResult(profile_records, target_row, not bool(profile_records))


def apply_profile_selection(
    current: QListWidgetItem | None,
    *,
    clear_form: Callable[[], None],
    find_profile: Callable[[int | None], Any | None],
    load_profile: Callable[[Any], None],
) -> int | None:
    if current is None:
        clear_form()
        return None
    profile_id = current.data(Qt.UserRole)
    current_profile_id = int(profile_id) if profile_id is not None else None
    profile = find_profile(current_profile_id)
    if profile is None:
        clear_form()
        return None
    load_profile(profile)
    return current_profile_id


__all__ = [
    "apply_profile_selection",
    "ReloadProfilesResult",
    "reload_profiles",
]
