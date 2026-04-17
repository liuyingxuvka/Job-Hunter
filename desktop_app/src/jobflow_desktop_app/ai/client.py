from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

DEFAULT_OPENAI_RESPONSES_API_URL = "https://api.openai.com/v1/responses"


class OpenAIResponsesError(RuntimeError):
    pass


def resolve_openai_responses_url(api_base_url: str = "") -> str:
    base = str(api_base_url or "").strip()
    if not base:
        return DEFAULT_OPENAI_RESPONSES_API_URL
    normalized = base.rstrip("/")
    if normalized.endswith("/responses"):
        return normalized
    if normalized.endswith("/openai/v1"):
        return f"{normalized}/responses"
    if normalized.endswith("/v1"):
        return f"{normalized}/responses"
    return f"{normalized}/v1/responses"


def extract_output_text(response_payload: Mapping[str, Any]) -> str:
    raw_output_text = response_payload.get("output_text")
    if isinstance(raw_output_text, str) and raw_output_text.strip():
        return raw_output_text.strip()

    texts: list[str] = []
    for output_item in response_payload.get("output", []) or []:
        if not isinstance(output_item, Mapping):
            continue
        for content_item in output_item.get("content", []) or []:
            if not isinstance(content_item, Mapping):
                continue
            text_value = content_item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                texts.append(text_value.strip())
    return "\n".join(texts).strip()


def extract_json_object_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    if text.startswith("{"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_response_json(
    response_payload: Mapping[str, Any],
    label: str = "OpenAI response",
) -> dict[str, Any]:
    output_parsed = response_payload.get("output_parsed")
    if isinstance(output_parsed, Mapping):
        return dict(output_parsed)

    text_candidates: list[str] = []
    raw_output_text = response_payload.get("output_text")
    if isinstance(raw_output_text, str) and raw_output_text.strip():
        text_candidates.append(raw_output_text)

    for output_item in response_payload.get("output", []) or []:
        if not isinstance(output_item, Mapping):
            continue
        for content_item in output_item.get("content", []) or []:
            if not isinstance(content_item, Mapping):
                continue
            parsed_json = content_item.get("json")
            if isinstance(parsed_json, Mapping):
                return dict(parsed_json)
            text_value = content_item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_candidates.append(text_value)

    last_error: Exception | None = None
    for candidate in text_candidates:
        json_text = extract_json_object_text(candidate)
        if not json_text:
            continue
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(payload, Mapping):
            return dict(payload)
        last_error = TypeError(f"{label} returned JSON that is not an object.")

    if last_error is not None:
        raise OpenAIResponsesError(f"{label} did not return parseable JSON: {last_error}") from last_error
    raise OpenAIResponsesError(f"{label} did not return a JSON object.")


def build_text_input_messages(system_text: str, user_text: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": str(system_text or "")}],
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": str(user_text or "")}],
        },
    ]


def build_json_schema_request(
    *,
    model: str,
    input_payload: str | list[dict[str, Any]],
    schema_name: str,
    schema: Mapping[str, Any],
    use_web_search: bool = False,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": str(model or "").strip(),
        "input": input_payload,
        "text": {
            "format": {
                "type": "json_schema",
                "name": str(schema_name or "").strip(),
                "strict": True,
                "schema": dict(schema),
            }
        },
    }
    if use_web_search:
        request["tools"] = [{"type": "web_search"}]
    return request


@dataclass
class OpenAIResponsesClient:
    api_key: str
    api_base_url: str = ""
    timeout_seconds: int = 90
    extra_headers: dict[str, str] = field(default_factory=dict)

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        api_key = str(self.api_key or "").strip()
        if not api_key:
            raise OpenAIResponsesError("OpenAI API key is required.")
        request = urllib.request.Request(
            resolve_openai_responses_url(self.api_base_url),
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self.extra_headers,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=max(1, int(self.timeout_seconds))) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise OpenAIResponsesError(
                f"OpenAI API request failed: HTTP {exc.code}. {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenAIResponsesError(f"Unable to connect OpenAI API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OpenAIResponsesError("OpenAI API request timed out.") from exc
        if not isinstance(response_payload, Mapping):
            raise OpenAIResponsesError("OpenAI API did not return a JSON object payload.")
        return dict(response_payload)

    def create_json_schema(
        self,
        *,
        model: str,
        input_payload: str | list[dict[str, Any]],
        schema_name: str,
        schema: Mapping[str, Any],
        use_web_search: bool = False,
    ) -> dict[str, Any]:
        request_payload = build_json_schema_request(
            model=model,
            input_payload=input_payload,
            schema_name=schema_name,
            schema=schema,
            use_web_search=use_web_search,
        )
        return self.create(request_payload)


__all__ = [
    "DEFAULT_OPENAI_RESPONSES_API_URL",
    "OpenAIResponsesClient",
    "OpenAIResponsesError",
    "build_json_schema_request",
    "build_text_input_messages",
    "extract_json_object_text",
    "extract_output_text",
    "parse_response_json",
    "resolve_openai_responses_url",
]
