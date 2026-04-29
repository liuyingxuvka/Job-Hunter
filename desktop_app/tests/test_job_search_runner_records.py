from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.orchestration import job_search_runner_records
from jobflow_desktop_app.search.output.final_output import materialize_output_eligibility


class JobSearchRunnerRecordsTests(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "search": {
                "allowPlatformListings": False,
                "platformListingDomains": ["linkedin.com"],
            },
            "filters": {
                "excludeUnavailableLinks": True,
                "excludeAggregatorLinks": True,
            },
            "analysis": {
                "postVerifyEnabled": False,
                "postVerifyRequireChecked": True,
                "recommendScoreThreshold": 50,
            },
        }

    def test_resolve_job_links_prefers_verified_apply_final_then_source(self) -> None:
        verified = {
            "url": "https://example.com/source",
            "analysis": {
                "postVerify": {
                    "finalUrl": "https://example.com/verified",
                    "isValidJobPage": True,
                }
            },
            "jd": {"applyUrl": "https://example.com/apply"},
        }
        apply_only = {
            "url": "https://example.com/source",
            "jd": {"applyUrl": "https://example.com/apply"},
        }
        source_only = {"url": "https://example.com/source"}

        self.assertEqual(
            job_search_runner_records.resolve_job_links(verified),
            ("https://example.com/source", "https://example.com/verified", "verified_final"),
        )
        self.assertEqual(
            job_search_runner_records.resolve_job_links(apply_only),
            ("https://example.com/source", "https://example.com/apply", "apply"),
        )
        self.assertEqual(
            job_search_runner_records.resolve_job_links(source_only),
            ("https://example.com/source", "https://example.com/source", "source"),
        )

    def test_filter_displayable_recommended_jobs_requires_current_output_stamp(self) -> None:
        jobs = [
            {
                "title": "Skip",
                "url": "https://example.com/jobs/skip",
                "analysis": {"recommend": True, "overallScore": 49},
                "jd": {"applyUrl": "https://example.com/jobs/skip/apply"},
            },
            {
                "title": "Keep",
                "url": "https://example.com/jobs/keep",
                "analysis": {"recommend": True, "overallScore": 80},
                "jd": {"applyUrl": "https://example.com/jobs/keep/apply"},
            },
            {
                "title": "No Score",
                "url": "https://example.com/jobs/no-score",
                "analysis": {"recommend": True},
                "jd": {"applyUrl": "https://example.com/jobs/no-score/apply"},
            },
            {
                "title": "Legacy Unstamped",
                "url": "https://example.com/jobs/legacy",
                "analysis": {"recommend": True, "overallScore": 90},
                "jd": {"applyUrl": "https://example.com/jobs/legacy/apply"},
            },
        ]
        stamped_jobs = [
            materialize_output_eligibility(item, self._config())
            for item in jobs
            if item["title"] != "Legacy Unstamped"
        ]

        filtered = job_search_runner_records.filter_displayable_recommended_jobs(
            [*stamped_jobs, jobs[-1]],
            config=self._config(),
        )

        self.assertEqual([item["title"] for item in filtered], ["Keep"])

    def test_filter_displayable_recommended_jobs_uses_output_eligibility_rules(self) -> None:
        jobs = [
            {
                "title": "View job",
                "url": "https://example.com/jobs/123",
                "analysis": {"recommend": True, "overallScore": 88},
                "jd": {"applyUrl": "https://example.com/jobs/123/apply"},
            }
        ]

        stamped_jobs = [materialize_output_eligibility(item, self._config()) for item in jobs]

        filtered = job_search_runner_records.filter_displayable_recommended_jobs(
            stamped_jobs,
            config=self._config(),
        )

        self.assertEqual(filtered, [])

    def test_build_job_records_maps_bound_role_fields(self) -> None:
        jobs = [
            {
                "title": "Fuel Cell Engineer",
                "company": "Acme",
                "location": "Berlin",
                "url": "https://example.com/source",
                "dateFound": "2026-04-15T10:00:00Z",
                "analysis": {
                    "recommend": True,
                    "overallScore": 82,
                    "fitLevelCn": "高推荐",
                    "fitTrack": "hydrogen_core",
                    "adjacentDirectionCn": "燃料电池",
                    "targetRoleScore": 92,
                    "boundTargetRole": {
                        "roleId": "role-1",
                        "profileId": "7",
                        "nameZh": "燃料电池工程师",
                        "nameEn": "Fuel Cell Engineer",
                        "displayName": "Fuel Cell Engineer",
                        "targetRoleText": "Fuel Cell Engineer / 燃料电池工程师",
                        "score": "90",
                    },
                    "recommendationDisplay": {
                        "currentFitStatus": "needs_rescore",
                        "reason": "target_role_changed",
                    },
                },
            }
        ]

        records = job_search_runner_records.build_job_records(
            jobs,
            job_result_factory=lambda **kwargs: kwargs,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["bound_target_role_profile_id"], 7)
        self.assertEqual(records[0]["bound_target_role_score"], 92)
        self.assertEqual(records[0]["current_target_role_status"], "needs_rescore")
        self.assertEqual(records[0]["recommendation_display_reason"], "target_role_changed")
        self.assertEqual(records[0]["match_score"], 92)
        self.assertEqual(records[0]["overall_match_score"], 82)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
