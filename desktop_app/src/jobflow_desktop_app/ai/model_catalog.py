from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ModelCatalogResult:
    models: list[str]
    error: str = ""


@dataclass(frozen=True)
class ModelProbeResult:
    model: str
    usable: bool
    error: str = ""


def resolve_models_url(api_base_url: str = "") -> str:
    base = str(api_base_url or "").strip()
    if not base:
        return "https://api.openai.com/v1/models"

    normalized = base.rstrip("/")
    if normalized.endswith("/responses"):
        normalized = normalized[: -len("/responses")]
    if normalized.endswith("/models"):
        return normalized
    return f"{normalized}/models"


def resolve_responses_url(api_base_url: str = "") -> str:
    base = str(api_base_url or "").strip()
    if not base:
        return "https://api.openai.com/v1/responses"

    normalized = base.rstrip("/")
    if normalized.endswith("/models"):
        normalized = normalized[: -len("/models")]
    if normalized.endswith("/responses"):
        return normalized
    return f"{normalized}/responses"


def fetch_available_models(
    api_key: str,
    api_base_url: str = "",
    timeout_seconds: int = 15,
) -> ModelCatalogResult:
    token = str(api_key or "").strip()
    if not token:
        return ModelCatalogResult(models=[], error="API key is empty.")

    url = resolve_models_url(api_base_url)
    errors: list[str] = []
    header_attempts = [
        {"Authorization": f"Bearer {token}"},
        {"api-key": token},
        {"Authorization": f"Bearer {token}", "api-key": token},
    ]

    for headers in header_attempts:
        request = Request(url=url, method="GET", headers=headers)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload_text = response.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            errors.append(f"HTTP {exc.code}: {detail[:220]}")
            continue
        except URLError as exc:
            errors.append(f"Connection failed: {exc.reason}")
            continue
        except Exception as exc:  # pragma: no cover - defensive fallback
            errors.append(f"Unexpected error: {exc}")
            continue

        models = parse_model_ids(payload_text)
        if models:
            return ModelCatalogResult(models=models, error="")
        errors.append("Model list response did not contain usable model ids.")

    return ModelCatalogResult(models=[], error=" | ".join(errors[-2:]))


def filter_response_usable_models(
    api_key: str,
    models: list[str],
    api_base_url: str = "",
    timeout_seconds: int = 10,
    max_probe: int = 12,
    preferred_models: list[str] | None = None,
    stop_after: int = 8,
    probe_fallback: bool = True,
) -> list[str]:
    token = str(api_key or "").strip()
    if not token:
        return []

    model_hints = list(preferred_models or [])
    model_hints.extend(_model_hints_from_environment())
    model_hints = _dedup_models(model_hints)

    prioritized = _prioritize_probe_candidates(models, max_probe=0)
    if model_hints and not probe_fallback:
        candidate_models = _merge_probe_candidates(
            preferred=model_hints,
            fallback=[],
            max_probe=max_probe,
        )
    else:
        candidate_models = _merge_probe_candidates(
            preferred=model_hints,
            fallback=prioritized,
            max_probe=max_probe,
        )
    if not candidate_models:
        return []

    url = resolve_responses_url(api_base_url)
    usable: list[str] = []
    for model_id in candidate_models:
        if _probe_responses_model(
            api_key=token,
            api_base_url=api_base_url,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
            responses_url=url,
        ):
            usable.append(model_id)
            if stop_after > 0 and len(usable) >= stop_after:
                break
    return _dedup_models(usable)


