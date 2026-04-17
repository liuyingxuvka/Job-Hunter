from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jobflow_desktop_app.db.bootstrap import initialize_database
from jobflow_desktop_app.db.connection import Database
from jobflow_desktop_app.search.state.runtime_db_mirror import SearchRuntimeMirror, build_search_runtime_mirror

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - direct discovery fallback
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore


class SearchRuntimeMirrorTests(unittest.TestCase):
    def test_progress_and_config_updates_are_mirrored_into_search_runs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            mirror = SearchRuntimeMirror(context.database)

            search_run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="preparing",
                started_at="2026-04-16T10:00:00+00:00",
            )
            mirror.update_progress(
                search_run_id,
                status="running",
                stage="company_sources",
                message="Collecting direct ATS jobs.",
                last_event="Processed 2 companies.",
                started_at="2026-04-16T10:00:00+00:00",
            )
            mirror.update_configs(
                search_run_id,
                runtime_config={"output": {"recommendedXlsxPath": "./jobs_recommended.xlsx"}},
                resume_config={"analysis": {"postVerifyEnabled": True}},
            )

            with context.database.session() as connection:
                row = connection.execute(
                    """
                    SELECT
                      candidate_id,
                      run_dir,
                      status,
                      current_stage,
                      last_message,
                      last_event,
                      config_json,
                      resume_config_json
                    FROM search_runs
                    WHERE id = ?
                    """,
                    (search_run_id,),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(int(row["candidate_id"]), candidate_id)
            self.assertTrue(str(row["run_dir"]).endswith(f"candidate_{candidate_id}"))
            self.assertEqual(str(row["status"]), "running")
            self.assertEqual(str(row["current_stage"]), "company_sources")
            self.assertEqual(str(row["last_message"]), "Collecting direct ATS jobs.")
            self.assertEqual(str(row["last_event"]), "Processed 2 companies.")
            self.assertIn('"recommendedXlsxPath"', str(row["config_json"]))
            self.assertIn('"postverifyenabled": true', str(row["resume_config_json"]).lower())

    def test_update_progress_marks_error_message_when_status_is_error(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            mirror = SearchRuntimeMirror(context.database)

            search_run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="preparing",
                started_at="2026-04-16T10:00:00+00:00",
            )
            mirror.update_progress(
                search_run_id,
                status="error",
                stage="company_sources",
                message="Company-stage failed.",
                last_event="Traceback placeholder",
            )

            with context.database.session() as connection:
                row = connection.execute(
                    "SELECT status, error_message FROM search_runs WHERE id = ?",
                    (search_run_id,),
                ).fetchone()

            self.assertEqual(str(row["status"]), "error")
            self.assertEqual(str(row["error_message"]), "Company-stage failed.")

    def test_replace_bucket_jobs_and_candidate_company_pool_mirror_runtime_state(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            profile_id = create_profile(context, candidate_id)
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            mirror = SearchRuntimeMirror(context.database)

            search_run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="resume",
                started_at="2026-04-16T10:00:00+00:00",
            )
            mirror.store_semantic_profile(
                candidate_id=candidate_id,
                profile_payload={
                    "source_signature": "sig-1",
                    "summary": "Hydrogen systems and aging diagnostics",
                    "background_keywords": ["hydrogen", "aging"],
                },
            )
            mirror.replace_candidate_company_pool(
                candidate_id=candidate_id,
                companies=[
                    {
                        "name": "Acme Hydrogen",
                        "website": "https://acme.example",
                        "careersUrl": "https://acme.example/careers",
                    }
                ],
            )
            analyzed_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/1",
                "canonicalUrl": "https://acme.example/jobs/1",
                "dateFound": "2026-04-16T10:00:00Z",
                "analysis": {
                    "overallScore": 78,
                    "matchScore": 78,
                    "fitLevelCn": "高推荐",
                    "recommend": True,
                    "fitTrack": "hydrogen_core",
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                    },
                },
            }
            pending_job = {
                "title": "Battery Reliability Engineer",
                "company": "Beta Power",
                "location": "Munich",
                "url": "https://beta.example/jobs/2",
                "canonicalUrl": "https://beta.example/jobs/2",
                "dateFound": "2026-04-16T11:00:00Z",
                "analysis": {},
            }

            mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="found",
                jobs=[analyzed_job],
            )
            mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[analyzed_job, pending_job],
            )
            mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                jobs=[analyzed_job],
            )
            mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="resume_pending",
                jobs=[pending_job],
            )

            with context.database.session() as connection:
                search_run = connection.execute(
                    """
                    SELECT jobs_found_count, jobs_scored_count, jobs_recommended_count
                    FROM search_runs
                    WHERE id = ?
                    """,
                    (search_run_id,),
                ).fetchone()
                company_rows = connection.execute(
                    """
                    SELECT company_name
                    FROM candidate_companies
                    WHERE candidate_id = ?
                    ORDER BY company_name
                    """,
                    (candidate_id,),
                ).fetchall()
                bucket_rows = connection.execute(
                    """
                    SELECT job_bucket, COUNT(*) AS total
                    FROM search_run_jobs
                    WHERE search_run_id = ?
                    GROUP BY job_bucket
                    ORDER BY job_bucket
                    """,
                    (search_run_id,),
                ).fetchall()
                jobs_total = int(
                    connection.execute("SELECT COUNT(*) AS total FROM jobs").fetchone()["total"]
                )
                analyses_total = int(
                    connection.execute("SELECT COUNT(*) AS total FROM job_analyses").fetchone()["total"]
                )
                semantic_row = connection.execute(
                    """
                    SELECT source_signature, summary
                    FROM candidate_semantic_profiles
                    WHERE candidate_id = ?
                    """,
                    (candidate_id,),
                ).fetchone()

            self.assertEqual(int(search_run["jobs_found_count"]), 1)
            self.assertEqual(int(search_run["jobs_scored_count"]), 1)
            self.assertEqual(int(search_run["jobs_recommended_count"]), 1)
            self.assertEqual(
                [str(row["company_name"]) for row in company_rows],
                ["Acme Hydrogen"],
            )
            self.assertEqual(
                [(str(row["job_bucket"]), int(row["total"])) for row in bucket_rows],
                [("all", 2), ("found", 1), ("recommended", 1), ("resume_pending", 1)],
            )
            self.assertEqual(jobs_total, 2)
            self.assertEqual(analyses_total, 1)
            self.assertEqual(str(semantic_row["source_signature"]), "sig-1")

    def test_candidate_company_pool_methods_round_trip_only_candidate_pool(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)

            mirror.replace_candidate_company_pool(
                candidate_id=candidate_id,
                companies=[
                    {
                        "name": "Acme Hydrogen",
                        "website": "https://acme.example",
                        "careersUrl": "https://acme.example/careers",
                    },
                    {
                        "name": "Beta Systems",
                        "website": "https://beta.example",
                    },
                ],
            )

            self.assertEqual(mirror.count_candidate_company_pool(candidate_id), 2)
            self.assertEqual(
                [item.get("name") for item in mirror.load_candidate_company_pool(candidate_id=candidate_id)],
                ["Acme Hydrogen", "Beta Systems"],
            )

    def test_commit_company_sources_round_refreshes_counts_once_after_batch_write(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            profile_id = create_profile(context, candidate_id)
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            mirror = SearchRuntimeMirror(context.database)

            search_run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=run_dir,
                status="running",
                current_stage="company_sources",
                started_at="2026-04-16T10:00:00+00:00",
            )
            analyzed_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://acme.example/jobs/1",
                "canonicalUrl": "https://acme.example/jobs/1",
                "dateFound": "2026-04-16T10:00:00Z",
                "analysis": {
                    "overallScore": 82,
                    "matchScore": 82,
                    "recommend": True,
                    "boundTargetRole": {
                        "profileId": profile_id,
                        "roleId": f"profile:{profile_id}",
                    },
                },
            }
            pending_job = {
                "title": "Battery Reliability Engineer",
                "company": "Beta Power",
                "location": "Munich",
                "url": "https://beta.example/jobs/2",
                "canonicalUrl": "https://beta.example/jobs/2",
                "dateFound": "2026-04-16T11:00:00Z",
                "analysis": {},
            }

            mirror.commit_company_sources_round(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                all_jobs=[analyzed_job, pending_job],
                found_jobs=[analyzed_job],
                candidate_companies=[
                    {"name": "Acme Hydrogen", "website": "https://acme.example"},
                ],
            )

            with context.database.session() as connection:
                search_run = connection.execute(
                    """
                    SELECT jobs_found_count, jobs_scored_count, jobs_recommended_count
                    FROM search_runs
                    WHERE id = ?
                    """,
                    (search_run_id,),
                ).fetchone()
                bucket_rows = connection.execute(
                    """
                    SELECT job_bucket, COUNT(*) AS total
                    FROM search_run_jobs
                    WHERE search_run_id = ?
                    GROUP BY job_bucket
                    ORDER BY job_bucket
                    """,
                    (search_run_id,),
                ).fetchall()
                company_rows = connection.execute(
                    """
                    SELECT company_name
                    FROM candidate_companies
                    WHERE candidate_id = ?
                    ORDER BY company_name
                    """,
                    (candidate_id,),
                ).fetchall()

            self.assertEqual(int(search_run["jobs_found_count"]), 1)
            self.assertEqual(int(search_run["jobs_scored_count"]), 1)
            self.assertEqual(int(search_run["jobs_recommended_count"]), 0)
            self.assertEqual(
                [(str(row["job_bucket"]), int(row["total"])) for row in bucket_rows],
                [("all", 2), ("found", 1)],
            )
            self.assertEqual(
                [str(row["company_name"]) for row in company_rows],
                ["Acme Hydrogen"],
            )

    def test_build_search_runtime_mirror_returns_none_without_db_and_mirror_with_db(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "src" / "jobflow_desktop_app" / "db" / "schema.sql"
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self.assertIsNone(build_search_runtime_mirror(project_root))

            db_path = project_root / "runtime" / "data" / "jobflow_desktop.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            initialize_database(Database(db_path), schema_path)

            mirror = build_search_runtime_mirror(project_root)
            self.assertIsNotNone(mirror)


if __name__ == "__main__":
    unittest.main()
