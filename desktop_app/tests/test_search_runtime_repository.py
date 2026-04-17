from __future__ import annotations

import unittest

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - direct discovery fallback
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore

from jobflow_desktop_app.db.repositories.search_runtime import (
    CandidateCompanyRepository,
    JobReviewStateRepository,
    SearchRunJobRepository,
    SearchRunRepository,
)


class SearchRuntimeRepositoryTests(unittest.TestCase):
    def test_candidate_company_repository_round_trips_candidate_pool_only(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            repository = CandidateCompanyRepository(context.database)

            repository.replace_candidate_pool(
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

            self.assertEqual(repository.count_candidate_pool(candidate_id=candidate_id), 2)
            self.assertEqual(
                [
                    item.get("name")
                    for item in repository.load_candidate_pool(candidate_id=candidate_id)
                ],
                ["Acme Hydrogen", "Beta Systems"],
            )

            with context.database.session() as connection:
                rows = connection.execute(
                    """
                    SELECT company_name
                    FROM candidate_companies
                    WHERE candidate_id = ?
                    ORDER BY company_name
                    """,
                    (candidate_id,),
                ).fetchall()

            self.assertEqual(
                [str(row["company_name"]) for row in rows],
                ["Acme Hydrogen", "Beta Systems"],
            )

    def test_search_run_job_repository_summarizes_bucket_counts_from_runtime_rows(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            runs = SearchRunRepository(context.database)
            run_jobs = SearchRunJobRepository(context.database)
            search_run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/test",
            )

            run_jobs.replace_bucket(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                rows=[
                    {
                        "job_key": "job-1",
                        "title": "Hydrogen Systems Engineer",
                        "analysis_completed": True,
                        "recommended": True,
                        "job_json": '{"title":"Hydrogen Systems Engineer"}',
                    },
                    {
                        "job_key": "job-2",
                        "title": "Battery Engineer",
                        "analysis_completed": False,
                        "recommended": False,
                        "job_json": '{"title":"Battery Engineer"}',
                    },
                ],
            )
            run_jobs.replace_bucket(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="found",
                rows=[
                    {
                        "job_key": "job-1",
                        "title": "Hydrogen Systems Engineer",
                        "analysis_completed": True,
                        "recommended": True,
                        "job_json": '{"title":"Hydrogen Systems Engineer"}',
                    }
                ],
            )
            run_jobs.replace_bucket(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                rows=[
                    {
                        "job_key": "job-1",
                        "title": "Hydrogen Systems Engineer",
                        "analysis_completed": True,
                        "recommended": True,
                        "job_json": '{"title":"Hydrogen Systems Engineer"}',
                    }
                ],
            )

            counts = run_jobs.summarize_bucket_counts(search_run_id=search_run_id)

            self.assertEqual(counts.jobs_found_count, 1)
            self.assertEqual(counts.jobs_scored_count, 1)
            self.assertEqual(counts.jobs_recommended_count, 1)

    def test_search_run_repository_recent_for_candidate_returns_newest_first(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            runs = SearchRunRepository(context.database)
            first_run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/first",
            )
            second_run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/second",
            )
            runs.update_progress(
                first_run_id,
                current_stage="done",
                status="success",
                last_message="first",
            )
            runs.update_progress(
                second_run_id,
                current_stage="running",
                status="running",
                last_message="second",
            )

            snapshots = runs.recent_for_candidate(candidate_id, limit=2)

            self.assertEqual([item.search_run_id for item in snapshots], [second_run_id, first_run_id])

    def test_search_run_repository_latest_for_candidate_uses_run_creation_order(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            runs = SearchRunRepository(context.database)
            first_run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/first",
            )
            second_run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/second",
            )

            runs.update_counts(
                first_run_id,
                jobs_found_count=10,
                jobs_scored_count=8,
                jobs_recommended_count=4,
            )

            latest = runs.latest_for_candidate(candidate_id)

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.search_run_id, second_run_id)

    def test_job_review_state_repository_resolves_job_id_by_newest_run_id(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            runs = SearchRunRepository(context.database)
            run_jobs = SearchRunJobRepository(context.database)
            review_states = JobReviewStateRepository(context.database)

            first_run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_old",
            )
            second_run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_new",
            )

            run_jobs.replace_bucket(
                search_run_id=first_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                rows=[
                    {
                        "job_key": "job-key",
                        "title": "Older Run Job",
                        "job_json": '{"title":"Older Run Job","canonicalUrl":"https://older.example/job"}',
                    }
                ],
            )
            run_jobs.replace_bucket(
                search_run_id=second_run_id,
                candidate_id=candidate_id,
                job_bucket="recommended",
                rows=[
                    {
                        "job_key": "job-key",
                        "title": "Newer Run Job",
                        "job_json": '{"title":"Newer Run Job","canonicalUrl":"https://newer.example/job"}',
                    }
                ],
            )

            runs.update_counts(
                first_run_id,
                jobs_found_count=5,
                jobs_scored_count=5,
                jobs_recommended_count=5,
            )

            review_states.replace_candidate_review_state(
                candidate_id=candidate_id,
                status_by_job_key={"job-key": "saved"},
                hidden_job_keys=set(),
            )

            saved = next(
                (
                    record
                    for record in review_states.list_for_candidate(candidate_id)
                    if record.job_key == "job-key"
                ),
                None,
            )

            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.canonical_url, "https://newer.example/job")


if __name__ == "__main__":
    unittest.main()
