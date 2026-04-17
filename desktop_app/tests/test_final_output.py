from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.output.final_output import (  # noqa: E402
    build_final_output_dedupe_key,
    is_output_eligible,
    passes_final_output_check,
    rebuild_recommended_output_payload,
)


class FinalOutputTests(unittest.TestCase):
    def _config(self, *, mode: str = "replace", post_verify_enabled: bool = False) -> dict:
        return {
            "search": {
                "allowPlatformListings": False,
                "platformListingDomains": ["linkedin.com"],
            },
            "filters": {
                "excludeUnavailableLinks": True,
                "excludeAggregatorLinks": True,
                "preferDirectEmployerSite": True,
            },
            "analysis": {
                "postVerifyEnabled": post_verify_enabled,
                "postVerifyRequireChecked": True,
            },
            "output": {
                "recommendedMode": mode,
            },
        }

    def _job(
        self,
        *,
        title: str,
        url: str,
        score: int,
        recommend: bool = True,
        date_found: str = "2026-04-14T12:00:00Z",
        apply_url: str = "",
        final_url: str = "",
        verified: dict | None = None,
        summary: str = "Responsibilities include hydrogen system diagnostics and durability analysis.",
    ) -> dict:
        analysis: dict = {
            "recommend": recommend,
            "overallScore": score,
            "matchScore": score,
            "fitTrack": "hydrogen_core",
        }
        if verified is not None:
            analysis["postVerify"] = verified
        return {
            "title": title,
            "company": "Acme Hydrogen",
            "location": "Berlin, Germany",
            "url": url,
            "dateFound": date_found,
            "summary": summary,
            "sourceType": "company",
            "jd": {
                "applyUrl": apply_url,
                "finalUrl": final_url,
                "status": 200,
                "ok": True,
                "rawText": "Responsibilities Qualifications Apply now",
            },
            "analysis": analysis,
        }

    def test_replace_mode_keeps_only_final_output_ready_recommendations(self) -> None:
        valid = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=68,
        )
        below_threshold = self._job(
            title="Electrolyzer Systems Engineer",
            url="https://acme.example.com/careers/jobs/67890",
            apply_url="https://acme.example.com/careers/jobs/67890/apply",
            score=49,
        )
        generic_title = self._job(
            title="View job",
            url="https://acme.example.com/careers/jobs/22222",
            apply_url="https://acme.example.com/careers/jobs/22222/apply",
            score=80,
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[valid, below_threshold, generic_title],
            existing_recommended_jobs=[],
            config=self._config(mode="replace"),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Fuel Cell Reliability Engineer")
        self.assertEqual(
            jobs[0]["outputUrl"],
            "https://acme.example.com/careers/jobs/12345/apply",
        )
        self.assertIn("推荐", jobs[0]["listTags"])

    def test_append_mode_restores_existing_history_and_keeps_original_date_found(self) -> None:
        existing_job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=58,
            date_found="2026-04-01T09:00:00Z",
            summary="Older summary.",
        )
        refreshed_job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=72,
            date_found="2026-04-14T12:00:00Z",
            summary="Updated summary with more hydrogen durability detail.",
        )
        historical_job = self._job(
            title="Electrolyzer Durability Scientist",
            url="https://acme.example.com/careers/jobs/55555",
            apply_url="https://acme.example.com/careers/jobs/55555/apply",
            score=63,
            date_found="2026-04-10T12:00:00Z",
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[refreshed_job, historical_job],
            existing_recommended_jobs=[existing_job],
            config=self._config(mode="append"),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 2)

        by_key = {
            build_final_output_dedupe_key(job, self._config(mode="append")): job
            for job in jobs
        }
        shared = by_key["https://acme.example.com/careers/jobs/12345/apply"]
        self.assertEqual(shared["dateFound"], "2026-04-01T09:00:00Z")
        self.assertEqual(shared["analysis"]["matchScore"], 72)
        self.assertIn(
            "https://acme.example.com/careers/jobs/55555/apply",
            by_key,
        )

    def test_post_verify_gate_can_block_unchecked_recommendations(self) -> None:
        unchecked = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/33333",
            apply_url="https://acme.example.com/careers/jobs/33333/apply",
            score=77,
            verified=None,
        )
        checked = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/44444",
            apply_url="https://acme.example.com/careers/jobs/44444/apply",
            score=77,
            verified={
                "isValidJobPage": True,
                "recommend": True,
                "finalUrl": "https://acme.example.com/careers/jobs/44444",
            },
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[unchecked, checked],
            existing_recommended_jobs=[],
            config=self._config(mode="replace", post_verify_enabled=True),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Hydrogen Diagnostics Engineer")
        self.assertEqual(
            jobs[0]["url"],
            "https://acme.example.com/careers/jobs/44444",
        )

    def test_post_verify_disabled_does_not_block_final_output(self) -> None:
        unchecked = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/55555",
            apply_url="https://acme.example.com/careers/jobs/55555/apply",
            score=77,
            verified={
                "isValidJobPage": False,
                "recommend": False,
                "finalUrl": "https://acme.example.com/careers/jobs/55555",
            },
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[unchecked],
            existing_recommended_jobs=[],
            config=self._config(mode="replace", post_verify_enabled=False),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Hydrogen Diagnostics Engineer")
        self.assertEqual(jobs[0]["outputUrl"], "https://acme.example.com/careers/jobs/55555/apply")

    def test_passes_final_output_check_accepts_resolved_apply_url_when_source_url_missing(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            final_url="https://acme.example.com/careers/jobs/12345",
            score=72,
        )

        self.assertTrue(passes_final_output_check(job, self._config(mode="replace")))

    def test_is_output_eligible_recomputes_instead_of_trusting_cached_flag(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            final_url="https://acme.example.com/careers/",
            score=72,
        )
        job["analysis"]["eligibleForOutput"] = True
        job["jd"]["status"] = 404

        self.assertFalse(is_output_eligible(job, self._config(mode="replace")))


if __name__ == "__main__":
    unittest.main()

