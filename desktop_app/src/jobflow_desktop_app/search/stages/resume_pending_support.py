from __future__ import annotations

from pathlib import Path
from typing import Any

from ..run_state import job_identity_key, job_item_key, merge_job_item, merge_job_items_from_job_lists


def _load_candidate_profile_payload(config: dict[str, Any], run_dir: Path) -> dict[str, Any] | None:
    del run_dir
    candidate = config.get("candidate")
    payload: dict[str, Any] = {}
    if isinstance(candidate, dict):
        semantic_profile = candidate.get("semanticProfile")
        if isinstance(semantic_profile, dict):
            payload.update(dict(semantic_profile))
        for key in ("locationPreference", "scopeProfile"):
            value = str(candidate.get(key) or "").strip()
            if value:
                payload[key] = value
        scope_profiles = candidate.get("scopeProfiles")
        if isinstance(scope_profiles, list):
            payload["scopeProfiles"] = [str(item or "").strip() for item in scope_profiles if str(item or "").strip()]
        target_roles = candidate.get("targetRoles")
        if isinstance(target_roles, list):
            payload["targetRoles"] = [dict(item) for item in target_roles if isinstance(item, dict)]
    return payload or None


def _build_data_availability_note(candidate_profile: dict[str, Any] | None) -> str:
    if candidate_profile:
        return ""
    return "候选人语义画像不可用，请主要依据候选人目标方向、地点偏好与岗位文本判断。"


def _merge_jobs_for_resume(existing_jobs: list[dict], pending_jobs: list[dict]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for item in merge_job_items_from_job_lists(
        [dict(job) for job in existing_jobs if isinstance(job, dict)],
        [dict(job) for job in pending_jobs if isinstance(job, dict)],
    ):
        key = job_item_key(item) or job_identity_key(item)
        if not key:
            continue
        merged[key] = dict(item)
    return merged


def _merge_with_existing_job(working_jobs: dict[str, dict], incoming: dict[str, Any]) -> dict[str, Any]:
    key = job_item_key(incoming) or job_identity_key(incoming)
    if not key:
        return dict(incoming)
    existing = working_jobs.get(key)
    return merge_job_item(existing or {}, incoming) if existing else dict(incoming)


def _store_job(working_jobs: dict[str, dict], job: dict[str, Any]) -> None:
    key = job_item_key(job) or job_identity_key(job)
    if not key:
        return
    existing = working_jobs.get(key)
    working_jobs[key] = merge_job_item(existing or {}, job) if existing else dict(job)


def _job_progress_label(index: int, total: int, job: dict[str, Any]) -> str:
    company = str(job.get("company") or "").strip()
    title = str(job.get("title") or "").strip()
    return f"Python resume {index}/{total}: {company or 'Unknown company'} | {title or 'Untitled job'}"


def _job_error_label(job: dict[str, Any]) -> str:
    company = str(job.get("company") or "").strip()
    title = str(job.get("title") or "").strip()
    url = str(job.get("url") or "").strip()
    return " / ".join(part for part in (company, title, url) if part)
