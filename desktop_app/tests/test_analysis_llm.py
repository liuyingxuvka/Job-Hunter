from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.analysis.prompts import (  # noqa: E402
    TargetRoleDefinition,
    apply_target_role_binding_to_analysis,
    build_full_scoring_request,
    build_lite_scoring_request,
    build_target_role_binding_prompt,
    extract_job_jd_text,
    normalize_full_scoring_payload,
    normalize_lite_scoring_payload,
    normalize_post_verify_payload,
    normalize_target_role_binding_payload,
    prepare_analysis_for_storage,
    target_role_binding_min_score,
    unified_overall_scoring_rubric,
)


class AnalysisLlmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "candidate": {
                "targetRoles": [
                    {
                        "roleId": "profile:0",
                        "profileId": 0,
                        "nameEn": "Hydrogen durability engineer",
                        "displayName": "Hydrogen durability engineer",
                        "targetRoleText": "Hydrogen durability engineer",
                    }
                ],
                "locationPreference": "Berlin / Remote",
            },
            "analysis": {
                "recommendScoreThreshold": 50,
                "targetRoleBindingMinScore": 45,
            },
        }
        self.job = {
            "title": "Senior Fuel Cell Durability Engineer",
            "company": "Acme Energy",
            "location": "Berlin",
            "url": "https://example.com/jobs/1",
            "summary": "Lead AST protocol design and PEM stack reliability analysis.",
            "jd": {"text": "Develop durability test plans and degradation models."},
        }
        self.candidate_profile = {
            "summary": "Hydrogen durability and PEM degradation researcher",
            "job_fit_core_terms": ["PEM", "degradation", "durability"],
        }
        self.target_roles = [
            TargetRoleDefinition(
                role_id="profile:1",
                profile_id=1,
                name_en="AST Protocol Development Engineer",
                display_name="AST Protocol Development Engineer",
                target_role_text="Durability / AST",
            ),
            TargetRoleDefinition(
                role_id="profile:2",
                profile_id=2,
                name_en="Hydrogen Business Development",
                display_name="Hydrogen Business Development",
                target_role_text="Commercial",
            ),
        ]

    def test_unified_overall_scoring_rubric_mentions_threshold_gate(self) -> None:
        rubric = unified_overall_scoring_rubric(recommend_threshold=50)
        self.assertIn("matchScore >= 50", rubric)
        self.assertIn("关键词", rubric)

    def test_build_lite_scoring_request_can_enable_web_search(self) -> None:
        request = build_lite_scoring_request(
            model="gpt-5-nano",
            config=self.config,
            candidate_profile=self.candidate_profile,
            job=self.job,
            jd_text=extract_job_jd_text(self.job),
            jd_limit=800,
            use_web_search=True,
        )
        self.assertEqual(request["model"], "gpt-5-nano")
        self.assertEqual(request["tools"], [{"type": "web_search"}])
        self.assertEqual(request["text"]["format"]["name"], "job_fit_score_lite")
        self.assertIn("Hydrogen durability engineer", str(request["input"]))

    def test_build_full_scoring_request_uses_full_schema(self) -> None:
        request = build_full_scoring_request(
            model="gpt-5",
            config=self.config,
            candidate_profile=self.candidate_profile,
            job=self.job,
            jd_text=extract_job_jd_text(self.job),
            jd_limit=2000,
        )
        schema = request["text"]["format"]["schema"]
        self.assertIn("fitLevelCn", schema["properties"])
        self.assertEqual(request["text"]["format"]["name"], "job_fit_score")

    def test_build_target_role_binding_prompt_contains_roles_and_overall_result(self) -> None:
        prompt = build_target_role_binding_prompt(
            config=self.config,
            candidate_profile=self.candidate_profile,
            job=self.job,
            jd_text=extract_job_jd_text(self.job),
            jd_limit=900,
            overall_analysis={"matchScore": 68, "fitLevelCn": "可能匹配", "recommend": True},
            target_roles=self.target_roles,
            recommend_threshold=50,
        )
        self.assertIn("bestRoleId", prompt)
        self.assertIn("profile:1", prompt)
        self.assertIn("整体粗筛结果", prompt)

    def test_normalize_lite_scoring_payload_applies_threshold_gate(self) -> None:
        normalized = normalize_lite_scoring_payload(
            {
                "matchScore": 47,
                "recommend": True,
                "isJobPosting": True,
                "location": "Berlin",
                "fitTrack": "hydrogen_core",
                "transferableScore": 66,
                "primaryEvidenceCn": "岗位职责与耐久测试高度相关",
            },
            recommend_threshold=50,
        )
        self.assertFalse(normalized["recommend"])
        self.assertEqual(normalized["fitLevelCn"], "不匹配")

    def test_normalize_full_scoring_payload_sanitizes_lists(self) -> None:
        normalized = normalize_full_scoring_payload(
            {
                "matchScore": 73,
                "fitLevelCn": "匹配",
                "isJobPosting": True,
                "jobPostingEvidenceCn": "ATS detail page",
                "recommend": True,
                "recommendReasonCn": "核心职责匹配",
                "location": "Berlin",
                "fitTrack": "hydrogen_core",
                "transferableScore": 80,
                "primaryEvidenceCn": "PEM degradation",
                "summaryCn": "氢能耐久岗位",
                "reasonsCn": ["耐久性", "", None],
                "gapsCn": ["量产经验"],
                "questionsCn": ["AST 范围?"],
                "nextActionCn": "准备面试",
            },
            recommend_threshold=50,
        )
        self.assertEqual(normalized["reasonsCn"], ["耐久性"])
        self.assertEqual(normalized["overallScore"], 73)
        self.assertNotIn("overallMatchScore", normalized)
        self.assertTrue(normalized["recommend"])

    def test_normalize_target_role_binding_payload_falls_back_to_highest_score(self) -> None:
        result = normalize_target_role_binding_payload(
            {
                "bestRoleId": "missing",
                "evaluations": [
                    {"roleId": "profile:1", "score": 81, "fitLevelCn": "匹配", "recommend": True, "reasonCn": "职责贴近"},
                    {"roleId": "profile:2", "score": 42, "fitLevelCn": "不匹配", "recommend": True, "reasonCn": "仅业务相关"},
                ],
            },
            target_roles=self.target_roles,
            recommend_threshold=50,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.best_role.role_id, "profile:1")
        self.assertFalse(result.evaluations[1].recommend)

    def test_normalize_post_verify_payload_requires_confirmed_final_url(self) -> None:
        normalized = normalize_post_verify_payload(
            {"isValidJobPage": True, "recommend": False, "location": "Remote", "finalUrl": ""},
            job_url=self.job["url"],
        )
        self.assertTrue(normalized["isValidJobPage"])
        self.assertEqual(normalized["finalUrl"], "")

    def test_target_role_binding_min_score_reads_analysis_override(self) -> None:
        self.assertEqual(target_role_binding_min_score(self.config), 45)

    def test_prepare_analysis_for_storage_keeps_overall_score_without_binding(self) -> None:
        prepared = prepare_analysis_for_storage(
            {"matchScore": 63, "fitLevelCn": "可能匹配", "recommend": True},
            None,
            config=self.config,
        )
        self.assertNotIn("overallMatchScore", prepared)
        self.assertEqual(prepared["overallScore"], 63)
        self.assertEqual(prepared["overallFitLevelCn"], "可能匹配")

    def test_apply_target_role_binding_to_analysis_rewrites_display_score_only(self) -> None:
        binding = normalize_target_role_binding_payload(
            {
                "bestRoleId": "profile:1",
                "evaluations": [
                    {"roleId": "profile:1", "score": 81, "fitLevelCn": "匹配", "recommend": True, "reasonCn": "职责贴近"},
                    {"roleId": "profile:2", "score": 42, "fitLevelCn": "不匹配", "recommend": False, "reasonCn": "方向较远"},
                ],
            },
            target_roles=self.target_roles,
            recommend_threshold=50,
        )
        assert binding is not None
        updated = apply_target_role_binding_to_analysis(
            {"matchScore": 68, "fitLevelCn": "可能匹配", "recommend": True, "recommendReasonCn": "整体匹配"},
            binding,
            config=self.config,
        )
        self.assertNotIn("overallMatchScore", updated)
        self.assertEqual(updated["overallScore"], 68)
        self.assertEqual(updated["matchScore"], 68)
        self.assertEqual(updated["fitLevelCn"], "可能匹配")
        self.assertEqual(updated["targetRoleScore"], 81)
        self.assertTrue(updated["recommend"])
        self.assertEqual(updated["boundTargetRole"]["roleId"], "profile:1")
        self.assertEqual(updated["boundTargetRole"]["score"], 81)


if __name__ == "__main__":
    unittest.main()