def probe_response_model(
    api_key: str,
    model_id: str,
    api_base_url: str = "",
    timeout_seconds: int = 12,
    responses_url: str = "",
) -> ModelProbeResult:
    token = str(api_key or "").strip()
    model_name = str(model_id or "").strip()
    if not token:
        return ModelProbeResult(model=model_name, usable=False, error="API key is empty.")
    if not model_name:
        return ModelProbeResult(model="", usable=False, error="Model is empty.")

    url = responses_url or resolve_responses_url(api_base_url)
    payload = json.dumps(
        {
            "model": model_name,
            "input": "ping",
            "max_output_tokens": 16,
        }
    ).encode("utf-8")
    header_attempts = (
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "api-key": api_key,
        },
        {
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    errors: list[str] = []

    for headers in header_attempts:
        request = Request(url=url, method="POST", headers=headers, data=payload)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200) or 200)
                if 200 <= status_code < 300:
                    return ModelProbeResult(model=model_name, usable=True, error="")
                errors.append(f"Unexpected HTTP status: {status_code}")
        except HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            detail = exc.read().decode("utf-8", errors="ignore")
            if code == 429:
                if _is_probe_retryable_throttle(detail):
                    return ModelProbeResult(model=model_name, usable=True, error="")
                errors.append(f"HTTP 429: {detail[:220]}")
                continue
            if code in {400, 422} and _is_probe_parameter_error(detail):
                return ModelProbeResult(model=model_name, usable=True, error="")
            if code in {400, 404, 422}:
                errors.append(f"Model probe rejected for {model_name}: HTTP {code}. {detail[:220]}")
                continue
            if code in {401, 403}:
                errors.append(f"Authentication failed: HTTP {code}. {detail[:220]}")
                continue
            errors.append(f"HTTP {code}: {detail[:220]}")
        except URLError as exc:
            errors.append(f"Connection failed: {exc.reason}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            errors.append(f"Unexpected error: {exc}")

    return ModelProbeResult(
        model=model_name,
        usable=False,
        error=errors[-1] if errors else f"Model probe failed for {model_name}.",
    )


def parse_model_ids(payload_text: str) -> list[str]:
    try:
        payload = json.loads(payload_text or "{}")
    except Exception:
        return []

    candidates: list[str] = []
    if isinstance(payload, dict):
        for key in ("data", "value", "models"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(_extract_from_items(value))
    elif isinstance(payload, list):
        candidates.extend(_extract_from_items(payload))
    return _dedup_models(candidates)


def _prioritize_probe_candidates(models: list[str], max_probe: int = 20) -> list[str]:
    cleaned = _dedup_models(models)
    scored: list[tuple[int, str]] = []
    for model_id in cleaned:
        score = _probe_priority(model_id)
        if score >= 1000:
            continue
        scored.append((score, model_id))
    scored.sort(key=lambda item: (item[0], item[1].casefold()))
    if max_probe <= 0:
        return [item[1] for item in scored]
    return [item[1] for item in scored[:max_probe]]


def _merge_probe_candidates(preferred: list[str], fallback: list[str], max_probe: int) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def push(raw: str) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append(text)

    for item in preferred:
        push(item)
    for item in fallback:
        push(item)

    if max_probe > 0:
        return ordered[:max_probe]
    return ordered


def _model_hints_from_environment() -> list[str]:
    names = (
        "JOBFLOW_OPENAI_MODEL",
        "AZURE_OPENAI_MODEL",
        "AZURE_OPENAI_DEPLOYMENT",
    )
    hints: list[str] = []
    for name in names:
        text = str(os.getenv(name, "") or "").strip()
        if text:
            hints.append(text)
    return hints


def _probe_priority(model_id: str) -> int:
    text = str(model_id or "").strip()
    if not text:
        return 2000
    lower = text.casefold()
    blocked_markers = (
        "embedding",
        "whisper",
        "transcribe",
        "audio",
        "tts",
        "speech",
        "realtime",
        "moderation",
        "image",
        "dall",
        "omni-moderation",
        "search",
        "ranker",
    )
    if any(marker in lower for marker in blocked_markers):
        return 2000

    if "gpt-5.3-codex" in lower:
        return 0
    if "codex" in lower:
        return 1
    if "nano" in lower:
        return 2
    if "mini" in lower:
        return 3
    if "small" in lower or "lite" in lower:
        return 4
    if "gpt" in lower:
        return 5
    if lower.startswith("o"):
        return 6
    return 20


def _probe_responses_model(
    api_key: str,
    api_base_url: str,
    model_id: str,
    timeout_seconds: int = 12,
    responses_url: str = "",
) -> bool:
    return probe_response_model(
        api_key=api_key,
        model_id=model_id,
        api_base_url=api_base_url,
        timeout_seconds=timeout_seconds,
        responses_url=responses_url,
    ).usable


def _is_probe_parameter_error(detail: str) -> bool:
    text = str(detail or "").casefold()
    if not text:
        return False
    if "max_output_tokens" in text and ("minimum" in text or "invalid" in text):
        return True
    if "invalid 'input'" in text or "input is required" in text:
        return True
    return False


def _is_probe_retryable_throttle(detail: str) -> bool:
    text = str(detail or "").casefold()
    if not text:
        return False
    if "insufficient_quota" in text:
        return False
    if "quota" in text and "insufficient" in text:
        return False
    if "rate_limit" in text or "rate limit" in text or "too many requests" in text:
        return True
    return False


def _extract_from_items(items: list) -> list[str]:
    found: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
            if text:
                found.append(text)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("id", "model", "name", "deployment_name"):
            text = str(item.get(key) or "").strip()
            if text:
                found.append(text)
                break
    return found


def _dedup_models(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered
