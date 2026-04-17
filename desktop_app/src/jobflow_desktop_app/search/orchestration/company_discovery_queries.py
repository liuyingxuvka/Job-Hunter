from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .candidate_search_signals import (
    CandidateSearchSignals,
    dedup_text,
)


DISCOVERY_BUCKETS = ("core", "adjacent", "explore")

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
    "explore": {
        "phrase_limit": 15,
        "query_limit": 1,
        "minimum_anchor_count": 1,
    },
}

MAINLINE_BUSINESS_ANCHORS = {
    "core": [
        ("hydrogen systems", r"\bhydrogen\b|\bh2\b|氢能|氢系统"),
        ("fuel cells", r"\bfuel cell\b|fuel-cell|燃料电池"),
        ("electrolyzers", r"\belectroly[sz]er\b|electrolysis|电解槽|制氢"),
        ("electrochemical diagnostics", r"electrochem|electrochemical|电化学|diagnostic|diagnostics|诊断"),
        ("degradation and aging", r"degradation|aging|老化|降解"),
        ("durability and reliability", r"durability|reliability|耐久|可靠性"),
        ("lifetime prediction", r"lifetime|remaining useful life|寿命预测|rul"),
        ("stack and balance-of-plant", r"\bstack\b|balance of plant|bop|堆|系统"),
        ("MEA and membrane materials", r"\bmea\b|membrane electrode|membrane|膜电极|膜|催化剂|catalyst"),
    ],
    "adjacent": [
        ("system validation and testing", r"validation|verification|test bench|testing|试验|验证|测试"),
        ("energy digitalization and PHM", r"digital twin|phm|condition monitoring|asset health|predictive maintenance|数字孪生|状态监测|健康管理"),
        ("industrial controls and automation", r"controls?|control systems?|automation|自动化|控制"),
        ("systems engineering and MBSE", r"mbse|systems engineering|sysml|requirements|traceability|系统工程|需求|可追溯"),
        ("technical diagnostics and monitoring", r"monitoring|diagnostics?|health management|监测|诊断|健康管理"),
    ],
    "explore": [
        ("battery aging and diagnostics", r"\bbattery\b|\bbms\b|储能|电池|soh|soc"),
        ("complex equipment reliability", r"complex equipment|industrial equipment|equipment reliability|高端装备|装备"),
        ("model-based diagnostics", r"model-based|simulation|parameter identification|calibration|建模|参数辨识|标定"),
        ("energy infrastructure platforms", r"energy infrastructure|industrial gas|grid|能源基础设施|工业气体"),
    ],
}

ADJACENT_SCOPE_BUSINESS_ANCHORS = {
    "core": [
        ("systems engineering", r"systems engineering|system engineer|系统工程"),
        ("MBSE and digital thread", r"mbse|sysml|digital thread|模型驱动|数字线程"),
        ("requirements and traceability", r"requirements|traceability|需求|可追溯"),
        ("verification and validation", r"verification|validation|v&v|验证|确认"),
        ("reliability and durability", r"reliability|durability|可靠性|耐久"),
        ("integration and qualification", r"integration|qualification|集成|鉴定"),
    ],
    "adjacent": [
        ("digital twin and PHM", r"digital twin|phm|condition monitoring|状态监测|健康管理"),
        ("industrial automation and controls", r"industrial automation|automation|controls?|工业自动化|控制"),
        ("complex equipment platforms", r"complex equipment|industrial equipment|装备|平台"),
        ("technical diagnostics", r"diagnostic|diagnostics|故障分析|诊断"),
    ],
    "explore": [
        ("automotive and powertrain systems", r"automotive|vehicle|powertrain|battery|bms|汽车|动力总成|电池"),
        ("energy infrastructure systems", r"energy infrastructure|grid|utility|能源基础设施|电网"),
        ("aerospace and high-end manufacturing", r"aerospace|manufacturing|航空航天|高端制造"),
    ],
}

