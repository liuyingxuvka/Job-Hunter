from __future__ import annotations

import unittest

from jobflow_desktop_app.ai.role_recommendations_parse import (
    parse_refined_manual_role,
    parse_role_suggestions,
)


class RoleRecommendationsParseTests(unittest.TestCase):
    def test_parse_role_suggestions_skips_generic_and_duplicate_titles(self) -> None:
        payload = """
        {
          "roles": [
            {"name_en": "Engineer", "description_en": "too generic"},
            {"name_en": "Fuel Cell Systems Engineer", "description_en": "hydrogen systems"},
            {"name_en": "fuel cell systems engineer", "description_en": "duplicate"},
            {"name_zh": "燃料电池测试工程师", "description_zh": "测试与验证"}
          ]
        }
        """.strip()

        suggestions = parse_role_suggestions(payload, max_items=5)

        self.assertEqual(
            [suggestion.name for suggestion in suggestions],
            ["Fuel Cell Systems Engineer", "燃料电池测试工程师"],
        )
        self.assertEqual(suggestions[0].scope_profile, "")

    def test_parse_refined_manual_role_accepts_nested_payload_and_fallback_description(self) -> None:
        payload = """
        {
          "role": {
            "name_en": "Hydrogen Reliability Engineer",
            "description_zh": ""
          }
        }
        """.strip()

        suggestion = parse_refined_manual_role(
            payload,
            fallback_name="Fallback Name",
            fallback_description="reliability validation for hydrogen systems",
        )

        self.assertIsNotNone(suggestion)
        assert suggestion is not None
        self.assertEqual(suggestion.name_en, "Hydrogen Reliability Engineer")
        self.assertEqual(suggestion.description_zh, "reliability validation for hydrogen systems")
        self.assertEqual(suggestion.description_en, "reliability validation for hydrogen systems")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
