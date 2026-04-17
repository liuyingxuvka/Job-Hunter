from __future__ import annotations

import re
from dataclasses import dataclass

from ...ai.role_recommendations import (
    CandidateSemanticProfile,
    load_resume_excerpt_result,
    role_name_query_lines,
)
from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord


@dataclass(frozen=True)
class SemanticSearchSignals:
    summary: str
    target_direction_keywords: list[str]
    background_keywords: list[str]
    core_business_areas: list[str]
    strong_capabilities: list[str]
    adjacent_business_areas: list[str]
    exploration_business_areas: list[str]
    avoid_business_areas: list[str]


@dataclass(frozen=True)
class CandidateInputSignals:
    target_directions: list[str]
    notes: list[str]


@dataclass(frozen=True)
class ProfileSearchSignals:
    role_names: list[str]
    target_roles: list[str]
    keyword_focus: list[str]
    company_focus: list[str]
    company_keyword_focus: list[str]
    queries: list[str]


@dataclass(frozen=True)
class CandidateSearchSignals:
    semantic: SemanticSearchSignals
    candidate: CandidateInputSignals
    profile: ProfileSearchSignals

    def semantic_core_anchor_terms(self) -> list[str]:
        return [
            *self.semantic.target_direction_keywords,
            *self.semantic.background_keywords,
            *self.semantic.core_business_areas,
            *self.semantic.strong_capabilities,
        ]

    def business_hint_terms(self) -> list[str]:
        return [
            *self.semantic.background_keywords,
            *self.semantic.target_direction_keywords,
            *self.semantic.strong_capabilities,
            *self.profile.company_focus,
            *self.profile.company_keyword_focus,
            *self.profile.keyword_focus,
            *self.profile.target_roles,
            *self.candidate.target_directions,
            *self.candidate.notes,
        ]

    def anchor_source_terms(self) -> list[str]:
        return [
            *self.business_hint_terms(),
            self.semantic.summary,
            *self.semantic.core_business_areas,
            *self.semantic.adjacent_business_areas,
            *self.semantic.exploration_business_areas,
            *self.profile.role_names,
            *self.profile.queries,
        ]


def resolve_candidate_search_signals(
    *,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    semantic_profile: CandidateSemanticProfile | None,
    signals: CandidateSearchSignals | None,
) -> CandidateSearchSignals:
    if signals is not None:
        return signals
    return collect_candidate_search_signals(
        candidate=candidate,
        profiles=profiles,
        semantic_profile=semantic_profile,
    )


def load_runtime_resume_text(resume_path: str, *, max_chars: int | None) -> str:
    excerpt = load_resume_excerpt_result(resume_path, max_chars=max_chars)
    return excerpt.text


def split_multivalue_text(raw: str) -> list[str]:
    text = str(raw or "")
    if not text.strip():
        return []
    tokens = re.split(r"[\n,;|，；、。]+", text)
    values: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        item = re.sub(r"\s+", " ", str(token or "").strip())
        if not item:
            continue
        if len(item) > 140:
            item = item[:140].strip()
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(item)
    return values


def dedup_text(values: list[str], limit: int = 40) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = re.sub(r"\s+", " ", str(raw or "").strip())
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _collect_semantic_search_signals(
    semantic_profile: CandidateSemanticProfile | None,
) -> SemanticSearchSignals:
    if semantic_profile is None or not semantic_profile.is_usable():
        return SemanticSearchSignals(
            summary="",
            target_direction_keywords=[],
            background_keywords=[],
            core_business_areas=[],
            strong_capabilities=[],
            adjacent_business_areas=[],
            exploration_business_areas=[],
            avoid_business_areas=[],
        )
    return SemanticSearchSignals(
        summary=str(semantic_profile.summary or "").strip(),
        target_direction_keywords=dedup_text(list(semantic_profile.target_direction_keywords), limit=40),
        background_keywords=dedup_text(list(semantic_profile.background_keywords), limit=40),
        core_business_areas=dedup_text(list(semantic_profile.core_business_areas), limit=40),
        strong_capabilities=dedup_text(list(semantic_profile.strong_capabilities), limit=40),
        adjacent_business_areas=dedup_text(list(semantic_profile.adjacent_business_areas), limit=30),
        exploration_business_areas=dedup_text(list(semantic_profile.exploration_business_areas), limit=20),
        avoid_business_areas=dedup_text(list(semantic_profile.avoid_business_areas), limit=20),
    )


def _collect_candidate_input_signals(candidate: CandidateRecord) -> CandidateInputSignals:
    return CandidateInputSignals(
        target_directions=dedup_text(split_multivalue_text(candidate.target_directions), limit=20),
        notes=dedup_text(split_multivalue_text(candidate.notes), limit=20),
    )


def _collect_profile_search_signals(profiles: list[SearchProfileRecord]) -> ProfileSearchSignals:
    role_names: list[str] = []
    target_roles: list[str] = []
    keyword_focus: list[str] = []
    company_focus: list[str] = []
    company_keyword_focus: list[str] = []
    queries: list[str] = []
    for profile in profiles:
        role_names.extend(
            role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name)
        )
        target_roles.extend(split_multivalue_text(profile.target_role))
        keyword_focus.extend(split_multivalue_text(profile.keyword_focus))
        company_focus.extend(split_multivalue_text(profile.company_focus))
        company_keyword_focus.extend(split_multivalue_text(profile.company_keyword_focus))
        queries.extend(split_multivalue_text("\n".join(profile.queries[:8])))
    return ProfileSearchSignals(
        role_names=dedup_text(role_names, limit=20),
        target_roles=dedup_text(target_roles, limit=20),
        keyword_focus=dedup_text(keyword_focus, limit=20),
        company_focus=dedup_text(company_focus, limit=20),
        company_keyword_focus=dedup_text(company_keyword_focus, limit=20),
        queries=dedup_text(queries, limit=24),
    )


def collect_candidate_search_signals(
    *,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    semantic_profile: CandidateSemanticProfile | None,
) -> CandidateSearchSignals:
    active_profiles = [profile for profile in profiles if profile.is_active]
    source = active_profiles if active_profiles else profiles

    return CandidateSearchSignals(
        semantic=_collect_semantic_search_signals(semantic_profile),
        candidate=_collect_candidate_input_signals(candidate),
        profile=_collect_profile_search_signals(source),
    )
__all__ = [
    "CandidateInputSignals",
    "CandidateSearchSignals",
    "ProfileSearchSignals",
    "SemanticSearchSignals",
    "collect_candidate_search_signals",
    "dedup_text",
    "load_runtime_resume_text",
    "resolve_candidate_search_signals",
    "split_multivalue_text",
]
