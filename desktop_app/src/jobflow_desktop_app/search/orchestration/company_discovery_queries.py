from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from .candidate_search_signals import (
    CandidateSearchSignals,
    dedup_text,
)


DISCOVERY_BUCKETS = ("core", "adjacent")

DISCOVERY_BUCKET_RULES = {
    "core": {
        "phrase_limit": 45,
        "query_limit": 4,
        "minimum_anchor_count": 2,
    },
    "adjacent": {
        "phrase_limit": 30,
        "query_limit": 2,
        "minimum_anchor_count": 1,
    },
}

DEFAULT_SCOPE_ANCHORS: dict[str, dict[str, list[str]]] = {}

TOOL_ONLY_DISCOVERY_TERMS = {
    "python",
    "sql",
    "excel",
    "power bi",
    "powerbi",
    "tableau",
}

DISCOVERY_CONTEXT_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "of",
    "in",
    "on",
    "to",
    "a",
    "an",
    "global",
    "cross",
    "functional",
}

DISCOVERY_GENERIC_SUPPORT_TOKENS = {
    "vendor",
    "management",
    "operations",
    "operation",
    "governance",
    "program",
    "programs",
    "performance",
    "workflow",
    "workflows",
    "process",
    "processes",
    "quality",
    "assurance",
    "leadership",
    "coordination",
    "delivery",
    "support",
    "execution",
}

BUSINESS_COMPANY_QUERY_TEMPLATES = (
    "{anchor} companies",
    "{anchor} employers",
)


@dataclass(frozen=True)
class DiscoveryAnchorPlan:
    core: list[str]
    adjacent: list[str]


def _bucket_rule(bucket: str, key: str) -> int:
    return int(DISCOVERY_BUCKET_RULES[bucket][key])


def _bucket_seed_offset(bucket: str) -> int:
    bucket_index = DISCOVERY_BUCKETS.index(bucket)
    return 11 + bucket_index * 12 + max(0, bucket_index - 1) * 2


def discovery_query_limit() -> int:
    return sum(_bucket_rule(bucket, "query_limit") for bucket in DISCOVERY_BUCKETS)


def _resolved_scope_profiles(scope_profiles: Sequence[str] | None) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in scope_profiles or ():
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    return tuple(values)


def default_anchor_buckets(scope_profiles: Sequence[str] | None) -> dict[str, list[str]]:
    del scope_profiles
    return {bucket: [] for bucket in DISCOVERY_BUCKETS}


def looks_like_role_phrase(text: str) -> bool:
    value = str(text or "").strip().casefold()
    if not value:
        return False
    return bool(re.search(r"\b(engineer|scientist|manager|specialist|analyst|developer|designer|intern|lead|director)\b|工程师|科学家|经理|专家|分析师|开发|总监", value))


