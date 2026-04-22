from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...ai.role_recommendations import (
    encode_bilingual_description,
    encode_bilingual_role_name,
)
from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord


@dataclass(frozen=True)
class PreparedProfileContent:
    canonical_name: str
    role_name_i18n: str
    keyword_focus: str
    is_generic: bool


def prepare_profile_content(
    *,
    role_name_zh: str,
    role_name_en: str,
    description_zh: str,
    description_en: str,
    fallback_name: str,
    untitled_label: str,
    canonical_role_name: Callable[[str, str], str],
    is_generic_role_name: Callable[[str], bool],
) -> PreparedProfileContent:
    role_name_i18n = encode_bilingual_role_name(role_name_zh, role_name_en)
    canonical_name = canonical_role_name(role_name_i18n, fallback_name)
    if not canonical_name:
        canonical_name = untitled_label
    keyword_focus = (
        encode_bilingual_description(description_zh, description_en)
        if (description_zh or description_en)
        else ""
    )
    return PreparedProfileContent(
        canonical_name=canonical_name,
        role_name_i18n=role_name_i18n,
        keyword_focus=keyword_focus,
        is_generic=is_generic_role_name(canonical_name),
    )


def build_new_profile_record(
    *,
    candidate: CandidateRecord,
    scope_profile: str,
    prepared: PreparedProfileContent,
    is_active: bool,
) -> SearchProfileRecord:
    return SearchProfileRecord(
        profile_id=None,
        candidate_id=int(candidate.candidate_id or 0),
        name=prepared.canonical_name,
        scope_profile=scope_profile,
        target_role=prepared.canonical_name,
        location_preference=candidate.preferred_locations,
        role_name_i18n=prepared.role_name_i18n,
        keyword_focus=prepared.keyword_focus,
        is_active=is_active,
    )


def build_updated_profile_record(
    *,
    profile_id: int,
    candidate: CandidateRecord,
    existing_profile: SearchProfileRecord | None,
    prepared: PreparedProfileContent,
    is_active: bool,
) -> SearchProfileRecord:
    return SearchProfileRecord(
        profile_id=profile_id,
        candidate_id=int(candidate.candidate_id or 0),
        name=prepared.canonical_name,
        scope_profile=existing_profile.scope_profile if existing_profile is not None else "",
        target_role=prepared.canonical_name,
        location_preference=(
            existing_profile.location_preference
            if existing_profile is not None and existing_profile.location_preference.strip()
            else candidate.preferred_locations
        ),
        role_name_i18n=prepared.role_name_i18n,
        keyword_focus=prepared.keyword_focus,
        is_active=is_active,
    )


__all__ = [
    "PreparedProfileContent",
    "build_new_profile_record",
    "build_updated_profile_record",
    "prepare_profile_content",
]
