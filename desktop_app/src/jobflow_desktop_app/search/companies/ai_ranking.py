from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ...ai.client import (
    build_json_schema_request,
    build_text_input_messages,
    parse_response_json,
)
from ...prompt_assets import load_prompt_asset
from ..analysis.service import ResponseRequestClient
from .selection import company_record_key
from ..state.work_unit_state import (
    clear_work_unit_state,
    is_abandoned,
    is_suspended_for_run,
    record_technical_failure,
    suspend_for_current_run,
)

COMPANY_FIT_PROMPT = load_prompt_asset("search_ranking", "company_fit_prompt.txt")
JOB_PRERANK_PROMPT = load_prompt_asset("search_ranking", "job_prerank_prompt.txt")
COMPANY_FIT_INPUT_VERSION = "company-fit-v3"
# Keep company-fit scoring single-company and lightweight; measured latency is low
# enough that batching is unnecessary and would only increase timeout risk.
COMPANY_FIT_BATCH_SIZE = 1
# A slightly wider rerank pass is cheaper than leaving specialized employers
# unscored behind stale generic pool entries across multiple runs.
COMPANY_FIT_MAX_RERANK_PER_PASS = 16
# Smaller prerank batches are slower in aggregate but materially reduce timeout
# risk on large mixed boards while still letting partial progress survive.
JOB_PRERANK_BATCH_SIZE = 5

COMPANY_FIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "companyKey": {"type": "string"},
                    "fitScore": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["companyKey", "fitScore"],
            },
        }
    },
    "required": ["companies"],
}

JOB_PRERANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "jobKey": {"type": "string"},
                    "preRankScore": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reason": {"type": "string"},
                },
                "required": ["jobKey", "preRankScore", "reason"],
            },
        }
    },
    "required": ["jobs"],
}


