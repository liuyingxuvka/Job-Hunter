from __future__ import annotations

import unittest

from jobflow_desktop_app.app.pages.target_direction_profile_completion import complete_profile_text
from jobflow_desktop_app.db.repositories.settings import OpenAISettings


class TargetDirectionProfileCompletionTests(unittest.TestCase):
    def test_complete_profile_text_uses_completed_name_for_description_stage(self) -> None:
        calls: list[tuple[str, ...]] = []

        def complete_role_name_pair(
            name_zh: str,
            name_en: str,
            _settings: OpenAISettings,
            _api_base_url: str,
            use_ai: bool,
        ) -> tuple[str, str]:
            calls.append(("role", name_zh, name_en, str(use_ai)))
            return "燃料电池测试工程师", "Fuel Cell Test Engineer"

        def complete_description_pair(
            role_name: str,
            description_zh: str,
            description_en: str,
            _settings: OpenAISettings,
            _api_base_url: str,
            use_ai: bool,
        ) -> tuple[str, str]:
            calls.append(("description", role_name, description_zh, description_en, str(use_ai)))
            return "负责测试", "Leads testing"

        completed = complete_profile_text(
            role_name_zh="测试工程师",
            role_name_en="Test Engineer",
            description_zh="",
            description_en="",
            fallback_name="Test Engineer",
            settings=OpenAISettings(api_key="key", model="gpt-5-nano"),
            api_base_url="",
            use_ai=True,
            complete_role_name_pair=complete_role_name_pair,
            complete_description_pair=complete_description_pair,
        )

        self.assertEqual(completed.role_name_en, "Fuel Cell Test Engineer")
        self.assertEqual(completed.description_en, "Leads testing")
        self.assertEqual(
            calls,
            [
                ("role", "测试工程师", "Test Engineer", "True"),
                ("description", "Fuel Cell Test Engineer", "", "", "True"),
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