DEFAULT_BUSINESS_ANCHORS = {
    "hydrogen_mainline": {
        "core": ["hydrogen systems", "fuel cells", "electrolyzers", "electrochemical diagnostics", "durability and reliability"],
        "adjacent": ["system validation and testing", "energy digitalization and PHM", "industrial controls and automation"],
        "explore": ["battery aging and diagnostics", "model-based diagnostics", "energy infrastructure platforms"],
    },
    "adjacent_mbse": {
        "core": ["systems engineering", "MBSE and digital thread", "requirements and traceability", "verification and validation"],
        "adjacent": ["digital twin and PHM", "industrial automation and controls", "technical diagnostics"],
        "explore": ["automotive and powertrain systems", "energy infrastructure systems", "aerospace and high-end manufacturing"],
    },
}

BUSINESS_COMPANY_QUERY_TEMPLATES = (
    "{anchor} companies",
    "{anchor} industrial technology companies",
)


@dataclass(frozen=True)
class DiscoveryAnchorPlan:
    core: list[str]
    adjacent: list[str]
    explore: list[str]


def _bucket_rule(bucket: str, key: str) -> int:
    return int(DISCOVERY_BUCKET_RULES[bucket][key])


def _bucket_seed_offset(bucket: str) -> int:
    bucket_index = DISCOVERY_BUCKETS.index(bucket)
    return 11 + bucket_index * 12 + max(0, bucket_index - 1) * 2


def discovery_query_limit() -> int:
    return sum(_bucket_rule(bucket, "query_limit") for bucket in DISCOVERY_BUCKETS)


def anchor_library(scope_profile: str) -> dict[str, list[tuple[str, str]]]:
    if scope_profile == "adjacent_mbse":
        return ADJACENT_SCOPE_BUSINESS_ANCHORS
    if scope_profile == "hydrogen_mainline":
        return MAINLINE_BUSINESS_ANCHORS
    merged: dict[str, list[tuple[str, str]]] = {}
    for bucket in DISCOVERY_BUCKETS:
        seen: set[tuple[str, str]] = set()
        values: list[tuple[str, str]] = []
        for label, pattern in MAINLINE_BUSINESS_ANCHORS.get(bucket, []) + ADJACENT_SCOPE_BUSINESS_ANCHORS.get(bucket, []):
            key = (label.casefold(), pattern)
            if key in seen:
                continue
            seen.add(key)
            values.append((label, pattern))
        merged[bucket] = values
    return merged


def default_anchor_buckets(scope_profile: str) -> dict[str, list[str]]:
    if scope_profile == "adjacent_mbse":
        return DEFAULT_BUSINESS_ANCHORS["adjacent_mbse"]
    if scope_profile == "hydrogen_mainline":
        return DEFAULT_BUSINESS_ANCHORS["hydrogen_mainline"]
    merged: dict[str, list[str]] = {}
    for bucket in DISCOVERY_BUCKETS:
        seen: set[str] = set()
        values: list[str] = []
        for label in DEFAULT_BUSINESS_ANCHORS["hydrogen_mainline"].get(bucket, []) + DEFAULT_BUSINESS_ANCHORS["adjacent_mbse"].get(bucket, []):
            normalized = label.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            values.append(label)
        merged[bucket] = values
    return merged


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
    return value.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")


def looks_like_business_noise(text: str) -> bool:
    value = str(text or "").strip().casefold()
    if not value or len(value) < 3 or len(value.split()) > 10:
        return True
    return bool(re.search(r"looking for|prefer|seeking|resume|curriculum vitae|\bcv\b|\bjob\b|博士背景|硕士背景|工作经历|专业背景|希望|想找|想做|保留|简历|候选人", value))


def collect_business_hint_terms(signals: CandidateSearchSignals) -> list[str]:
    filtered: list[str] = []
    for hint in signals.business_hint_terms():
        text = normalize_business_hint(hint)
        if not text or len(text) > 72 or looks_like_role_phrase(text) or looks_like_business_noise(text):
            continue
        filtered.append(text)
    return dedup_text(filtered, limit=24)


def classify_business_hint(hint: str, scope_profile: str) -> str:
    text = str(hint or "").strip()
    if not text:
        return "adjacent"
    for bucket in DISCOVERY_BUCKETS:
        for _, pattern in anchor_library(scope_profile).get(bucket, []):
            if re.search(pattern, text, flags=re.IGNORECASE):
                return bucket
    return "adjacent"


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

