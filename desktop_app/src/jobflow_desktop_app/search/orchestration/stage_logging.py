from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class StageLogToken:
    log_id: int | None
    stage_name: str
    round_number: int
    started_monotonic: float


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


def _status_from_result(result: Any) -> str:
    if bool(getattr(result, "cancelled", False)):
        return "cancelled"
    if bool(getattr(result, "success", False)):
        return "success"
    return "hard_failed"


def _counts_from_payload(payload: object) -> dict[str, int | float | bool]:
    if not isinstance(payload, dict):
        return {}
    counts: dict[str, int | float | bool] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if isinstance(raw_value, bool):
            counts[key] = raw_value
        elif isinstance(raw_value, int):
            counts[key] = raw_value
        elif isinstance(raw_value, float):
            counts[key] = raw_value
        elif isinstance(raw_value, list):
            counts[f"{key}Count"] = len(raw_value)
    return counts


def _attach_log_id(result: Any, log_id: int | None) -> Any:
    if log_id is None or result is None:
        return result
    try:
        return replace(result, stage_log_id=log_id)
    except Exception:
        try:
            setattr(result, "stage_log_id", log_id)
        except Exception:
            pass
        return result


class SearchStageLogger:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @property
    def _runtime_mirror(self) -> Any | None:
        runner = getattr(self.runtime, "runner", None)
        return getattr(runner, "runtime_mirror", None)

    def start(
        self,
        stage_name: str,
        *,
        round_number: int = 0,
        message: str = "",
        counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StageLogToken:
        log_id: int | None = None
        runtime_mirror = self._runtime_mirror
        search_run_id = _optional_int(getattr(self.runtime, "search_run_id", None))
        start_fn = getattr(runtime_mirror, "start_stage_log", None)
        if search_run_id is not None and callable(start_fn):
            try:
                log_id = int(
                    start_fn(
                        search_run_id=search_run_id,
                        candidate_id=_optional_int(getattr(self.runtime, "candidate_id", None)),
                        round_number=max(0, int(round_number or 0)),
                        stage_name=stage_name,
                        message=message,
                        counts=counts,
                        metadata=metadata,
                    )
                )
            except Exception:
                log_id = None
        return StageLogToken(
            log_id=log_id,
            stage_name=str(stage_name or "").strip(),
            round_number=max(0, int(round_number or 0)),
            started_monotonic=time.monotonic(),
        )

    def finish(
        self,
        token: StageLogToken | None,
        *,
        status: str,
        exit_code: int | None = None,
        message: str = "",
        error_summary: str = "",
        counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if token is None or token.log_id is None:
            return
        runtime_mirror = self._runtime_mirror
        finish_fn = getattr(runtime_mirror, "finish_stage_log", None)
        if not callable(finish_fn):
            return
        duration_ms = int(max(0.0, time.monotonic() - token.started_monotonic) * 1000)
        try:
            finish_fn(
                token.log_id,
                status=status,
                exit_code=exit_code,
                message=message,
                error_summary=error_summary,
                counts=counts,
                metadata=metadata,
                duration_ms=duration_ms,
            )
        except Exception:
            return

    def finish_result(
        self,
        token: StageLogToken | None,
        result: Any,
        *,
        counts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        if result is None:
            self.finish(
                token,
                status="skipped",
                exit_code=0,
                message="Stage skipped.",
                counts=counts,
                metadata=metadata,
            )
            return result
        merged_counts = _counts_from_payload(getattr(result, "payload", None))
        if counts:
            merged_counts.update(counts)
        status = _status_from_result(result)
        error_summary = str(getattr(result, "stderr_tail", "") or "") if status != "success" else ""
        self.finish(
            token,
            status=status,
            exit_code=_optional_int(getattr(result, "exit_code", None)),
            message=str(getattr(result, "message", "") or ""),
            error_summary=error_summary,
            counts=merged_counts,
            metadata=metadata,
        )
        return _attach_log_id(result, token.log_id if token is not None else None)

    def mark_status(
        self,
        result_or_log_id: Any,
        *,
        status: str,
        message: str = "",
        error_summary: str = "",
    ) -> None:
        log_id = (
            _optional_int(result_or_log_id)
            if isinstance(result_or_log_id, int)
            else _optional_int(getattr(result_or_log_id, "stage_log_id", None))
        )
        if log_id is None:
            return
        runtime_mirror = self._runtime_mirror
        update_fn = getattr(runtime_mirror, "update_stage_log_status", None)
        if not callable(update_fn):
            return
        try:
            update_fn(
                log_id,
                status=status,
                message=message,
                error_summary=error_summary,
            )
        except Exception:
            return


def stage_logger_for(runtime: Any) -> SearchStageLogger:
    logger = getattr(runtime, "stage_logger", None)
    if isinstance(logger, SearchStageLogger):
        return logger
    logger = SearchStageLogger(runtime)
    try:
        setattr(runtime, "stage_logger", logger)
    except Exception:
        pass
    return logger


__all__ = ["SearchStageLogger", "StageLogToken", "stage_logger_for"]
