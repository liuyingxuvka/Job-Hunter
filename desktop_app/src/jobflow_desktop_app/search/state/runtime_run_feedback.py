from __future__ import annotations

import re
from typing import Any

from ..analysis.scoring_contract import overall_score


PREFERRED_FEEDBACK_RUN_STATUSES = {
    "success": 0,
    "running": 1,
    "queued": 2,
    "preparing": 2,
}


def _dedup_text(values: list[str], limit: int = 40) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = re.sub(r"\s+", " ", str(raw or "").strip())
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _analysis_blocks_live_review(analysis: object) -> bool:
    if not isinstance(analysis, dict):
        return False
    if bool(analysis.get("landingPageNoise")) or bool(analysis.get("signalOnlyNoise")):
        return True
    if analysis.get("isJobPosting") is False and not bool(analysis.get("recommend")):
        return True
    return False


def extract_feedback_keywords(title: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(title or "").strip())
    if not text:
        return []
    candidates: list[str] = []
    if len(text) <= 90:
        candidates.append(text)
    stop = {"senior", "junior", "lead", "principal", "staff", "manager", "engineer", "specialist", "intern"}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+/-]{2,}", text):
        lower = token.casefold()
        if lower in stop or len(lower) < 4:
            continue
        candidates.append(token)
    return candidates[:8]


def clean_company_name(raw: object) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text:
        return ""
    text = re.sub(r"\([^)]*listed via[^)]*\)", "", text, flags=re.IGNORECASE).strip(" -|,;")
    if not text:
        return ""
    lowered = text.casefold()
    if lowered in {"n/a", "unknown", "company", "confidential"}:
        return ""
    if len(text) > 120:
        text = text[:120].strip()
    return text


class SearchRunFeedbackStore:
    def __init__(self, *, artifacts: Any) -> None:
        self.artifacts = artifacts

    def load_latest_run_feedback(self, *, candidate_id: int) -> dict[str, list[str]]:
        recent_runs = []
        search_runs = getattr(self.artifacts, "search_runs", None)
        run_jobs = getattr(self.artifacts, "run_jobs", None)
        if search_runs is not None and run_jobs is not None and hasattr(search_runs, "recent_for_candidate"):
            recent_runs = list(search_runs.recent_for_candidate(int(candidate_id), limit=20))

        if not recent_runs:
            jobs_by_bucket = [
                (
                    "recommended",
                    self.artifacts.load_latest_bucket_jobs(
                        candidate_id=int(candidate_id),
                        job_bucket="recommended",
                    ),
                ),
                (
                    "all",
                    self.artifacts.load_latest_bucket_jobs(
                        candidate_id=int(candidate_id),
                        job_bucket="all",
                    ),
                ),
            ]
            return _collect_feedback_from_bucket_jobs(jobs_by_bucket)

        for snapshot in _prioritize_feedback_runs(recent_runs):
            jobs_by_bucket = [
                (
                    "recommended",
                    run_jobs.load_bucket_jobs(
                        search_run_id=int(snapshot.search_run_id),
                        job_bucket="recommended",
                    ),
                ),
                (
                    "all",
                    run_jobs.load_bucket_jobs(
                        search_run_id=int(snapshot.search_run_id),
                        job_bucket="all",
                    ),
                ),
            ]
            feedback = _collect_feedback_from_bucket_jobs(jobs_by_bucket)
            if feedback["companies"] or feedback["keywords"]:
                return feedback
        return {"companies": [], "keywords": []}


def _feedback_run_priority(snapshot: object) -> tuple[int, int]:
    status = str(getattr(snapshot, "status", "") or "").strip().lower()
    priority = PREFERRED_FEEDBACK_RUN_STATUSES.get(status, 3)
    return priority, -int(getattr(snapshot, "search_run_id", 0) or 0)


def _prioritize_feedback_runs(recent_runs: list[object]) -> list[object]:
    return sorted(recent_runs, key=_feedback_run_priority)


def _collect_feedback_from_bucket_jobs(
    jobs_by_bucket: list[tuple[str, list[dict[str, Any]]]],
) -> dict[str, list[str]]:
    company_candidates: list[str] = []
    keyword_candidates: list[str] = []
    for bucket_name, jobs in jobs_by_bucket:
        for job in jobs:
            analysis = job.get("analysis", {})
            if not isinstance(analysis, dict):
                analysis = {}
            if _analysis_blocks_live_review(analysis):
                continue
            score = overall_score(analysis)
            recommend = bool(analysis.get("recommend"))
            is_job_posting = analysis.get("isJobPosting") is True
            accepted = bucket_name == "recommended" or recommend or (
                isinstance(score, int) and score >= 70 and is_job_posting
            )
            if not accepted:
                continue
            company = clean_company_name(job.get("company"))
            if company:
                company_candidates.append(company)
            title = re.sub(r"\s+", " ", str(job.get("title") or "").strip())
            if title:
                keyword_candidates.extend(extract_feedback_keywords(title))
    return {
        "companies": _dedup_text(company_candidates, limit=40),
        "keywords": _dedup_text(keyword_candidates, limit=40),
    }


__all__ = [
    "SearchRunFeedbackStore",
    "clean_company_name",
    "extract_feedback_keywords",
]