def build_anchor_source_text(
    *,
    signals: CandidateSearchSignals,
    resume_text: str = "",
    feedback_keywords: list[str] | None = None,
) -> str:
    parts = list(signals.anchor_source_terms())
    resolved_resume_text = str(resume_text or "").strip()
    if resolved_resume_text:
        parts.append(resolved_resume_text)
    parts.extend(feedback_keywords or [])
    return "\n".join(part for part in parts if str(part or "").strip())


def build_discovery_anchor_plan(
    *,
    scope_profile: str,
    signals: CandidateSearchSignals,
    resume_text: str = "",
    feedback_keywords: list[str] | None = None,
) -> DiscoveryAnchorPlan:
    defaults = default_anchor_buckets(scope_profile)
    avoid_terms = dedup_text(
        [
            normalize_business_hint(item)
            for item in signals.semantic.avoid_business_areas
        ],
        limit=16,
    )
    if _has_semantic_anchor_seed(signals):
        buckets = {
            "core": [
                normalize_business_hint(item)
                for item in signals.semantic_core_anchor_terms()
            ],
            "adjacent": [
                normalize_business_hint(item)
                for item in signals.semantic.adjacent_business_areas
            ],
            "explore": [
                normalize_business_hint(item)
                for item in signals.semantic.exploration_business_areas
            ],
        }
    else:
        buckets = {"core": [], "adjacent": [], "explore": []}
    source_text = build_anchor_source_text(
        signals=signals,
        resume_text=resume_text,
        feedback_keywords=feedback_keywords,
    )
    library = anchor_library(scope_profile)
    for bucket in DISCOVERY_BUCKETS:
        for label, pattern in library.get(bucket, []):
            if re.search(pattern, source_text, flags=re.IGNORECASE):
                buckets[bucket].append(label)
    for hint in collect_business_hint_terms(signals):
        buckets[classify_business_hint(hint, scope_profile)].append(hint)
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
        for default_value in defaults.get(bucket, []):
            if len(values) >= _bucket_rule(bucket, "minimum_anchor_count"):
                break
            if is_avoided_anchor(default_value, avoid_terms):
                continue
            if default_value.casefold() not in {item.casefold() for item in values}:
                values.append(default_value)
        normalized[bucket] = values[: _bucket_rule(bucket, "phrase_limit")]
    return DiscoveryAnchorPlan(core=normalized["core"], adjacent=normalized["adjacent"], explore=normalized["explore"])

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
    rotated_anchors = rotate_list(dedup_text(anchors, limit=32), seed)
    rotated_templates = rotate_list(list(templates), seed // 7 if seed else 0)
    queries: list[str] = []
    seen: set[str] = set()
    max_attempts = max(limit * 8, len(rotated_anchors) * len(rotated_templates))
    for attempt in range(max_attempts):
        anchor = rotated_anchors[attempt % len(rotated_anchors)]
        template = rotated_templates[(attempt // max(1, len(rotated_anchors))) % len(rotated_templates)]
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
    indices = {"core": 0, "adjacent": 0, "explore": 0}
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
    dedup = generate_discovery_query_plan(
        anchor_plan=anchor_plan,
        templates=BUSINESS_COMPANY_QUERY_TEMPLATES,
        limit=discovery_query_limit(),
        seed=rotation_seed,
        normalize=normalize_company_discovery_query,
    )
    if not dedup:
        dedup = ["hydrogen systems companies", "systems engineering companies", "industrial technology companies"]
    return dedup[: discovery_query_limit()]


__all__ = [
    "DISCOVERY_BUCKET_RULES",
    "DiscoveryAnchorPlan",
    "anchor_library",
    "build_anchor_source_text",
    "build_company_discovery_queries_from_anchor_plan",
    "build_discovery_anchor_plan",
    "discovery_query_bucket_order",
    "discovery_query_limit",
    "classify_business_hint",
    "collect_business_hint_terms",
    "default_anchor_buckets",
    "generate_bucket_queries",
    "generate_discovery_query_plan",
    "is_avoided_anchor",
    "looks_like_business_noise",
    "looks_like_role_phrase",
    "normalize_business_hint",
    "normalize_company_discovery_query",
    "rotate_list",
]
def _has_semantic_anchor_seed(signals: CandidateSearchSignals) -> bool:
    return any(
        (
            signals.semantic.summary,
            *signals.semantic_core_anchor_terms(),
            *signals.semantic.adjacent_business_areas,
            *signals.semantic.exploration_business_areas,
        )
    )
