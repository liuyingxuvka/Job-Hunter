from __future__ import annotations

from typing import Callable

from ...db.repositories.profiles import SearchProfileRecord
from ...db.repositories.settings import OpenAISettings
from ...ai.role_recommendations import (
    decode_bilingual_description,
    decode_bilingual_role_name,
    encode_bilingual_description,
    encode_bilingual_role_name,
)


def ensure_profile_bilingual_for_ui(
    profile: SearchProfileRecord,
    *,
    current_name: str,
    settings: OpenAISettings,
    api_base_url: str,
    untitled_label: str,
    canonical_role_name: Callable[[str, str], str],
    complete_role_name_pair: Callable[[str, str, OpenAISettings, str, bool], tuple[str, str]],
    complete_description_pair: Callable[[str, str, str, OpenAISettings, str, bool], tuple[str, str]],
) -> SearchProfileRecord:
    if profile.profile_id is None:
        return profile

    name_zh, name_en = decode_bilingual_role_name(
        profile.role_name_i18n,
        fallback_name=current_name,
    )
    description_zh, description_en = decode_bilingual_description(profile.keyword_focus)

    needs_role_name_completion = not (name_zh.strip() and name_en.strip())
    has_any_description = bool(description_zh.strip() or description_en.strip())
    needs_description_completion = has_any_description and not (
        description_zh.strip() and description_en.strip()
    )
    if not needs_role_name_completion and not needs_description_completion:
        return profile

    completed_name_zh, completed_name_en = complete_role_name_pair(
        name_zh,
        name_en,
        settings,
        api_base_url,
        False,
    )
    canonical_name = completed_name_en or completed_name_zh or current_name
    completed_description_zh, completed_description_en = complete_description_pair(
        canonical_name,
        description_zh,
        description_en,
        settings,
        api_base_url,
        False,
    )

    updated_role_name_i18n = encode_bilingual_role_name(completed_name_zh, completed_name_en)
    updated_name = canonical_role_name(updated_role_name_i18n, current_name)
    if not updated_name:
        updated_name = untitled_label
    updated_target_role = updated_name
    if completed_description_zh or completed_description_en:
        updated_keyword_focus = encode_bilingual_description(
            completed_description_zh,
            completed_description_en,
        )
    else:
        updated_keyword_focus = str(profile.keyword_focus or "").strip()

    current_role_name_i18n = str(profile.role_name_i18n or "").strip()
    current_keyword_focus = str(profile.keyword_focus or "").strip()
    current_target_role = str(profile.target_role or "").strip()
    if (
        updated_role_name_i18n == current_role_name_i18n
        and updated_keyword_focus == current_keyword_focus
        and updated_name == current_name
        and updated_target_role == current_target_role
    ):
        return profile

    return SearchProfileRecord(
        profile_id=profile.profile_id,
        candidate_id=profile.candidate_id,
        name=updated_name,
        scope_profile=profile.scope_profile,
        target_role=updated_target_role,
        location_preference=profile.location_preference,
        role_name_i18n=updated_role_name_i18n,
        keyword_focus=updated_keyword_focus,
        is_active=profile.is_active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


__all__ = ["ensure_profile_bilingual_for_ui"]
