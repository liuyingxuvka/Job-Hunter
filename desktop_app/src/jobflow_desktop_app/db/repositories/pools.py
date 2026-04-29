from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ...search.output.final_output import has_current_output_eligibility
from ..connection import Database
from ..target_role_cleanup import (
    CURRENT_FIT_STATUS_KEY,
    HISTORICAL_ONLY_STATUS,
    NEEDS_RESCORE_STATUS,
    NOT_CURRENT_FIT_STATUS,
    RECOMMENDATION_DISPLAY_KEY,
    mark_preserved_recommendation_analysis,
    sanitize_job_payload_role_bindings,
    valid_profile_ids_for_candidate,
)


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


def _loads_object(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(_text(value))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _table_exists(connection: Any, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_text(table_name),),
    ).fetchone()
    return row is not None


def _analysis_completed(analysis: dict[str, Any]) -> bool:
    if not analysis:
        return False
    if bool(analysis.get("prefilterRejected")):
        return True
    return _optional_int(analysis.get("overallScore")) is not None


def _analysis_score(analysis: dict[str, Any]) -> int | None:
    return _optional_int(analysis.get("overallScore"))


def _has_any_current_output_stamp(analysis: dict[str, Any]) -> bool:
    return (
        analysis.get("eligibleForOutput") is True
        and "outputEligibilityRuleVersion" in analysis
        and bool(_text(analysis.get("outputEligibilityPolicyKey")))
    )


@dataclass(frozen=True)
class CandidateJobPoolRecord:
    candidate_id: int
    job_id: int
    job_key: str
    canonical_url: str
    source_url: str
    title: str
    company_name: str
    location_text: str
    discovery_status: str
    url_status: str
    prefilter_status: str
    jd_fetch_status: str
    scoring_status: str
    recommendation_status: str
    output_status: str
    pool_status: str
    user_status: str
    application_status: str
    trash_status: str
    review_status_code: str
    hidden: bool
    not_interested: bool
    match_score: int | None
    last_run_id: int | None


@dataclass(frozen=True)
class CandidateJobPoolSummary:
    total_jobs: int = 0
    scored_jobs: int = 0
    recommended_jobs: int = 0
    pending_jobs: int = 0
    trashed_jobs: int = 0
    rejected_jobs: int = 0


@dataclass(frozen=True)
class CandidateJobPoolBackfillResult:
    candidate_id: int
    source_rows: int
    upserted_jobs: int
    recommended_jobs: int


