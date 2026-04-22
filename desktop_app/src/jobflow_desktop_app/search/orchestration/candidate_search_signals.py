from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...ai.role_recommendations import (
    CandidateSemanticProfile,
    description_query_lines,
    role_name_query_lines,
)
from ...db.repositories.profiles import SearchProfileRecord


@dataclass(frozen=True)
class SemanticSearchSignals:
    summary: str = ""
    company_discovery_primary_anchors: list[str] = field(default_factory=list)
    company_discovery_secondary_anchors: list[str] = field(default_factory=list)
    job_fit_core_terms: list[str] = field(default_factory=list)
    job_fit_support_terms: list[str] = field(default_factory=list)
    avoid_business_areas: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileSearchSignals:
    role_names: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)
    keyword_focus_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateSearchSignals:
    semantic: SemanticSearchSignals
    profile: ProfileSearchSignals

    def core_discovery_terms(self) -> list[str]:
        return dedup_text(
            [
                *self.semantic.company_discovery_primary_anchors,
                *self.profile.keyword_focus_terms,
                *self.profile.target_roles,
                *self.semantic.job_fit_core_terms,
            ],
            limit=40,
        )

    def adjacent_discovery_terms(self) -> list[str]:
        return dedup_text(
            [
                *self.semantic.company_discovery_secondary_anchors,
                *self.profile.role_names,
                *self.semantic.job_fit_support_terms,
            ],
            limit=40,
        )

    def explore_discovery_terms(self) -> list[str]:
        return []


def resolve_candidate_search_signals(
    *,
    profiles: list[SearchProfileRecord],
    semantic_profile: CandidateSemanticProfile | None,
    signals: CandidateSearchSignals | None,
) -> CandidateSearchSignals:
    if signals is not None:
        return signals
    return collect_candidate_search_signals(
        profiles=profiles,
        semantic_profile=semantic_profile,
    )
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
            company_discovery_primary_anchors=[],
            company_discovery_secondary_anchors=[],
            job_fit_core_terms=[],
            job_fit_support_terms=[],
            avoid_business_areas=[],
        )
    return SemanticSearchSignals(
        summary=str(semantic_profile.summary or "").strip(),
        company_discovery_primary_anchors=dedup_text(
            list(getattr(semantic_profile, "company_discovery_primary_anchors", ())),
            limit=16,
        ),
        company_discovery_secondary_anchors=dedup_text(
            list(getattr(semantic_profile, "company_discovery_secondary_anchors", ())),
            limit=12,
        ),
        job_fit_core_terms=dedup_text(list(semantic_profile.job_fit_core_terms), limit=30),
        job_fit_support_terms=dedup_text(list(semantic_profile.job_fit_support_terms), limit=20),
        avoid_business_areas=dedup_text(list(semantic_profile.avoid_business_areas), limit=20),
    )
def _collect_profile_search_signals(profiles: list[SearchProfileRecord]) -> ProfileSearchSignals:
    role_names: list[str] = []
    target_roles: list[str] = []
    keyword_focus_terms: list[str] = []
    for profile in profiles:
        role_names.extend(
            role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name)
        )
        target_roles.extend(split_multivalue_text(profile.target_role))
        keyword_focus_terms.extend(description_query_lines(profile.keyword_focus))
    return ProfileSearchSignals(
        role_names=dedup_text(role_names, limit=20),
        target_roles=dedup_text(target_roles, limit=20),
        keyword_focus_terms=dedup_text(keyword_focus_terms, limit=30),
    )


def collect_candidate_search_signals(
    *,
    profiles: list[SearchProfileRecord],
    semantic_profile: CandidateSemanticProfile | None,
) -> CandidateSearchSignals:
    active_profiles = [profile for profile in profiles if profile.is_active]
    source = active_profiles if active_profiles else profiles

    return CandidateSearchSignals(
        semantic=_collect_semantic_search_signals(semantic_profile),
        profile=_collect_profile_search_signals(source),
    )


__all__ = [
    "CandidateSearchSignals",
    "ProfileSearchSignals",
    "SemanticSearchSignals",
    "collect_candidate_search_signals",
    "dedup_text",
    "resolve_candidate_search_signals",
    "split_multivalue_text",
]
