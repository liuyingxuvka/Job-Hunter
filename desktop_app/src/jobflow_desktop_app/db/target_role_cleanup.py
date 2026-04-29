from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

RECOMMENDATION_DISPLAY_KEY = "recommendationDisplay"
CURRENT_FIT_STATUS_KEY = "currentFitStatus"
NEEDS_RESCORE_STATUS = "needs_rescore"
NOT_CURRENT_FIT_STATUS = "not_current_fit"
HISTORICAL_ONLY_STATUS = "historical_only"


def _text(value: object) -> str:
    return str(value or "").strip()


def _table_exists(connection: Any, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_text(table_name),),
    ).fetchone()
    return row is not None


def _column_exists(connection: Any, table_name: str, column_name: str) -> bool:
    if not _table_exists(connection, table_name):
        return False
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(_text(row["name"]) == column_name for row in rows)


def _loads_object(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(_text(value))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _profile_id_from_role_payload(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    profile_id = _optional_int(value.get("profileId"))
    if profile_id is not None and profile_id > 0:
        return profile_id
    role_id = _text(value.get("roleId"))
    if role_id.casefold().startswith("profile:"):
        parsed = _optional_int(role_id.split(":", 1)[1])
        return parsed if parsed is not None and parsed > 0 else None
    return None


def valid_profile_ids_for_candidate(connection: Any, candidate_id: int) -> frozenset[int]:
    if not _table_exists(connection, "search_profiles"):
        return frozenset()
    rows = connection.execute(
        "SELECT id FROM search_profiles WHERE candidate_id = ?",
        (int(candidate_id),),
    ).fetchall()
    return frozenset(int(row["id"]) for row in rows)


def _valid_profile_ids_by_candidate(connection: Any) -> dict[int, frozenset[int]]:
    if not _table_exists(connection, "search_profiles"):
        return {}
    rows = connection.execute(
        "SELECT candidate_id, id FROM search_profiles ORDER BY candidate_id, id"
    ).fetchall()
    grouped: dict[int, set[int]] = {}
    for row in rows:
        grouped.setdefault(int(row["candidate_id"]), set()).add(int(row["id"]))
    return {candidate_id: frozenset(profile_ids) for candidate_id, profile_ids in grouped.items()}


def analysis_has_stale_target_role_binding(
    analysis: dict[str, Any],
    valid_profile_ids: frozenset[int],
) -> bool:
    if not isinstance(analysis, dict) or not analysis:
        return False
    referenced_profile_ids: set[int] = set()
    bound_profile_id = _profile_id_from_role_payload(analysis.get("boundTargetRole"))
    if bound_profile_id is not None:
        referenced_profile_ids.add(bound_profile_id)
    role_scores = analysis.get("targetRoleScores")
    if isinstance(role_scores, list):
        for item in role_scores:
            profile_id = _profile_id_from_role_payload(item)
            if profile_id is not None:
                referenced_profile_ids.add(profile_id)
    return any(profile_id not in valid_profile_ids for profile_id in referenced_profile_ids)


def analysis_references_target_role_profile(
    analysis: dict[str, Any],
    profile_id: int,
) -> bool:
    if not isinstance(analysis, dict) or not analysis:
        return False
    target_profile_id = int(profile_id)
    bound_profile_id = _profile_id_from_role_payload(analysis.get("boundTargetRole"))
    if bound_profile_id == target_profile_id:
        return True
    role_scores = analysis.get("targetRoleScores")
    if isinstance(role_scores, list):
        return any(
            _profile_id_from_role_payload(item) == target_profile_id
            for item in role_scores
        )
    return False


def mark_preserved_recommendation_analysis(
    analysis: dict[str, Any],
    *,
    status: str = NEEDS_RESCORE_STATUS,
    reason: str,
) -> dict[str, Any]:
    normalized = dict(analysis) if isinstance(analysis, dict) else {}
    display = normalized.get(RECOMMENDATION_DISPLAY_KEY)
    normalized_display = dict(display) if isinstance(display, dict) else {}
    normalized_display[CURRENT_FIT_STATUS_KEY] = _text(status) or NEEDS_RESCORE_STATUS
    normalized_display["reason"] = _text(reason)
    normalized_display["preservedHistoricalRecommendation"] = True
    bound_target_role = normalized.get("boundTargetRole")
    if isinstance(bound_target_role, dict) and bound_target_role:
        normalized_display.setdefault("historicalBoundTargetRole", dict(bound_target_role))
    normalized[RECOMMENDATION_DISPLAY_KEY] = normalized_display
    return normalized


@dataclass(frozen=True)
class JobPayloadSanitization:
    payload: dict[str, Any]
    analysis: dict[str, Any]
    changed: bool
    reset_analysis: bool


def sanitize_job_payload_role_bindings(
    payload: dict[str, Any],
    valid_profile_ids: frozenset[int],
) -> JobPayloadSanitization:
    sanitized_payload = dict(payload)
    analysis = sanitized_payload.get("analysis")
    if not isinstance(analysis, dict) or not analysis:
        return JobPayloadSanitization(
            payload=sanitized_payload,
            analysis={},
            changed=False,
            reset_analysis=False,
        )
    if not analysis_has_stale_target_role_binding(analysis, valid_profile_ids):
        return JobPayloadSanitization(
            payload=sanitized_payload,
            analysis=dict(analysis),
            changed=False,
            reset_analysis=False,
        )
    sanitized_payload.pop("analysis", None)
    return JobPayloadSanitization(
        payload=sanitized_payload,
        analysis={},
        changed=True,
        reset_analysis=True,
    )


@dataclass(frozen=True)
class CandidateJobRowSanitization:
    analysis_json: str
    job_json: str
    reset_analysis: bool
    preserved_visible_recommendation: bool
    changed: bool


def _is_visible_recommendation_row(row: Any) -> bool:
    return (
        _text(row["recommendation_status"]) == "pass"
        and _text(row["output_status"]) == "pass"
        and _text(row["trash_status"]) != "trashed"
        and not bool(row["hidden"])
        and not bool(row["not_interested"])
        and _text(row["review_status_code"]) not in {"rejected", "dropped"}
    )


def _payload_with_analysis(payload: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if analysis:
        normalized["analysis"] = dict(analysis)
    else:
        normalized.pop("analysis", None)
    return normalized


def _sanitize_candidate_job_row(
    row: Any,
    valid_profile_ids: frozenset[int],
    *,
    force_profile_id: int | None = None,
    reason: str = "stale_target_role",
) -> CandidateJobRowSanitization:
    analysis = _loads_object(row["analysis_json"])
    payload = _loads_object(row["job_json"])
    payload_analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    reset_analysis = analysis_has_stale_target_role_binding(
        analysis,
        valid_profile_ids,
    ) or analysis_has_stale_target_role_binding(
        payload_analysis,
        valid_profile_ids,
    )
    if force_profile_id is not None:
        reset_analysis = reset_analysis or analysis_references_target_role_profile(
            analysis,
            force_profile_id,
        ) or analysis_references_target_role_profile(
            payload_analysis,
            force_profile_id,
        )
    if not reset_analysis:
        return CandidateJobRowSanitization(
            analysis_json=_text(row["analysis_json"]),
            job_json=_text(row["job_json"]),
            reset_analysis=False,
            preserved_visible_recommendation=False,
            changed=False,
        )
    if _is_visible_recommendation_row(row):
        preserved_analysis = mark_preserved_recommendation_analysis(
            analysis or payload_analysis,
            status=NEEDS_RESCORE_STATUS,
            reason=reason,
        )
        preserved_payload = _payload_with_analysis(payload, preserved_analysis) if payload else {}
        analysis_json = json.dumps(preserved_analysis, ensure_ascii=False)
        job_json = json.dumps(preserved_payload, ensure_ascii=False) if preserved_payload else ""
        return CandidateJobRowSanitization(
            analysis_json=analysis_json,
            job_json=job_json,
            reset_analysis=False,
            preserved_visible_recommendation=True,
            changed=analysis_json != _text(row["analysis_json"]) or job_json != _text(row["job_json"]),
        )
    if payload:
        payload.pop("analysis", None)
    job_json = json.dumps(payload, ensure_ascii=False) if payload else ""
    return CandidateJobRowSanitization(
        analysis_json="",
        job_json=job_json,
        reset_analysis=True,
        preserved_visible_recommendation=False,
        changed=True,
    )


def sanitize_candidate_job_role_bindings(
    connection: Any,
    *,
    candidate_id: int | None = None,
) -> int:
    if not _table_exists(connection, "candidate_jobs"):
        return 0
    if candidate_id is None:
        valid_by_candidate = _valid_profile_ids_by_candidate(connection)
        rows = connection.execute(
            """
            SELECT
              id,
              candidate_id,
              analysis_json,
              job_json,
              recommendation_status,
              output_status,
              trash_status,
              hidden,
              not_interested,
              review_status_code
            FROM candidate_jobs
            ORDER BY id
            """
        ).fetchall()
    else:
        valid_by_candidate = {
            int(candidate_id): valid_profile_ids_for_candidate(connection, int(candidate_id))
        }
        rows = connection.execute(
            """
            SELECT
              id,
              candidate_id,
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
            ORDER BY id
            """,
            (int(candidate_id),),
        ).fetchall()
    reset_updates: list[tuple[str, str, int]] = []
    preserve_updates: list[tuple[str, str, int]] = []
    for row in rows:
        row_candidate_id = int(row["candidate_id"])
        sanitized = _sanitize_candidate_job_row(
            row,
            valid_by_candidate.get(row_candidate_id, frozenset()),
        )
        if not sanitized.changed:
            continue
        if sanitized.preserved_visible_recommendation:
            preserve_updates.append((sanitized.analysis_json, sanitized.job_json, int(row["id"])))
        else:
            reset_updates.append((sanitized.analysis_json, sanitized.job_json, int(row["id"])))
    if reset_updates:
        connection.executemany(
            """
            UPDATE candidate_jobs
            SET analysis_json = ?,
                job_json = ?,
                scoring_status = 'pending',
                recommendation_status = 'pending',
                output_status = 'pending',
                rejection_reason = '',
                match_score = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            reset_updates,
        )
    if preserve_updates:
        connection.executemany(
            """
            UPDATE candidate_jobs
            SET analysis_json = ?,
                job_json = ?,
                scoring_status = 'scored',
                recommendation_status = 'pass',
                output_status = 'pass',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            preserve_updates,
        )
    return len(reset_updates) + len(preserve_updates)


def mark_candidate_job_target_role_changed(
    connection: Any,
    *,
    candidate_id: int,
    profile_id: int,
    reason: str = "target_role_changed",
) -> int:
    if not _table_exists(connection, "candidate_jobs"):
        return 0
    rows = connection.execute(
        """
        SELECT
          id,
          candidate_id,
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
        ORDER BY id
        """,
        (int(candidate_id),),
    ).fetchall()
    valid_profile_ids = valid_profile_ids_for_candidate(connection, int(candidate_id))
    reset_updates: list[tuple[str, str, int]] = []
    preserve_updates: list[tuple[str, str, int]] = []
    for row in rows:
        sanitized = _sanitize_candidate_job_row(
            row,
            valid_profile_ids,
            force_profile_id=int(profile_id),
            reason=reason,
        )
        if not sanitized.changed:
            continue
        if sanitized.preserved_visible_recommendation:
            preserve_updates.append((sanitized.analysis_json, sanitized.job_json, int(row["id"])))
        elif sanitized.reset_analysis:
            reset_updates.append((sanitized.analysis_json, sanitized.job_json, int(row["id"])))
    if reset_updates:
        connection.executemany(
            """
            UPDATE candidate_jobs
            SET analysis_json = ?,
                job_json = ?,
                scoring_status = 'pending',
                recommendation_status = 'pending',
                output_status = 'pending',
                rejection_reason = '',
                match_score = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            reset_updates,
        )
    if preserve_updates:
        connection.executemany(
            """
            UPDATE candidate_jobs
            SET analysis_json = ?,
                job_json = ?,
                scoring_status = 'scored',
                recommendation_status = 'pass',
                output_status = 'pass',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            preserve_updates,
        )
    return len(reset_updates) + len(preserve_updates)


@dataclass(frozen=True)
class TargetRoleCleanupResult:
    orphan_analyses_deleted: int = 0
    orphan_review_states_deleted: int = 0
    search_runs_unbound: int = 0
    candidate_jobs_reset: int = 0


def _rowcount(cursor: Any) -> int:
    value = getattr(cursor, "rowcount", 0)
    return value if isinstance(value, int) and value > 0 else 0


def cleanup_stale_target_role_references(
    connection: Any,
    *,
    candidate_id: int | None = None,
) -> TargetRoleCleanupResult:
    orphan_analyses_deleted = 0
    orphan_review_states_deleted = 0
    search_runs_unbound = 0
    if _table_exists(connection, "job_analyses"):
        orphan_analyses_deleted = _rowcount(
            connection.execute(
                """
                DELETE FROM job_analyses
                WHERE search_profile_id NOT IN (
                  SELECT id FROM search_profiles
                )
                """
            )
        )
    if _table_exists(connection, "job_review_states"):
        params: tuple[object, ...] = ()
        candidate_filter = ""
        if candidate_id is not None:
            candidate_filter = "AND candidate_id = ?"
            params = (int(candidate_id),)
        orphan_review_states_deleted = _rowcount(
            connection.execute(
                f"""
                DELETE FROM job_review_states
                WHERE search_profile_id NOT IN (
                  SELECT id FROM search_profiles
                )
                {candidate_filter}
                """,
                params,
            )
        )
    if _column_exists(connection, "search_runs", "search_profile_id"):
        search_runs_unbound = _rowcount(
            connection.execute(
                """
                UPDATE search_runs
                SET search_profile_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE search_profile_id IS NOT NULL
                  AND search_profile_id NOT IN (
                    SELECT id FROM search_profiles
                  )
                """
            )
        )
    candidate_jobs_reset = sanitize_candidate_job_role_bindings(
        connection,
        candidate_id=candidate_id,
    )
    return TargetRoleCleanupResult(
        orphan_analyses_deleted=orphan_analyses_deleted,
        orphan_review_states_deleted=orphan_review_states_deleted,
        search_runs_unbound=search_runs_unbound,
        candidate_jobs_reset=candidate_jobs_reset,
    )


__all__ = [
    "JobPayloadSanitization",
    "TargetRoleCleanupResult",
    "analysis_has_stale_target_role_binding",
    "analysis_references_target_role_profile",
    "HISTORICAL_ONLY_STATUS",
    "cleanup_stale_target_role_references",
    "mark_candidate_job_target_role_changed",
    "mark_preserved_recommendation_analysis",
    "sanitize_candidate_job_role_bindings",
    "sanitize_job_payload_role_bindings",
    "valid_profile_ids_for_candidate",
]