class CandidateJobPoolRepository:
    """Durable per-candidate job pool with one row per real job."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def backfill_candidate_from_legacy(self, candidate_id: int) -> CandidateJobPoolBackfillResult:
        with self.database.session() as connection:
            return self.backfill_candidate_from_legacy_connection(
                connection,
                candidate_id,
            )

    @classmethod
    def backfill_candidate_from_legacy_connection(
        cls,
        connection: Any,
        candidate_id: int,
    ) -> CandidateJobPoolBackfillResult:
        if not _table_exists(connection, "search_run_jobs"):
            return CandidateJobPoolBackfillResult(
                candidate_id=int(candidate_id),
                source_rows=0,
                upserted_jobs=0,
                recommended_jobs=0,
            )
        runtime_config = cls._latest_runtime_config(connection, candidate_id)
        source_rows = connection.execute(
            """
            SELECT
              srj.search_run_id,
              srj.job_id,
              srj.job_key,
              srj.canonical_url,
              srj.source_url,
              srj.title,
              srj.company_name,
              srj.location_text,
              srj.date_found,
              srj.match_score,
              srj.analysis_completed,
              srj.recommended,
              srj.pending_resume,
              srj.job_json,
              srj.updated_at,
              COALESCE(j.canonical_url, '') AS persisted_canonical_url,
              COALESCE(j.title, '') AS persisted_title,
              COALESCE(j.company_name, '') AS persisted_company_name,
              COALESCE(j.location_text, '') AS persisted_location_text,
              COALESCE(j.date_posted, '') AS persisted_date_posted,
              COALESCE(j.last_seen_at, '') AS persisted_last_seen_at,
              cc.id AS candidate_company_id
            FROM search_run_jobs srj
            LEFT JOIN jobs j ON j.id = srj.job_id
            LEFT JOIN candidate_companies cc
              ON cc.candidate_id = srj.candidate_id
             AND lower(cc.company_name) = lower(srj.company_name)
            WHERE srj.candidate_id = ?
              AND srj.job_id IS NOT NULL
              AND COALESCE(srj.job_key, '') <> ''
            ORDER BY srj.updated_at ASC, srj.id ASC
            """,
            (int(candidate_id),),
        ).fetchall()
        review_rows = connection.execute(
            """
            SELECT
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
              COALESCE(j.canonical_url, '') AS canonical_url
            FROM job_review_states jrs
            LEFT JOIN jobs j ON j.id = jrs.job_id
            WHERE jrs.candidate_id = ?
            ORDER BY jrs.updated_at ASC, jrs.id ASC
            """,
            (int(candidate_id),),
        ).fetchall()
        rows = cls._build_pool_rows(
            candidate_id,
            source_rows,
            review_rows,
            runtime_config=runtime_config,
        )
        cls._upsert_pool_rows(connection, rows)
        return CandidateJobPoolBackfillResult(
            candidate_id=int(candidate_id),
            source_rows=len(source_rows),
            upserted_jobs=len(rows),
            recommended_jobs=sum(1 for row in rows if cls._row_is_visible_recommendation(row)),
        )

    def upsert_runtime_jobs(
        self,
        *,
        candidate_id: int,
        search_run_id: int,
        jobs_by_key: dict[str, dict[str, Any]],
        job_ids: dict[str, int],
    ) -> None:
        with self.database.session() as connection:
            valid_profile_ids = valid_profile_ids_for_candidate(connection, int(candidate_id))
            rows: list[dict[str, Any]] = []
            for key, item in jobs_by_key.items():
                if not isinstance(item, dict):
                    continue
                job_id = job_ids.get(key)
                if job_id is None:
                    continue
                sanitized = sanitize_job_payload_role_bindings(item, valid_profile_ids)
                rows.append(
                    self._build_runtime_row(
                        candidate_id=candidate_id,
                        search_run_id=search_run_id,
                        job_id=job_id,
                        job_key=key,
                        item=sanitized.payload,
                    )
                )
            self._upsert_pool_rows(connection, rows)

    def mark_recommended_output_set(self, *, candidate_id: int, job_keys: set[str]) -> None:
        normalized_keys = {_text(key) for key in job_keys if _text(key)}
        with self.database.session() as connection:
            if normalized_keys:
                placeholders = ",".join("?" for _ in normalized_keys)
                rows = connection.execute(
                    f"""
                    SELECT
                      id,
                      analysis_json,
                      job_json,
                      recommendation_status,
                      output_status,
                      trash_status,
                      hidden,
                      not_interested,
                      review_status_code
                    FROM candidate_jobs
                    WHERE candidate_id = ?
                      AND recommendation_status = 'pass'
                      AND pool_status = 'active'
                      AND job_key NOT IN ({placeholders})
                    """,
                    (int(candidate_id), *sorted(normalized_keys)),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT
                      id,
                      analysis_json,
                      job_json,
                      recommendation_status,
                      output_status,
                      trash_status,
                      hidden,
                      not_interested,
                      review_status_code
                    FROM candidate_jobs
                    WHERE candidate_id = ?
                      AND recommendation_status = 'pass'
                      AND pool_status = 'active'
                    """,
                    (int(candidate_id),),
                ).fetchall()
            reject_updates: list[tuple[int]] = []
            preserve_updates: list[tuple[str, str, int]] = []
            for row in rows:
                if self._sqlite_row_is_visible_recommendation(row):
                    preserve_updates.append(
                        self._preserve_visible_recommendation_on_output_refresh(row)
                    )
                else:
                    reject_updates.append((int(row["id"]),))
            if reject_updates:
                connection.executemany(
                    """
                    UPDATE candidate_jobs
                    SET output_status = 'reject',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    reject_updates,
                )
            if preserve_updates:
                connection.executemany(
                    """
                    UPDATE candidate_jobs
                    SET analysis_json = ?,
                        job_json = ?,
                        output_status = 'pass',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    preserve_updates,
                )

    def persist_display_i18n(self, *, candidate_id: int, updates: dict[str, dict[str, Any]]) -> None:
        normalized_updates = {
            _text(key): value
            for key, value in updates.items()
            if _text(key) and isinstance(value, dict)
        }
        if not normalized_updates:
            return
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT id, job_key, canonical_url, source_url, job_json
                FROM candidate_jobs
                WHERE candidate_id = ?
                """,
                (int(candidate_id),),
            ).fetchall()
            update_rows: list[tuple[str, int]] = []
            for row in rows:
                aliases = [
                    _text(row["job_key"]),
                    _text(row["canonical_url"]),
                    _text(row["source_url"]),
                ]
                display_i18n = next(
                    (
                        normalized_updates[alias]
                        for alias in aliases
                        if alias in normalized_updates
                    ),
                    None,
                )
                if display_i18n is None:
                    continue
                payload = _loads_object(row["job_json"])
                payload["displayI18n"] = dict(display_i18n)
                update_rows.append((json.dumps(payload, ensure_ascii=False), int(row["id"])))
            if update_rows:
                connection.executemany(
                    """
                    UPDATE candidate_jobs
                    SET job_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    update_rows,
                )

    def list_for_candidate(self, candidate_id: int) -> list[CandidateJobPoolRecord]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT
                  candidate_id,
                  job_id,
                  job_key,
                  canonical_url,
                  source_url,
                  title,
                  company_name,
                  location_text,
                  discovery_status,
                  url_status,
                  prefilter_status,
                  jd_fetch_status,
                  scoring_status,
                  recommendation_status,
                  output_status,
                  pool_status,
                  user_status,
                  application_status,
                  trash_status,
                  review_status_code,
                  hidden,
                  not_interested,
                  match_score,
                  last_run_id
                FROM candidate_jobs
                WHERE candidate_id = ?
                ORDER BY last_seen_at DESC, id DESC
                """,
                (int(candidate_id),),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_recommended_for_candidate(self, candidate_id: int) -> list[CandidateJobPoolRecord]:
        return [
            record
            for record in self.list_for_candidate(candidate_id)
            if self._is_visible_recommendation(record)
        ]

    def load_job_payloads_for_candidate(self, candidate_id: int) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT
                  job_json,
                  analysis_json,
                  title,
                  company_name,
                  location_text,
                  source_url,
                  canonical_url,
                  date_found,
                  match_score
                FROM candidate_jobs
                WHERE candidate_id = ?
                  AND pool_status = 'active'
                ORDER BY last_seen_at DESC, id DESC
                """,
                (int(candidate_id),),
            ).fetchall()
        return [self._payload_from_row(row) for row in rows]

    def load_recommended_payloads_for_candidate(self, candidate_id: int) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT
                  job_json,
                  analysis_json,
                  title,
                  company_name,
                  location_text,
                  source_url,
                  canonical_url,
                  date_found,
                  match_score
                FROM candidate_jobs
                WHERE candidate_id = ?
                  AND recommendation_status = 'pass'
                  AND output_status = 'pass'
                  AND trash_status <> 'trashed'
                  AND hidden = 0
                  AND not_interested = 0
                  AND review_status_code NOT IN ('rejected', 'dropped')
                  AND pool_status = 'active'
                ORDER BY last_seen_at DESC, id DESC
                """,
                (int(candidate_id),),
            ).fetchall()
        return [self._payload_from_row(row) for row in rows]

    def load_pending_payloads_for_candidate(self, candidate_id: int) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT
                  job_json,
                  analysis_json,
                  title,
                  company_name,
                  location_text,
                  source_url,
                  canonical_url,
                  date_found,
                  match_score
                FROM candidate_jobs
                WHERE candidate_id = ?
                  AND scoring_status = 'pending'
                  AND url_status = 'pass'
                  AND trash_status <> 'trashed'
                  AND hidden = 0
                  AND not_interested = 0
                  AND review_status_code NOT IN ('rejected', 'dropped')
                  AND pool_status = 'active'
                ORDER BY last_seen_at ASC, id ASC
                """,
                (int(candidate_id),),
            ).fetchall()
        return [self._payload_from_row(row) for row in rows]

    def summarize_candidate(self, candidate_id: int) -> CandidateJobPoolSummary:
        records = self.list_for_candidate(candidate_id)
        return CandidateJobPoolSummary(
            total_jobs=len(records),
            scored_jobs=sum(1 for record in records if record.scoring_status == "scored"),
            recommended_jobs=sum(1 for record in records if self._is_visible_recommendation(record)),
            pending_jobs=sum(1 for record in records if self._is_pending(record)),
            trashed_jobs=sum(1 for record in records if record.trash_status == "trashed" or record.hidden),
            rejected_jobs=sum(
                1
                for record in records
                if record.recommendation_status == "reject"
                or record.output_status == "reject"
                or record.review_status_code in {"rejected", "dropped"}
                or record.not_interested
            ),
        )

    @classmethod
    def _build_pool_rows(
        cls,
        candidate_id: int,
        source_rows: list[Any],
        review_rows: list[Any],
        *,
        runtime_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        review_by_job_id: dict[int, dict[str, Any]] = {}
        review_by_key: dict[str, dict[str, Any]] = {}
        for row in review_rows:
            review = {
                "status_code": _text(row["status_code"]),
                "hidden": bool(row["hidden"]),
                "interest_level": _text(row["interest_level"]),
                "applied_date": _text(row["applied_date"]),
                "applied_status": _text(row["applied_status"]),
                "response_status": _text(row["response_status"]),
                "not_interested": bool(row["not_interested"]),
                "notes": _text(row["notes"]),
            }
            job_id = _optional_int(row["job_id"])
            if job_id is not None:
                review_by_job_id[job_id] = review
            for alias in (_text(row["job_key"]), _text(row["canonical_url"])):
                if alias:
                    review_by_key[alias] = review

        by_job_id: dict[int, dict[str, Any]] = {}
        for row in source_rows:
            job_id = _optional_int(row["job_id"])
            if job_id is None:
                continue
            payload = _loads_object(row["job_json"])
            analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
            existing = by_job_id.get(job_id)
            merged = cls._merge_legacy_row(
                existing,
                row,
                payload,
                analysis,
                candidate_id,
                runtime_config=runtime_config,
            )
            by_job_id[job_id] = merged

        rows: list[dict[str, Any]] = []
        for job_id, state in sorted(by_job_id.items()):
            review = review_by_job_id.get(job_id) or review_by_key.get(_text(state.get("job_key"))) or {}
            state.update(cls._review_state_columns(review))
            rows.append(state)
        return rows

    @classmethod
    def _build_runtime_row(
        cls,
        *,
        candidate_id: int,
        search_run_id: int,
        job_id: int,
        job_key: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
        row = {
            "candidate_id": int(candidate_id),
            "job_id": int(job_id),
            "candidate_company_id": None,
            "job_key": _text(job_key),
            "canonical_url": _text(item.get("canonicalUrl")) or _text(item.get("url")),
            "source_url": _text(item.get("url")),
            "title": _text(item.get("title")),
            "company_name": _text(item.get("company")),
            "location_text": _text(item.get("location")),
            "date_found": _text(item.get("dateFound")),
            "discovery_status": "found",
            "url_status": "pass" if _text(item.get("canonicalUrl")) or _text(item.get("url")) else "unknown",
            "job_json": json.dumps(item, ensure_ascii=False),
            "analysis_json": json.dumps(analysis, ensure_ascii=False) if analysis else "",
            "match_score": _analysis_score(analysis),
            "first_seen_at": "",
            "last_seen_at": "",
            "last_run_id": int(search_run_id),
        }
        cls._merge_analysis_stamps(
            row,
            analysis,
            {
                "analysis_completed": _analysis_completed(analysis),
                "recommended": bool(analysis.get("recommend")),
            },
            item,
            {},
        )
        row.update(cls._review_state_columns({}))
        return row

    @classmethod
    def _merge_legacy_row(
        cls,
        existing: dict[str, Any] | None,
        row: Any,
        payload: dict[str, Any],
        analysis: dict[str, Any],
        candidate_id: int,
        *,
        runtime_config: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = int(row["job_id"])
        canonical_url = (
            _text(row["canonical_url"])
            or _text(row["persisted_canonical_url"])
            or _text(payload.get("canonicalUrl"))
            or _text(payload.get("url"))
        )
        job_key = _text(row["job_key"]) or canonical_url
        state = dict(existing or {})
        state.setdefault("candidate_id", int(candidate_id))
        state.setdefault("job_id", job_id)
        state.setdefault("first_seen_at", _text(row["updated_at"]) or _text(row["date_found"]))
        state["candidate_company_id"] = row["candidate_company_id"] if row["candidate_company_id"] is not None else state.get("candidate_company_id")
        state["job_key"] = job_key or _text(state.get("job_key"))
        state["canonical_url"] = canonical_url or _text(state.get("canonical_url"))
        state["source_url"] = _text(row["source_url"]) or _text(payload.get("url")) or _text(state.get("source_url"))
        state["title"] = _text(row["title"]) or _text(row["persisted_title"]) or _text(payload.get("title")) or _text(state.get("title"))
        state["company_name"] = _text(row["company_name"]) or _text(row["persisted_company_name"]) or _text(payload.get("company")) or _text(state.get("company_name"))
        state["location_text"] = _text(row["location_text"]) or _text(row["persisted_location_text"]) or _text(payload.get("location")) or _text(state.get("location_text"))
        state["date_found"] = _text(row["date_found"]) or _text(row["persisted_date_posted"]) or _text(payload.get("dateFound")) or _text(state.get("date_found"))
        state["last_seen_at"] = _text(row["updated_at"]) or _text(row["persisted_last_seen_at"]) or _text(state.get("last_seen_at"))
        state["last_run_id"] = _optional_int(row["search_run_id"]) or state.get("last_run_id")
        state["job_json"] = json.dumps(payload, ensure_ascii=False) if payload else _text(state.get("job_json"))
        if analysis:
            state["analysis_json"] = json.dumps(analysis, ensure_ascii=False)
        state["match_score"] = _optional_int(row["match_score"]) or _analysis_score(analysis) or state.get("match_score")
        state["discovery_status"] = "found"
        state["url_status"] = "pass" if state.get("canonical_url") else "unknown"
        cls._merge_analysis_stamps(state, analysis, row, payload, runtime_config)
        return state

    @staticmethod
    def _merge_analysis_stamps(
        state: dict[str, Any],
        analysis: dict[str, Any],
        row: Any,
        payload: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> None:
        completed = bool(row["analysis_completed"]) or _analysis_completed(analysis)
        recommended = bool(row["recommended"]) or bool(analysis.get("recommend"))
        if bool(analysis.get("prefilterRejected")):
            state["prefilter_status"] = "reject"
            state["scoring_status"] = "skipped"
        elif analysis or completed:
            state["prefilter_status"] = "pass"
            state["scoring_status"] = "scored" if completed else "pending"
        else:
            state.setdefault("prefilter_status", "pending")
            state.setdefault("scoring_status", "pending")

        if analysis or completed or recommended:
            state["jd_fetch_status"] = "pass"
        else:
            state.setdefault("jd_fetch_status", "pending")

        if recommended:
            state["recommendation_status"] = "pass"
            has_current_stamp = (
                bool(analysis.get("eligibleForOutput"))
                and (
                    has_current_output_eligibility(payload, runtime_config)
                    or (not runtime_config and _has_any_current_output_stamp(analysis))
                )
            )
            state["output_status"] = "pass" if has_current_stamp else "reject"
        elif completed:
            state["recommendation_status"] = "reject"
            state["output_status"] = "reject"
        else:
            state.setdefault("recommendation_status", "pending")
            state.setdefault("output_status", "pending")

        state.setdefault("pool_status", "active")
        state.setdefault("user_status", "")
        state.setdefault("application_status", "")
        state.setdefault("trash_status", "active")
        state.setdefault("rejection_reason", _text(analysis.get("recommendReasonCn")))

    @staticmethod
    def _review_state_columns(review: dict[str, Any]) -> dict[str, Any]:
        status_code = _text(review.get("status_code"))
        hidden = bool(review.get("hidden"))
        not_interested = bool(review.get("not_interested"))
        user_status = status_code
        if hidden:
            user_status = "hidden"
        elif not_interested and not user_status:
            user_status = "not_interested"
        return {
            "user_status": user_status,
            "application_status": status_code if status_code in {"applied", "offered", "rejected"} else "",
            "trash_status": "trashed" if hidden else "active",
            "review_status_code": status_code,
            "hidden": hidden,
            "interest_level": _text(review.get("interest_level")),
            "applied_date": _text(review.get("applied_date")),
            "applied_status": _text(review.get("applied_status")),
            "response_status": _text(review.get("response_status")),
            "not_interested": not_interested,
            "notes": _text(review.get("notes")),
        }

    @staticmethod
    def _latest_runtime_config(connection: Any, candidate_id: int) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT config_json
            FROM search_runs
            WHERE candidate_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(candidate_id),),
        ).fetchone()
        if row is None:
            return {}
        return _loads_object(row["config_json"])

    @staticmethod
    def _payload_from_row(row: Any) -> dict[str, Any]:
        payload = _loads_object(row["job_json"])
        if not payload:
            payload = {
                "title": _text(row["title"]),
                "company": _text(row["company_name"]),
                "location": _text(row["location_text"]),
                "url": _text(row["source_url"]) or _text(row["canonical_url"]),
                "canonicalUrl": _text(row["canonical_url"]),
                "dateFound": _text(row["date_found"]),
            }
        analysis = _loads_object(row["analysis_json"])
        if analysis:
            payload["analysis"] = analysis
        payload.setdefault("title", _text(row["title"]))
        payload.setdefault("company", _text(row["company_name"]))
        payload.setdefault("location", _text(row["location_text"]))
        payload.setdefault("url", _text(row["source_url"]) or _text(row["canonical_url"]))
        payload.setdefault("canonicalUrl", _text(row["canonical_url"]))
        payload.setdefault("dateFound", _text(row["date_found"]))
        return payload

    @staticmethod
    def _upsert_pool_rows(connection: Any, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        rows = CandidateJobPoolRepository._preserve_visible_recommendations_on_current_reject(
            connection,
            rows,
        )
        connection.executemany(
            """
            INSERT INTO candidate_jobs (
              candidate_id,
              job_id,
              candidate_company_id,
              job_key,
              canonical_url,
              source_url,
              title,
              company_name,
              location_text,
              date_found,
              discovery_status,
              url_status,
              prefilter_status,
              jd_fetch_status,
              scoring_status,
              recommendation_status,
              output_status,
              pool_status,
              user_status,
              application_status,
              trash_status,
              review_status_code,
              hidden,
              interest_level,
              applied_date,
              applied_status,
              response_status,
              not_interested,
              notes,
              rejection_reason,
              match_score,
              analysis_json,
              job_json,
              first_seen_at,
              last_seen_at,
              last_run_id,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(candidate_id, job_id) DO UPDATE SET
              candidate_company_id = excluded.candidate_company_id,
              job_key = excluded.job_key,
              canonical_url = excluded.canonical_url,
              source_url = excluded.source_url,
              title = excluded.title,
              company_name = excluded.company_name,
              location_text = excluded.location_text,
              date_found = excluded.date_found,
              discovery_status = excluded.discovery_status,
              url_status = excluded.url_status,
              prefilter_status = CASE
                WHEN excluded.prefilter_status = 'pending' THEN candidate_jobs.prefilter_status
                ELSE excluded.prefilter_status
              END,
              jd_fetch_status = CASE
                WHEN excluded.jd_fetch_status = 'pending' THEN candidate_jobs.jd_fetch_status
                ELSE excluded.jd_fetch_status
              END,
              scoring_status = CASE
                WHEN excluded.scoring_status = 'pending' THEN candidate_jobs.scoring_status
                ELSE excluded.scoring_status
              END,
              recommendation_status = CASE
                WHEN excluded.recommendation_status = 'pending' THEN candidate_jobs.recommendation_status
                ELSE excluded.recommendation_status
              END,
              output_status = CASE
                WHEN excluded.output_status = 'pending' THEN candidate_jobs.output_status
                ELSE excluded.output_status
              END,
              pool_status = excluded.pool_status,
              user_status = CASE
                WHEN excluded.user_status = '' THEN candidate_jobs.user_status
                ELSE excluded.user_status
              END,
              application_status = CASE
                WHEN excluded.application_status = '' THEN candidate_jobs.application_status
                ELSE excluded.application_status
              END,
              trash_status = CASE
                WHEN excluded.trash_status = 'active' THEN candidate_jobs.trash_status
                ELSE excluded.trash_status
              END,
              review_status_code = CASE
                WHEN excluded.review_status_code = '' THEN candidate_jobs.review_status_code
                ELSE excluded.review_status_code
              END,
              hidden = CASE
                WHEN excluded.hidden = 0 THEN candidate_jobs.hidden
                ELSE excluded.hidden
              END,
              interest_level = CASE
                WHEN excluded.interest_level = '' THEN candidate_jobs.interest_level
                ELSE excluded.interest_level
              END,
              applied_date = CASE
                WHEN excluded.applied_date = '' THEN candidate_jobs.applied_date
                ELSE excluded.applied_date
              END,
              applied_status = CASE
                WHEN excluded.applied_status = '' THEN candidate_jobs.applied_status
                ELSE excluded.applied_status
              END,
              response_status = CASE
                WHEN excluded.response_status = '' THEN candidate_jobs.response_status
                ELSE excluded.response_status
              END,
              not_interested = CASE
                WHEN excluded.not_interested = 0 THEN candidate_jobs.not_interested
                ELSE excluded.not_interested
              END,
              notes = CASE
                WHEN excluded.notes = '' THEN candidate_jobs.notes
                ELSE excluded.notes
              END,
              rejection_reason = excluded.rejection_reason,
              match_score = CASE
                WHEN excluded.match_score IS NULL THEN candidate_jobs.match_score
                ELSE excluded.match_score
              END,
              analysis_json = CASE
                WHEN excluded.analysis_json = '' THEN candidate_jobs.analysis_json
                ELSE excluded.analysis_json
              END,
              job_json = CASE
                WHEN excluded.analysis_json = '' AND candidate_jobs.job_json <> '' THEN candidate_jobs.job_json
                ELSE excluded.job_json
              END,
              last_seen_at = excluded.last_seen_at,
              last_run_id = excluded.last_run_id,
              updated_at = CURRENT_TIMESTAMP
            """,
            [CandidateJobPoolRepository._row_values(row) for row in rows],
        )

    @staticmethod
    def _preserve_visible_recommendation_on_output_refresh(row: Any) -> tuple[str, str, int]:
        existing_analysis = _loads_object(row["analysis_json"])
        existing_payload = _loads_object(row["job_json"])
        payload_analysis = (
            existing_payload.get("analysis")
            if isinstance(existing_payload.get("analysis"), dict)
            else {}
        )
        source_analysis = existing_analysis or payload_analysis
        display = (
            source_analysis.get(RECOMMENDATION_DISPLAY_KEY)
            if isinstance(source_analysis.get(RECOMMENDATION_DISPLAY_KEY), dict)
            else {}
        )
        existing_status = _text(display.get(CURRENT_FIT_STATUS_KEY))
        status = (
            existing_status
            if existing_status in {NEEDS_RESCORE_STATUS, NOT_CURRENT_FIT_STATUS}
            else HISTORICAL_ONLY_STATUS
        )
        preserved_analysis = mark_preserved_recommendation_analysis(
            source_analysis,
            status=status,
            reason="current_output_refresh_excluded",
        )
        preserved_payload = dict(existing_payload)
        if preserved_payload:
            preserved_payload["analysis"] = preserved_analysis
        analysis_json = json.dumps(preserved_analysis, ensure_ascii=False)
        job_json = (
            json.dumps(preserved_payload, ensure_ascii=False)
            if preserved_payload
            else _text(row["job_json"])
        )
        return (analysis_json, job_json, int(row["id"]))

    @staticmethod
    def _preserve_visible_recommendations_on_current_reject(
        connection: Any,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for row in rows:
            if _text(row.get("recommendation_status")) != "reject":
                prepared.append(row)
                continue
            existing = connection.execute(
                """
                SELECT
                  analysis_json,
                  job_json,
                  match_score,
                  recommendation_status,
                  output_status,
                  trash_status,
                  hidden,
                  not_interested,
                  review_status_code
                FROM candidate_jobs
                WHERE candidate_id = ?
                  AND job_id = ?
                """,
                (int(row["candidate_id"]), int(row["job_id"])),
            ).fetchone()
            if existing is None or not CandidateJobPoolRepository._sqlite_row_is_visible_recommendation(existing):
                prepared.append(row)
                continue
            existing_analysis = _loads_object(existing["analysis_json"])
            existing_payload = _loads_object(existing["job_json"])
            incoming_analysis = _loads_object(row.get("analysis_json"))
            preserved_analysis = mark_preserved_recommendation_analysis(
                existing_analysis,
                status=NOT_CURRENT_FIT_STATUS,
                reason="current_rescore_reject",
            )
            if incoming_analysis:
                display = preserved_analysis.setdefault("recommendationDisplay", {})
                if isinstance(display, dict):
                    display["latestCurrentEvaluation"] = {
                        "overallScore": _analysis_score(incoming_analysis),
                        "recommend": bool(incoming_analysis.get("recommend")),
                    }
            preserved_payload = dict(existing_payload)
            if preserved_payload:
                preserved_payload["analysis"] = preserved_analysis
            updated = dict(row)
            updated["scoring_status"] = "scored"
            updated["recommendation_status"] = "pass"
            updated["output_status"] = "pass"
            updated["analysis_json"] = json.dumps(preserved_analysis, ensure_ascii=False)
            updated["job_json"] = (
                json.dumps(preserved_payload, ensure_ascii=False)
                if preserved_payload
                else _text(existing["job_json"])
            )
            updated["match_score"] = _optional_int(existing["match_score"])
            prepared.append(updated)
        return prepared

    @staticmethod
    def _sqlite_row_is_visible_recommendation(row: Any) -> bool:
        return (
            _text(row["recommendation_status"]) == "pass"
            and _text(row["output_status"]) == "pass"
            and _text(row["trash_status"]) != "trashed"
            and not bool(row["hidden"])
            and not bool(row["not_interested"])
            and _text(row["review_status_code"]) not in {"rejected", "dropped"}
        )

    @staticmethod
    def _row_values(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(row["candidate_id"]),
            int(row["job_id"]),
            row.get("candidate_company_id"),
            _text(row.get("job_key")),
            _text(row.get("canonical_url")),
            _text(row.get("source_url")),
            _text(row.get("title")) or "Untitled job",
            _text(row.get("company_name")),
            _text(row.get("location_text")),
            _text(row.get("date_found")),
            _text(row.get("discovery_status")) or "found",
            _text(row.get("url_status")) or "unknown",
            _text(row.get("prefilter_status")) or "pending",
            _text(row.get("jd_fetch_status")) or "pending",
            _text(row.get("scoring_status")) or "pending",
            _text(row.get("recommendation_status")) or "pending",
            _text(row.get("output_status")) or "pending",
            _text(row.get("pool_status")) or "active",
            _text(row.get("user_status")),
            _text(row.get("application_status")),
            _text(row.get("trash_status")) or "active",
            _text(row.get("review_status_code")),
            1 if bool(row.get("hidden")) else 0,
            _text(row.get("interest_level")),
            _text(row.get("applied_date")),
            _text(row.get("applied_status")),
            _text(row.get("response_status")),
            1 if bool(row.get("not_interested")) else 0,
            _text(row.get("notes")),
            _text(row.get("rejection_reason")),
            row.get("match_score"),
            _text(row.get("analysis_json")),
            _text(row.get("job_json")),
            _text(row.get("first_seen_at")),
            _text(row.get("last_seen_at")),
            row.get("last_run_id"),
        )

    @staticmethod
    def _record_from_row(row: Any) -> CandidateJobPoolRecord:
        return CandidateJobPoolRecord(
            candidate_id=int(row["candidate_id"]),
            job_id=int(row["job_id"]),
            job_key=_text(row["job_key"]),
            canonical_url=_text(row["canonical_url"]),
            source_url=_text(row["source_url"]),
            title=_text(row["title"]),
            company_name=_text(row["company_name"]),
            location_text=_text(row["location_text"]),
            discovery_status=_text(row["discovery_status"]),
            url_status=_text(row["url_status"]),
            prefilter_status=_text(row["prefilter_status"]),
            jd_fetch_status=_text(row["jd_fetch_status"]),
            scoring_status=_text(row["scoring_status"]),
            recommendation_status=_text(row["recommendation_status"]),
            output_status=_text(row["output_status"]),
            pool_status=_text(row["pool_status"]),
            user_status=_text(row["user_status"]),
            application_status=_text(row["application_status"]),
            trash_status=_text(row["trash_status"]),
            review_status_code=_text(row["review_status_code"]),
            hidden=bool(row["hidden"]),
            not_interested=bool(row["not_interested"]),
            match_score=_optional_int(row["match_score"]),
            last_run_id=_optional_int(row["last_run_id"]),
        )

    @staticmethod
    def _row_is_visible_recommendation(row: dict[str, Any]) -> bool:
        return (
            _text(row.get("recommendation_status")) == "pass"
            and _text(row.get("output_status")) == "pass"
            and _text(row.get("trash_status")) != "trashed"
            and not bool(row.get("hidden"))
            and not bool(row.get("not_interested"))
            and _text(row.get("review_status_code")) not in {"rejected", "dropped"}
        )

    @staticmethod
    def _is_visible_recommendation(record: CandidateJobPoolRecord) -> bool:
        return (
            record.recommendation_status == "pass"
            and record.output_status == "pass"
            and record.trash_status != "trashed"
            and not record.hidden
            and not record.not_interested
            and record.review_status_code not in {"rejected", "dropped"}
        )

    @staticmethod
    def _is_pending(record: CandidateJobPoolRecord) -> bool:
        return (
            record.scoring_status == "pending"
            and record.url_status == "pass"
            and record.pool_status == "active"
            and record.trash_status != "trashed"
            and not record.hidden
            and not record.not_interested
            and record.review_status_code not in {"rejected", "dropped"}
        )


__all__ = [
    "CandidateJobPoolBackfillResult",
    "CandidateJobPoolRecord",
    "CandidateJobPoolRepository",
    "CandidateJobPoolSummary",
]