def score_companies_for_candidate(
    client: ResponseRequestClient | None,
    *,
    config: Mapping[str, Any] | None,
    companies: list[Mapping[str, Any]],
    current_run_id: int | None = None,
) -> list[dict[str, Any]]:
    def _normalized_company(company: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(company)
        normalized.pop("aiCompanyRankingPending", None)
        normalized.pop("aiCompanyRankingError", None)
        normalized.pop("aiCompanyFitReason", None)
        return normalized

    if client is None:
        return [_normalized_company(company) for company in companies if isinstance(company, Mapping)]
    model = _resolve_company_fit_model(config)
    if not model:
        return [_normalized_company(company) for company in companies if isinstance(company, Mapping)]

    normalized_companies = [_normalized_company(company) for company in companies if isinstance(company, Mapping)]
    candidate_context = _company_fit_candidate_context(config)
    pending_companies = sorted(
        normalized_companies,
        key=lambda company: (
            _company_rerank_priority(company, candidate_context=candidate_context),
            -_coerce_positive_int(company.get("signalCount")),
            -_coerce_positive_int(company.get("repeatCount")),
            str(company.get("name") or "").casefold(),
        ),
    )
    payload_companies: list[dict[str, Any]] = []
    payload_hash_by_key: dict[str, str] = {}
    for company in pending_companies:
        if not _company_needs_ai_ranking(
            company,
            candidate_context=candidate_context,
            current_run_id=current_run_id,
        ):
            continue
        if len(payload_companies) >= COMPANY_FIT_MAX_RERANK_PER_PASS:
            break
        company_key = company_record_key(company)
        if not company_key:
            continue
        payload_company = _company_fit_payload(company)
        payload_companies.append(payload_company)
        payload_hash_by_key[company_key] = _company_fit_input_hash(
            candidate_context=candidate_context,
            payload_company=payload_company,
        )
    if not payload_companies:
        return normalized_companies

    scores_by_key, pending_keys, pending_error = _collect_company_fit_scores(
        client,
        model=model,
        candidate_context=candidate_context,
        payload_companies=payload_companies,
    )
    merged: list[dict[str, Any]] = []
    for company in normalized_companies:
        company_key = company_record_key(company)
        ranked = scores_by_key.get(company_key)
        updated = dict(company)
        if isinstance(ranked, Mapping):
            updated["aiCompanyFitScore"] = _clamp_score(ranked.get("fitScore"))
            input_hash = payload_hash_by_key.get(company_key)
            if input_hash:
                updated["aiCompanyFitInputHash"] = input_hash
            updated.pop("aiCompanyRankingError", None)
            updated.pop("aiCompanyFitReason", None)
            updated["rankingWorkState"] = clear_work_unit_state()
        elif company_key in pending_keys:
            if _company_ai_fit_score(company) is None:
                updated["rankingWorkState"] = record_technical_failure(
                    company.get("rankingWorkState"),
                    run_id=current_run_id,
                    reason="company_fit_error",
                )
            else:
                updated["rankingWorkState"] = suspend_for_current_run(
                    company.get("rankingWorkState"),
                    run_id=current_run_id,
                    reason="company_fit_refresh_error",
                )
            updated.pop("aiCompanyRankingError", None)
        merged.append(updated)
    return merged


def prerank_company_jobs_for_candidate(
    client: ResponseRequestClient | None,
    *,
    config: Mapping[str, Any] | None,
    company: Mapping[str, Any],
    jobs: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    def _normalized_job(job: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(job)
        normalized.pop("aiPreRankError", None)
        return normalized

    if client is None:
        return [_normalized_job(job) for job in jobs if isinstance(job, Mapping)]
    model = _resolve_job_prerank_model(config)
    if not model:
        return [_normalized_job(job) for job in jobs if isinstance(job, Mapping)]

    normalized_jobs = [_normalized_job(job) for job in jobs if isinstance(job, Mapping)]
    payload_jobs: list[dict[str, Any]] = []
    for job in normalized_jobs:
        if _job_ai_prerank_score(job) is not None:
            continue
        job_key = str(job.get("url") or job.get("canonicalUrl") or "").strip()
        if not job_key:
            continue
        payload_jobs.append(
            {
                "jobKey": job_key,
                "title": str(job.get("title") or "").strip(),
                "location": str(job.get("location") or "").strip(),
                "summary": _truncate_text(job.get("summary"), 260),
                "url": job_key,
            }
        )
    if not payload_jobs:
        return normalized_jobs

    scores_by_key, pending_keys, pending_error = _collect_job_prerank_scores(
        client,
        model=model,
        config=config,
        company=company,
        payload_jobs=payload_jobs,
    )
    if pending_keys and not scores_by_key:
        raise RuntimeError(pending_error or "Job prerank timed out.")
    merged: list[dict[str, Any]] = []
    for job in normalized_jobs:
        job_key = str(job.get("url") or job.get("canonicalUrl") or "").strip()
        ranked = scores_by_key.get(job_key)
        updated = dict(job)
        if isinstance(ranked, Mapping):
            updated["aiPreRankScore"] = _clamp_score(ranked.get("preRankScore"))
            updated["aiPreRankReason"] = str(ranked.get("reason") or "").strip()
            updated["aiPreRankPending"] = False
            updated.pop("aiPreRankError", None)
        elif job_key in pending_keys:
            updated["aiPreRankPending"] = True
            updated.pop("aiPreRankError", None)
        merged.append(updated)
    return merged


def _resolve_company_fit_model(config: Mapping[str, Any] | None) -> str:
    company_discovery = config.get("companyDiscovery") if isinstance(config, Mapping) else {}
    analysis = config.get("analysis") if isinstance(config, Mapping) else {}
    return str(
        (company_discovery.get("model") if isinstance(company_discovery, Mapping) else "")
        or (analysis.get("model") if isinstance(analysis, Mapping) else "")
        or ""
    ).strip()


def _resolve_job_prerank_model(config: Mapping[str, Any] | None) -> str:
    analysis = config.get("analysis") if isinstance(config, Mapping) else {}
    company_discovery = config.get("companyDiscovery") if isinstance(config, Mapping) else {}
    return str(
        (analysis.get("model") if isinstance(analysis, Mapping) else "")
        or (company_discovery.get("model") if isinstance(company_discovery, Mapping) else "")
        or ""
    ).strip()


def _candidate_context_payload(config: Mapping[str, Any] | None) -> dict[str, Any]:
    candidate = dict(config.get("candidate") or {}) if isinstance(config, Mapping) else {}
    sources = dict(config.get("sources") or {}) if isinstance(config, Mapping) else {}
    semantic = (
        dict(candidate.get("semanticProfile") or {})
        if isinstance(candidate.get("semanticProfile"), Mapping)
        else {}
    )
    company_fit_terms = (
        dict(sources.get("companyFitTerms") or {})
        if isinstance(sources.get("companyFitTerms"), Mapping)
        else {}
    )
    fallback_core_terms = _trim_text_list(company_fit_terms.get("core"), limit=12)
    fallback_support_terms = _trim_text_list(company_fit_terms.get("support"), limit=10)
    target_roles = candidate.get("targetRoles")
    role_names: list[str] = []
    if isinstance(target_roles, list):
        for raw in target_roles:
            if not isinstance(raw, Mapping):
                continue
            value = str(
                raw.get("displayName")
                or raw.get("targetRoleText")
                or raw.get("nameEn")
                or raw.get("nameZh")
                or ""
            ).strip()
            if value and value not in role_names:
                role_names.append(value)
    return {
        "summary": _truncate_text(semantic.get("summary"), 220),
        "targetRoles": role_names[:6],
        "preferredLocations": _non_empty_lines(candidate.get("preferredLocations"), limit=6),
        "companyDiscoveryPrimaryAnchors": _trim_text_list(
            semantic.get("company_discovery_primary_anchors"),
            limit=10,
        )
        or fallback_core_terms[:6],
        "companyDiscoverySecondaryAnchors": _trim_text_list(
            semantic.get("company_discovery_secondary_anchors"),
            limit=8,
        )
        or fallback_support_terms[:5],
        "jobFitCoreTerms": _trim_text_list(semantic.get("job_fit_core_terms"), limit=12)
        or fallback_core_terms,
        "jobFitSupportTerms": _trim_text_list(semantic.get("job_fit_support_terms"), limit=10)
        or fallback_support_terms,
        "avoidBusinessAreas": _trim_text_list(semantic.get("avoid_business_areas"), limit=8),
    }


def _company_fit_candidate_context(config: Mapping[str, Any] | None) -> dict[str, Any]:
    candidate_context = _candidate_context_payload(config)
    return {
        "summary": _truncate_text(candidate_context.get("summary"), 180),
        "targetRoles": list(candidate_context.get("targetRoles") or [])[:4],
        "companyDiscoveryPrimaryAnchors": list(
            candidate_context.get("companyDiscoveryPrimaryAnchors") or []
        )[:4],
        "companyDiscoverySecondaryAnchors": list(
            candidate_context.get("companyDiscoverySecondaryAnchors") or []
        )[:3],
        "jobFitCoreTerms": list(candidate_context.get("jobFitCoreTerms") or [])[:6],
        "jobFitSupportTerms": list(candidate_context.get("jobFitSupportTerms") or [])[:4],
        "avoidBusinessAreas": list(candidate_context.get("avoidBusinessAreas") or [])[:4],
    }


def _job_prerank_candidate_context(config: Mapping[str, Any] | None) -> dict[str, Any]:
    candidate_context = _candidate_context_payload(config)
    return {
        "summary": _truncate_text(candidate_context.get("summary"), 180),
        "targetRoles": list(candidate_context.get("targetRoles") or [])[:4],
        "preferredLocations": list(candidate_context.get("preferredLocations") or [])[:4],
        "jobFitCoreTerms": list(candidate_context.get("jobFitCoreTerms") or [])[:6],
        "jobFitSupportTerms": list(candidate_context.get("jobFitSupportTerms") or [])[:4],
        "avoidBusinessAreas": list(candidate_context.get("avoidBusinessAreas") or [])[:4],
    }


def _build_company_fit_user_text(
    candidate_context: Mapping[str, Any],
    payload_companies: list[dict[str, Any]],
) -> str:
    return (
        "Candidate context:\n"
        + json.dumps(candidate_context, ensure_ascii=False, indent=2)
        + "\n\nCompanies:\n"
        + json.dumps(payload_companies, ensure_ascii=False, indent=2)
        + "\n\nReturn one score for every companyKey."
    )


def _build_job_prerank_user_text(
    config: Mapping[str, Any] | None,
    company: Mapping[str, Any],
    payload_jobs: list[dict[str, Any]],
) -> str:
    candidate_context = _job_prerank_candidate_context(config)
    company_context = {
        "name": str(company.get("name") or "").strip(),
        "website": str(company.get("website") or "").strip(),
        "businessSummary": _truncate_text(company.get("businessSummary"), 180),
        "tags": _trim_text_list(company.get("tags"), limit=5),
        "jobsPageUrl": str(company.get("jobsPageUrl") or company.get("careersUrl") or "").strip(),
    }
    return (
        "Candidate context:\n"
        + json.dumps(candidate_context, ensure_ascii=False, indent=2)
        + "\n\nCompany:\n"
        + json.dumps(company_context, ensure_ascii=False, indent=2)
        + "\n\nJobs:\n"
        + json.dumps(payload_jobs, ensure_ascii=False, indent=2)
        + "\n\nReturn one score for every jobKey."
    )


def _trim_text_list(values: object, *, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    trimmed: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = _truncate_text(raw, 96)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        trimmed.append(text)
        if len(trimmed) >= limit:
            break
    return trimmed


def _non_empty_lines(value: object, *, limit: int) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in str(value or "").splitlines():
        line = _truncate_text(raw_line, 96)
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _truncate_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _company_fit_payload(company: Mapping[str, Any]) -> dict[str, Any]:
    company_key = company_record_key(company)
    return {
        "companyKey": company_key,
        "name": str(company.get("name") or "").strip(),
        "website": str(company.get("website") or "").strip(),
        "businessSummary": _truncate_text(company.get("businessSummary"), 180),
        "tags": _trim_text_list(company.get("tags"), limit=8),
    }


def _company_fit_input_hash(
    *,
    candidate_context: Mapping[str, Any],
    payload_company: Mapping[str, Any],
) -> str:
    normalized_payload = {
        "version": COMPANY_FIT_INPUT_VERSION,
        "candidateContext": dict(candidate_context),
        "company": dict(payload_company),
    }
    blob = json.dumps(
        normalized_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def _collect_company_fit_scores(
    client: ResponseRequestClient,
    *,
    model: str,
    candidate_context: Mapping[str, Any],
    payload_companies: list[dict[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], set[str], str]:
    scores_by_key: dict[str, Mapping[str, Any]] = {}
    pending_keys: set[str] = set()
    pending_error = ""
    batches = _chunk_items(payload_companies, COMPANY_FIT_BATCH_SIZE)
    for batch_index, batch in enumerate(batches):
        request = build_json_schema_request(
            model=model,
            input_payload=build_text_input_messages(
                COMPANY_FIT_PROMPT,
                _build_company_fit_user_text(candidate_context, batch),
            ),
            schema_name="company_fit_scores",
            schema=COMPANY_FIT_SCHEMA,
            use_web_search=False,
        )
        try:
            response = client.create(request)
            parsed = parse_response_json(response, "Company fit ranking")
        except Exception as exc:
            pending_error = _truncate_text(exc, 160)
            for remaining_batch in batches[batch_index:]:
                for item in remaining_batch:
                    company_key = str(item.get("companyKey") or "").strip()
                    if company_key:
                        pending_keys.add(company_key)
            break
        ranked_companies = parsed.get("companies", [])
        if not isinstance(ranked_companies, list):
            continue
        for item in ranked_companies:
            if not isinstance(item, Mapping):
                continue
            company_key = str(item.get("companyKey") or "").strip()
            if not company_key:
                continue
            scores_by_key[company_key] = item
    return scores_by_key, pending_keys, pending_error


def _collect_job_prerank_scores(
    client: ResponseRequestClient,
    *,
    model: str,
    config: Mapping[str, Any] | None,
    company: Mapping[str, Any],
    payload_jobs: list[dict[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], set[str], str]:
    scores_by_key: dict[str, Mapping[str, Any]] = {}
    pending_keys: set[str] = set()
    pending_error = ""
    batches = _chunk_items(payload_jobs, JOB_PRERANK_BATCH_SIZE)
    for batch_index, batch in enumerate(batches):
        request = build_json_schema_request(
            model=model,
            input_payload=build_text_input_messages(
                JOB_PRERANK_PROMPT,
                _build_job_prerank_user_text(config, company, batch),
            ),
            schema_name="job_prerank_scores",
            schema=JOB_PRERANK_SCHEMA,
            use_web_search=False,
        )
        try:
            response = client.create(request)
            parsed = parse_response_json(response, "Company job pre-ranking")
        except Exception as exc:
            pending_error = _truncate_text(exc, 160)
            for remaining_batch in batches[batch_index:]:
                for item in remaining_batch:
                    job_key = str(item.get("jobKey") or "").strip()
                    if job_key:
                        pending_keys.add(job_key)
            break
        ranked_jobs = parsed.get("jobs", [])
        if not isinstance(ranked_jobs, list):
            continue
        for item in ranked_jobs:
            if not isinstance(item, Mapping):
                continue
            job_key = str(item.get("jobKey") or "").strip()
            if not job_key:
                continue
            scores_by_key[job_key] = item
    return scores_by_key, pending_keys, pending_error


def _chunk_items(items: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    normalized_batch_size = max(1, int(batch_size))
    return [
        items[index : index + normalized_batch_size]
        for index in range(0, len(items), normalized_batch_size)
    ]


def _company_needs_ai_ranking(
    company: Mapping[str, Any],
    *,
    candidate_context: Mapping[str, Any],
    current_run_id: int | None = None,
) -> bool:
    if is_abandoned(company.get("sourceWorkState")):
        return False
    if is_abandoned(company.get("rankingWorkState")):
        return False
    if is_suspended_for_run(company.get("rankingWorkState"), current_run_id):
        return False
    return _company_rerank_priority(company, candidate_context=candidate_context) < 3


def _company_rerank_priority(
    company: Mapping[str, Any],
    *,
    candidate_context: Mapping[str, Any],
) -> int:
    if _company_ai_fit_score(company) is None:
        return 0
    company_key = company_record_key(company)
    if not company_key:
        return 3
    current_hash = _company_fit_input_hash(
        candidate_context=candidate_context,
        payload_company=_company_fit_payload(company),
    )
    if str(company.get("aiCompanyFitInputHash") or "").strip() != current_hash:
        return 2
    return 3


def _company_ai_fit_score(company: Mapping[str, Any]) -> float | None:
    value = company.get("aiCompanyFitScore")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:
        return None
    return max(0.0, min(100.0, score))


def _job_ai_prerank_score(job: Mapping[str, Any]) -> float | None:
    value = job.get("aiPreRankScore")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:
        return None
    return max(0.0, min(100.0, score))


def _clamp_score(value: object) -> int:
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def _coerce_positive_int(value: object) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, number)


__all__ = [
    "prerank_company_jobs_for_candidate",
    "score_companies_for_candidate",
]
