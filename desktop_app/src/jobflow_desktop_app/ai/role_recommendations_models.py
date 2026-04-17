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
    background_keywords: tuple[str, ...] = ()
    target_direction_keywords: tuple[str, ...] = ()
    core_business_areas: tuple[str, ...] = ()
    adjacent_business_areas: tuple[str, ...] = ()
    exploration_business_areas: tuple[str, ...] = ()
    avoid_business_areas: tuple[str, ...] = ()
    strong_capabilities: tuple[str, ...] = ()
    seniority_signals: tuple[str, ...] = ()

    def is_usable(self) -> bool:
        return bool(
            self.core_business_areas
            or self.adjacent_business_areas
            or self.exploration_business_areas
            or self.background_keywords
            or self.target_direction_keywords
            or self.strong_capabilities
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_signature": self.source_signature,
            "summary": self.summary,
            "background_keywords": list(self.background_keywords),
            "target_direction_keywords": list(self.target_direction_keywords),
            "core_business_areas": list(self.core_business_areas),
            "adjacent_business_areas": list(self.adjacent_business_areas),
            "exploration_business_areas": list(self.exploration_business_areas),
            "avoid_business_areas": list(self.avoid_business_areas),
            "strong_capabilities": list(self.strong_capabilities),
            "seniority_signals": list(self.seniority_signals),
        }

    def company_discovery_phrase_library_en(self) -> tuple[str, ...]:
        return _normalize_phrase_library_list(
            [
                *self.target_direction_keywords,
                *self.background_keywords,
                *self.core_business_areas,
                *self.strong_capabilities,
                *self.adjacent_business_areas,
                *self.exploration_business_areas,
            ],
            max_items=100,
        )

    def job_search_phrase_library_en(self) -> tuple[str, ...]:
        return _normalize_phrase_library_list(
            [
                *self.target_direction_keywords,
                *self.background_keywords,
                *self.core_business_areas,
                *self.strong_capabilities,
                *self.adjacent_business_areas,
                *self.exploration_business_areas,
            ],
            max_items=100,
        )


__all__ = [
    "CandidateSemanticProfile",
    "ResumeReadResult",
    "RoleRecommendationError",
    "TargetRoleSuggestion",
]
