from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.ai.client import OpenAIResponsesClient  # noqa: E402
from jobflow_desktop_app.search.stages.executor import (  # noqa: E402
    COMPANY_DISCOVERY_REQUEST_TIMEOUT_SECONDS,
    PythonStageExecutor,
    PythonStageRunResult,
)
from jobflow_desktop_app.search.stages.executor_company_stages import (  # noqa: E402
    run_company_discovery_stage_db,
    run_company_selection_stage_db,
)


class StageExecutorCompanyTimeoutTests(unittest.TestCase):
    def test_company_discovery_stage_uses_direct_company_discovery_input_and_clears_state(self) -> None:
        class _Mirror:
            def load_candidate_company_pool(self, *, candidate_id: int):
                return []

            def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]):
                self.replaced_companies = companies

        captured: dict[str, object] = {}

        def _fake_auto_discover(*args, **kwargs):
            captured["config"] = kwargs["config"]
            return {
                "added": 0,
                "total": 0,
                "companies": [],
                "newCompanies": [],
                "changed": False,
            }

        mirror = _Mirror()
        with patch(
            "jobflow_desktop_app.search.stages.executor_company_stages.auto_discover_companies_in_pool",
            side_effect=_fake_auto_discover,
        ):
            result = run_company_discovery_stage_db(
                runtime_mirror=mirror,
                search_run_id=1,
                candidate_id=1,
                config={
                    "companyDiscovery": {
                        "model": "gpt-5-nano",
                        "companyDiscoveryInput": {
                            "summary": "Localization candidate",
                            "targetRoles": ["Localization Program Manager"],
                            "desiredWorkDirections": ["translation management"],
                            "avoidBusinessAreas": [],
                        },
                    }
                },
                client_instance=object(),
                progress_callback=None,
            )

        self.assertTrue(result.success)
        self.assertEqual(
            captured["config"]["companyDiscovery"]["companyDiscoveryInput"]["summary"],
            "Localization candidate",
        )

    def test_company_discovery_stage_scores_new_directly_discovered_companies(self) -> None:
        class _Mirror:
            def __init__(self) -> None:
                self.pool: list[dict] = []
                self.saved_state: dict | None = None

            def load_candidate_company_pool(self, *, candidate_id: int):
                return list(self.pool)

            def count_candidate_company_pool(self, candidate_id: int):
                return len(self.pool)

            def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]):
                self.pool = list(companies)

        auto_discover_calls = 0

        def _fake_auto_discover(*args, **kwargs):
            nonlocal auto_discover_calls
            auto_discover_calls += 1
            companies = [
                {
                    "name": "Microsoft",
                    "website": "https://microsoft.example",
                },
                {
                    "name": "Lokalise",
                    "website": "https://lokalise.example",
                },
            ]
            return {
                "added": 2,
                "total": 2,
                "companies": companies,
                "newCompanies": list(companies),
                "changed": True,
            }

        def _fake_score_companies(*args, **kwargs):
            companies = []
            for item in kwargs["companies"]:
                company = dict(item)
                if company["name"] == "Microsoft":
                    company["aiCompanyFitScore"] = 20
                elif company["name"] == "Lokalise":
                    company["aiCompanyFitScore"] = 82
                companies.append(company)
            return companies

        mirror = _Mirror()
        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.auto_discover_companies_in_pool",
                side_effect=_fake_auto_discover,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.score_companies_for_candidate",
                side_effect=_fake_score_companies,
            ),
        ):
            result = run_company_discovery_stage_db(
                runtime_mirror=mirror,
                search_run_id=1,
                candidate_id=1,
                config={
                    "companyDiscovery": {
                        "model": "gpt-5-nano",
                        "companyDiscoveryInput": {
                            "summary": "localization candidate",
                            "targetRoles": ["Localization Program Manager"],
                            "desiredWorkDirections": ["translation management"],
                            "avoidBusinessAreas": [],
                        },
                    }
                },
                client_instance=object(),
                progress_callback=None,
            )

        self.assertTrue(result.success)
        self.assertEqual(auto_discover_calls, 1)
        self.assertEqual(
            [item["name"] for item in mirror.pool],
            ["Microsoft", "Lokalise"],
        )
        self.assertEqual(mirror.pool[0]["aiCompanyFitScore"], 20)
        self.assertEqual(mirror.pool[1]["aiCompanyFitScore"], 82)

    def test_company_discovery_stage_treats_company_fit_failures_as_pending_new_work(self) -> None:
        class _Mirror:
            def __init__(self) -> None:
                self.pool: list[dict] = []
                self.saved_state: dict | None = None

            def load_candidate_company_pool(self, *, candidate_id: int):
                return list(self.pool)

            def count_candidate_company_pool(self, candidate_id: int):
                return len(self.pool)

            def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]):
                self.pool = list(companies)

        def _fake_auto_discover(*args, **kwargs):
            companies = [
                {
                    "name": "Lokalise",
                    "website": "https://lokalise.example",
                }
            ]
            return {
                "added": 1,
                "total": 1,
                "companies": companies,
                "newCompanies": list(companies),
                "changed": True,
            }

        def _fake_score_companies(*args, **kwargs):
            return [
                {
                    "name": "Lokalise",
                    "website": "https://lokalise.example",
                    "aiCompanyFitScore": None,
                    "rankingWorkState": {
                        "technicalFailureCount": 1,
                        "abandoned": False,
                        "suspendedRunId": 55,
                        "lastFailureReason": "company_fit_error",
                    },
                }
            ]

        mirror = _Mirror()
        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.auto_discover_companies_in_pool",
                side_effect=_fake_auto_discover,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.score_companies_for_candidate",
                side_effect=_fake_score_companies,
            ),
        ):
            result = run_company_discovery_stage_db(
                runtime_mirror=mirror,
                search_run_id=55,
                candidate_id=1,
                config={
                    "companyDiscovery": {
                        "model": "gpt-5-nano",
                        "companyDiscoveryInput": {
                            "summary": "localization candidate",
                            "targetRoles": ["Localization Program Manager"],
                            "desiredWorkDirections": ["translation management"],
                            "avoidBusinessAreas": [],
                        },
                    }
                },
                client_instance=object(),
                progress_callback=None,
            )

        self.assertTrue(result.success)
        self.assertFalse(result.payload["noQualifiedNewCompanies"])
        self.assertIn("pendingNew=1", result.message)

    def test_company_selection_stage_uses_ready_scored_companies_without_rerank(self) -> None:
        class _Mirror:
            def load_candidate_company_pool(self, *, candidate_id: int):
                self.candidate_id = candidate_id
                return [
                    {
                        "name": "Specialized Employer",
                        "website": "https://specialized.example",
                        "aiCompanyFitScore": 82,
                        "jobsPageUrl": "https://specialized.example/jobs",
                    }
                ]

            def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]):
                self.replaced_candidate_id = candidate_id
                self.replaced_companies = companies

        with patch(
            "jobflow_desktop_app.search.stages.executor_company_stages.score_companies_for_candidate",
            side_effect=AssertionError("rerank should not run when ready scored companies already exist"),
        ):
            result = run_company_selection_stage_db(
                runtime_mirror=_Mirror(),
                candidate_id=1,
                config={},
                max_companies=3,
                progress_callback=None,
                client_instance=object(),
            )

        self.assertTrue(result.success)
        self.assertEqual(
            [item["name"] for item in result.payload["selectedCompanies"]],
            ["Specialized Employer"],
        )

    def test_company_selection_stage_leaves_jobs_entry_materialization_to_sources_stage(self) -> None:
        class _Mirror:
            def load_candidate_company_pool(self, *, candidate_id: int):
                return [
                    {
                        "name": "Specialized Employer",
                        "website": "https://specialized.example",
                        "aiCompanyFitScore": 82,
                    }
                ]

            def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]):
                self.replaced_candidate_id = candidate_id
                self.replaced_companies = companies

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.score_companies_for_candidate",
                side_effect=AssertionError("rerank should not run when all companies are already scored"),
            ),
        ):
            mirror = _Mirror()
            result = run_company_selection_stage_db(
                runtime_mirror=mirror,
                candidate_id=1,
                config={},
                max_companies=3,
                progress_callback=None,
                client_instance=object(),
            )

        self.assertTrue(result.success)
        self.assertEqual(result.payload["selectedCompanies"][0]["name"], "Specialized Employer")
        self.assertNotIn("jobsPageUrl", result.payload["selectedCompanies"][0])
        self.assertFalse(hasattr(mirror, "replaced_companies"))

    def test_company_selection_stage_keeps_ready_and_missing_entries_without_preheating(self) -> None:
        class _Mirror:
            def load_candidate_company_pool(self, *, candidate_id: int):
                return [
                    {
                        "name": "Ready Employer",
                        "website": "https://ready.example",
                        "aiCompanyFitScore": 82,
                        "jobsPageUrl": "https://ready.example/jobs",
                        "jobsPageType": "jobs_listing",
                    },
                    {
                        "name": "Missing Entry Employer",
                        "website": "https://missing.example",
                        "aiCompanyFitScore": 80,
                    },
                ]

            def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]):
                self.replaced_candidate_id = candidate_id
                self.replaced_companies = companies

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.score_companies_for_candidate",
                side_effect=AssertionError("rerank should not run when all companies are already scored"),
            ),
        ):
            mirror = _Mirror()
            result = run_company_selection_stage_db(
                runtime_mirror=mirror,
                candidate_id=1,
                config={},
                max_companies=3,
                progress_callback=None,
                client_instance=object(),
            )

        self.assertTrue(result.success)
        self.assertEqual(
            [item["name"] for item in result.payload["selectedCompanies"]],
            ["Ready Employer", "Missing Entry Employer"],
        )
        self.assertEqual(
            result.payload["selectedCompanies"][0]["jobsPageUrl"],
            "https://ready.example/jobs",
        )
        self.assertNotIn("jobsPageUrl", result.payload["selectedCompanies"][1])
        self.assertFalse(hasattr(mirror, "replaced_companies"))

    def test_company_selection_stage_skips_unresolved_companies_until_retry(self) -> None:
        class _Mirror:
            def load_candidate_company_pool(self, *, candidate_id: int):
                self.candidate_id = candidate_id
                return [{"name": "Acme", "website": "https://acme.example"}]
            def replace_candidate_company_pool(self, *, candidate_id: int, companies: list[dict]):
                self.replaced_candidate_id = candidate_id
                self.replaced_companies = companies

        with patch(
            "jobflow_desktop_app.search.stages.executor_company_stages.score_companies_for_candidate",
            return_value=[
                {
                    "name": "Acme",
                    "website": "https://acme.example",
                }
            ],
        ):
            result = run_company_selection_stage_db(
                runtime_mirror=_Mirror(),
                candidate_id=1,
                config={},
                max_companies=3,
                progress_callback=None,
                client_instance=object(),
            )

        self.assertTrue(result.success)
        self.assertEqual(result.payload["selectedCompanies"], [])
        self.assertEqual(result.payload["availableCompanies"], 1)
        self.assertEqual(result.payload["unresolvedCompanyRankings"], 1)

    def test_company_discovery_stage_caps_openai_request_timeout(self) -> None:
        client = OpenAIResponsesClient(api_key="test-key", timeout_seconds=999)
        captured: dict[str, int] = {}

        def _fake_run(**kwargs):
            captured["timeout"] = int(kwargs["client_instance"].timeout_seconds)
            return PythonStageRunResult(
                success=True,
                exit_code=0,
                message="ok",
                stdout_tail="",
                stderr_tail="",
            )

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor._build_openai_client",
                return_value=client,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.run_company_discovery_stage_db",
                side_effect=_fake_run,
            ),
        ):
            result = PythonStageExecutor.run_company_discovery_stage_for_runtime(
                runtime_mirror=object(),
                search_run_id=1,
                candidate_id=1,
                config={"companyDiscovery": {"model": "gpt-5-nano", "queries": ["risk employers"]}},
                timeout_seconds=300,
            )

        self.assertTrue(result.success)
        self.assertEqual(captured["timeout"], COMPANY_DISCOVERY_REQUEST_TIMEOUT_SECONDS)

    def test_company_sources_stage_uses_session_timeout_for_client(self) -> None:
        client = OpenAIResponsesClient(api_key="test-key", timeout_seconds=999)
        captured: dict[str, int] = {}

        def _fake_run(**kwargs):
            captured["timeout"] = int(kwargs["client_instance"].timeout_seconds)
            return PythonStageRunResult(
                success=True,
                exit_code=0,
                message="ok",
                stdout_tail="",
                stderr_tail="",
            )

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor._build_openai_client",
                return_value=client,
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.run_company_sources_stage_db",
                side_effect=_fake_run,
            ),
        ):
            result = PythonStageExecutor.run_company_sources_stage_for_runtime(
                runtime_mirror=object(),
                search_run_id=1,
                candidate_id=1,
                config={},
                selected_companies=[{"name": "Acme"}],
                timeout_seconds=480,
            )

        self.assertTrue(result.success)
        self.assertEqual(captured["timeout"], 480)

    def test_company_sources_stage_returns_deferred_companies_in_payload(self) -> None:
        class _Mirror:
            def load_run_bucket_jobs(self, *, search_run_id: int, job_bucket: str):
                return []

            def load_candidate_company_pool(self, *, candidate_id: int):
                return [{"name": "Smartling", "website": "https://www.smartling.com"}]

            def commit_company_sources_round(self, **kwargs):
                self.committed = kwargs

        with (
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.collect_supported_company_source_jobs",
                return_value=SimpleNamespace(
                    jobs=[],
                    processed_companies=[],
                    remaining_companies=[
                        {"name": "Smartling", "website": "https://www.smartling.com"}
                    ],
                    jobs_found_count=0,
                    companies_handled_count=0,
                ),
            ),
            patch(
                "jobflow_desktop_app.search.stages.executor_company_stages.build_company_sources_stage_artifacts",
                side_effect=lambda **kwargs: SimpleNamespace(
                    candidate_companies=[{"name": "Smartling", "website": "https://www.smartling.com"}],
                    deferred_companies=list(kwargs["fetch_result"].remaining_companies),
                    all_jobs=[],
                    found_jobs=[],
                ),
            ),
        ):
            mirror = _Mirror()
            result = PythonStageExecutor.run_company_sources_stage_for_runtime(
                runtime_mirror=mirror,
                search_run_id=1,
                candidate_id=1,
                config={},
                selected_companies=[{"name": "Smartling", "website": "https://www.smartling.com"}],
                timeout_seconds=300,
                client=object(),
            )

        self.assertTrue(result.success)
        self.assertEqual(
            result.payload["remainingSelectedCompanies"],
            [
                {
                    "name": "Smartling",
                    "website": "https://www.smartling.com",
                    "sourceWorkState": {
                        "technicalFailureCount": 0,
                        "abandoned": False,
                        "suspendedRunId": 1,
                        "lastFailureReason": "source_stage_deferred",
                    },
                }
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
