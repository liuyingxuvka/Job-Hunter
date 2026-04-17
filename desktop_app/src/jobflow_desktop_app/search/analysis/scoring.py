from __future__ import annotations

from .scoring_contract import (
    bound_role_score,
    fit_level,
    overall_score,
    passes_unified_recommendation_threshold as passes_unified_recommendation_threshold_contract,
    target_role_score,
    unified_recommend_threshold,
)


def overall_analysis_score(job_or_analysis: object) -> int:
    return overall_score(job_or_analysis)


def bound_target_role_score(job_or_analysis: object) -> int:
    return bound_role_score(job_or_analysis)


def target_role_score_value(job_or_analysis: object) -> int:
    return target_role_score(job_or_analysis)


def to_fit_level_cn(score: object) -> str:
    return fit_level(score)


def passes_unified_recommendation_threshold(
    job_or_analysis: object,
    *,
    config: object | None = None,
    threshold: int | None = None,
) -> bool:
    effective_threshold = (
        threshold if threshold is not None else unified_recommend_threshold(config)
    )
    return passes_unified_recommendation_threshold_contract(
        job_or_analysis,
        threshold=effective_threshold,
    )


__all__ = [
    "bound_target_role_score",
    "overall_analysis_score",
    "passes_unified_recommendation_threshold",
    "target_role_score_value",
    "to_fit_level_cn",
    "unified_recommend_threshold",
]
