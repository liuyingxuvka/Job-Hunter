from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...ai.role_recommendations import (
    TargetRoleSuggestion,
    description_for_prompt,
    encode_bilingual_description,
    encode_bilingual_role_name,
    role_name_query_lines,
    select_bilingual_role_name,
)
from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord


@dataclass(frozen=True)
class AppliedSuggestionResult:
    added_names: tuple[str, ...]
    last_profile_id: int | None


def build_existing_role_context(
    profiles: list[SearchProfileRecord],
    *,
    canonical_role_name: Callable[[str, str], str],
) -> list[tuple[str, str]]:
    return [
        (
            canonical_name,
            description_for_prompt(profile.keyword_focus),
        )
        for profile in profiles
        if (canonical_name := canonical_role_name(profile.role_name_i18n, profile.name))
    ]


def apply_role_suggestions(
    suggestions: list[TargetRoleSuggestion],
    *,
    candidate: CandidateRecord,
    existing_profiles: list[SearchProfileRecord],
    save_profile: Callable[[SearchProfileRecord], int],
    canonical_role_name: Callable[[str, str], str],
    ui_language: str,
) -> AppliedSuggestionResult:
    existing_name_keys: set[str] = set()
    for profile in existing_profiles:
        for name_line in role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name):
            existing_name_keys.add(name_line.casefold())

    added_names: list[str] = []
    last_profile_id: int | None = None
    for suggestion in suggestions:
        suggestion_name_i18n = encode_bilingual_role_name(
            suggestion.name_zh,
            suggestion.name_en or suggestion.name,
        )
        suggestion_keys = {
            line.casefold()
            for line in role_name_query_lines(
                suggestion_name_i18n,
                fallback_name=suggestion.name,
            )
        }
        if suggestion_keys & existing_name_keys:
            continue
        canonical_name = canonical_role_name(
            suggestion_name_i18n,
            suggestion.name,
        )
        last_profile_id = save_profile(
            SearchProfileRecord(
                profile_id=None,
                candidate_id=int(candidate.candidate_id or 0),
                name=canonical_name,
                scope_profile=suggestion.scope_profile,
                target_role=canonical_name,
                location_preference=candidate.preferred_locations,
                company_focus="",
                company_keyword_focus="",
                role_name_i18n=suggestion_name_i18n,
                keyword_focus=encode_bilingual_description(
                    suggestion.description_zh,
                    suggestion.description_en,
                ),
                is_active=True,
                queries=[],
            )
        )
        existing_name_keys.update(suggestion_keys)
        added_names.append(
            select_bilingual_role_name(
                suggestion_name_i18n,
                ui_language,
                fallback_name=canonical_name,
            )
        )
    return AppliedSuggestionResult(tuple(added_names), last_profile_id)


__all__ = [
    "AppliedSuggestionResult",
    "apply_role_suggestions",
    "build_existing_role_context",
]
