from __future__ import annotations

from collections.abc import Mapping

TECHNICAL_FAILURE_LIMIT = 3


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


def normalize_work_unit_state(value: object) -> dict[str, object]:
    payload = dict(value) if isinstance(value, Mapping) else {}
    technical_failure_count = max(0, int(_optional_int(payload.get("technicalFailureCount")) or 0))
    last_failed_run_id = _optional_int(payload.get("lastFailedRunId"))
    suspended_run_id = _optional_int(payload.get("suspendedRunId"))
    last_failure_reason = str(payload.get("lastFailureReason") or "").strip()
    abandoned = bool(payload.get("abandoned")) or technical_failure_count >= TECHNICAL_FAILURE_LIMIT
    normalized = {
        "technicalFailureCount": technical_failure_count,
        "abandoned": abandoned,
    }
    if last_failed_run_id is not None:
        normalized["lastFailedRunId"] = last_failed_run_id
    if suspended_run_id is not None:
        normalized["suspendedRunId"] = suspended_run_id
    if last_failure_reason:
        normalized["lastFailureReason"] = last_failure_reason
    if (
        normalized.get("technicalFailureCount") == 0
        and normalized.get("abandoned") is False
        and "lastFailedRunId" not in normalized
        and "suspendedRunId" not in normalized
        and "lastFailureReason" not in normalized
    ):
        return {}
    return normalized


def clear_work_unit_state() -> dict[str, object]:
    return {}


def record_technical_failure(
    value: object,
    *,
    run_id: int | None,
    reason: str,
) -> dict[str, object]:
    state = normalize_work_unit_state(value)
    normalized_run_id = _optional_int(run_id)
    current_count = int(state.get("technicalFailureCount") or 0)
    if normalized_run_id is None or normalized_run_id != _optional_int(state.get("lastFailedRunId")):
        current_count += 1
    next_state = {
        "technicalFailureCount": current_count,
        "abandoned": current_count >= TECHNICAL_FAILURE_LIMIT,
        "lastFailureReason": str(reason or "").strip()[:200],
    }
    if normalized_run_id is not None:
        next_state["lastFailedRunId"] = normalized_run_id
        next_state["suspendedRunId"] = normalized_run_id
    return next_state


def suspend_for_current_run(
    value: object,
    *,
    run_id: int | None,
    reason: str = "",
) -> dict[str, object]:
    state = normalize_work_unit_state(value)
    normalized_run_id = _optional_int(run_id)
    next_state = dict(state)
    if normalized_run_id is not None:
        next_state["suspendedRunId"] = normalized_run_id
    if reason:
        next_state["lastFailureReason"] = str(reason).strip()[:200]
    return normalize_work_unit_state(next_state)


def is_abandoned(value: object) -> bool:
    return bool(normalize_work_unit_state(value).get("abandoned"))


def is_suspended_for_run(value: object, run_id: int | None) -> bool:
    normalized_run_id = _optional_int(run_id)
    if normalized_run_id is None:
        return False
    return _optional_int(normalize_work_unit_state(value).get("suspendedRunId")) == normalized_run_id


def has_active_failure_reason(value: object, reason: str) -> bool:
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        return False
    state = normalize_work_unit_state(value)
    if not state or bool(state.get("abandoned")):
        return False
    return str(state.get("lastFailureReason") or "").strip() == normalized_reason


__all__ = [
    "TECHNICAL_FAILURE_LIMIT",
    "clear_work_unit_state",
    "has_active_failure_reason",
    "is_abandoned",
    "is_suspended_for_run",
    "normalize_work_unit_state",
    "record_technical_failure",
    "suspend_for_current_run",
]
