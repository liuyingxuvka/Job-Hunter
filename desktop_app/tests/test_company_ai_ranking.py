from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.companies.ai_ranking import (  # noqa: E402
    prerank_company_jobs_for_candidate,
    score_companies_for_candidate,
)
from jobflow_desktop_app.search.companies.company_sources_careers import (  # noqa: E402
    resolve_company_jobs_entries,
)
from jobflow_desktop_app.prompt_assets import load_prompt_asset  # noqa: E402


class _FakeClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[dict] = []

    def create(self, payload: dict) -> dict:
        self.requests.append(payload)
        if not self.payloads:
            raise AssertionError("No fake payload left for client.create().")
        return self.payloads.pop(0)


class CompanyAiRankingTests(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "companyDiscovery": {"model": "gpt-5-nano"},
            "analysis": {"model": "gpt-5-nano"},
            "candidate": {
                "targetRoles": [{"displayName": "Talent Acquisition Manager"}],
                "preferredLocations": "Berlin\nRemote Germany",
                "semanticProfile": {
                    "summary": "TA leader focused on in-house recruiting and people operations.",
                    "company_discovery_primary_anchors": ["talent acquisition", "people operations"],
                    "company_discovery_secondary_anchors": ["recruiting operations"],
                    "job_fit_core_terms": ["talent acquisition", "people operations", "recruiting leadership"],
                    "job_fit_support_terms": ["interview operations", "stakeholder management"],
                    "avoid_business_areas": ["executive search services"],
                },
            },
        }

    def test_score_companies_for_candidate_merges_ai_scores(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:linkedin.com",
                                    "fitScore": 92,
                                    "reason": "Large employer with clear in-house TA leadership demand.",
                                },
                            ]
                        }
                    )
                },
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:kornferry.com",
                                    "fitScore": 28,
                                    "reason": "Executive-search firm rather than a general employer target.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        scored = score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {"name": "LinkedIn", "website": "https://www.linkedin.com"},
                {"name": "Korn Ferry", "website": "https://www.kornferry.com"},
            ],
        )

        self.assertEqual(scored[0]["aiCompanyFitScore"], 92)
        self.assertEqual(scored[1]["aiCompanyFitScore"], 28)
        self.assertEqual(len(client.requests), 2)

    def test_prerank_company_jobs_for_candidate_merges_ai_scores(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "jobKey": "https://www.linkedin.com/jobs/view/ta-manager",
                                    "preRankScore": 95,
                                    "reason": "Direct talent-acquisition leadership match.",
                                },
                                {
                                    "jobKey": "https://www.linkedin.com/jobs/view/account-manager",
                                    "preRankScore": 14,
                                    "reason": "Sales role outside the candidate focus.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        ranked = prerank_company_jobs_for_candidate(
            client,
            config=self._config(),
            company={"name": "LinkedIn", "website": "https://www.linkedin.com"},
            jobs=[
                {
                    "title": "Talent Acquisition Manager",
                    "url": "https://www.linkedin.com/jobs/view/ta-manager",
                    "summary": "Lead talent acquisition for a global product group.",
                },
                {
                    "title": "Account Manager",
                    "url": "https://www.linkedin.com/jobs/view/account-manager",
                    "summary": "Own advertiser accounts and pipeline.",
                },
            ],
        )

        self.assertEqual(ranked[0]["aiPreRankScore"], 95)
        self.assertEqual(ranked[1]["aiPreRankScore"], 14)
        self.assertEqual(len(client.requests), 1)

    def test_prerank_company_jobs_for_candidate_keeps_partial_scores_when_later_batch_times_out(self) -> None:
        class _PartiallyFailingClient(_FakeClient):
            def create(self, payload: dict) -> dict:
                if len(self.requests) >= 1:
                    raise TimeoutError("timed out")
                return super().create(payload)

        client = _PartiallyFailingClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "jobKey": f"https://www.linkedin.com/jobs/view/{index}",
                                    "preRankScore": 80 - index,
                                    "reason": "Relevant job.",
                                }
                                for index in range(5)
                            ]
                        }
                    )
                }
            ]
        )

        ranked = prerank_company_jobs_for_candidate(
            client,
            config=self._config(),
            company={"name": "LinkedIn", "website": "https://www.linkedin.com"},
            jobs=[
                {
                    "title": f"Talent Acquisition Manager {index}",
                    "url": f"https://www.linkedin.com/jobs/view/{index}",
                    "summary": "Lead recruiting operations.",
                }
                for index in range(7)
            ],
        )

        self.assertEqual(ranked[0]["aiPreRankScore"], 80)
        self.assertFalse(ranked[0]["aiPreRankPending"])
        self.assertTrue(ranked[5]["aiPreRankPending"])
        self.assertNotIn("aiPreRankError", ranked[5])

    def test_score_companies_for_candidate_batches_requests(self) -> None:
        companies = [
            {"name": f"Company {index}", "website": f"https://company{index}.example.com"}
            for index in range(7)
        ]
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": f"domain:company{index}.example.com",
                                    "fitScore": 70 + index,
                                    "reason": "Relevant employer.",
                                }
                            ]
                        }
                    )
                }
                for index in range(7)
            ]
        )

        scored = score_companies_for_candidate(
            client,
            config=self._config(),
            companies=companies,
        )

        self.assertEqual(len(client.requests), 7)
        self.assertEqual(scored[6]["aiCompanyFitScore"], 76)

    def test_score_companies_for_candidate_uses_company_fit_terms_when_semantic_profile_missing(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:lokalise.com",
                                    "fitScore": 84,
                                    "reason": "Specialized localization employer.",
                                },
                            ]
                        }
                    )
                }
            ]
        )
        config = {
            "companyDiscovery": {"model": "gpt-5-nano"},
            "analysis": {"model": "gpt-5-nano"},
            "candidate": {
                "targetRoles": [{"displayName": "Localization Program Manager"}],
                "preferredLocations": "Berlin\nRemote Germany",
            },
            "sources": {
                "companyFitTerms": {
                    "core": ["localization operations", "translation management"],
                    "support": ["vendor management", "linguistic quality"],
                }
            },
        }

        score_companies_for_candidate(
            client,
            config=config,
            companies=[{"name": "Lokalise", "website": "https://lokalise.com"}],
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("localization operations", user_text)
        self.assertIn("translation management", user_text)
        self.assertIn("vendor management", user_text)
        self.assertIn("linguistic quality", user_text)

    def test_score_companies_for_candidate_includes_business_summary(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:lokalise.com",
                                    "fitScore": 84,
                                    "reason": "Specialized localization employer.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Lokalise",
                    "website": "https://lokalise.com",
                    "businessSummary": "Localization workflow software for global product teams.",
                    "tags": ["localization_platform", "tms"],
                }
            ],
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("Localization workflow software for global product teams.", user_text)

    def test_score_companies_for_candidate_omits_source_query_from_company_payload(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:google.com",
                                    "fitScore": 55,
                                    "reason": "Broad global employer with plausible localization operations.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Google",
                    "website": "https://www.google.com",
                    "businessSummary": "Global technology company offering a broad range of internet services and products.",
                    "tags": ["search", "cloud", "localization"],
                    "sourceQuery": "localization project governance employers",
                }
            ],
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertNotIn("localization project governance employers", user_text)

    def test_resolve_company_jobs_entries_materializes_jobs_page(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "website": "https://smartling.com",
                            "jobsPageUrl": "https://job-boards.greenhouse.io/smartling",
                            "pageType": "ats_board",
                            "careersUrl": "https://job-boards.greenhouse.io/smartling",
                            "sampleJobUrls": [
                                "https://job-boards.greenhouse.io/smartling/jobs/12345"
                            ],
                        }
                    )
                }
            ]
        )

        resolved = resolve_company_jobs_entries(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Smartling",
                    "website": "https://smartling.com",
                    "aiCompanyFitScore": 85,
                }
            ],
            limit=4,
        )

        self.assertEqual(
            resolved[0]["jobsPageUrl"],
            "https://job-boards.greenhouse.io/smartling",
        )
        self.assertEqual(resolved[0]["jobsPageType"], "ats_board")
        self.assertEqual(
            resolved[0]["sampleJobUrls"],
            ["https://job-boards.greenhouse.io/smartling/jobs/12345"],
        )

    def test_resolve_company_jobs_entries_continues_after_single_company_timeout(self) -> None:
        class _PartiallyFailingClient(_FakeClient):
            def create(self, payload: dict) -> dict:
                self.requests.append(payload)
                if len(self.requests) <= 2:
                    raise TimeoutError("timed out")
                return super().create(payload)

        client = _PartiallyFailingClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "website": "https://smartling.com",
                            "jobsPageUrl": "https://job-boards.greenhouse.io/smartling",
                            "pageType": "ats_board",
                            "careersUrl": "https://job-boards.greenhouse.io/smartling",
                            "sampleJobUrls": [],
                        }
                    )
                }
            ]
        )

        resolved = resolve_company_jobs_entries(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Slow Employer",
                    "website": "https://slow.example",
                    "aiCompanyFitScore": 90,
                },
                {
                    "name": "Smartling",
                    "website": "https://smartling.com",
                    "aiCompanyFitScore": 85,
                },
            ],
            limit=4,
        )

        self.assertEqual(str(resolved[0].get("jobsPageUrl") or "").strip(), "")
        self.assertEqual(
            resolved[1]["jobsPageUrl"],
            "https://job-boards.greenhouse.io/smartling",
        )

    def test_score_companies_for_candidate_reranks_when_input_signature_changes(self) -> None:
        first_client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:lokalise.com",
                                    "fitScore": 72,
                                    "reason": "Plausible employer for localization operations.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        scored_once = score_companies_for_candidate(
            first_client,
            config=self._config(),
            companies=[
                {
                    "name": "Lokalise",
                    "website": "https://lokalise.com",
                    "tags": ["localization_platform"],
                }
            ],
        )

        second_client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:lokalise.com",
                                    "fitScore": 90,
                                    "reason": "Specialized localization platform with concentrated hiring path.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        rescored = score_companies_for_candidate(
            second_client,
            config=self._config(),
            companies=[
                {
                    **scored_once[0],
                    "businessSummary": "Localization workflow software for global product teams.",
                }
            ],
        )

        self.assertEqual(len(first_client.requests), 1)
        self.assertEqual(len(second_client.requests), 1)
        self.assertEqual(rescored[0]["aiCompanyFitScore"], 90)
        self.assertTrue(str(rescored[0].get("aiCompanyFitInputHash") or "").strip())

    def test_score_companies_for_candidate_prioritizes_unscored_companies_before_stale_rescores(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:smartling.com",
                                    "fitScore": 91,
                                    "reason": "Specialized localization platform employer.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Microsoft",
                    "website": "https://www.microsoft.com",
                    "businessSummary": "Global technology company providing software, devices, and cloud services.",
                    "aiCompanyFitScore": 88,
                    "aiCompanyFitInputHash": "stale-hash",
                },
                {
                    "name": "Smartling",
                    "website": "https://www.smartling.com",
                    "businessSummary": "Localization management software company that automates translation workflows for digital products.",
                },
            ],
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("Smartling", user_text)
        self.assertNotIn("Microsoft", user_text)

    def test_score_companies_for_candidate_does_not_require_business_summary_for_unscored_priority(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "companyKey": "domain:welocalize.com",
                                    "fitScore": 83,
                                    "reason": "Localization employer with recurring in-house operations roles.",
                                },
                            ]
                        }
                    )
                }
            ]
        )

        score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Microsoft",
                    "website": "https://www.microsoft.com",
                    "businessSummary": "Global technology company providing software, devices, and cloud services.",
                    "aiCompanyFitScore": 88,
                    "aiCompanyFitInputHash": "stale-hash",
                },
                {
                    "name": "Welocalize",
                    "website": "https://www.welocalize.com",
                    "tags": ["localization", "language_services"],
                },
            ],
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("Welocalize", user_text)
        self.assertNotIn("Microsoft", user_text)

    def test_score_companies_for_candidate_marks_timeout_as_pending_retry(self) -> None:
        class _TimeoutClient:
            def __init__(self) -> None:
                self.requests: list[dict] = []

            def create(self, payload: dict) -> dict:
                self.requests.append(payload)
                raise TimeoutError("OpenAI API request timed out.")

        client = _TimeoutClient()

        scored = score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {"name": "LinkedIn", "website": "https://www.linkedin.com"},
                {"name": "Amazon", "website": "https://www.amazon.jobs"},
            ],
            current_run_id=42,
        )

        self.assertNotIn("aiCompanyRankingPending", scored[0])
        self.assertNotIn("aiCompanyRankingPending", scored[1])
        self.assertNotIn("aiCompanyRankingError", scored[0])
        self.assertEqual(scored[0]["rankingWorkState"]["technicalFailureCount"], 1)
        self.assertEqual(scored[0]["rankingWorkState"]["suspendedRunId"], 42)
        self.assertEqual(len(client.requests), 1)

    def test_score_companies_for_candidate_keeps_existing_score_when_refresh_times_out(self) -> None:
        class _TimeoutClient:
            def __init__(self) -> None:
                self.requests: list[dict] = []

            def create(self, payload: dict) -> dict:
                self.requests.append(payload)
                raise TimeoutError("OpenAI API request timed out.")

        client = _TimeoutClient()

        scored = score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Smartling",
                    "website": "https://www.smartling.com",
                    "businessSummary": "Localization management software company that automates translation workflows for digital products.",
                    "aiCompanyFitScore": 85,
                    "aiCompanyFitInputHash": "stale-hash",
                }
            ],
            current_run_id=42,
        )

        self.assertEqual(scored[0]["aiCompanyFitScore"], 85)
        self.assertNotIn("aiCompanyRankingPending", scored[0])
        self.assertNotIn("aiCompanyRankingError", scored[0])
        self.assertEqual(scored[0]["rankingWorkState"]["suspendedRunId"], 42)
        self.assertEqual(scored[0]["rankingWorkState"]["lastFailureReason"], "company_fit_refresh_error")
        self.assertEqual(scored[0]["rankingWorkState"]["technicalFailureCount"], 0)
        self.assertEqual(len(client.requests), 1)

    def test_score_companies_for_candidate_does_not_abandon_cached_score_after_refresh_failures(self) -> None:
        class _TimeoutClient:
            def create(self, payload: dict) -> dict:
                raise TimeoutError("OpenAI API request timed out.")

        client = _TimeoutClient()

        scored = score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Smartling",
                    "website": "https://www.smartling.com",
                    "businessSummary": "Localization management software company that automates translation workflows for digital products.",
                    "aiCompanyFitScore": 85,
                    "aiCompanyFitInputHash": "stale-hash",
                    "rankingWorkState": {
                        "technicalFailureCount": 2,
                        "abandoned": False,
                    },
                }
            ],
            current_run_id=42,
        )

        self.assertEqual(scored[0]["aiCompanyFitScore"], 85)
        self.assertFalse(scored[0]["rankingWorkState"].get("abandoned", False))
        self.assertEqual(scored[0]["rankingWorkState"]["technicalFailureCount"], 2)
        self.assertEqual(scored[0]["rankingWorkState"]["suspendedRunId"], 42)
        self.assertEqual(scored[0]["rankingWorkState"]["lastFailureReason"], "company_fit_refresh_error")

    def test_score_companies_for_candidate_abandons_after_third_failure(self) -> None:
        class _TimeoutClient:
            def create(self, payload: dict) -> dict:
                raise TimeoutError("OpenAI API request timed out.")

        client = _TimeoutClient()

        scored = score_companies_for_candidate(
            client,
            config=self._config(),
            companies=[
                {
                    "name": "Phrase",
                    "website": "https://phrase.com",
                    "rankingWorkState": {
                        "technicalFailureCount": 2,
                        "abandoned": False,
                    },
                }
            ],
            current_run_id=77,
        )

        self.assertTrue(scored[0]["rankingWorkState"]["abandoned"])
        self.assertEqual(scored[0]["rankingWorkState"]["technicalFailureCount"], 3)

    def test_prerank_company_jobs_for_candidate_batches_requests(self) -> None:
        jobs = [
            {
                "title": f"Role {index}",
                "url": f"https://www.linkedin.com/jobs/view/{index}",
                "summary": "Relevant recruiting role.",
            }
            for index in range(11)
        ]
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "jobKey": f"https://www.linkedin.com/jobs/view/{index}",
                                    "preRankScore": 80 - index,
                                    "reason": "Relevant recruiting role.",
                                }
                                for index in range(5)
                            ]
                        }
                    )
                },
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "jobKey": f"https://www.linkedin.com/jobs/view/{index}",
                                    "preRankScore": 75 - index,
                                    "reason": "Relevant recruiting role.",
                                }
                                for index in range(5, 10)
                            ]
                        }
                    )
                },
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "jobKey": "https://www.linkedin.com/jobs/view/10",
                                    "preRankScore": 55,
                                    "reason": "Relevant recruiting role.",
                                }
                            ]
                        }
                    )
                },
            ]
        )

        ranked = prerank_company_jobs_for_candidate(
            client,
            config=self._config(),
            company={"name": "LinkedIn", "website": "https://www.linkedin.com"},
            jobs=jobs,
        )

        self.assertEqual(len(client.requests), 3)
        self.assertEqual(ranked[10]["aiPreRankScore"], 55)

    def test_job_prerank_prompt_explicitly_rejects_partner_signup_pages(self) -> None:
        prompt = load_prompt_asset("search_ranking", "job_prerank_prompt.txt")
        self.assertIn("partner sign-up pages", prompt)
        self.assertIn("agency registration pages", prompt)
        self.assertIn("freelancer registration pages", prompt)
        self.assertIn("talent community pages", prompt)
        self.assertIn("concrete public employee requisition", prompt)
        self.assertIn("business-adjacent pages for partner ecosystems", prompt)
        self.assertIn("Do not require an exact title match", prompt)
        self.assertIn("same business domain, workflow, or functional problem-space", prompt)
        self.assertIn("Scores from 30 to 60 are appropriate", prompt)

    def test_prerank_company_jobs_for_candidate_includes_business_summary(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "jobKey": "https://careers.example.com/jobs/director-language-ai",
                                    "preRankScore": 41,
                                    "reason": "Adjacent language-platform leadership role worth deeper review.",
                                }
                            ]
                        }
                    )
                }
            ]
        )

        prerank_company_jobs_for_candidate(
            client,
            config=self._config(),
            company={
                "name": "Lionbridge",
                "website": "https://careers.lionbridge.com",
                "businessSummary": "Language and localization services company for global content and AI workflows.",
                "tags": ["localization", "language_services"],
                "jobsPageUrl": "https://careers.lionbridge.com/jobs",
            },
            jobs=[
                {
                    "title": "Director of Language AI",
                    "url": "https://careers.example.com/jobs/director-language-ai",
                    "summary": "Lead language-AI programs across multilingual content operations.",
                }
            ],
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("Language and localization services company", user_text)
        self.assertIn("\"businessSummary\"", user_text)

    def test_company_fit_prompt_prefers_internal_employer_plausibility(self) -> None:
        prompt = load_prompt_asset("search_ranking", "company_fit_prompt.txt")
        self.assertIn("internal employee roles", prompt)
        self.assertIn("businessSummary", prompt)
        self.assertIn("talent services, recruiting, staffing, executive search", prompt)
        self.assertIn("Do not boost a company just because it sells adjacent services", prompt)
        self.assertIn("huge generic global employer", prompt)
        self.assertIn("recurring internal hiring area", prompt)
        self.assertIn("service vendors and agencies", prompt)
        self.assertIn("Google, Amazon, Apple, or Microsoft", prompt)
        self.assertIn("Do not assume hidden internal teams", prompt)
        self.assertIn("keep the score moderate rather than elite", prompt)
        self.assertIn("Scores from 80 to 100 are reserved", prompt)
        self.assertIn("giant generic employers", prompt)
        self.assertIn("clearer and more concentrated hiring path", prompt)
        self.assertIn("Elite scores require concrete evidence", prompt)
        self.assertIn("require unusually strong evidence", prompt)


if __name__ == "__main__":
    unittest.main()
