from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.analysis.scoring import (
    bound_target_role_score,
    overall_analysis_score,
    passes_unified_recommendation_threshold,
    to_fit_level_cn,
    unified_recommend_threshold,
)


class SearchEngineScoringTests(unittest.TestCase):
    def test_fit_level_cn_uses_unified_bands(self) -> None:
        self.assertEqual(to_fit_level_cn(85), "强匹配")
        self.assertEqual(to_fit_level_cn(70), "匹配")
        self.assertEqual(to_fit_level_cn(50), "可能匹配")
        self.assertEqual(to_fit_level_cn(49), "不匹配")

    def test_overall_analysis_score_prefers_overall_match_score(self) -> None:
        payload = {"analysis": {"matchScore": 18, "overallScore": 64}}
        self.assertEqual(overall_analysis_score(payload), 64)

    def test_bound_target_role_score_prefers_canonical_target_role_score(self) -> None:
        payload = {"analysis": {"matchScore": 63, "targetRoleScore": 19, "boundTargetRole": {"score": 18}}}
        self.assertEqual(bound_target_role_score(payload), 19)

    def test_passes_unified_threshold_uses_overall_score_only(self) -> None:
        payload = {
            "analysis": {
                "overallScore": 63,
                "matchScore": 18,
                "boundTargetRole": {"score": 18},
            }
        }
        self.assertTrue(passes_unified_recommendation_threshold(payload, threshold=50))

    def test_unified_threshold_can_read_nested_config(self) -> None:
        config = {"analysis": {"recommendScoreThreshold": 55}}
        self.assertEqual(unified_recommend_threshold(config), 55)
        self.assertFalse(
            passes_unified_recommendation_threshold(
                {"analysis": {"overallScore": 54}},
                config=config,
            )
        )


if __name__ == "__main__":
    unittest.main()

