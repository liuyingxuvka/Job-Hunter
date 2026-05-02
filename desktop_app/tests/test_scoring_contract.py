from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.analysis.scoring_contract import (
    UNIFIED_RECOMMEND_THRESHOLD,
    bound_role_score,
    fit_level,
    overall_score,
    passes_unified_recommendation_threshold,
    should_keep_for_final_list,
    unified_recommend_threshold,
)


class ScoringContractTests(unittest.TestCase):
    def test_fit_level_uses_expected_boundaries(self) -> None:
        self.assertEqual(fit_level(85), "强匹配")
        self.assertEqual(fit_level(84), "匹配")
        self.assertEqual(fit_level(70), "匹配")
        self.assertEqual(fit_level(69), "可能匹配")
        self.assertEqual(fit_level(50), "可能匹配")
        self.assertEqual(fit_level(49), "不匹配")
        self.assertEqual(fit_level(30), "不匹配")
        self.assertEqual(fit_level(29), "不匹配")

    def test_overall_threshold_is_the_only_hard_gate(self) -> None:
        self.assertEqual(UNIFIED_RECOMMEND_THRESHOLD, 20)
        self.assertTrue(passes_unified_recommendation_threshold(20))
        self.assertTrue(passes_unified_recommendation_threshold({"overallScore": 21}))
        self.assertFalse(passes_unified_recommendation_threshold(19))
        self.assertFalse(
            passes_unified_recommendation_threshold(
                {"overallScore": 19, "targetRoleScore": 100}
            )
        )

    def test_bound_role_score_does_not_act_as_a_hard_filter(self) -> None:
        self.assertEqual(bound_role_score({"targetRoleScore": 18}), 18)
        self.assertEqual(bound_role_score({"boundTargetRole": {"score": 22}}), 22)
        self.assertEqual(bound_role_score({"matchScore": 88}), 0)
        self.assertTrue(should_keep_for_final_list(63, 18))
        self.assertTrue(should_keep_for_final_list(63, 99))
        self.assertFalse(should_keep_for_final_list(19, 99))

    def test_score_extractors_read_current_contract_fields(self) -> None:
        self.assertEqual(overall_score({"overallScore": 64, "matchScore": 52}), 64)
        self.assertEqual(
            bound_role_score({"targetRoleScore": 19, "boundTargetRole": {"score": 18}, "matchScore": 52}),
            19,
        )
        self.assertEqual(bound_role_score({"roleScore": 91}), 0)
        self.assertEqual(overall_score({"matchScore": 81}), 0)
        self.assertEqual(overall_score({"score": 81}), 0)
        self.assertEqual(unified_recommend_threshold(None), 20)
        self.assertEqual(unified_recommend_threshold(65), 65)
        self.assertEqual(
            unified_recommend_threshold({"analysis": {"recommendScoreThreshold": 55}}),
            55,
        )


if __name__ == "__main__":
    unittest.main()

