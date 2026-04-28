from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..connection import Database

_MAX_TEXT_LENGTH = 1000
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api|key|secret|token|password|resume|cover|email|phone|contact|rawtext|file|path)",
    re.IGNORECASE,
)
_SECRET_PATTERN = re.compile(r"\b(?:sk|sk-proj|sess)-[A-Za-z0-9_-]{12,}\b")
_WINDOWS_PATH_PATTERN = re.compile(r"\b[A-Za-z]:(?:\\|/)[^\s'\"<>|]+")


@dataclass(frozen=True)
class SearchStageLogRecord:
    log_id: int
    search_run_id: int
    candidate_id: int | None
    round_number: int
    stage_name: str
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    exit_code: int | None
    message: str
    error_summary: str
    counts: dict[str, Any]
    metadata: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: object, *, max_length: int = _MAX_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _SECRET_PATTERN.sub("[redacted-secret]", text)
    text = _WINDOWS_PATH_PATTERN.sub("[redacted-path]", text)
    if len(text) > max_length:
        text = f"{text[: max(0, max_length - 3)]}..."
    return text


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


def _safe_json_payload(value: object, *, depth: int = 0) -> object:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else 0
    if isinstance(value, str):
        return _text(value, max_length=300)
    if isinstance(value, (list, tuple)):
        return [_safe_json_payload(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for raw_key, raw_item in list(value.items())[:80]:
            key = _text(raw_key, max_length=80)
            if not key:
                continue
            if _SENSITIVE_KEY_PATTERN.search(key):
                sanitized[key] = "[redacted]"
                continue
            sanitized[key] = _safe_json_payload(raw_item, depth=depth + 1)
        return sanitized
    return _text(value, max_length=300)


def _json_dumps(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    sanitized = _safe_json_payload(payload)
    if not isinstance(sanitized, dict):
        return ""
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)


def _json_loads(payload: object) -> dict[str, Any]:
    try:
        value = json.loads(str(payload or ""))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


class SearchStageLogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        *,
        search_run_id: int,
        candidate_id: int | None,
        round_number: int = 0,
        stage_name: str,
        message: str = "",
        counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        started_at = _now_iso()
        with self.database.session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO search_stage_logs (
                  search_run_id,
                  candidate_id,
                  round_number,
                  stage_name,
                  status,
                  started_at,
                  message,
                  counts_json,
                  metadata_json,
                  updated_at
                )
                VALUES (?, ?, ?, ?, 'started', ?, ?, ?, ?, ?)
                """,
                (
                    int(search_run_id),
                    _optional_int(candidate_id),
                    max(0, int(round_number or 0)),
                    _text(stage_name, max_length=120),
                    started_at,
                    _text(message),
                    _json_dumps(counts),
                    _json_dumps(metadata),
                    started_at,
                ),
            )
            return int(cursor.lastrowid)

    def finish(
        self,
        log_id: int,
        *,
        status: str,
        exit_code: int | None = None,
        message: str = "",
        error_summary: str = "",
        counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        finished_at = _now_iso()
        with self.database.session() as connection:
            connection.execute(
                """
                UPDATE search_stage_logs
                SET status = ?,
                    finished_at = ?,
                    duration_ms = ?,
                    exit_code = ?,
                    message = CASE WHEN ? <> '' THEN ? ELSE message END,
                    error_summary = ?,
                    counts_json = CASE WHEN ? <> '' THEN ? ELSE counts_json END,
                    metadata_json = CASE WHEN ? <> '' THEN ? ELSE metadata_json END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    _text(status, max_length=40) or "finished",
                    finished_at,
                    max(0, int(duration_ms or 0)),
                    _optional_int(exit_code),
                    _text(message),
                    _text(message),
                    _text(error_summary),
                    _json_dumps(counts),
                    _json_dumps(counts),
                    _json_dumps(metadata),
                    _json_dumps(metadata),
                    finished_at,
                    int(log_id),
                ),
            )

    def update_status(
        self,
        log_id: int,
        *,
        status: str,
        message: str = "",
        error_summary: str = "",
    ) -> None:
        updated_at = _now_iso()
        with self.database.session() as connection:
            connection.execute(
                """
                UPDATE search_stage_logs
                SET status = ?,
                    message = CASE WHEN ? <> '' THEN ? ELSE message END,
                    error_summary = CASE WHEN ? <> '' THEN ? ELSE error_summary END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    _text(status, max_length=40) or "finished",
                    _text(message),
                    _text(message),
                    _text(error_summary),
                    _text(error_summary),
                    updated_at,
                    int(log_id),
                ),
            )

    def list_for_run(self, search_run_id: int) -> list[SearchStageLogRecord]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT
                  id,
                  search_run_id,
                  candidate_id,
                  round_number,
                  stage_name,
                  status,
                  started_at,
                  finished_at,
                  duration_ms,
                  exit_code,
                  message,
                  error_summary,
                  counts_json,
                  metadata_json
                FROM search_stage_logs
                WHERE search_run_id = ?
                ORDER BY id
                """,
                (int(search_run_id),),
            ).fetchall()
        return [
            SearchStageLogRecord(
                log_id=int(row["id"]),
                search_run_id=int(row["search_run_id"]),
                candidate_id=_optional_int(row["candidate_id"]),
                round_number=int(row["round_number"] or 0),
                stage_name=_text(row["stage_name"], max_length=120),
                status=_text(row["status"], max_length=40),
                started_at=_text(row["started_at"]),
                finished_at=_text(row["finished_at"]),
                duration_ms=max(0, int(row["duration_ms"] or 0)),
                exit_code=_optional_int(row["exit_code"]),
                message=_text(row["message"]),
                error_summary=_text(row["error_summary"]),
                counts=_json_loads(row["counts_json"]),
                metadata=_json_loads(row["metadata_json"]),
            )
            for row in rows
        ]


__all__ = ["SearchStageLogRecord", "SearchStageLogRepository"]
