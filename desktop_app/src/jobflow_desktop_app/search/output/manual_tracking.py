from __future__ import annotations

from collections.abc import Mapping


MANUAL_TRACKING_KEYS = (
    "interest",
    "appliedDate",
    "appliedCn",
    "responseStatus",
    "notInterested",
    "notesCn",
)


def has_manual_tracking(row: Mapping[str, object] | None) -> bool:
    if not row:
        return False
    interest = str(row.get("interest") or "").strip()
    valid_interest = {"感兴趣", "一般", "不感兴趣"}
    if interest in valid_interest:
        return True
    return any(str(row.get(key) or "").strip() for key in MANUAL_TRACKING_KEYS if key != "interest")


def merge_manual_fields(
    *sources: Mapping[str, Mapping[str, object]] | None,
) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for url, row in source.items():
            if not url:
                continue
            previous = merged.get(url, {})
            current = row if isinstance(row, Mapping) else {}
            merged[url] = {
                key: str(current.get(key) or previous.get(key) or "").strip()
                for key in MANUAL_TRACKING_KEYS
            }
    return merged
