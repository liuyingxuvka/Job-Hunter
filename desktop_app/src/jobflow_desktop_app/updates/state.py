from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from ..paths import AppPaths


UPDATE_STATUSES = {
    "idle",
    "checking",
    "up_to_date",
    "available",
    "downloading",
    "prepared",
    "stale",
    "applying",
    "failed",
}


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class UpdateState:
    status: str = "idle"
    current_version: str = ""
    latest_version: str = ""
    downloaded_version: str = ""
    prepared_version: str = ""
    release_url: str = ""
    package_path: str = ""
    checksum_path: str = ""
    prepared_dir: str = ""
    sha256: str = ""
    error_message: str = ""
    checked_at: str = ""
    updated_at: str = ""

    @classmethod
    def idle(cls, *, current_version: str) -> "UpdateState":
        now = utc_now_text()
        return cls(status="idle", current_version=current_version, updated_at=now)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, current_version: str = "") -> "UpdateState":
        values: dict[str, str] = {}
        for field_name in cls.__dataclass_fields__:
            raw_value = payload.get(field_name, "")
            values[field_name] = str(raw_value or "")

        status = values.get("status", "idle")
        if status not in UPDATE_STATUSES:
            status = "idle"
        values["status"] = status
        if current_version:
            values["current_version"] = current_version
        if not values.get("updated_at"):
            values["updated_at"] = utc_now_text()
        return cls(**values)

    def with_changes(self, **changes: str) -> "UpdateState":
        payload = asdict(self)
        for key, value in changes.items():
            if key not in payload:
                continue
            payload[key] = str(value or "")
        status = payload.get("status", "idle")
        if status not in UPDATE_STATUSES:
            status = "idle"
        payload["status"] = status
        payload["updated_at"] = utc_now_text()
        return UpdateState(**payload)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class UpdateStateStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.state_path = Path(paths.updates_dir) / "update_state.json"

    def load(self, *, current_version: str = "") -> UpdateState:
        if not self.state_path.exists():
            return UpdateState.idle(current_version=current_version)
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return UpdateState.idle(current_version=current_version).with_changes(
                status="failed",
                error_message="Could not read update_state.json.",
            )
        if not isinstance(payload, dict):
            return UpdateState.idle(current_version=current_version)
        return UpdateState.from_mapping(payload, current_version=current_version)

    def save(self, state: UpdateState) -> UpdateState:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        normalized = state.with_changes()
        temp_path = self.state_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(normalized.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.state_path)
        return normalized