def normalize_business_hint(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if not value:
        return ""
    value = value.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")
    value = re.sub(r"^(focus(?:ed)? on|speciali[sz](?:e|ed|ing) in|work(?:ed|ing)? on|experience in|background in|expertise in|interested in|related to|around)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(and|with|for|toward|towards|future|target|desired|adjacent|explor(?:e|ation))\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(聚焦(?:于)?|专注(?:于)?|从事|擅长|熟悉|研究方向(?:为)?|研究|方向(?:为)?|相关(?:方向|领域)?|主要(?:方向|关注|做)?|涉及|偏向|偏重|以及|还有|并|和|未来(?:方向)?|目标(?:方向)?|邻近(?:方向)?|相邻(?:方向)?|探索(?:方向)?)\s*", "", value)
    value = re.sub(r"\b(companies|company|employers|employer|careers|career|jobs|job)\b$", "", value, flags=re.IGNORECASE).strip()
    return value.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")


def looks_like_business_noise(text: str) -> bool:
    value = str(text or "").strip().casefold()
    if not value or len(value) < 3 or len(value.split()) > 10:
        return True
    if value in TOOL_ONLY_DISCOVERY_TERMS:
        return True
    return bool(re.search(r"looking for|prefer|seeking|resume|curriculum vitae|\bcv\b|\bjob\b|博士背景|硕士背景|工作经历|专业背景|希望|想找|想做|保留|简历|候选人", value))


def normalize_discovery_terms(values: Sequence[str], *, limit: int) -> list[str]:
    filtered: list[str] = []
    for hint in values:
        text = normalize_business_hint(hint)
        if not text or len(text) > 72 or looks_like_role_phrase(text) or looks_like_business_noise(text):
            continue
        filtered.append(text)
    return dedup_text(filtered, limit=limit)


def is_avoided_anchor(anchor: str, avoid_terms: list[str]) -> bool:
    normalized_anchor = normalize_business_hint(anchor).casefold()
    if not normalized_anchor:
        return False
    for raw_term in avoid_terms:
        normalized_term = normalize_business_hint(raw_term).casefold()
        if not normalized_term:
            continue
        if normalized_term in normalized_anchor or normalized_anchor in normalized_term:
            return True
    return False

def build_discovery_anchor_plan(
    *,
    scope_profiles: Sequence[str] | None,
    signals: CandidateSearchSignals,
) -> DiscoveryAnchorPlan:
    defaults = default_anchor_buckets(scope_profiles)
    avoid_terms = dedup_text(
        [
            normalize_business_hint(item)
            for item in signals.semantic.avoid_business_areas
        ],
        limit=16,
    )
    buckets = {
        "core": normalize_discovery_terms(
            signals.core_discovery_terms(),
            limit=_bucket_rule("core", "phrase_limit"),
        ),
        "adjacent": normalize_discovery_terms(
            signals.adjacent_discovery_terms(),
            limit=_bucket_rule("adjacent", "phrase_limit"),
        ),
    }
    normalized = {
        bucket: dedup_text(
            [
                item
                for item in buckets[bucket]
                if not is_avoided_anchor(item, avoid_terms)
            ],
            limit=_bucket_rule(bucket, "phrase_limit"),
        )
        for bucket in DISCOVERY_BUCKETS
    }
    for bucket in DISCOVERY_BUCKETS:
        values = normalized[bucket]
        if values:
            normalized[bucket] = values[: _bucket_rule(bucket, "phrase_limit")]
            continue
        for default_value in defaults.get(bucket, []):
            if len(values) >= _bucket_rule(bucket, "minimum_anchor_count"):
                break
            if is_avoided_anchor(default_value, avoid_terms):
                continue
            if default_value.casefold() not in {item.casefold() for item in values}:
                values.append(default_value)
        normalized[bucket] = values[: _bucket_rule(bucket, "phrase_limit")]
    return DiscoveryAnchorPlan(core=normalized["core"], adjacent=normalized["adjacent"])

def rotate_list(values: list[str], seed: int) -> list[str]:
    if not values:
        return []
    offset = abs(int(seed)) % len(values)
    return list(values[offset:]) + list(values[:offset])


def discovery_query_bucket_order(limit: int) -> list[str]:
    if limit <= 0:
        return []
    total_limit = discovery_query_limit()
    weighted_positions: list[tuple[float, int, str]] = []
    for bucket_index, bucket in enumerate(DISCOVERY_BUCKETS):
        query_limit = _bucket_rule(bucket, "query_limit")
        if query_limit <= 0:
            continue
        interval = total_limit / query_limit
        for slot_index in range(query_limit):
            weighted_positions.append(
                ((slot_index + 0.5) * interval, bucket_index, bucket)
            )
    weighted_positions.sort()
    return [bucket for _, _, bucket in weighted_positions[:limit]]


def normalize_company_discovery_query(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text:
        return ""
    if len(text) > 200:
        text = text[:200].strip()
    return text


def generate_bucket_queries(*, anchors: list[str], templates: list[str], limit: int, seed: int, normalize: Callable[[str], str]) -> list[str]:
    if limit <= 0 or not anchors or not templates:
        return []
    ordered_anchors = dedup_text(anchors, limit=32)
    rotated_templates = rotate_list(list(templates), seed // 7 if seed else 0)
    queries: list[str] = []
    seen: set[str] = set()
    max_attempts = max(limit * 8, len(ordered_anchors) * len(rotated_templates))
    for attempt in range(max_attempts):
        anchor = ordered_anchors[attempt % len(ordered_anchors)]
        template = rotated_templates[(attempt // max(1, len(ordered_anchors))) % len(rotated_templates)]
        normalized = normalize(template.format(anchor=anchor))
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        queries.append(normalized)
        if len(queries) >= limit:
            break
    return queries


def _discovery_context_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").casefold())
        if len(token) > 2 and token not in DISCOVERY_CONTEXT_STOPWORDS
    }


def _domain_context_phrase(text: str) -> str:
    normalized = normalize_business_hint(text)
    if not normalized:
        return ""
    parts = [part for part in re.split(r"\s+", normalized) if part]
    focused_parts = [
        part
        for part in parts
        if part.casefold() not in DISCOVERY_GENERIC_SUPPORT_TOKENS
    ]
    if not focused_parts:
        return normalized
    return normalize_business_hint(" ".join(focused_parts))


def _contextualize_adjacent_anchor(anchor: str, core_anchors: list[str]) -> str:
    normalized_anchor = normalize_business_hint(anchor)
    if not normalized_anchor:
        return ""
    anchor_tokens = _discovery_context_tokens(normalized_anchor)
    if not anchor_tokens or not core_anchors:
        return normalized_anchor
    preferred_context = ""
    for core_anchor in core_anchors:
        core_tokens = _discovery_context_tokens(core_anchor)
        overlap = core_tokens & anchor_tokens
        if not overlap:
            continue
        if any(token not in DISCOVERY_GENERIC_SUPPORT_TOKENS for token in overlap):
            return normalized_anchor
        if not preferred_context:
            preferred_context = _domain_context_phrase(core_anchor)
    context_anchor = preferred_context or next(
        (_domain_context_phrase(item) for item in core_anchors if _domain_context_phrase(item)),
        normalize_business_hint(core_anchors[0]),
    )
    combined = normalize_business_hint(f"{context_anchor} {normalized_anchor}")
    if not combined or len(combined) > 72:
        return normalized_anchor
    return combined


def generate_discovery_query_plan(*, anchor_plan: DiscoveryAnchorPlan, templates: tuple[str, ...], limit: int, seed: int, normalize: Callable[[str], str]) -> list[str]:
    bucket_queries = {
        bucket: generate_bucket_queries(
            anchors=getattr(anchor_plan, bucket),
            templates=list(templates),
            limit=max(limit, _bucket_rule(bucket, "query_limit")),
            seed=seed + _bucket_seed_offset(bucket),
            normalize=normalize,
        )
        for bucket in DISCOVERY_BUCKETS
    }
    indices = {bucket: 0 for bucket in DISCOVERY_BUCKETS}
    planned: list[str] = []
    seen: set[str] = set()
    bucket_order = discovery_query_bucket_order(discovery_query_limit())
    for bucket in bucket_order:
        bucket_list = bucket_queries.get(bucket, [])
        if indices[bucket] >= len(bucket_list):
            continue
        value = bucket_list[indices[bucket]]
        indices[bucket] += 1
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        planned.append(value)
        if len(planned) >= limit:
            break
    while len(planned) < limit:
        added_in_pass = False
        for bucket in bucket_order:
            bucket_list = bucket_queries.get(bucket, [])
            if indices[bucket] >= len(bucket_list):
                continue
            value = bucket_list[indices[bucket]]
            indices[bucket] += 1
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            planned.append(value)
            added_in_pass = True
            if len(planned) >= limit:
                break
        if not added_in_pass:
            break
    return planned


def build_company_discovery_queries_from_anchor_plan(
    *,
    anchor_plan: DiscoveryAnchorPlan,
    rotation_seed: int = 0,
) -> list[str]:
    adjusted_plan = DiscoveryAnchorPlan(
        core=list(anchor_plan.core),
        adjacent=[
            _contextualize_adjacent_anchor(anchor, list(anchor_plan.core))
            for anchor in anchor_plan.adjacent
        ],
    )
    dedup = generate_discovery_query_plan(
        anchor_plan=adjusted_plan,
        templates=BUSINESS_COMPANY_QUERY_TEMPLATES,
        limit=discovery_query_limit(),
        seed=rotation_seed,
        normalize=normalize_company_discovery_query,
    )
    if not dedup:
        dedup = ["relevant companies", "target employers", "industry companies"]
    return dedup[: discovery_query_limit()]


__all__ = [
    "DISCOVERY_BUCKET_RULES",
    "DiscoveryAnchorPlan",
    "build_company_discovery_queries_from_anchor_plan",
    "build_discovery_anchor_plan",
    "discovery_query_bucket_order",
    "discovery_query_limit",
    "default_anchor_buckets",
    "generate_bucket_queries",
    "generate_discovery_query_plan",
    "is_avoided_anchor",
    "looks_like_business_noise",
    "looks_like_role_phrase",
    "normalize_discovery_terms",
    "normalize_business_hint",
    "normalize_company_discovery_query",
    "rotate_list",
]
