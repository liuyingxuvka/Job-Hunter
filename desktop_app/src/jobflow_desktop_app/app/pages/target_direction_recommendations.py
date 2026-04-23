from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...ai.role_recommendations import (
    ADJACENT_SCOPE,
    CORE_SCOPE,
    EXPLORATORY_SCOPE,
    RoleRecommendationMixPlan,
    TargetRoleSuggestion,
    description_for_prompt,
    encode_bilingual_description,
    encode_bilingual_role_name,
    normalize_scope_profile,
    role_name_query_lines,
    select_bilingual_role_name,
)
from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord


@dataclass(frozen=True)
class AppliedSuggestionResult:
    added_names: tuple[str, ...]
    last_profile_id: int | None


RECOMMENDATION_TOTAL_CAP = 12
_BASE_ROLE_TARGETS = {
    CORE_SCOPE: 3,
    ADJACENT_SCOPE: 2,
    EXPLORATORY_SCOPE: 1,
}
_EXTENDED_ROLE_TARGETS = {
    CORE_SCOPE: 6,
    ADJACENT_SCOPE: 4,
    EXPLORATORY_SCOPE: 2,
}
_SCOPE_PRIORITY = {
    EXPLORATORY_SCOPE: 3,
    ADJACENT_SCOPE: 2,
    CORE_SCOPE: 1,
}


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


def scope_profile_label(scope_profile: str, ui_language: str) -> str:
    normalized = normalize_scope_profile(scope_profile)
    if normalized == CORE_SCOPE:
        return "Core" if ui_language == "en" else "核心"
    if normalized == ADJACENT_SCOPE:
        return "Adjacent" if ui_language == "en" else "相邻"
    if normalized == EXPLORATORY_SCOPE:
        return "Exploratory" if ui_language == "en" else "探索"
    return ""


def build_role_recommendation_mix_plan(
    profiles: list[SearchProfileRecord],
    *,
    total_cap: int = RECOMMENDATION_TOTAL_CAP,
) -> RoleRecommendationMixPlan:
    current_core = 0
    current_adjacent = 0
    current_exploratory = 0
    for profile in profiles:
        normalized = normalize_scope_profile(profile.scope_profile)
        if normalized == CORE_SCOPE:
            current_core += 1
        elif normalized == ADJACENT_SCOPE:
            current_adjacent += 1
        elif normalized == EXPLORATORY_SCOPE:
            current_exploratory += 1

    current_total = len(profiles)
    remaining_capacity = max(0, total_cap - current_total)
    request_counts = {
        CORE_SCOPE: 0,
        ADJACENT_SCOPE: 0,
        EXPLORATORY_SCOPE: 0,
    }
    current_counts = {
        CORE_SCOPE: current_core,
        ADJACENT_SCOPE: current_adjacent,
        EXPLORATORY_SCOPE: current_exploratory,
    }
    slots_left = remaining_capacity
    base_reached = all(current_counts[scope] >= _BASE_ROLE_TARGETS[scope] for scope in request_counts)
    stage_targets = _EXTENDED_ROLE_TARGETS if (current_total >= 6 and base_reached) else _BASE_ROLE_TARGETS
    deficits = {
        scope: max(0, stage_targets[scope] - current_counts[scope])
        for scope in request_counts
    }
    while slots_left > 0 and any(deficits.values()):
        ranked_scope = max(
            deficits,
            key=lambda scope: (
                deficits[scope] / max(1, stage_targets[scope]),
                _SCOPE_PRIORITY[scope],
            ),
        )
        if deficits[ranked_scope] <= 0:
            break
        request_counts[ranked_scope] += 1
        deficits[ranked_scope] -= 1
        slots_left -= 1

    return RoleRecommendationMixPlan(
        total_cap=total_cap,
        current_total=current_total,
        current_core=current_core,
        current_adjacent=current_adjacent,
        current_exploratory=current_exploratory,
        request_core=request_counts[CORE_SCOPE],
        request_adjacent=request_counts[ADJACENT_SCOPE],
        request_exploratory=request_counts[EXPLORATORY_SCOPE],
    )


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
                scope_profile=normalize_scope_profile(suggestion.scope_profile),
                target_role=canonical_name,
                location_preference=candidate.preferred_locations,
                role_name_i18n=suggestion_name_i18n,
                keyword_focus=encode_bilingual_description(
                    suggestion.description_zh,
                    suggestion.description_en,
                ),
                is_active=True,
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
    "build_role_recommendation_mix_plan",
    "scope_profile_label",
]
