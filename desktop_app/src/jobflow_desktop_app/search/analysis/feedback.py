from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


TRACK_KEYS = (
    "direct_fit",
    "adjacent_fit",
    "transferable_fit",
    "exploratory_fit",
)

DEFAULT_TRACK = "direct_fit"


def job_review_key(job: Mapping[str, Any]) -> str:
    url = str(job.get("url") or "").strip()
    if url:
        return url.casefold()
    return (
        f"{str(job.get('title') or '').strip()}|"
        f"{str(job.get('company') or '').strip()}|"
        f"{str(job.get('dateFound') or '').strip()}"
    ).casefold()


def normalize_review_status_code(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"pending", "focus", "applied", "offered", "rejected", "dropped"}:
        return normalized
    map_by_label = {
        "待定": "pending",
        "pending": "pending",
        "重点": "focus",
        "focus": "focus",
        "已投递": "applied",
        "applied": "applied",
        "已得到 offer": "offered",
        "已得到offer": "offered",
        "offer received": "offered",
        "offered": "offered",
        "已被拒绝": "rejected",
        "rejected": "rejected",
        "已放弃": "dropped",
        "dropped": "dropped",
    }
    return map_by_label.get(str(value or "").strip()) or map_by_label.get(normalized)


def load_review_state_snapshot(path: str | Path) -> dict[str, Any]:
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        return {"statuses": {}, "hiddenJobKeys": []}
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return {"statuses": {}, "hiddenJobKeys": []}
    if not isinstance(payload, Mapping):
        return {"statuses": {}, "hiddenJobKeys": []}
    statuses = payload.get("statuses")
    hidden = payload.get("hiddenJobKeys")
    return {
        "generatedAt": str(payload.get("generatedAt") or "").strip(),
        "candidateId": payload.get("candidateId"),
        "statuses": dict(statuses) if isinstance(statuses, Mapping) else {},
        "hiddenJobKeys": list(hidden) if isinstance(hidden, list) else [],
    }


def review_feedback_row_for_job(
    job: Mapping[str, Any],
    *,
    status_code: str | None = None,
    hidden: bool = False,
) -> dict[str, str]:
    analysis = job.get("analysis")
    if not isinstance(analysis, Mapping):
        analysis = {}
    normalized_status = normalize_review_status_code(status_code or "")
    row = {
        "fitTrack": str(analysis.get("fitTrack") or DEFAULT_TRACK).strip().lower() or DEFAULT_TRACK,
        "interest": "",
        "applied": "",
        "appliedCn": "",
        "status": "",
        "responseStatus": "",
        "notInterested": "",
        "hidden": "是" if hidden else "",
    }
    if normalized_status == "focus":
        row["interest"] = "感兴趣"
    elif normalized_status == "applied":
        row["appliedCn"] = "已投递"
    elif normalized_status == "offered":
        row["appliedCn"] = "Offer"
        row["responseStatus"] = "Offer"
    elif normalized_status == "rejected":
        row["responseStatus"] = "已拒"
    elif normalized_status == "dropped":
        row["notInterested"] = "是"
    return row


def normalize_feedback_row(row: Mapping[str, Any] | None) -> dict[str, str]:
    source = row if isinstance(row, Mapping) else {}
    fit_track = str(source.get("fitTrack") or "").strip().lower()
    if fit_track not in TRACK_KEYS:
        fit_track = DEFAULT_TRACK
    return {
        "fitTrack": fit_track,
        "interest": str(source.get("interest") or "").strip(),
        "applied": str(source.get("applied") or "").strip(),
        "appliedCn": str(source.get("appliedCn") or "").strip(),
        "status": str(source.get("status") or "").strip(),
        "responseStatus": str(source.get("responseStatus") or "").strip(),
        "notInterested": str(source.get("notInterested") or "").strip(),
        "hidden": str(source.get("hidden") or "").strip(),
    }


def classify_feedback_row(row: Mapping[str, Any] | None) -> dict[str, bool]:
    normalized = normalize_feedback_row(row)
    applied = normalized["applied"] or normalized["appliedCn"]
    status = normalized["status"] or normalized["responseStatus"]
    is_positive = (
        applied in {"面试", "面试中", "Offer", "已投", "已投递"}
        or normalized["interest"] == "感兴趣"
        or status in {"面试中", "已回复", "积极", "Offer"}
    )
    is_negative = (
        normalized["interest"] == "不感兴趣"
        or normalized["notInterested"] == "是"
        or applied in {"拒", "已拒", "已失效", "无回复"}
        or status in {"已拒", "已失效", "无回复", "拒绝"}
    )
    return {
        "positive": is_positive,
        "negative": is_negative,
    }


def build_feedback_rows_from_review_state(
    jobs: list[Mapping[str, Any]],
    statuses_by_job_key: Mapping[str, Any] | None,
    hidden_job_keys: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, str]]:
    statuses = {
        str(key).strip().casefold(): value
        for key, value in (statuses_by_job_key.items() if isinstance(statuses_by_job_key, Mapping) else [])
        if str(key).strip()
    }
    hidden = {
        str(item).strip().casefold()
        for item in (hidden_job_keys or [])
        if str(item).strip()
    }
    rows: list[dict[str, str]] = []
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        key = job_review_key(job)
        rows.append(
            review_feedback_row_for_job(
                job,
                status_code=str(statuses.get(key) or ""),
                hidden=key in hidden,
            )
        )
    return rows


def compute_track_feedback_stats(
    rows: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, dict[str, int]]:
    stats = {key: {"positive": 0, "negative": 0} for key in TRACK_KEYS}
    for row in rows:
        normalized = normalize_feedback_row(row)
        key = normalized["fitTrack"]
        result = classify_feedback_row(normalized)
        if result["positive"]:
            stats[key]["positive"] += 1
        if result["negative"]:
            stats[key]["negative"] += 1
    return stats
