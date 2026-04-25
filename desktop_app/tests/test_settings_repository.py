from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from jobflow_desktop_app.db.repositories.settings import AppSettingsRepository, OpenAISettings

try:
    from ._helpers import make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import make_temp_context  # type: ignore


class SettingsRepositoryTests(unittest.TestCase):
    def test_windows_session_env_var_is_listed_for_api_key_selection(self) -> None:
        with make_temp_context() as context:
            repo = context.settings
            repo.save_openai_settings(
                OpenAISettings(
                    api_key="",
                    model="gpt-5-nano",
                    api_key_source="env",
                    api_key_env_var="OPENAI_API_KEY",
                )
            )

            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "sk-session-test"}, clear=False),
                patch("jobflow_desktop_app.db.repositories.settings.os.name", "nt"),
                patch.object(
                    AppSettingsRepository,
                    "_list_persisted_environment_variable_names",
                    autospec=True,
                    return_value=set(),
                ),
            ):
                names = repo.list_api_key_environment_variables()

            self.assertIn("OPENAI_API_KEY", names)

    def test_openai_settings_persist_fast_and_quality_models_separately(self) -> None:
        with make_temp_context() as context:
            repo = context.settings
            repo.save_openai_settings(
                OpenAISettings(
                    api_key="test-key",
                    model="gpt-5-nano",
                    quality_model="gpt-5.4",
                    api_key_source="direct",
                    api_key_env_var="",
                )
            )

            saved = repo.get_openai_settings()
            self.assertEqual(saved.model, "gpt-5-nano")
            self.assertEqual(saved.fast_model, "gpt-5-nano")
            self.assertEqual(saved.quality_model, "gpt-5.4")
            self.assertEqual(repo.get_fast_openai_settings().model, "gpt-5-nano")
            self.assertEqual(repo.get_quality_openai_settings().model, "gpt-5.4")

    def test_effective_openai_settings_can_read_fast_and_quality_models_from_environment(self) -> None:
        with make_temp_context() as context:
            context.settings.save_openai_settings(
                OpenAISettings(
                    api_key="",
                    model="",
                    quality_model="",
                    api_key_source="env",
                    api_key_env_var="OPENAI_API_KEY",
                )
            )
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "sk-env-test",
                    "JOBFLOW_OPENAI_FAST_MODEL": "gpt-5-nano",
                    "JOBFLOW_OPENAI_QUALITY_MODEL": "gpt-5.4",
                },
                clear=False,
            ):
                effective = context.settings.get_effective_openai_settings()

            self.assertEqual(effective.api_key, "sk-env-test")
            self.assertEqual(effective.model, "gpt-5-nano")
            self.assertEqual(effective.quality_model, "gpt-5.4")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
