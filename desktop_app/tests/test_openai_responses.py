from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.ai.client import (  # noqa: E402
    OpenAIResponsesClient,
    OpenAIResponsesError,
    build_json_schema_request,
    build_text_input_messages,
    extract_json_object_text,
    extract_output_text,
    parse_response_json,
    resolve_openai_responses_url,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class OpenAIResponsesTests(unittest.TestCase):
    def test_resolve_openai_responses_url_normalizes_common_base_urls(self) -> None:
        self.assertEqual(
            resolve_openai_responses_url("https://example.com/v1"),
            "https://example.com/v1/responses",
        )
        self.assertEqual(
            resolve_openai_responses_url("https://example.com/openai/v1"),
            "https://example.com/openai/v1/responses",
        )
        self.assertEqual(
            resolve_openai_responses_url("https://example.com/openai"),
            "https://example.com/openai/v1/responses",
        )

    def test_extract_output_text_collects_output_content_text(self) -> None:
        payload = {
            "output": [
                {"content": [{"text": "first"}, {"text": "second"}]},
                {"content": [{"text": ""}]},
            ]
        }
        self.assertEqual(extract_output_text(payload), "first\nsecond")

    def test_extract_json_object_text_strips_code_fences(self) -> None:
        raw = "```json\n{\"value\": 1}\n```"
        self.assertEqual(extract_json_object_text(raw), '{"value": 1}')

    def test_parse_response_json_prefers_output_parsed_and_content_json(self) -> None:
        self.assertEqual(
            parse_response_json({"output_parsed": {"value": 1}}, "test"),
            {"value": 1},
        )
        self.assertEqual(
            parse_response_json({"output": [{"content": [{"json": {"value": 2}}]}]}, "test"),
            {"value": 2},
        )

    def test_parse_response_json_reads_json_from_output_text(self) -> None:
        payload = {"output_text": "Answer:\n{\"value\": 3}"}
        self.assertEqual(parse_response_json(payload, "test"), {"value": 3})

    def test_parse_response_json_raises_for_unparseable_payload(self) -> None:
        with self.assertRaises(OpenAIResponsesError):
            parse_response_json({"output_text": "not json"}, "broken")

    def test_build_helpers_create_expected_payload_shapes(self) -> None:
        messages = build_text_input_messages("system", "user")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["content"][0]["text"], "user")
        request = build_json_schema_request(
            model="gpt-5-nano",
            input_payload="hello",
            schema_name="demo",
            schema={"type": "object", "properties": {}, "required": []},
            use_web_search=True,
        )
        self.assertEqual(request["model"], "gpt-5-nano")
        self.assertEqual(request["tools"], [{"type": "web_search"}])
        self.assertEqual(request["text"]["format"]["name"], "demo")

    def test_openai_responses_client_posts_to_responses_api(self) -> None:
        client = OpenAIResponsesClient(api_key="test-key", api_base_url="https://example.com/v1")
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = request.data.decode("utf-8")
            captured["timeout"] = timeout
            return _FakeResponse({"output_text": "{\"ok\": true}"})

        with patch("urllib.request.urlopen", fake_urlopen):
            response = client.create_json_schema(
                model="gpt-5-nano",
                input_payload="hello",
                schema_name="demo",
                schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            )
        self.assertEqual(captured["url"], "https://example.com/v1/responses")
        self.assertEqual(captured["timeout"], 90)
        self.assertIn('"model": "gpt-5-nano"', str(captured["body"]))
        self.assertEqual(response["output_text"], '{"ok": true}')


if __name__ == "__main__":
    unittest.main()

