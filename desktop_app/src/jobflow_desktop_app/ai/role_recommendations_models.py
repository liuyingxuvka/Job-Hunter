from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _normalize_phrase_library_list(
    raw_values: list[str] | tuple[str, ...],
    *,
    max_items: int,
    max_length: int = 72,
) -> tuple[str, ...]:
    if not isinstance(raw_values, (list, tuple)):
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = " ".join(str(item or "").split()).strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")
        if not text:
            continue
        if len(text) > max_length:
            text = text[:max_length].rstrip(" ,;|，；、。.!?：:()[]{}<>\"'`")
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
        if len(ordered) >= max_items:
            break
    return tuple(ordered)


@dataclass(frozen=True)
class TargetRoleSuggestion:
    name: str
    description_zh: str
    description_en: str
    scope_profile: str = ""
    name_zh: str = ""
    name_en: str = ""

    @property
    def description(self) -> str:
        return self.description_zh or self.description_en


@dataclass(frozen=True)
class RoleRecommendationMixPlan:
    total_cap: int = 12
    current_total: int = 0
    current_core: int = 0
    current_adjacent: int = 0
    current_exploratory: int = 0
    request_core: int = 0
    request_adjacent: int = 0
    request_exploratory: int = 0

    @property
    def request_total(self) -> int:
        return self.request_core + self.request_adjacent + self.request_exploratory

    @property
    def remaining_capacity(self) -> int:
        return max(0, self.total_cap - self.current_total)


class RoleRecommendationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResumeReadResult:
    text: str = ""
    error: str = ""
    source_type: str = ""


@dataclass(frozen=True)
class CandidateSemanticProfile:
    source_signature: str = ""
    summary: str = ""
    company_discovery_primary_anchors: tuple[str, ...] = ()
    company_discovery_secondary_anchors: tuple[str, ...] = ()
    job_fit_core_terms: tuple[str, ...] = ()
    job_fit_support_terms: tuple[str, ...] = ()
    avoid_business_areas: tuple[str, ...] = ()

    def is_usable(self) -> bool:
        return bool(
            self.summary
            or self.company_discovery_primary_anchors
            or self.company_discovery_secondary_anchors
            or self.job_fit_core_terms
            or self.job_fit_support_terms
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_signature": self.source_signature,
            "summary": self.summary,
            "company_discovery_primary_anchors": list(self.company_discovery_primary_anchors),
            "company_discovery_secondary_anchors": list(self.company_discovery_secondary_anchors),
            "job_fit_core_terms": list(self.job_fit_core_terms),
            "job_fit_support_terms": list(self.job_fit_support_terms),
            "avoid_business_areas": list(self.avoid_business_areas),
        }

    def company_discovery_phrase_library_en(self) -> tuple[str, ...]:
        return _normalize_phrase_library_list(
            [
                *self.company_discovery_primary_anchors,
                *self.company_discovery_secondary_anchors,
                *self.job_fit_core_terms,
                *self.job_fit_support_terms,
            ],
            max_items=100,
        )

    def job_search_phrase_library_en(self) -> tuple[str, ...]:
        return _normalize_phrase_library_list(
            [
                *self.job_fit_core_terms,
                *self.job_fit_support_terms,
            ],
            max_items=100,
        )


__all__ = [
    "CandidateSemanticProfile",
    "RoleRecommendationMixPlan",
    "ResumeReadResult",
    "RoleRecommendationError",
    "TargetRoleSuggestion",
]
