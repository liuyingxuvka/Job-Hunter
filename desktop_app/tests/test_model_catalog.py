from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from jobflow_desktop_app.ai.model_catalog import ModelProbeResult, probe_response_model


def _http_error(code: int, payload: dict[str, object]) -> HTTPError:
    return HTTPError(
        url="https://api.openai.com/v1/responses",
        code=code,
        msg="error",
        hdrs=None,
        fp=BytesIO(json.dumps(payload).encode("utf-8")),
    )


class ModelCatalogTests(unittest.TestCase):
    def test_probe_response_model_treats_insufficient_quota_as_unusable(self) -> None:
        error = _http_error(
            429,
            {
                "error": {
                    "message": "You exceeded your current quota.",
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            },
        )
        with patch("jobflow_desktop_app.ai.model_catalog.urlopen", side_effect=error):
            result = probe_response_model("sk-test", "gpt-5-nano")

        self.assertIsInstance(result, ModelProbeResult)
        self.assertFalse(result.usable)
        self.assertIn("HTTP 429", result.error)

    def test_probe_response_model_keeps_transient_rate_limits_usable(self) -> None:
        error = _http_error(
            429,
            {
                "error": {
                    "message": "Rate limit exceeded.",
                    "type": "rate_limit_exceeded",
                    "code": "rate_limit_exceeded",
                }
            },
        )
        with patch("jobflow_desktop_app.ai.model_catalog.urlopen", side_effect=error):
            result = probe_response_model("sk-test", "gpt-5-nano")

        self.assertTrue(result.usable)
        self.assertEqual(result.error, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
