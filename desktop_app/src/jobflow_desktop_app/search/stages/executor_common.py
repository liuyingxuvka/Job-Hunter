from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ...ai.client import OpenAIResponsesClient, OpenAIResponsesError


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _remaining_seconds(deadline: float | None) -> int:
    if deadline is None:
        return 90
    return max(0, int(deadline - time.monotonic()))


def _tail_lines(lines: list[str], limit: int = 40) -> str:
    if not lines:
        return ""
    return "\n".join(lines[-max(1, int(limit)) :])


def _relay_progress(
    line: str,
    stdout_lines: list[str],
    progress_callback: Callable[[str], None] | None,
) -> None:
    stdout_lines.append(line)
    if progress_callback is not None:
        progress_callback(line)


def _config_mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def _resolve_run_relative_path(run_dir: Path, raw_path: object) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        return run_dir
    path = Path(text)
    return path if path.is_absolute() else (run_dir / path)


def _build_openai_client(
    *,
    env: dict[str, str] | None,
    timeout_seconds: int,
) -> OpenAIResponsesClient:
    runtime_env = dict(os.environ)
    if env:
        runtime_env.update({str(key): str(value) for key, value in env.items()})
    api_key = (
        str(runtime_env.get("OPENAI_API_KEY") or "").strip()
        or str(runtime_env.get("AZURE_OPENAI_API_KEY") or "").strip()
    )
    if not api_key:
        raise OpenAIResponsesError("OpenAI API key is required.")
    api_base_url = (
        str(runtime_env.get("OPENAI_BASE_URL") or "").strip()
        or str(runtime_env.get("OPENAI_API_BASE") or "").strip()
    )
    return OpenAIResponsesClient(
        api_key=api_key,
        api_base_url=api_base_url,
        timeout_seconds=max(1, int(timeout_seconds or 90)),
    )


def _load_jobs_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "generatedAt": _now_iso(), "jobs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "generatedAt": _now_iso(), "jobs": []}
    if not isinstance(payload, dict):
        return {"version": 1, "generatedAt": _now_iso(), "jobs": []}
    return payload


def _persist_jobs_payload(path: Path, payload: dict[str, Any], jobs: list[dict]) -> None:
    next_payload = dict(payload)
    next_payload["generatedAt"] = _now_iso()
    next_payload["jobs"] = jobs
    path.write_text(json.dumps(next_payload, ensure_ascii=False, indent=2), encoding="utf-8")
