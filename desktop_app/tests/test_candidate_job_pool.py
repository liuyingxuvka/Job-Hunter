from __future__ import annotations

import json
import unittest

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - direct discovery fallback
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore

from jobflow_desktop_app.db.repositories.pools import CandidateJobPoolRepository
from jobflow_desktop_app.db.bootstrap import initialize_database
from jobflow_desktop_app.db.repositories.search_runtime import (
    CandidateCompanyRepository,
    JobRepository,
    JobReviewStateRepository,
    SearchRunRepository,
)
from jobflow_desktop_app.search.output.final_output import materialize_output_eligibility
from jobflow_desktop_app.search.state.runtime_db_mirror import SearchRuntimeMirror


def _ensure_legacy_search_run_jobs(context) -> None:
    with context.database.session() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS search_run_jobs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              search_run_id INTEGER NOT NULL,
              candidate_id INTEGER NOT NULL,
              job_id INTEGER,
              job_key TEXT NOT NULL,
              job_bucket TEXT NOT NULL DEFAULT 'jobs',
              canonical_url TEXT DEFAULT '',
              source_url TEXT DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              company_name TEXT DEFAULT '',
              location_text TEXT DEFAULT '',
              date_found TEXT DEFAULT '',
              match_score INTEGER,
              analysis_completed INTEGER NOT NULL DEFAULT 0,
              recommended INTEGER NOT NULL DEFAULT 0,
              pending_resume INTEGER NOT NULL DEFAULT 0,
              job_json TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def _replace_legacy_bucket(
    context,
    *,
    search_run_id: int,
    candidate_id: int,
    job_bucket: str,
    rows: list[dict],
) -> None:
    _ensure_legacy_search_run_jobs(context)
    with context.database.session() as connection:
        connection.execute(
            "DELETE FROM search_run_jobs WHERE search_run_id = ? AND job_bucket = ?",
            (int(search_run_id), str(job_bucket)),
        )
        connection.executemany(
            """
            INSERT INTO search_run_jobs (
              search_run_id,
              candidate_id,
              job_id,
              job_key,
              job_bucket,
              canonical_url,
              source_url,
              title,
              company_name,
              location_text,
              date_found,
              match_score,
              analysis_completed,
              recommended,
              pending_resume,
              job_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(search_run_id),
                    int(candidate_id),
                    row.get("job_id"),
                    str(row.get("job_key") or ""),
                    str(job_bucket),
                    str(row.get("canonical_url") or ""),
                    str(row.get("source_url") or ""),
                    str(row.get("title") or ""),
                    str(row.get("company_name") or ""),
                    str(row.get("location_text") or ""),
                    str(row.get("date_found") or ""),
                    row.get("match_score"),
                    1 if row.get("analysis_completed") else 0,
                    1 if row.get("recommended") else 0,
                    1 if row.get("pending_resume") else 0,
                    str(row.get("job_json") or ""),
                )
                for row in rows
            ],
        )


class CandidateJobPoolRepositoryTests(unittest.TestCase):
    def test_runtime_bucket_write_dual_writes_candidate_job_pool(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)
            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="running",
                current_stage="direct",
                started_at="2026-04-27T10:00:00+00:00",
            )
            job = materialize_output_eligibility(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "url": "https://acme.example/jobs/1",
                    "canonicalUrl": "https://acme.example/jobs/1",
                    "jd": {
                        "applyUrl": "https://acme.example/jobs/1",
                        "finalUrl": "https://acme.example/jobs/1",
                    },
                    "analysis": {
                        "overallScore": 82,
                        "recommend": True,
                    },
                },
                {},
            )

            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[job],
            )

            records = CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)
            payloads = CandidateJobPoolRepository(context.database).load_recommended_payloads_for_candidate(
                candidate_id
            )

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].recommendation_status, "pass")
            self.assertEqual(records[0].output_status, "pass")
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["title"], "Hydrogen Systems Engineer")

    def test_candidate_job_pool_exposes_resume_pending_queue(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)
            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="running",
                current_stage="direct",
                started_at="2026-04-27T10:00:00+00:00",
            )
            pending_job = {
                "title": "Fuel Cell Validation Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/pending",
                "canonicalUrl": "https://acme.example/jobs/pending",
                "dateFound": "2026-04-27T10:00:00Z",
                "analysis": {},
            }
            completed_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/scored",
                "canonicalUrl": "https://acme.example/jobs/scored",
                "dateFound": "2026-04-27T10:01:00Z",
                "analysis": {
                    "overallScore": 82,
                    "recommend": True,
                },
            }

            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[pending_job, completed_job],
            )
            pending_payloads = mirror.load_candidate_pending_job_pool_payloads(
                candidate_id=candidate_id
            )
            summary = mirror.summarize_candidate_job_pool(candidate_id=candidate_id)

            self.assertEqual([item["title"] for item in pending_payloads], ["Fuel Cell Validation Engineer"])
            self.assertEqual(summary.pending_jobs, 1)

    def test_shallow_found_write_does_not_erase_existing_analysis_stamp(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)
            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="running",
                current_stage="direct",
                started_at="2026-04-27T10:00:00+00:00",
            )
            scored_job = materialize_output_eligibility(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "url": "https://acme.example/jobs/1",
                    "canonicalUrl": "https://acme.example/jobs/1",
                    "jd": {
                        "applyUrl": "https://acme.example/jobs/1",
                        "finalUrl": "https://acme.example/jobs/1",
                    },
                    "analysis": {
                        "overallScore": 82,
                        "recommend": True,
                    },
                },
                {},
            )
            shallow_found_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/1",
                "canonicalUrl": "https://acme.example/jobs/1",
            }

            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[scored_job],
            )
            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="found",
                jobs=[shallow_found_job],
            )
            payloads = mirror.load_candidate_job_pool_payloads(candidate_id=candidate_id)
            summary = mirror.summarize_candidate_job_pool(candidate_id=candidate_id)

            self.assertEqual(payloads[0]["analysis"]["overallScore"], 82)
            self.assertEqual(payloads[0]["jd"]["applyUrl"], "https://acme.example/jobs/1")
            self.assertEqual(summary.scored_jobs, 1)
            self.assertEqual(summary.recommended_jobs, 1)

    def test_backfill_collapses_legacy_buckets_into_one_stamped_job_row(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            create_profile(context, candidate_id, name="Hydrogen Systems", is_active=True)
            companies = CandidateCompanyRepository(context.database)
            companies.replace_candidate_pool(
                candidate_id=candidate_id,
                companies=[{"name": "Acme Hydrogen", "companyKey": "acme"}],
            )
            job_id = JobRepository(context.database).upsert_job(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "canonicalUrl": "https://acme.example/jobs/1",
                }
            )
            self.assertIsNotNone(job_id)
            assert job_id is not None
            run_id = SearchRunRepository(context.database).create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_test",
            )
            scored_job = materialize_output_eligibility(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "url": "https://acme.example/jobs/1",
                    "canonicalUrl": "https://acme.example/jobs/1",
                    "jd": {
                        "applyUrl": "https://acme.example/jobs/1",
                        "finalUrl": "https://acme.example/jobs/1",
                    },
                    "analysis": {
                        "overallScore": 82,
                        "recommend": True,
                        "recommendReasonCn": "Strong fit.",
                    },
                },
                {},
            )
            found_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/1",
                "canonicalUrl": "https://acme.example/jobs/1",
            }
            for bucket, payload, recommended in (
                ("found", found_job, False),
                ("all", scored_job, True),
                ("recommended", scored_job, True),
            ):
                _replace_legacy_bucket(
                    context,
                    search_run_id=run_id,
                    candidate_id=candidate_id,
                    job_bucket=bucket,
                    rows=[
                        {
                            "job_id": job_id,
                            "job_key": "https://acme.example/jobs/1",
                            "canonical_url": "https://acme.example/jobs/1",
                            "source_url": "https://acme.example/jobs/1",
                            "title": "Hydrogen Systems Engineer",
                            "company_name": "Acme Hydrogen",
                            "location_text": "Berlin",
                            "match_score": 82 if recommended else None,
                            "analysis_completed": recommended,
                            "recommended": recommended,
                            "job_json": json.dumps(payload, ensure_ascii=False),
                        }
                    ],
                )

            result = CandidateJobPoolRepository(context.database).backfill_candidate_from_legacy(
                candidate_id
            )
            records = CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)
            summary = CandidateJobPoolRepository(context.database).summarize_candidate(candidate_id)

            self.assertEqual(result.source_rows, 3)
            self.assertEqual(result.upserted_jobs, 1)
            self.assertEqual(result.recommended_jobs, 1)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record.job_id, job_id)
            self.assertEqual(record.recommendation_status, "pass")
            self.assertEqual(record.output_status, "pass")
            self.assertEqual(record.scoring_status, "scored")
            self.assertEqual(record.match_score, 82)
            self.assertEqual(summary.total_jobs, 1)
            self.assertEqual(summary.recommended_jobs, 1)

    def test_database_initialization_migrates_and_drops_legacy_search_run_jobs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            job_id = JobRepository(context.database).upsert_job(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "canonicalUrl": "https://acme.example/jobs/1",
                }
            )
            self.assertIsNotNone(job_id)
            assert job_id is not None
            run_id = SearchRunRepository(context.database).create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_test",
            )
            job = materialize_output_eligibility(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "url": "https://acme.example/jobs/1",
                    "canonicalUrl": "https://acme.example/jobs/1",
                    "jd": {
                        "applyUrl": "https://acme.example/jobs/1",
                        "finalUrl": "https://acme.example/jobs/1",
                    },
                    "analysis": {
                        "overallScore": 82,
                        "recommend": True,
                    },
                },
                {},
            )
            _replace_legacy_bucket(
                context,
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                rows=[
                    {
                        "job_id": job_id,
                        "job_key": "https://acme.example/jobs/1",
                        "canonical_url": "https://acme.example/jobs/1",
                        "source_url": "https://acme.example/jobs/1",
                        "title": "Hydrogen Systems Engineer",
                        "company_name": "Acme Hydrogen",
                        "match_score": 82,
                        "analysis_completed": True,
                        "recommended": True,
                        "job_json": json.dumps(job, ensure_ascii=False),
                    }
                ],
            )

            initialize_database(context.database, context.paths.schema_path)

            records = CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)
            with context.database.session() as connection:
                legacy_table = connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'search_run_jobs'
                    """
                ).fetchone()

            self.assertIsNone(legacy_table)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].output_status, "pass")

    def test_backfill_preserves_review_hidden_state_on_job_pool_row(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            create_profile(context, candidate_id, name="Hydrogen Systems", is_active=True)
            job_id = JobRepository(context.database).upsert_job(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "canonicalUrl": "https://acme.example/jobs/1",
                }
            )
            self.assertIsNotNone(job_id)
            assert job_id is not None
            run_id = SearchRunRepository(context.database).create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_test",
            )
            _replace_legacy_bucket(
                context,
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                rows=[
                    {
                        "job_id": job_id,
                        "job_key": "https://acme.example/jobs/1",
                        "canonical_url": "https://acme.example/jobs/1",
                        "source_url": "https://acme.example/jobs/1",
                        "title": "Hydrogen Systems Engineer",
                        "company_name": "Acme Hydrogen",
                        "location_text": "Berlin",
                        "match_score": 82,
                        "analysis_completed": True,
                        "recommended": True,
                        "job_json": json.dumps(
                            materialize_output_eligibility(
                                {
                                    "title": "Hydrogen Systems Engineer",
                                    "company": "Acme Hydrogen",
                                    "url": "https://acme.example/jobs/1",
                                    "canonicalUrl": "https://acme.example/jobs/1",
                                    "jd": {
                                        "applyUrl": "https://acme.example/jobs/1",
                                        "finalUrl": "https://acme.example/jobs/1",
                                    },
                                    "analysis": {
                                        "overallScore": 82,
                                        "recommend": True,
                                    },
                                },
                                {},
                            ),
                            ensure_ascii=False,
                        ),
                    }
                ],
            )
            JobReviewStateRepository(context.database).replace_candidate_review_state(
                candidate_id=candidate_id,
                status_by_job_key={"https://acme.example/jobs/1": "rejected"},
                hidden_job_keys={"https://acme.example/jobs/1"},
            )

            CandidateJobPoolRepository(context.database).backfill_candidate_from_legacy(candidate_id)
            record = CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)[0]
            summary = CandidateJobPoolRepository(context.database).summarize_candidate(candidate_id)

            self.assertEqual(record.review_status_code, "rejected")
            self.assertTrue(record.hidden)
            self.assertEqual(record.trash_status, "trashed")
            self.assertEqual(summary.recommended_jobs, 0)
            self.assertEqual(summary.trashed_jobs, 1)

            JobReviewStateRepository(context.database).replace_candidate_review_state(
                candidate_id=candidate_id,
                status_by_job_key={},
                hidden_job_keys=set(),
            )
            restored = CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)[0]
            restored_summary = CandidateJobPoolRepository(context.database).summarize_candidate(
                candidate_id
            )

            self.assertEqual(restored.review_status_code, "")
            self.assertFalse(restored.hidden)
            self.assertEqual(restored.trash_status, "active")
            self.assertEqual(restored_summary.recommended_jobs, 1)

    def test_backfill_separates_analysis_recommendation_from_output_stamp(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            job_id = JobRepository(context.database).upsert_job(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "canonicalUrl": "https://acme.example/jobs/1",
                }
            )
            self.assertIsNotNone(job_id)
            assert job_id is not None
            run_id = SearchRunRepository(context.database).create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_test",
            )
            _replace_legacy_bucket(
                context,
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                rows=[
                    {
                        "job_id": job_id,
                        "job_key": "https://acme.example/jobs/1",
                        "canonical_url": "https://acme.example/jobs/1",
                        "source_url": "https://acme.example/jobs/1",
                        "title": "Hydrogen Systems Engineer",
                        "company_name": "Acme Hydrogen",
                        "location_text": "Berlin",
                        "match_score": 82,
                        "analysis_completed": True,
                        "recommended": True,
                        "job_json": json.dumps(
                            {
                                "title": "Hydrogen Systems Engineer",
                                "company": "Acme Hydrogen",
                                "url": "https://acme.example/jobs/1",
                                "canonicalUrl": "https://acme.example/jobs/1",
                                "analysis": {
                                    "overallScore": 82,
                                    "recommend": True,
                                },
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            )

            CandidateJobPoolRepository(context.database).backfill_candidate_from_legacy(candidate_id)
            record = CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)[0]
            summary = CandidateJobPoolRepository(context.database).summarize_candidate(candidate_id)

            self.assertEqual(record.recommendation_status, "pass")
            self.assertEqual(record.output_status, "reject")
            self.assertEqual(summary.recommended_jobs, 0)

    def test_current_rescore_reject_preserves_previously_visible_recommendation(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            profile_id = create_profile(context, candidate_id, name="Fuel Cell Systems Engineer")
            mirror = SearchRuntimeMirror(context.database)
            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="running",
                current_stage="direct",
                started_at="2026-04-27T10:00:00+00:00",
            )
            recommended_job = materialize_output_eligibility(
                {
                    "title": "Hydrogen Systems Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "url": "https://acme.example/jobs/visible",
                    "canonicalUrl": "https://acme.example/jobs/visible",
                    "jd": {
                        "applyUrl": "https://acme.example/jobs/visible",
                        "finalUrl": "https://acme.example/jobs/visible",
                    },
                    "analysis": {
                        "overallScore": 86,
                        "recommend": True,
                        "boundTargetRole": {
                            "profileId": profile_id,
                            "roleId": f"profile:{profile_id}",
                            "displayName": "Fuel Cell Systems Engineer",
                        },
                    },
                },
                {},
            )
            rejected_current_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/visible",
                "canonicalUrl": "https://acme.example/jobs/visible",
                "analysis": {
                    "overallScore": 42,
                    "recommend": False,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                        "displayName": "Fuel Cell Systems Engineer",
                    },
                },
            }

            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[recommended_job],
            )
            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[rejected_current_job],
            )

            record = CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)[0]
            payload = CandidateJobPoolRepository(context.database).load_recommended_payloads_for_candidate(
                candidate_id
            )[0]

            self.assertEqual(record.recommendation_status, "pass")
            self.assertEqual(record.output_status, "pass")
            self.assertEqual(
                payload["analysis"]["recommendationDisplay"]["currentFitStatus"],
                "not_current_fit",
            )
            self.assertEqual(
                payload["analysis"]["recommendationDisplay"]["reason"],
                "current_rescore_reject",
            )

    def test_output_set_refresh_preserves_previously_visible_recommendation(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)
            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="running",
                current_stage="direct",
                started_at="2026-04-27T10:00:00+00:00",
            )
            jobs = [
                materialize_output_eligibility(
                    {
                        "title": "Hydrogen Systems Engineer",
                        "company": "Acme Hydrogen",
                        "location": "Berlin",
                        "url": "https://acme.example/jobs/kept",
                        "canonicalUrl": "https://acme.example/jobs/kept",
                        "jd": {
                            "applyUrl": "https://acme.example/jobs/kept",
                            "finalUrl": "https://acme.example/jobs/kept",
                        },
                        "analysis": {
                            "overallScore": 86,
                            "recommend": True,
                        },
                    },
                    {},
                ),
                materialize_output_eligibility(
                    {
                        "title": "Fuel Cell Modeling Engineer",
                        "company": "Beta Hydrogen",
                        "location": "Berlin",
                        "url": "https://beta.example/jobs/history",
                        "canonicalUrl": "https://beta.example/jobs/history",
                        "jd": {
                            "applyUrl": "https://beta.example/jobs/history",
                            "finalUrl": "https://beta.example/jobs/history",
                        },
                        "analysis": {
                            "overallScore": 82,
                            "recommend": True,
                        },
                    },
                    {},
                ),
            ]

            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=jobs,
            )
            CandidateJobPoolRepository(context.database).mark_recommended_output_set(
                candidate_id=candidate_id,
                job_keys={"https://acme.example/jobs/kept"},
            )

            records = {
                record.job_key: record
                for record in CandidateJobPoolRepository(context.database).list_for_candidate(candidate_id)
            }
            payloads = {
                payload["canonicalUrl"]: payload
                for payload in CandidateJobPoolRepository(
                    context.database
                ).load_recommended_payloads_for_candidate(candidate_id)
            }

            self.assertEqual(records["https://beta.example/jobs/history"].output_status, "pass")
            self.assertIn("https://beta.example/jobs/history", payloads)
            self.assertEqual(
                payloads["https://beta.example/jobs/history"]["analysis"]["recommendationDisplay"][
                    "currentFitStatus"
                ],
                "historical_only",
            )
            self.assertEqual(
                payloads["https://beta.example/jobs/history"]["analysis"]["recommendationDisplay"][
                    "reason"
                ],
                "current_output_refresh_excluded",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
