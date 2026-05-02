from __future__ import annotations

from collections.abc import Mapping
from typing import Any

UNIFIED_RECOMMEND_THRESHOLD = 20


def normalize_score(value: Any, *, default: int = 0) -> int:
    text = value
    if text is None:
        return _clamp_score(default)
    if isinstance(text, bool):
        return _clamp_score(int(text))
    if isinstance(text, (int, float)):
        return _clamp_score(int(float(text)))
    raw = str(text).strip()
    if not raw:
        return _clamp_score(default)
    try:
        return _clamp_score(int(float(raw)))
    except (TypeError, ValueError):
        return _clamp_score(default)


def unified_recommend_threshold(threshold: Any | None = None) -> int:
    if isinstance(threshold, Mapping):
        analysis = threshold.get("analysis")
        if isinstance(analysis, Mapping) and "recommendScoreThreshold" in analysis:
            return normalize_score(
                analysis.get("recommendScoreThreshold"),
                default=UNIFIED_RECOMMEND_THRESHOLD,
            )
        if "recommendScoreThreshold" in threshold:
            return normalize_score(
                threshold.get("recommendScoreThreshold"),
                default=UNIFIED_RECOMMEND_THRESHOLD,
            )
    if threshold is None:
        return UNIFIED_RECOMMEND_THRESHOLD
    return normalize_score(threshold, default=UNIFIED_RECOMMEND_THRESHOLD)


def overall_score(analysis_or_score: Any) -> int:
    if isinstance(analysis_or_score, Mapping):
        nested = analysis_or_score.get("analysis")
        if isinstance(nested, Mapping):
            return overall_score(nested)
        if "overallScore" in analysis_or_score:
            return normalize_score(analysis_or_score.get("overallScore"))
        return 0
    return normalize_score(analysis_or_score)


def target_role_score(analysis_or_score: Any) -> int:
    if isinstance(analysis_or_score, Mapping):
        nested = analysis_or_score.get("analysis")
        if isinstance(nested, Mapping):
            return target_role_score(nested)
        if "targetRoleScore" in analysis_or_score:
            return normalize_score(analysis_or_score.get("targetRoleScore"))
        bound_target_role = analysis_or_score.get("boundTargetRole")
        if isinstance(bound_target_role, Mapping):
            return normalize_score(
                bound_target_role.get("score"),
                default=0,
            )
        return 0
    return normalize_score(analysis_or_score)


def bound_role_score(analysis_or_score: Any) -> int:
    return target_role_score(analysis_or_score)


def fit_level(score: Any) -> str:
    value = normalize_score(score)
    if value >= 85:
        return "强匹配"
    if value >= 70:
        return "匹配"
    if value >= 50:
        return "可能匹配"
    return "不匹配"


def passes_unified_recommendation_threshold(
    analysis_or_score: Any,
    *,
    threshold: Any | None = None,
) -> bool:
    return overall_score(analysis_or_score) >= unified_recommend_threshold(threshold)


def should_keep_for_final_list(
    analysis_or_score: Any,
    bound_role_score_or_analysis: Any | None = None,
    *,
    threshold: Any | None = None,
) -> bool:
    return passes_unified_recommendation_threshold(analysis_or_score, threshold=threshold)


def _clamp_score(value: int) -> int:
    return max(0, min(100, int(value)))
