from __future__ import annotations

import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.output.final_output import (  # noqa: E402
    FRESH_FINAL_OUTPUT_SOURCE,
    POOL_READBACK_SOURCE,
    PoolRecommendationVisibilityContext,
    build_final_output_dedupe_key,
    decide_source_aware_final_recommendation_visibility,
    has_current_output_eligibility,
    is_output_eligible,
    materialize_output_eligibility,
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
            score=19,
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

    def test_replace_mode_allows_score_at_new_final_floor(self) -> None:
        floor_job = self._job(
            title="Hydrogen Systems Analyst",
            url="https://acme.example.com/careers/jobs/2020",
            apply_url="https://acme.example.com/careers/jobs/2020/apply",
            score=20,
        )
        below_floor_job = self._job(
            title="Hydrogen Systems Associate",
            url="https://acme.example.com/careers/jobs/1919",
            apply_url="https://acme.example.com/careers/jobs/1919/apply",
            score=19,
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[floor_job, below_floor_job],
            existing_recommended_jobs=[],
            config=self._config(mode="replace"),
            generated_at="2026-04-14T12:30:00Z",
        )

        self.assertEqual([job["title"] for job in result.payload["jobs"]], ["Hydrogen Systems Analyst"])

    def test_replace_mode_merges_duplicate_final_output_keys(self) -> None:
        first = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/dupe?utm_source=one",
            apply_url="https://acme.example.com/careers/jobs/dupe/apply",
            score=72,
            summary="Responsibilities include hydrogen durability testing.",
        )
        better = self._job(
            title="Senior Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/dupe?utm_source=two",
            apply_url="https://acme.example.com/careers/jobs/dupe/apply",
            score=86,
            summary="Responsibilities include hydrogen durability testing, diagnostics, and validation.",
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[first, better],
            existing_recommended_jobs=[],
            config=self._config(mode="replace"),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Senior Fuel Cell Reliability Engineer")
        self.assertEqual(
            build_final_output_dedupe_key(jobs[0], self._config(mode="replace")),
            "https://acme.example.com/careers/jobs/dupe/apply",
        )

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

    def test_append_mode_materializes_existing_only_recommendation_stamp(self) -> None:
        existing_job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=72,
            date_found="2026-04-01T09:00:00Z",
        )
        existing_job["interest"] = "感兴趣"

        result = rebuild_recommended_output_payload(
            all_jobs=[],
            existing_recommended_jobs=[existing_job],
            config=self._config(mode="append"),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["dateFound"], "2026-04-01T09:00:00Z")
        self.assertEqual(jobs[0]["interest"], "感兴趣")
        self.assertTrue(has_current_output_eligibility(jobs[0], self._config(mode="append")))

    def test_post_verify_gate_can_block_unchecked_recommendations(self) -> None:
        unchecked = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/33333",
            apply_url="https://acme.example.com/careers/jobs/33333/apply",
            score=77,
            verified=None,
        )
        unchecked["canonicalUrl"] = unchecked["url"]
        unchecked.pop("jd", None)
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

    def test_post_verify_gate_accepts_current_detail_evidence_without_ai_post_verify(self) -> None:
        unchecked = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/33333",
            apply_url="https://acme.example.com/careers/jobs/33333/apply",
            score=77,
            verified=None,
        )
        unchecked["analysis"]["postVerifySkipped"] = True

        result = rebuild_recommended_output_payload(
            all_jobs=[unchecked],
            existing_recommended_jobs=[],
            config=self._config(mode="replace", post_verify_enabled=True),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://acme.example.com/careers/jobs/33333")
        self.assertTrue(jobs[0]["analysis"]["eligibleForOutput"])

    def test_failed_post_verify_overrides_current_detail_evidence(self) -> None:
        failed = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/33333",
            apply_url="https://acme.example.com/careers/jobs/33333/apply",
            score=77,
            verified={
                "isValidJobPage": False,
                "recommend": False,
                "finalUrl": "https://acme.example.com/careers/jobs/33333",
            },
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[failed],
            existing_recommended_jobs=[],
            config=self._config(mode="replace", post_verify_enabled=True),
            generated_at="2026-04-14T12:30:00Z",
        )

        self.assertEqual(result.payload["jobs"], [])

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

    def test_post_verify_skipped_flag_blocks_output_when_main_config_requires_check(self) -> None:
        skipped = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/66666",
            apply_url="https://acme.example.com/careers/jobs/66666/apply",
            score=77,
            verified=None,
        )
        skipped["analysis"]["postVerifySkipped"] = True
        skipped["canonicalUrl"] = skipped["url"]
        skipped.pop("jd", None)

        result = rebuild_recommended_output_payload(
            all_jobs=[skipped],
            existing_recommended_jobs=[],
            config=self._config(mode="replace", post_verify_enabled=True),
            generated_at="2026-04-14T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(jobs, [])

    def test_append_mode_retains_existing_history_without_new_post_verify(self) -> None:
        existing_job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=72,
            date_found="2026-04-01T09:00:00Z",
        )
        existing_job["analysis"].pop("postVerify", None)

        result = rebuild_recommended_output_payload(
            all_jobs=[],
            existing_recommended_jobs=[existing_job],
            config=self._config(mode="append", post_verify_enabled=True),
            generated_at="2026-04-30T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Fuel Cell Reliability Engineer")
        self.assertEqual(
            jobs[0]["analysis"]["outputEligibilityReason"],
            "historical_recommendation_retained",
        )

    def test_post_verify_requires_current_detail_fetch_evidence(self) -> None:
        job = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/77777",
            apply_url="https://acme.example.com/careers/jobs/77777/apply",
            score=77,
            verified={
                "isValidJobPage": True,
                "recommend": True,
                "finalUrl": "https://acme.example.com/careers/jobs/77777",
            },
        )
        job["jd"]["ok"] = False
        job["jd"]["status"] = 0
        job["jd"]["rawText"] = ""

        result = rebuild_recommended_output_payload(
            all_jobs=[job],
            existing_recommended_jobs=[],
            config=self._config(mode="replace", post_verify_enabled=True),
            generated_at="2026-04-30T12:30:00Z",
        )

        self.assertEqual(result.payload["jobs"], [])

    def test_post_verify_can_rescue_reachable_dynamic_detail_page(self) -> None:
        job = self._job(
            title="Hydrogen Diagnostics Engineer",
            url="https://acme.example.com/careers/jobs/88888",
            apply_url="",
            score=77,
            verified={
                "isValidJobPage": True,
                "recommend": True,
                "finalUrl": "https://acme.example.com/careers/jobs/88888",
            },
        )
        job["jd"]["ok"] = False
        job["jd"]["status"] = 200
        job["jd"]["rawText"] = ""
        job["jd"]["finalUrl"] = "https://acme.example.com/careers/jobs/88888"

        result = rebuild_recommended_output_payload(
            all_jobs=[job],
            existing_recommended_jobs=[],
            config=self._config(mode="replace", post_verify_enabled=True),
            generated_at="2026-04-30T12:30:00Z",
        )

        jobs = result.payload["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["outputUrl"], "https://acme.example.com/careers/jobs/88888")

    def test_passes_final_output_check_accepts_resolved_apply_url_when_source_url_missing(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            final_url="https://acme.example.com/careers/jobs/12345",
            score=72,
        )

        self.assertTrue(passes_final_output_check(job, self._config(mode="replace")))

    def test_source_url_alone_is_not_enough_for_recommended_output(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="",
            final_url="",
            score=72,
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[job],
            existing_recommended_jobs=[],
            config=self._config(mode="replace"),
            generated_at="2026-04-14T12:30:00Z",
        )

        self.assertEqual(result.payload["jobs"], [])

    def test_disabled_apply_url_is_not_treated_as_live_output(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://jobdb.example.com/jobdb/public/jobposting/disabled.html",
            final_url="https://acme.example.com/careers/jobs/12345",
            score=72,
        )
        job["jd"]["rawText"] = "Responsibilities Qualifications Apply now"

        result = rebuild_recommended_output_payload(
            all_jobs=[job],
            existing_recommended_jobs=[],
            config=self._config(mode="replace"),
            generated_at="2026-04-14T12:30:00Z",
        )

        self.assertEqual(result.payload["jobs"], [])

    def test_rebuild_materializes_current_output_stamp(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=72,
        )

        result = rebuild_recommended_output_payload(
            all_jobs=[job],
            existing_recommended_jobs=[],
            config=self._config(mode="replace"),
            generated_at="2026-04-14T12:30:00Z",
        )

        [stamped] = result.payload["jobs"]
        self.assertTrue(stamped["analysis"]["eligibleForOutput"])
        self.assertEqual(stamped["analysis"]["outputEligibilityReason"], "eligible")
        self.assertIn("outputEligibilityRuleVersion", stamped["analysis"])
        self.assertIn("outputEligibilityPolicyKey", stamped["analysis"])
        self.assertTrue(has_current_output_eligibility(stamped, self._config(mode="replace")))

    def test_current_output_stamp_requires_matching_policy(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=72,
        )
        stamped = materialize_output_eligibility(job, self._config(mode="replace"))
        stricter_config = self._config(mode="replace")
        stricter_config["analysis"]["recommendScoreThreshold"] = 80

        self.assertTrue(has_current_output_eligibility(stamped, self._config(mode="replace")))
        self.assertFalse(has_current_output_eligibility(stamped, stricter_config))

    def test_top_level_apply_url_counts_as_link_evidence_when_materialized(self) -> None:
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="",
            score=72,
        )
        job["applyUrl"] = "https://acme.example.com/careers/jobs/12345/apply"

        stamped = materialize_output_eligibility(job, self._config(mode="replace"))

        self.assertTrue(stamped["analysis"]["eligibleForOutput"])
        self.assertEqual(stamped["jd"]["applyUrl"], "https://acme.example.com/careers/jobs/12345/apply")

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

    def test_source_aware_visibility_keeps_pool_stamp_without_recomputing_fresh_output(self) -> None:
        config = self._config(mode="replace")
        job = self._job(
            title="Fuel Cell Reliability Engineer",
            url="https://acme.example.com/careers/jobs/12345",
            apply_url="https://acme.example.com/careers/jobs/12345/apply",
            score=72,
        )
        stamped = materialize_output_eligibility(job, config)
        stamped.pop("jd", None)
        stamped.pop("outputUrl", None)

        fresh = decide_source_aware_final_recommendation_visibility(
            stamped,
            config,
            source=FRESH_FINAL_OUTPUT_SOURCE,
        )
        pool = decide_source_aware_final_recommendation_visibility(
            stamped,
            config,
            source=POOL_READBACK_SOURCE,
            pool_context=PoolRecommendationVisibilityContext(),
        )

        self.assertFalse(fresh.visible)
        self.assertEqual(fresh.reason, "final_output_check_failed")
        self.assertTrue(pool.visible)
        self.assertEqual(pool.reason, "visible_materialized_pool")

    def test_source_aware_visibility_hides_stale_pool_stamp(self) -> None:
        old_config = self._config(mode="replace")
        old_config["analysis"]["recommendScoreThreshold"] = 20
        current_config = self._config(mode="replace")
        current_config["analysis"]["recommendScoreThreshold"] = 80
        stamped = materialize_output_eligibility(
            self._job(
                title="Fuel Cell Reliability Engineer",
                url="https://acme.example.com/careers/jobs/12345",
                apply_url="https://acme.example.com/careers/jobs/12345/apply",
                score=72,
            ),
            old_config,
        )

        decision = decide_source_aware_final_recommendation_visibility(
            stamped,
            current_config,
            source=POOL_READBACK_SOURCE,
            pool_context=PoolRecommendationVisibilityContext(),
        )

        self.assertFalse(decision.visible)
        self.assertEqual(decision.reason, "stale_eligibility_stamp")


if __name__ == "__main__":
    unittest.main()

