from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


TOOL_ONLY_TERMS = {
    "python",
    "sql",
    "excel",
    "power bi",
    "powerbi",
    "tableau",
}
COMPANY_CATEGORY_TERMS = frozenset(
    {
        "agency",
        "agencies",
        "company",
        "companies",
        "employer",
        "employers",
        "firm",
        "firms",
        "provider",
        "providers",
        "vendor",
        "vendors",
    }
)


@dataclass(frozen=True)
class CandidateCompanyFocus:
    role_phrases: tuple[str, ...]
    core_terms: tuple[str, ...]
    support_terms: tuple[str, ...]

    def primary_focus_terms(self, limit: int = 3) -> list[str]:
        selected: list[str] = []
        for group in (self.role_phrases, self.core_terms, self.support_terms):
            for item in group:
                if _overlaps_existing(item, selected):
                    continue
                selected.append(item)
                if len(selected) >= limit:
                    return selected
        return selected

    def fallback_query_terms(self) -> list[str]:
        selected: list[str] = []
        if self.role_phrases:
            selected.append(self.role_phrases[0])
        for group in (self.core_terms, self.support_terms):
            for item in group:
                if _overlaps_existing(item, selected):
                    continue
                selected.append(item)
                if len(selected) >= 2:
                    return selected
        return selected

    def job_search_terms(self, limit: int = 3) -> list[str]:
        selected: list[str] = []
        if self.role_phrases:
            selected.append(self.role_phrases[0])
        for group in (self.core_terms, self.support_terms):
            for item in group:
                if _overlaps_existing(item, selected) or _looks_like_company_category_term(item):
                    continue
                selected.append(item)
                if len(selected) >= limit:
                    return selected
        if selected:
            return selected
        return self.fallback_query_terms()

    def business_focus_summary(self, limit: int = 3) -> str:
        return " ; ".join(self.primary_focus_terms(limit=limit))


def build_candidate_company_focus(
    candidate: Mapping[str, Any] | None,
    *,
    fit_terms: Mapping[str, Any] | None = None,
) -> CandidateCompanyFocus:
    payload = dict(candidate or {}) if isinstance(candidate, Mapping) else {}
    normalized_fit_terms = dict(fit_terms or {}) if isinstance(fit_terms, Mapping) else {}
    semantic = dict(payload.get("semanticProfile") or {}) if isinstance(payload.get("semanticProfile"), Mapping) else {}
    return CandidateCompanyFocus(
        role_phrases=tuple(_candidate_role_phrases(payload)),
        core_terms=tuple(
            _payload_terms(normalized_fit_terms, "core")
            or _normalize_terms(
                [
                    *_semantic_terms(semantic, "job_fit_core_terms"),
                    *_candidate_role_descriptions(payload),
                ],
                limit=16,
                drop_tool_only=False,
            )
        ),
        support_terms=tuple(
            _payload_terms(normalized_fit_terms, "support")
            or _normalize_terms(
                [
                    *_semantic_terms(semantic, "job_fit_support_terms"),
                ],
                limit=10,
                drop_tool_only=False,
            )
        ),
    )


def build_candidate_company_focus_from_config(
    config: Mapping[str, Any] | None,
) -> CandidateCompanyFocus:
    candidate = dict(config.get("candidate") or {}) if isinstance(config, Mapping) else {}
    sources = dict(config.get("sources") or {}) if isinstance(config, Mapping) else {}
    fit_terms = sources.get("companyFitTerms")
    return build_candidate_company_focus(candidate, fit_terms=fit_terms if isinstance(fit_terms, Mapping) else None)


def company_candidate_evidence_text(company: Mapping[str, Any]) -> str:
    tags = " ".join(
        str(item or "").strip()
        for item in company.get("tags", [])
        if str(item or "").strip()
    )
    web_search = company.get("sourceEvidence")
    web_search_query = ""
    if isinstance(web_search, Mapping):
        web_payload = web_search.get("webSearch")
        if isinstance(web_payload, Mapping):
            web_search_query = str(web_payload.get("query") or "").strip()
    return " ".join(
        part
        for part in (
            str(company.get("name") or "").strip(),
            tags,
            web_search_query,
        )
        if part
    )


def _candidate_role_phrases(candidate: Mapping[str, Any]) -> list[str]:
    target_roles = candidate.get("targetRoles")
    if isinstance(target_roles, list):
        role_values: list[str] = []
        for raw in target_roles:
            if not isinstance(raw, Mapping):
                continue
            role_values.append(
                str(
                    raw.get("displayName")
                    or raw.get("targetRoleText")
                    or raw.get("nameEn")
                    or raw.get("nameZh")
                    or ""
                ).strip()
            )
        normalized = _normalize_terms(role_values, limit=8, drop_tool_only=False)
        if normalized:
            return normalized
    return []


def _candidate_role_descriptions(candidate: Mapping[str, Any]) -> list[str]:
    target_roles = candidate.get("targetRoles")
    descriptions: list[str] = []
    if isinstance(target_roles, list):
        for raw in target_roles:
            if not isinstance(raw, Mapping):
                continue
            descriptions.append(str(raw.get("descriptionEn") or raw.get("descriptionZh") or "").strip())
    return _normalize_terms(descriptions, limit=8, drop_tool_only=True)


def _semantic_terms(semantic: Mapping[str, Any], key: str) -> list[str]:
    raw_values = semantic.get(key)
    if isinstance(raw_values, Sequence) and not isinstance(raw_values, (str, bytes)):
        return [str(item or "").strip() for item in raw_values if str(item or "").strip()]
    return []


def _payload_terms(payload: Mapping[str, Any], key: str) -> list[str]:
    raw_values = payload.get(key)
    if isinstance(raw_values, Sequence) and not isinstance(raw_values, (str, bytes)):
        return _normalize_terms([str(item or "").strip() for item in raw_values], limit=16)
    return []


def _normalize_terms(
    values: Sequence[str],
    *,
    limit: int,
    drop_tool_only: bool = False,
    keep_tool_only: bool = False,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = _clean_phrase(raw)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        is_tool_only = key in TOOL_ONLY_TERMS
        if drop_tool_only and is_tool_only:
            continue
        if keep_tool_only and not is_tool_only:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _clean_phrase(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text:
        return ""
    if "|" in text:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        if parts:
            latin_parts = [part for part in parts if re.search(r"[A-Za-z]", part)]
            text = latin_parts[0] if latin_parts else parts[0]
    text = text.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")
    if not text or len(text) > 96:
        return ""
    return text


def _overlaps_existing(candidate: str, existing: Sequence[str]) -> bool:
    normalized_candidate = candidate.casefold()
    return any(
        normalized_candidate in current.casefold() or current.casefold() in normalized_candidate
        for current in existing
    )


def _looks_like_company_category_term(term: str) -> bool:
    normalized_tokens = [token for token in _clean_phrase(term).casefold().split() if token]
    if not normalized_tokens:
        return False
    return normalized_tokens[-1] in COMPANY_CATEGORY_TERMS


__all__ = [
    "CandidateCompanyFocus",
    "build_candidate_company_focus",
    "build_candidate_company_focus_from_config",
    "company_candidate_evidence_text",
]
