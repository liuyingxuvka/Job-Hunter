from __future__ import annotations

import unittest

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - direct discovery fallback
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore

from jobflow_desktop_app.db.repositories.search_runtime import (
    CandidateCompanyRepository,
    JobReviewStateRepository,
    SearchRunRepository,
)
from jobflow_desktop_app.app.pages.search_results_runtime_state import (
    cancel_running_searches_for_candidate,
)
from jobflow_desktop_app.search.state.runtime_db_mirror import SearchRuntimeMirror
from jobflow_desktop_app.search.state.runtime_recovery import (
    INTERRUPTED_SEARCH_EVENT,
    INTERRUPTED_SEARCH_MESSAGE,
    recover_interrupted_search_runs,
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
                        "careersDiscoveryCache": {
                            "website": "https://acme.example",
                            "jobsPageUrl": "https://acme.example/careers",
                            "pageType": "jobs_listing",
                            "careersUrl": "https://acme.example/careers",
                            "sampleJobUrls": ["https://acme.example/jobs/1"],
                        },
                        "jobPageCoverage": {
                            "companySearchCache": {
                                "cache-key": {
                                    "query": "site:acme.example Acme careers jobs",
                                    "jobs": [{"title": "Role", "url": "https://acme.example/jobs/1"}],
                                }
                            }
                        },
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
            payload = repository.load_candidate_pool(candidate_id=candidate_id)[0]
            self.assertEqual(
                payload["careersDiscoveryCache"]["jobsPageUrl"],
                "https://acme.example/careers",
            )
            self.assertIn("cache-key", payload["jobPageCoverage"]["companySearchCache"])

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

    def test_candidate_company_repository_preserves_pool_stamps_on_replace(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            repository = CandidateCompanyRepository(context.database)
            repository.replace_candidate_pool(
                candidate_id=candidate_id,
                companies=[
                    {
                        "name": "Acme Hydrogen",
                        "website": "https://acme.example",
                        "companyKey": "acme",
                    }
                ],
            )
            with context.database.session() as connection:
                connection.execute(
                    """
                    UPDATE candidate_companies
                    SET fit_status = 'pass',
                        careers_url_status = 'pass',
                        job_fetch_status = 'pass',
                        search_status = 'cooldown',
                        user_status = 'focus'
                    WHERE candidate_id = ? AND company_key = ?
                    """,
                    (candidate_id, "acme"),
                )

            repository.replace_candidate_pool(
                candidate_id=candidate_id,
                companies=[
                    {
                        "name": "Acme Hydrogen",
                        "website": "https://acme.example",
                        "companyKey": "acme",
                        "careersUrl": "https://acme.example/careers",
                    }
                ],
            )

            with context.database.session() as connection:
                row = connection.execute(
                    """
                    SELECT fit_status, careers_url_status, job_fetch_status, search_status, user_status
                    FROM candidate_companies
                    WHERE candidate_id = ? AND company_key = ?
                    """,
                    (candidate_id, "acme"),
                ).fetchone()

            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(str(row["fit_status"]), "pass")
            self.assertEqual(str(row["careers_url_status"]), "pass")
            self.assertEqual(str(row["job_fetch_status"]), "pass")
            self.assertEqual(str(row["search_status"]), "cooldown")
            self.assertEqual(str(row["user_status"]), "focus")

    def test_candidate_company_repository_keeps_omitted_companies_as_durable_pool_rows(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            repository = CandidateCompanyRepository(context.database)
            repository.replace_candidate_pool(
                candidate_id=candidate_id,
                companies=[
                    {
                        "name": "Acme Hydrogen",
                        "website": "https://acme.example",
                        "companyKey": "acme",
                    },
                    {
                        "name": "Beta Systems",
                        "website": "https://beta.example",
                        "companyKey": "beta",
                    },
                ],
            )

            repository.replace_candidate_pool(
                candidate_id=candidate_id,
                companies=[
                    {
                        "name": "Acme Hydrogen GmbH",
                        "website": "https://acme.example",
                        "companyKey": "acme",
                    }
                ],
            )

            self.assertEqual(repository.count_candidate_pool(candidate_id=candidate_id), 2)
            self.assertEqual(
                sorted(item.get("name") for item in repository.load_candidate_pool(candidate_id=candidate_id)),
                ["Acme Hydrogen GmbH", "Beta Systems"],
            )

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

    def test_recover_interrupted_search_runs_marks_running_runs_cancelled_and_refreshes_counts(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)
            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="running",
                current_stage="company_sources",
                started_at="2026-04-21T10:00:00+00:00",
            )
            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[
                    {
                        "title": "Localization Program Manager",
                        "company": "Acme",
                        "url": "https://acme.example/jobs/loc-pm",
                        "canonicalUrl": "https://acme.example/jobs/loc-pm",
                        "analysis": {},
                    }
                ],
            )

            recovered = recover_interrupted_search_runs(mirror)
            latest = mirror.latest_run(candidate_id)

            self.assertEqual(recovered, [run_id])
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.status, "cancelled")
            self.assertEqual(latest.current_stage, "done")
            self.assertEqual(latest.last_message, INTERRUPTED_SEARCH_MESSAGE)
            self.assertEqual(latest.last_event, INTERRUPTED_SEARCH_EVENT)
            self.assertEqual(latest.jobs_found_count, 1)

    def test_cancel_running_searches_marks_owner_running_run_cancelled(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)
            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="running",
                current_stage="direct_job_discovery",
                started_at="2026-04-26T10:00:00+00:00",
            )

            class _Page:
                pass

            page = _Page()
            page.context = context

            recovered = cancel_running_searches_for_candidate(
                page,
                candidate_id,
                message="Search cancelled by the user.",
                last_event="User clicked Stop Search.",
            )
            latest = SearchRunRepository(context.database).get(run_id)

            self.assertEqual(recovered, [run_id])
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.status, "cancelled")
            self.assertEqual(latest.current_stage, "done")
            self.assertIn("cancelled by the user", latest.last_message)

    def test_terminal_search_run_status_is_not_overwritten_by_late_worker_progress(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            runs = SearchRunRepository(context.database)
            run_id = runs.create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/candidate_test",
                status="running",
                current_stage="direct_job_discovery",
            )

            runs.update_progress(
                run_id,
                status="cancelled",
                current_stage="done",
                last_message="Search cancelled by the user.",
                last_event="User clicked Stop Search.",
            )
            runs.update_progress(
                run_id,
                status="running",
                current_stage="company_sources",
                last_message="Late worker progress.",
            )
            runs.update_progress(
                run_id,
                status="success",
                current_stage="done",
                last_message="Late worker success.",
            )

            latest = runs.get(run_id)

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.status, "cancelled")
            self.assertEqual(latest.current_stage, "done")
            self.assertEqual(latest.last_message, "Search cancelled by the user.")

    def test_job_review_state_repository_resolves_job_id_from_candidate_job_pool(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            mirror = SearchRuntimeMirror(context.database)
            review_states = JobReviewStateRepository(context.database)

            run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / "candidate_test",
                status="success",
                current_stage="done",
                started_at="2026-04-27T10:00:00+00:00",
            )
            mirror.replace_bucket_jobs(
                search_run_id=run_id,
                candidate_id=candidate_id,
                job_bucket="all",
                jobs=[
                    {
                        "title": "Fuel Cell Validation Engineer",
                        "company": "Acme",
                        "url": "https://newer.example/job",
                        "canonicalUrl": "https://newer.example/job",
                        "analysis": {},
                    }
                ],
            )

            review_states.replace_candidate_review_state(
                candidate_id=candidate_id,
                status_by_job_key={"https://newer.example/job": "saved"},
                hidden_job_keys=set(),
            )

            saved = next(
                (
                    record
                    for record in review_states.list_for_candidate(candidate_id)
                    if record.job_key == "https://newer.example/job"
                ),
                None,
            )

            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.canonical_url, "https://newer.example/job")


if __name__ == "__main__":
    unittest.main()
