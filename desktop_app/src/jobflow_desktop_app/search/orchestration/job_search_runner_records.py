from __future__ import annotations

from typing import Any, Callable

from ..output.final_output import is_output_eligible
from ..run_state import extract_overall_score as state_extract_overall_score
from . import runtime_config_builder
from .job_result_i18n import normalize_display_i18n


def extract_overall_score(analysis: object) -> int | None:
    return state_extract_overall_score(analysis)


def extract_match_score(analysis: object) -> int | None:
    return extract_overall_score(analysis)


def extract_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def analysis_blocks_live_review(analysis: object) -> bool:
    if not isinstance(analysis, dict):
        return False
    if bool(analysis.get("landingPageNoise")) or bool(analysis.get("signalOnlyNoise")):
        return True
    if analysis.get("isJobPosting") is False and not bool(analysis.get("recommend")):
        return True
    return False


def filter_review_ready_jobs(jobs: list[dict]) -> list[dict]:
    ready_jobs: list[dict] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        analysis = item.get("analysis", {})
        if extract_overall_score(analysis) is None:
            continue
        ready_jobs.append(item)
    return ready_jobs


def filter_live_review_jobs(jobs: list[dict]) -> list[dict]:
    return [
        item
        for item in filter_review_ready_jobs(jobs)
        if not analysis_blocks_live_review(item.get("analysis"))
    ]


def passes_displayable_recommendation_threshold(
    item: dict,
    threshold: int = 50,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    if not isinstance(item, dict):
        return False
    analysis = item.get("analysis", {})
    if not isinstance(analysis, dict):
        return False
    if not bool(analysis.get("recommend")):
        return False
    if "eligibleForOutput" in analysis:
        return bool(analysis.get("eligibleForOutput"))
    eligibility_config = config or {"analysis": {"recommendScoreThreshold": threshold}}
    return is_output_eligible(item, eligibility_config)


def filter_displayable_recommended_jobs(
    jobs: list[dict],
    *,
    threshold: int = 50,
    config: dict[str, Any] | None = None,
) -> list[dict]:
    return [
        item
        for item in filter_review_ready_jobs(jobs)
        if passes_displayable_recommendation_threshold(item, threshold=threshold, config=config)
    ]


def resolve_job_links(item: dict) -> tuple[str, str, str]:
    source_url = str(item.get("url") or "").strip()
    canonical_url = str(item.get("canonicalUrl") or "").strip()
    analysis = item.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    post_verify = analysis.get("postVerify")
    if not isinstance(post_verify, dict):
        post_verify = {}
    jd = item.get("jd")
    if not isinstance(jd, dict):
        jd = {}

    verified_final_url = str(post_verify.get("finalUrl") or "").strip()
    if verified_final_url and runtime_config_builder.coerce_bool(post_verify.get("isValidJobPage")):
        return source_url, verified_final_url, "verified_final"

    apply_url = str(jd.get("applyUrl") or item.get("applyUrl") or "").strip()
    if apply_url:
        return source_url, apply_url, "apply"

    final_url = str(jd.get("finalUrl") or item.get("finalUrl") or "").strip()
    if final_url:
        return source_url, final_url, "final"

    if canonical_url:
        return source_url, canonical_url, "canonical"

    if source_url:
        return source_url, source_url, "source"

    return "", "", "source"


def build_job_records(
    jobs: list[dict],
    *,
    job_result_factory: Callable[..., Any],
) -> list[Any]:
    records: list[Any] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        analysis = item.get("analysis", {})
        if not isinstance(analysis, dict):
            analysis = {}
        recommend = bool(analysis.get("recommend"))
        score = extract_overall_score(analysis)
        overall_score = score
        bound_target_role = analysis.get("boundTargetRole")
        if not isinstance(bound_target_role, dict):
            bound_target_role = {}
        bound_profile_id = extract_optional_int(bound_target_role.get("profileId"))
        bound_score = extract_optional_int(analysis.get("targetRoleScore"))
        if bound_score is None:
            bound_score = extract_optional_int(bound_target_role.get("score"))
        display_score = bound_score if bound_score is not None else overall_score if overall_score is not None else score
        display_fit_level = str(
            analysis.get("targetRoleFitLevelCn")
            or bound_target_role.get("fitLevelCn")
            or analysis.get("fitLevelCn")
            or ""
        ).strip()
        source_url, final_url, link_status = resolve_job_links(item)
        canonical_url = str(item.get("canonicalUrl") or "").strip()
        display_i18n = normalize_display_i18n(item.get("displayI18n"))

        records.append(
            job_result_factory(
                title=str(item.get("title") or "").strip(),
                company=str(item.get("company") or "").strip(),
                location=str(item.get("location") or "").strip(),
                url=source_url or canonical_url,
                date_found=str(item.get("dateFound") or "").strip(),
                match_score=display_score,
                recommend=recommend,
                fit_level_cn=display_fit_level,
                fit_track=str(analysis.get("fitTrack") or "").strip(),
                adjacent_direction_cn=str(analysis.get("adjacentDirectionCn") or "").strip(),
                overall_match_score=overall_score,
                bound_target_role_id=str(bound_target_role.get("roleId") or "").strip(),
                bound_target_role_profile_id=bound_profile_id,
                bound_target_role_name_zh=str(bound_target_role.get("nameZh") or "").strip(),
                bound_target_role_name_en=str(bound_target_role.get("nameEn") or "").strip(),
                bound_target_role_display_name=str(bound_target_role.get("displayName") or "").strip(),
                bound_target_role_text=str(bound_target_role.get("targetRoleText") or "").strip(),
                bound_target_role_score=bound_score,
                source_url=source_url,
                final_url=final_url,
                link_status=link_status,
                title_zh=display_i18n["zh"]["title"],
                title_en=display_i18n["en"]["title"],
                location_zh=display_i18n["zh"]["location"],
                location_en=display_i18n["en"]["location"],
            )
        )
    return records


__all__ = [
    "analysis_blocks_live_review",
    "build_job_records",
    "extract_overall_score",
    "extract_match_score",
    "extract_optional_int",
    "filter_displayable_recommended_jobs",
    "filter_live_review_jobs",
    "filter_review_ready_jobs",
    "passes_displayable_recommendation_threshold",
    "resolve_job_links",
]
