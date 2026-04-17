from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ...search.analysis.scoring_contract import overall_score
from ..connection import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        text = str(value).strip()
        return int(text) if text else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SearchRunSnapshot:
    search_run_id: int
    candidate_id: int
    run_dir: str
    status: str
    current_stage: str
    last_message: str
    last_event: str
    started_at: str
    updated_at: str
    jobs_found_count: int
    jobs_scored_count: int
    jobs_recommended_count: int


@dataclass(frozen=True)
class JobReviewStateRecord:
    candidate_id: int
    search_profile_id: int
    job_id: int
    job_key: str
    status_code: str
    hidden: bool
    interest_level: str
    applied_date: str
    applied_status: str
    response_status: str
    not_interested: bool
    notes: str
    canonical_url: str
    updated_at: str


@dataclass(frozen=True)
class SearchRunJobBucketCounts:
    jobs_found_count: int = 0
    jobs_scored_count: int = 0
    jobs_recommended_count: int = 0


class SearchRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_run(
        self,
        *,
        candidate_id: int,
        run_dir: str,
        run_type: str = "full",
        status: str = "running",
        current_stage: str = "preparing",
        started_at: str = "",
    ) -> int:
        started = _text(started_at) or _now_iso()
        with self.database.session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO search_runs (
                  candidate_id,
                  run_type,
                  status,
                  run_dir,
                  current_stage,
                  started_at,
                  updated_at,
                  cancelled
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0)
                """,
                (
                    int(candidate_id),
                    _text(run_type) or "full",
                    _text(status) or "running",
                    _text(run_dir),
                    _text(current_stage) or "preparing",
                    started,
                ),
            )
            return int(cursor.lastrowid)

    def update_progress(
        self,
        search_run_id: int,
        *,
        status: str,
        current_stage: str,
        last_message: str = "",
        last_event: str = "",
        started_at: str = "",
    ) -> None:
        normalized_status = _text(status) or "queued"
        normalized_stage = _text(current_stage) or "queued"
        normalized_started = _text(started_at)
        terminal = normalized_status in {"success", "error", "cancelled"}
        with self.database.session() as connection:
            connection.execute(
                """
                UPDATE search_runs
                SET status = ?,
                    current_stage = ?,
                    last_message = ?,
                    last_event = ?,
                    started_at = CASE
                      WHEN ? <> '' THEN ?
                      WHEN COALESCE(started_at, '') = '' THEN ?
                      ELSE started_at
                    END,
                    finished_at = CASE
                      WHEN ? THEN CURRENT_TIMESTAMP
                      ELSE finished_at
                    END,
                    cancelled = CASE
                      WHEN ? = 'cancelled' THEN 1
                      ELSE cancelled
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    normalized_status,
                    normalized_stage,
                    _text(last_message),
                    _text(last_event),
                    normalized_started,
                    normalized_started,
                    _now_iso(),
                    1 if terminal else 0,
                    normalized_status,
                    int(search_run_id),
                ),
            )

    def update_configs(
        self,
        search_run_id: int,
        *,
        config_json: str | None = None,
        resume_config_json: str | None = None,
    ) -> None:
        fields: list[str] = []
        params: list[object] = []
        if config_json is not None:
            fields.append("config_json = ?")
            params.append(config_json)
        if resume_config_json is not None:
            fields.append("resume_config_json = ?")
            params.append(resume_config_json)
        if not fields:
            return
        fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(search_run_id))
        with self.database.session() as connection:
            connection.execute(
                f"UPDATE search_runs SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )

    def update_counts(
        self,
        search_run_id: int,
        *,
        jobs_found_count: int,
        jobs_scored_count: int,
        jobs_recommended_count: int,
    ) -> None:
        with self.database.session() as connection:
            connection.execute(
                """
                UPDATE search_runs
                SET jobs_found_count = ?,
                    jobs_scored_count = ?,
                    jobs_recommended_count = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    max(0, int(jobs_found_count)),
                    max(0, int(jobs_scored_count)),
                    max(0, int(jobs_recommended_count)),
                    int(search_run_id),
                ),
            )

    def mark_error(self, search_run_id: int, message: str) -> None:
        with self.database.session() as connection:
            connection.execute(
                """
                UPDATE search_runs
                SET error_message = ?,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (_text(message), int(search_run_id)),
            )

    def get(self, search_run_id: int) -> SearchRunSnapshot | None:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT
                  id,
                  candidate_id,
                  run_dir,
                  status,
                  current_stage,
                  last_message,
                  last_event,
                  started_at,
                  updated_at,
                  jobs_found_count,
                  jobs_scored_count,
                  jobs_recommended_count
                FROM search_runs
                WHERE id = ?
                """,
                (int(search_run_id),),
            ).fetchone()
        if row is None:
            return None
        return SearchRunSnapshot(
            search_run_id=int(row["id"]),
            candidate_id=int(row["candidate_id"]),
            run_dir=_text(row["run_dir"]),
            status=_text(row["status"]),
            current_stage=_text(row["current_stage"]),
            last_message=_text(row["last_message"]),
            last_event=_text(row["last_event"]),
            started_at=_text(row["started_at"]),
            updated_at=_text(row["updated_at"]),
            jobs_found_count=int(row["jobs_found_count"] or 0),
            jobs_scored_count=int(row["jobs_scored_count"] or 0),
            jobs_recommended_count=int(row["jobs_recommended_count"] or 0),
        )

    def latest_for_candidate(self, candidate_id: int) -> SearchRunSnapshot | None:
        # Runtime workspaces are candidate-scoped, not one unique directory per run.
        # Treat "latest" as the most recently created run record, not the row whose
        # mutable updated_at was touched last by config/count refreshes.
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT
                  id,
                  candidate_id,
                  run_dir,
                  status,
                  current_stage,
                  last_message,
                  last_event,
                  started_at,
                  updated_at,
                  jobs_found_count,
                  jobs_scored_count,
                  jobs_recommended_count
                FROM search_runs
                WHERE candidate_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(candidate_id),),
            ).fetchone()
        if row is None:
            return None
        return SearchRunSnapshot(
            search_run_id=int(row["id"]),
            candidate_id=int(row["candidate_id"]),
            run_dir=_text(row["run_dir"]),
            status=_text(row["status"]),
            current_stage=_text(row["current_stage"]),
            last_message=_text(row["last_message"]),
            last_event=_text(row["last_event"]),
            started_at=_text(row["started_at"]),
            updated_at=_text(row["updated_at"]),
            jobs_found_count=int(row["jobs_found_count"] or 0),
            jobs_scored_count=int(row["jobs_scored_count"] or 0),
            jobs_recommended_count=int(row["jobs_recommended_count"] or 0),
        )

    def recent_for_candidate(self, candidate_id: int, *, limit: int = 5) -> list[SearchRunSnapshot]:
        # Keep recent-run ordering aligned with latest_for_candidate(): newest run id
        # first, independent of later progress/count updates on older rows.
        max_rows = max(1, int(limit))
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT
                  id,
                  candidate_id,
                  run_dir,
                  status,
                  current_stage,
                  last_message,
                  last_event,
                  started_at,
                  updated_at,
                  jobs_found_count,
                  jobs_scored_count,
                  jobs_recommended_count
                FROM search_runs
                WHERE candidate_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(candidate_id), max_rows),
            ).fetchall()
        return [
            SearchRunSnapshot(
                search_run_id=int(row["id"]),
                candidate_id=int(row["candidate_id"]),
                run_dir=_text(row["run_dir"]),
                status=_text(row["status"]),
                current_stage=_text(row["current_stage"]),
                last_message=_text(row["last_message"]),
                last_event=_text(row["last_event"]),
                started_at=_text(row["started_at"]),
                updated_at=_text(row["updated_at"]),
                jobs_found_count=int(row["jobs_found_count"] or 0),
                jobs_scored_count=int(row["jobs_scored_count"] or 0),
                jobs_recommended_count=int(row["jobs_recommended_count"] or 0),
            )
            for row in rows
        ]

    def load_config_payload(
        self,
        search_run_id: int,
        *,
        resume: bool = False,
    ) -> dict[str, Any]:
        column = "resume_config_json" if resume else "config_json"
        with self.database.session() as connection:
            row = connection.execute(
                f"SELECT {column} AS payload_json FROM search_runs WHERE id = ?",
                (int(search_run_id),),
            ).fetchone()
        if row is None:
            return {}
        try:
            payload = json.loads(_text(row["payload_json"]))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}


class CandidateCompanyRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def replace_candidate_pool(
        self,
        *,
        candidate_id: int,
        companies: list[dict[str, Any]],
    ) -> None:
        rows: list[tuple[object, ...]] = []
        for item in companies:
            if not isinstance(item, dict):
                continue
            company_name = _text(item.get("name"))
            website = _text(item.get("website"))
            careers_url = _text(item.get("careersUrl") or item.get("careers_url"))
            company_key = _text(item.get("companyKey"))
            if not company_key:
                company_key = (website or careers_url or company_name.casefold()).casefold()
            if not company_key:
                continue
            rows.append(
                (
                    int(candidate_id),
                    company_key,
                    company_name,
                    website,
                    careers_url,
                    json.dumps(item, ensure_ascii=False),
                )
            )
        with self.database.session() as connection:
            connection.execute(
                "DELETE FROM candidate_companies WHERE candidate_id = ?",
                (int(candidate_id),),
            )
            if rows:
                connection.executemany(
                    """
                    INSERT INTO candidate_companies (
                      candidate_id,
                      company_key,
                      company_name,
                      website,
                      careers_url,
                      company_json,
                      updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    rows,
                )

    def count_candidate_pool(self, *, candidate_id: int) -> int:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM candidate_companies
                WHERE candidate_id = ?
                """,
                (int(candidate_id),),
            ).fetchone()
        return int(row["total"] or 0) if row is not None else 0

    def load_candidate_pool(
        self,
        *,
        candidate_id: int,
    ) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT company_json
                FROM candidate_companies
                WHERE candidate_id = ?
                ORDER BY updated_at ASC, id ASC
                """,
                (int(candidate_id),),
            ).fetchall()
        companies: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(_text(row["company_json"]))
            except Exception:
                continue
            if isinstance(payload, dict):
                companies.append(payload)
        return companies


class CandidateSemanticProfileRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_profile(
        self,
        *,
        candidate_id: int,
        payload: dict[str, Any],
    ) -> None:
        with self.database.session() as connection:
            connection.execute(
                """
                INSERT INTO candidate_semantic_profiles (
                  candidate_id,
                  source_signature,
                  summary,
                  profile_json,
                  updated_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(candidate_id) DO UPDATE SET
                  source_signature = excluded.source_signature,
                  summary = excluded.summary,
                  profile_json = excluded.profile_json,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(candidate_id),
                    _text(payload.get("source_signature")),
                    _text(payload.get("summary")),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def delete_profile(self, *, candidate_id: int) -> None:
        with self.database.session() as connection:
            connection.execute(
                "DELETE FROM candidate_semantic_profiles WHERE candidate_id = ?",
                (int(candidate_id),),
            )


class JobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_job(self, item: dict[str, Any]) -> int | None:
        canonical_url = _text(item.get("canonicalUrl") or item.get("url"))
        if not canonical_url:
            return None
        title = _text(item.get("title")) or "Untitled job"
        company_name = _text(item.get("company"))
        location_text = _text(item.get("location"))
        date_posted = _text(item.get("dateFound"))
        source_quality = _text(item.get("sourceQuality"))
        region_tag = _text(item.get("regionTag"))
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT id FROM jobs WHERE canonical_url = ?",
                (canonical_url,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO jobs (
                      canonical_url,
                      title,
                      company_name,
                      location_text,
                      date_posted,
                      first_seen_at,
                      last_seen_at,
                      is_active,
                      source_quality,
                      region_tag
                    )
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, ?, ?)
                    """,
                    (
                        canonical_url,
                        title,
                        company_name,
                        location_text,
                        date_posted,
                        source_quality,
                        region_tag,
                    ),
                )
                return int(cursor.lastrowid)
            job_id = int(row["id"])
            connection.execute(
                """
                UPDATE jobs
                SET title = ?,
                    company_name = ?,
                    location_text = ?,
                    date_posted = ?,
                    last_seen_at = CURRENT_TIMESTAMP,
                    is_active = 1,
                    source_quality = ?,
                    region_tag = ?
                WHERE id = ?
                """,
                (
                    title,
                    company_name,
                    location_text,
                    date_posted,
                    source_quality,
                    region_tag,
                    job_id,
                ),
            )
            return job_id


class JobAnalysisRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_analysis(
        self,
        *,
        job_id: int,
        search_profile_id: int,
        analysis: dict[str, Any],
    ) -> None:
        match_score = overall_score(analysis)
        transferable_score = _optional_int(analysis.get("transferableScore")) or 0
        domain_score = _optional_int(analysis.get("domainScore")) or 0
        with self.database.session() as connection:
            connection.execute(
                """
                INSERT INTO job_analyses (
                  job_id,
                  search_profile_id,
                  analysis_version,
                  match_score,
                  fit_level_cn,
                  fit_track,
                  job_cluster,
                  industry_track_cn,
                  transferable_score,
                  domain_score,
                  primary_evidence_cn,
                  summary_cn,
                  recommend,
                  recommend_reason_cn,
                  is_job_posting,
                  job_posting_evidence_cn,
                  adjacent_direction_cn,
                  industry_cluster_cn,
                  analysis_json,
                  created_at
                )
                VALUES (?, ?, 'v1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(job_id, search_profile_id, analysis_version) DO UPDATE SET
                  match_score = excluded.match_score,
                  fit_level_cn = excluded.fit_level_cn,
                  fit_track = excluded.fit_track,
                  job_cluster = excluded.job_cluster,
                  industry_track_cn = excluded.industry_track_cn,
                  transferable_score = excluded.transferable_score,
                  domain_score = excluded.domain_score,
                  primary_evidence_cn = excluded.primary_evidence_cn,
                  summary_cn = excluded.summary_cn,
                  recommend = excluded.recommend,
                  recommend_reason_cn = excluded.recommend_reason_cn,
                  is_job_posting = excluded.is_job_posting,
                  job_posting_evidence_cn = excluded.job_posting_evidence_cn,
                  adjacent_direction_cn = excluded.adjacent_direction_cn,
                  industry_cluster_cn = excluded.industry_cluster_cn,
                  analysis_json = excluded.analysis_json,
                  created_at = CURRENT_TIMESTAMP
                """,
                (
                    int(job_id),
                    int(search_profile_id),
                    match_score,
                    _text(analysis.get("fitLevelCn")),
                    _text(analysis.get("fitTrack")),
                    _text(analysis.get("jobCluster")),
                    _text(analysis.get("industryTrackCn")),
                    transferable_score,
                    domain_score,
                    _text(analysis.get("primaryEvidenceCn")),
                    _text(analysis.get("summaryCn") or analysis.get("summary")),
                    1 if bool(analysis.get("recommend")) else 0,
                    _text(analysis.get("recommendReasonCn")),
                    _optional_int(analysis.get("isJobPosting")),
                    _text(analysis.get("jobPostingEvidenceCn")),
                    _text(analysis.get("adjacentDirectionCn")),
                    _text(analysis.get("industryClusterCn")),
                    json.dumps(analysis, ensure_ascii=False),
                ),
            )


class JobReviewStateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_for_candidate(self, candidate_id: int) -> list[JobReviewStateRecord]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT
                  jrs.candidate_id,
                  jrs.search_profile_id,
                  jrs.job_id,
                  jrs.job_key,
                  jrs.status_code,
                  jrs.hidden,
                  jrs.interest_level,
                  jrs.applied_date,
                  jrs.applied_status,
                  jrs.response_status,
                  jrs.not_interested,
                  jrs.notes,
                  jrs.updated_at,
                  COALESCE(j.canonical_url, '') AS canonical_url
                FROM job_review_states jrs
                LEFT JOIN jobs j ON j.id = jrs.job_id
                WHERE jrs.candidate_id = ?
                ORDER BY jrs.updated_at DESC, jrs.id DESC
                """,
                (int(candidate_id),),
            ).fetchall()
        records: list[JobReviewStateRecord] = []
        seen: set[str] = set()
        for row in rows:
            job_key = _text(row["job_key"]) or _text(row["canonical_url"])
            if not job_key or job_key in seen:
                continue
            seen.add(job_key)
            records.append(
                JobReviewStateRecord(
                    candidate_id=int(row["candidate_id"]),
                    search_profile_id=int(row["search_profile_id"]),
                    job_id=int(row["job_id"]),
                    job_key=job_key,
                    status_code=_text(row["status_code"]),
                    hidden=bool(row["hidden"]),
                    interest_level=_text(row["interest_level"]),
                    applied_date=_text(row["applied_date"]),
                    applied_status=_text(row["applied_status"]),
                    response_status=_text(row["response_status"]),
                    not_interested=bool(row["not_interested"]),
                    notes=_text(row["notes"]),
                    canonical_url=_text(row["canonical_url"]),
                    updated_at=_text(row["updated_at"]),
                )
            )
        return records

    def load_candidate_review_state(self, candidate_id: int) -> tuple[dict[str, str], set[str]]:
        statuses: dict[str, str] = {}
        hidden: set[str] = set()
        for record in self.list_for_candidate(candidate_id):
            if record.status_code:
                statuses[record.job_key] = record.status_code
            if record.hidden:
                hidden.add(record.job_key)
        return statuses, hidden

    def load_candidate_manual_alias_map(self, candidate_id: int) -> dict[str, dict[str, str]]:
        manual_by_alias: dict[str, dict[str, str]] = {}
        for record in self.list_for_candidate(candidate_id):
            manual = {
                "interest": record.interest_level,
                "appliedDate": record.applied_date,
                "appliedCn": record.applied_status,
                "responseStatus": record.response_status,
                "notInterested": "是" if record.not_interested else "",
                "notesCn": record.notes,
            }
            if not any(str(value or "").strip() for value in manual.values()):
                continue
            for alias in (record.job_key, record.canonical_url):
                normalized_alias = _text(alias)
                if normalized_alias:
                    manual_by_alias[normalized_alias] = dict(manual)
        return manual_by_alias

    def replace_candidate_review_state(
        self,
        *,
        candidate_id: int,
        status_by_job_key: dict[str, str],
        hidden_job_keys: set[str],
    ) -> None:
        existing = {
            record.job_key: {
                "search_profile_id": record.search_profile_id,
                "job_id": record.job_id,
                "status_code": record.status_code,
                "hidden": record.hidden,
                "interest_level": record.interest_level,
                "applied_date": record.applied_date,
                "applied_status": record.applied_status,
                "response_status": record.response_status,
                "not_interested": record.not_interested,
                "notes": record.notes,
            }
            for record in self.list_for_candidate(candidate_id)
            if record.job_key
        }
        keys = {
            str(key).strip()
            for key in {*(existing.keys()), *(status_by_job_key.keys()), *hidden_job_keys}
            if str(key).strip()
        }
        if not keys and not existing:
            return
        default_profile_id = self._default_profile_id(candidate_id)
        persisted_rows: list[dict[str, Any]] = []
        for job_key in sorted(keys):
            state = dict(existing.get(job_key, {}))
            state["status_code"] = _text(status_by_job_key.get(job_key))
            state["hidden"] = job_key in hidden_job_keys
            if state.get("search_profile_id") is None:
                state["search_profile_id"] = default_profile_id
            if state.get("job_id") is None:
                state["job_id"] = self._resolve_job_id(candidate_id, job_key)
            if not self._row_has_any_state(state):
                continue
            if not state.get("search_profile_id") or not state.get("job_id"):
                continue
            persisted_rows.append(
                {
                    "candidate_id": int(candidate_id),
                    "search_profile_id": int(state["search_profile_id"]),
                    "job_id": int(state["job_id"]),
                    "job_key": job_key,
                    "status_code": _text(state.get("status_code")),
                    "hidden": bool(state.get("hidden")),
                    "interest_level": _text(state.get("interest_level")),
                    "applied_date": _text(state.get("applied_date")),
                    "applied_status": _text(state.get("applied_status")),
                    "response_status": _text(state.get("response_status")),
                    "not_interested": bool(state.get("not_interested")),
                    "notes": _text(state.get("notes")),
                }
            )
        self._replace_candidate_rows(candidate_id, persisted_rows)

    def merge_manual_fields_from_jobs(
        self,
        *,
        candidate_id: int,
        jobs: list[dict[str, Any]],
    ) -> None:
        existing = {
            record.job_key: {
                "search_profile_id": record.search_profile_id,
                "job_id": record.job_id,
                "status_code": record.status_code,
                "hidden": record.hidden,
                "interest_level": record.interest_level,
                "applied_date": record.applied_date,
                "applied_status": record.applied_status,
                "response_status": record.response_status,
                "not_interested": record.not_interested,
                "notes": record.notes,
                "canonical_url": record.canonical_url,
            }
            for record in self.list_for_candidate(candidate_id)
            if record.job_key
        }
        by_alias: dict[str, str] = {}
        for job_key, state in existing.items():
            for alias in (job_key, _text(state.get("canonical_url"))):
                if alias:
                    by_alias[alias] = job_key

        default_profile_id = self._default_profile_id(candidate_id)
        updated = dict(existing)
        for job in jobs:
            if not isinstance(job, dict):
                continue
            manual = {
                "interest_level": _text(job.get("interest")),
                "applied_date": _text(job.get("appliedDate")),
                "applied_status": _text(job.get("appliedCn")),
                "response_status": _text(job.get("responseStatus")),
                "not_interested": _text(job.get("notInterested")) in {"是", "yes", "true", "1"},
                "notes": _text(job.get("notesCn")),
            }
            if not self._row_has_manual_state(manual):
                continue
            aliases = self._job_aliases(job)
            target_key = next((by_alias[alias] for alias in aliases if alias in by_alias), "")
            if not target_key:
                target_key = aliases[0] if aliases else ""
            if not target_key:
                continue
            state = dict(updated.get(target_key, {}))
            if state.get("search_profile_id") is None:
                state["search_profile_id"] = default_profile_id
            if state.get("job_id") is None:
                state["job_id"] = self._resolve_job_id(candidate_id, target_key, job=job)
            if not state.get("search_profile_id") or not state.get("job_id"):
                continue
            state["status_code"] = _text(state.get("status_code"))
            state["hidden"] = bool(state.get("hidden"))
            state["interest_level"] = manual["interest_level"]
            state["applied_date"] = manual["applied_date"]
            state["applied_status"] = manual["applied_status"]
            state["response_status"] = manual["response_status"]
            state["not_interested"] = manual["not_interested"]
            state["notes"] = manual["notes"]
            updated[target_key] = state
            by_alias[target_key] = target_key
            for alias in aliases:
                if alias:
                    by_alias[alias] = target_key

        persisted_rows: list[dict[str, Any]] = []
        for job_key, state in sorted(updated.items()):
            if not self._row_has_any_state(state):
                continue
            if not state.get("search_profile_id") or not state.get("job_id"):
                continue
            persisted_rows.append(
                {
                    "candidate_id": int(candidate_id),
                    "search_profile_id": int(state["search_profile_id"]),
                    "job_id": int(state["job_id"]),
                    "job_key": job_key,
                    "status_code": _text(state.get("status_code")),
                    "hidden": bool(state.get("hidden")),
                    "interest_level": _text(state.get("interest_level")),
                    "applied_date": _text(state.get("applied_date")),
                    "applied_status": _text(state.get("applied_status")),
                    "response_status": _text(state.get("response_status")),
                    "not_interested": bool(state.get("not_interested")),
                    "notes": _text(state.get("notes")),
                }
            )
        self._replace_candidate_rows(candidate_id, persisted_rows)

    def _replace_candidate_rows(self, candidate_id: int, rows: list[dict[str, Any]]) -> None:
        with self.database.session() as connection:
            connection.execute(
                "DELETE FROM job_review_states WHERE candidate_id = ?",
                (int(candidate_id),),
            )
            if not rows:
                return
            connection.executemany(
                """
                INSERT INTO job_review_states (
                  candidate_id,
                  search_profile_id,
                  job_id,
                  job_key,
                  status_code,
                  hidden,
                  interest_level,
                  applied_date,
                  applied_status,
                  response_status,
                  not_interested,
                  notes,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [
                    (
                        int(row["candidate_id"]),
                        int(row["search_profile_id"]),
                        int(row["job_id"]),
                        _text(row["job_key"]),
                        _text(row.get("status_code")),
                        1 if bool(row.get("hidden")) else 0,
                        _text(row.get("interest_level")),
                        _text(row.get("applied_date")),
                        _text(row.get("applied_status")),
                        _text(row.get("response_status")),
                        1 if bool(row.get("not_interested")) else 0,
                        _text(row.get("notes")),
                    )
                    for row in rows
                    if _text(row.get("job_key"))
                ],
            )

    def _default_profile_id(self, candidate_id: int) -> int | None:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM search_profiles
                WHERE candidate_id = ?
                ORDER BY is_active DESC, updated_at DESC, id DESC
                LIMIT 1
                """,
                (int(candidate_id),),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def _resolve_job_id(
        self,
        candidate_id: int,
        job_key: str,
        *,
        job: dict[str, Any] | None = None,
    ) -> int | None:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT
                  srj.job_id,
                  srj.job_json
                FROM search_run_jobs srj
                JOIN search_runs sr ON sr.id = srj.search_run_id
                WHERE srj.candidate_id = ? AND srj.job_key = ?
                ORDER BY sr.id DESC, srj.updated_at DESC, srj.id DESC
                LIMIT 1
                """,
                (int(candidate_id), _text(job_key)),
            ).fetchone()
        if row is not None and row["job_id"] is not None:
            return int(row["job_id"])
        payload = job if isinstance(job, dict) else None
        if payload is None and row is not None:
            try:
                decoded = json.loads(_text(row["job_json"]))
            except Exception:
                decoded = None
            if isinstance(decoded, dict):
                payload = decoded
        if payload is None and _text(job_key).startswith(("http://", "https://")):
            payload = {
                "canonicalUrl": _text(job_key),
                "url": _text(job_key),
                "title": "Untitled job",
            }
        if not isinstance(payload, dict):
            return None
        return JobRepository(self.database).upsert_job(payload)

    @staticmethod
    def _job_aliases(job: dict[str, Any]) -> list[str]:
        aliases: list[str] = []
        for candidate in (
            _text(job.get("url")),
            _text(job.get("canonicalUrl")),
            _text(job.get("outputUrl")),
        ):
            if candidate and candidate not in aliases:
                aliases.append(candidate)
        title = _text(job.get("title")).casefold()
        company = _text(job.get("company")).casefold()
        location = _text(job.get("location")).casefold().replace(",", "").replace(" ", "")
        if title and company:
            composite = f"{company}|{title}|{location}"
            if composite not in aliases:
                aliases.append(composite)
        return aliases

    @staticmethod
    def _row_has_manual_state(state: dict[str, Any]) -> bool:
        return any(
            [
                _text(state.get("interest_level")),
                _text(state.get("applied_date")),
                _text(state.get("applied_status")),
                _text(state.get("response_status")),
                _text(state.get("notes")),
                "1" if bool(state.get("not_interested")) else "",
            ]
        )

    @classmethod
    def _row_has_any_state(cls, state: dict[str, Any]) -> bool:
        return bool(_text(state.get("status_code")) or bool(state.get("hidden")) or cls._row_has_manual_state(state))


class SearchRunJobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def replace_bucket(
        self,
        *,
        search_run_id: int,
        candidate_id: int,
        job_bucket: str,
        rows: list[dict[str, Any]],
    ) -> None:
        normalized_bucket = _text(job_bucket) or "jobs"
        with self.database.session() as connection:
            connection.execute(
                "DELETE FROM search_run_jobs WHERE search_run_id = ? AND job_bucket = ?",
                (int(search_run_id), normalized_bucket),
            )
            if not rows:
                return
            connection.executemany(
                """
                INSERT INTO search_run_jobs (
                  search_run_id,
                  candidate_id,
                  job_id,
                  job_key,
                  job_bucket,
                  canonical_url,
                  source_url,
                  title,
                  company_name,
                  location_text,
                  date_found,
                  match_score,
                  analysis_completed,
                  recommended,
                  pending_resume,
                  job_json,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [
                    (
                        int(search_run_id),
                        int(candidate_id),
                        row.get("job_id"),
                        _text(row.get("job_key")),
                        normalized_bucket,
                        _text(row.get("canonical_url")),
                        _text(row.get("source_url")),
                        _text(row.get("title")) or "Untitled job",
                        _text(row.get("company_name")),
                        _text(row.get("location_text")),
                        _text(row.get("date_found")),
                        row.get("match_score"),
                        1 if bool(row.get("analysis_completed")) else 0,
                        1 if bool(row.get("recommended")) else 0,
                        1 if bool(row.get("pending_resume")) else 0,
                        _text(row.get("job_json")),
                    )
                    for row in rows
                    if _text(row.get("job_key"))
                ],
            )

    def load_bucket_jobs(self, *, search_run_id: int, job_bucket: str) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT job_json
                FROM search_run_jobs
                WHERE search_run_id = ? AND job_bucket = ?
                ORDER BY updated_at ASC, id ASC
                """,
                (int(search_run_id), _text(job_bucket) or "jobs"),
            ).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(_text(row["job_json"]))
            except Exception:
                continue
            if isinstance(payload, dict):
                jobs.append(payload)
        return jobs

    def summarize_bucket_counts(self, *, search_run_id: int) -> SearchRunJobBucketCounts:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT
                  SUM(CASE WHEN job_bucket = 'found' THEN 1 ELSE 0 END) AS jobs_found_count,
                  SUM(
                    CASE
                      WHEN job_bucket = 'all' AND analysis_completed = 1 THEN 1
                      ELSE 0
                    END
                  ) AS jobs_scored_count,
                  SUM(CASE WHEN job_bucket = 'recommended' THEN 1 ELSE 0 END) AS jobs_recommended_count
                FROM search_run_jobs
                WHERE search_run_id = ?
                """,
                (int(search_run_id),),
            ).fetchone()
        if row is None:
            return SearchRunJobBucketCounts()
        return SearchRunJobBucketCounts(
            jobs_found_count=int(row["jobs_found_count"] or 0),
            jobs_scored_count=int(row["jobs_scored_count"] or 0),
            jobs_recommended_count=int(row["jobs_recommended_count"] or 0),
        )


__all__ = [
    "CandidateCompanyRepository",
    "CandidateSemanticProfileRepository",
    "JobAnalysisRepository",
    "JobReviewStateRecord",
    "JobReviewStateRepository",
    "JobRepository",
    "SearchRunJobRepository",
    "SearchRunJobBucketCounts",
    "SearchRunRepository",
    "SearchRunSnapshot",
]
