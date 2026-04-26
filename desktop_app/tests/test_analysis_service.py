from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.analysis.service import (  # noqa: E402
    JobAnalysisService,
)


class _FakeClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[dict] = []

    def create(self, payload: dict) -> dict:
        self.requests.append(payload)
        if not self.payloads:
            raise AssertionError("No fake payload left for client.create().")
        return self.payloads.pop(0)


class AnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "candidate": {
                "locationPreference": "Berlin / Remote",
                "targetRoles": [
                    {
                        "roleId": "profile:1",
                        "profileId": 1,
                        "nameEn": "AST Protocol Development Engineer",
                        "displayName": "AST Protocol Development Engineer",
                        "targetRoleText": "Durability / AST",
                    },
                    {
                        "roleId": "profile:2",
                        "profileId": 2,
                        "nameEn": "Hydrogen Business Development",
                        "displayName": "Hydrogen Business Development",
                        "targetRoleText": "Commercial",
                    },
                ],
            },
            "analysis": {
                "model": "gpt-5-nano",
                "postVerifyModel": "gpt-5-nano",
                "recommendScoreThreshold": 50,
                "targetRoleBindingMinScore": 45,
                "lowTokenMode": True,
                "scoringUseWebSearch": False,
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

    def test_score_job_fit_low_token_mode_normalizes_payload(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": (
                        '{"matchScore": 74, "recommend": true, "isJobPosting": true, '
                        '"location": "Berlin", "fitTrack": "hydrogen_core", '
                        '"transferableScore": 69, "primaryEvidenceCn": "耐久与降解方向直接匹配"}'
                    )
                }
            ]
        )
        analysis = JobAnalysisService.score_job_fit(
            client,
            config=self.config,
            candidate_profile=self.candidate_profile,
            job=self.job,
        )
        self.assertEqual(analysis["matchScore"], 74)
        self.assertEqual(analysis["overallScore"], 74)
        self.assertNotIn("overallMatchScore", analysis)
        self.assertTrue(analysis["recommend"])
        self.assertEqual(client.requests[0]["text"]["format"]["name"], "job_fit_score_lite")
        self.assertIn("岗位证据上下文", client.requests[0]["input"])
        self.assertIn("negativeEvidenceCn", client.requests[0]["text"]["format"]["schema"]["properties"])

    def test_evaluate_target_roles_for_job_calls_model_for_single_role_case(self) -> None:
        config = {
            **self.config,
            "candidate": {
                **self.config["candidate"],
                "targetRoles": [self.config["candidate"]["targetRoles"][0]],
            },
        }
        client = _FakeClient(
            [
                {
                    "output_text": (
                        '{"bestRoleId": "profile:1", "evaluations": ['
                        '{"roleId": "profile:1", "score": 78, "fitLevelCn": "匹配", "recommend": true, "reasonCn": "职责非常贴近"}'
                        ']}'
                    )
                }
            ]
        )
        binding = JobAnalysisService.evaluate_target_roles_for_job(
            client,
            config=config,
            candidate_profile=self.candidate_profile,
            job=self.job,
            analysis={"matchScore": 62, "fitLevelCn": "可能匹配", "recommend": True, "primaryEvidenceCn": "整体贴近"},
        )
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.best_role.role_id, "profile:1")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.requests[0]["text"]["format"]["name"], "target_role_binding")

    def test_evaluate_target_roles_for_job_calls_model_for_multi_role_case(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": (
                        '{"bestRoleId": "profile:1", "evaluations": ['
                        '{"roleId": "profile:1", "score": 82, "fitLevelCn": "匹配", "recommend": true, "reasonCn": "职责非常贴近"},'
                        '{"roleId": "profile:2", "score": 33, "fitLevelCn": "不匹配", "recommend": false, "reasonCn": "偏商业"}'
                        ']}'
                    )
                }
            ]
        )
        binding = JobAnalysisService.evaluate_target_roles_for_job(
            client,
            config=self.config,
            candidate_profile=self.candidate_profile,
            job=self.job,
            analysis={"matchScore": 66, "fitLevelCn": "可能匹配", "recommend": True, "primaryEvidenceCn": "整体贴近"},
        )
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding.best_role.role_id, "profile:1")
        self.assertEqual(client.requests[0]["text"]["format"]["name"], "target_role_binding")

    def test_evaluate_target_roles_for_job_skips_low_score_non_recommended_jobs(self) -> None:
        client = _FakeClient([])
        binding = JobAnalysisService.evaluate_target_roles_for_job(
            client,
            config=self.config,
            candidate_profile=self.candidate_profile,
            job=self.job,
            analysis={"matchScore": 22, "fitLevelCn": "不匹配", "recommend": False},
        )
        self.assertIsNone(binding)
        self.assertEqual(client.requests, [])

    def test_post_verify_recommended_job_normalizes_output(self) -> None:
        self.config["analysis"]["postVerifyUseWebSearch"] = True
        client = _FakeClient(
            [
                {
                    "output_text": (
                        '{"isValidJobPage": true, "recommend": false, "location": "Berlin", '
                        '"finalUrl": "https://example.com/jobs/1/apply"}'
                    )
                }
            ]
        )
        result = JobAnalysisService.post_verify_recommended_job(
            client,
            config=self.config,
            job=self.job,
        )
        self.assertTrue(result["isValidJobPage"])
        self.assertEqual(result["finalUrl"], "https://example.com/jobs/1/apply")
        self.assertEqual(client.requests[0]["text"]["format"]["name"], "post_verify_recommended_job")
        self.assertEqual(client.requests[0]["tools"], [{"type": "web_search"}])

    def test_prepare_analysis_for_storage_rewrites_bound_role_fields(self) -> None:
        stored = JobAnalysisService.prepare_analysis_for_storage(
            {"matchScore": 66, "fitLevelCn": "可能匹配", "recommend": True},
            {
                "bestRole": {
                    "roleId": "profile:1",
                    "profileId": 1,
                    "nameEn": "AST Protocol Development Engineer",
                    "displayName": "AST Protocol Development Engineer",
                    "targetRoleText": "Durability / AST",
                    "score": 82,
                    "fitLevelCn": "匹配",
                    "recommend": True,
                    "reasonCn": "职责非常贴近",
                },
                "evaluations": [
                    {
                        "roleId": "profile:1",
                        "profileId": 1,
                        "nameEn": "AST Protocol Development Engineer",
                        "displayName": "AST Protocol Development Engineer",
                        "targetRoleText": "Durability / AST",
                        "score": 82,
                        "fitLevelCn": "匹配",
                        "recommend": True,
                        "reasonCn": "职责非常贴近",
                    }
                ],
            },
            config=self.config,
        )
        self.assertNotIn("overallMatchScore", stored)
        self.assertEqual(stored["overallScore"], 66)
        self.assertEqual(stored["matchScore"], 66)
        self.assertEqual(stored["targetRoleScore"], 82)
        self.assertEqual(stored["boundTargetRole"]["roleId"], "profile:1")


if __name__ == "__main__":
    unittest.main()

