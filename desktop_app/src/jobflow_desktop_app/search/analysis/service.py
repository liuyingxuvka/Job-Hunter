from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .prompts import (
    TargetRoleBindingResult,
    TargetRoleEvaluation,
    TargetRoleDefinition,
    build_full_scoring_request,
    build_post_verify_request,
    build_target_role_binding_request,
    build_lite_scoring_request,
    extract_job_jd_text,
    normalize_full_scoring_payload,
    normalize_lite_scoring_payload,
    normalize_post_verify_payload,
    normalize_target_role_binding_payload,
    normalize_target_roles,
    prepare_analysis_for_storage,
)
from ...ai.client import parse_response_json
from .scoring import unified_recommend_threshold


class ResponseRequestClient(Protocol):
    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        ...


class JobAnalysisService:
    @staticmethod
    def score_job_fit(
        client: ResponseRequestClient,
        *,
        config: Mapping[str, Any],
        candidate_profile: Mapping[str, Any] | None,
        job: Mapping[str, Any],
        data_availability_note: str = "",
    ) -> dict[str, Any]:
        analysis_config = config.get("analysis") if isinstance(config.get("analysis"), Mapping) else {}
        model = str(analysis_config.get("model") or "").strip()
        if not model:
            raise ValueError("analysis.model is required for Python job scoring.")
        recommend_threshold = unified_recommend_threshold(config)
        low_token_mode = bool(analysis_config.get("lowTokenMode"))
        use_web_search = bool(analysis_config.get("scoringUseWebSearch"))
        jd_text = extract_job_jd_text(job)
        if low_token_mode:
            jd_limit = max(600, int(analysis_config.get("lowTokenJdMaxChars") or 1800))
            request = build_lite_scoring_request(
                model=model,
                config=config,
                candidate_profile=candidate_profile,
                job=job,
                jd_text=jd_text,
                jd_limit=jd_limit,
                data_availability_note=data_availability_note,
                use_web_search=use_web_search,
            )
            response = client.create(request)
            parsed = parse_response_json(response, "Job fit scoring lite")
            return normalize_lite_scoring_payload(parsed, recommend_threshold=recommend_threshold)

        jd_limit = max(1200, int(analysis_config.get("scoringJdMaxChars") or 12000))
        request = build_full_scoring_request(
            model=model,
            config=config,
            candidate_profile=candidate_profile,
            job=job,
            jd_text=jd_text,
            jd_limit=jd_limit,
            recommend_threshold=recommend_threshold,
            data_availability_note=data_availability_note,
            use_web_search=use_web_search,
        )
        response = client.create(request)
        parsed = parse_response_json(response, "Job fit scoring")
        return normalize_full_scoring_payload(parsed, recommend_threshold=recommend_threshold)

    @staticmethod
    def evaluate_target_roles_for_job(
        client: ResponseRequestClient,
        *,
        config: Mapping[str, Any],
        candidate_profile: Mapping[str, Any] | None,
        job: Mapping[str, Any],
        analysis: Mapping[str, Any],
    ) -> TargetRoleBindingResult | None:
        recommend_threshold = unified_recommend_threshold(config)
        target_roles = normalize_target_roles(_target_roles_from_config(config))
        if not target_roles:
            return None

        analysis_config = config.get("analysis") if isinstance(config.get("analysis"), Mapping) else {}
        model = str(analysis_config.get("model") or "").strip()
        if not model:
            raise ValueError("analysis.model is required for Python target-role binding.")
        jd_limit = max(900, 1600 if analysis_config.get("lowTokenMode") else 2400)
        request = build_target_role_binding_request(
            model=model,
            config=config,
            candidate_profile=candidate_profile,
            job=job,
            jd_text=extract_job_jd_text(job),
            jd_limit=jd_limit,
            overall_analysis=analysis,
            target_roles=target_roles,
            recommend_threshold=recommend_threshold,
        )
        response = client.create(request)
        parsed = parse_response_json(response, "Target role binding")
        return normalize_target_role_binding_payload(
            parsed,
            target_roles=target_roles,
            recommend_threshold=recommend_threshold,
        )

    @staticmethod
    def post_verify_recommended_job(
        client: ResponseRequestClient,
        *,
        config: Mapping[str, Any],
        job: Mapping[str, Any],
    ) -> dict[str, Any]:
        analysis_config = config.get("analysis") if isinstance(config.get("analysis"), Mapping) else {}
        model = str(analysis_config.get("postVerifyModel") or analysis_config.get("model") or "").strip()
        if not model:
            raise ValueError("analysis.postVerifyModel or analysis.model is required for Python post-verify.")
        jd_limit = max(500, int(analysis_config.get("postVerifyJdMaxChars") or 1200))
        request = build_post_verify_request(
            model=model,
            config=config,
            job=job,
            jd_text=extract_job_jd_text(job),
            jd_limit=jd_limit,
            use_web_search=bool(analysis_config.get("postVerifyUseWebSearch")),
        )
        response = client.create(request)
        parsed = parse_response_json(response, "Post verify recommended job")
        return normalize_post_verify_payload(parsed, job_url=str(job.get("url") or ""))

    @staticmethod
    def prepare_analysis_for_storage(
        analysis: Mapping[str, Any],
        role_binding: TargetRoleBindingResult | Mapping[str, Any] | None,
        *,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        return prepare_analysis_for_storage(analysis, role_binding, config=config)


def _target_roles_from_config(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidate = config.get("candidate")
    if not isinstance(candidate, Mapping):
        return []
    target_roles = candidate.get("targetRoles")
    if not isinstance(target_roles, list):
        return []
    return [item for item in target_roles if isinstance(item, Mapping)]


__all__ = [
    "JobAnalysisService",
    "ResponseRequestClient",
]
