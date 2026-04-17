from __future__ import annotations

import unittest

from jobflow_desktop_app.ai.role_recommendations_text import (
    decode_bilingual_description,
    decode_bilingual_role_name,
    description_for_prompt,
    infer_scope_profile,
    is_generic_role_name,
    role_name_query_lines,
)


class RoleRecommendationsTextTests(unittest.TestCase):
    def test_decode_bilingual_helpers_and_prompt_rendering(self) -> None:
        zh, en = decode_bilingual_description('{"zh":"中文描述","en":"English description"}')
        self.assertEqual((zh, en), ("中文描述", "English description"))
        self.assertEqual(
            description_for_prompt('{"zh":"中文描述","en":"English description"}'),
            "ZH: 中文描述\nEN: English description",
        )
        self.assertEqual(
            decode_bilingual_role_name('{"zh":"系统工程师","en":"Systems Engineer"}'),
            ("系统工程师", "Systems Engineer"),
        )
        self.assertEqual(
            role_name_query_lines('{"zh":"系统工程师","en":"Systems Engineer"}', fallback_name="Systems Engineer"),
            ["Systems Engineer", "系统工程师"],
        )

    def test_scope_profile_and_generic_name_detection(self) -> None:
        self.assertEqual(
            infer_scope_profile("Fuel Cell Test Engineer", "PEM hydrogen system validation"),
            "hydrogen_mainline",
        )
        self.assertTrue(is_generic_role_name("Engineer"))
        self.assertTrue(is_generic_role_name("高级工程师"))
        self.assertFalse(is_generic_role_name("Battery Systems Engineer"))
        self.assertFalse(is_generic_role_name("燃料电池系统工程师"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
