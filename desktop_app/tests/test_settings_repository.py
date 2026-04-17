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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
