from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.companies.sources import (  # noqa: E402
    COMPANY_JOB_PRERANK_TIMEOUT_SECONDS,
    COMPANY_SEARCH_FALLBACK_TIMEOUT_SECONDS,
    build_found_job_records,
    build_company_search_fallback_query,
    collect_supported_company_source_jobs,
    collect_careers_page_job_candidates,
    company_search_fallback_enabled,
    detect_ats_from_url,
    enrich_job_with_details,
    merge_company_source_jobs,
)
from jobflow_desktop_app.search.companies.company_sources_careers import (  # noqa: E402
    CareersPageFetchTrace,
    build_company_search_cache_key,
    discover_company_careers,
    fetch_careers_page_jobs_with_trace,
    normalize_company_careers_discovery_cache,
    openai_search_jobs,
)
from jobflow_desktop_app.search.companies.ai_job_enumeration import (  # noqa: E402
    enumerate_jobs_and_listing_hints_from_careers_page,
    enumerate_jobs_from_careers_page,
)
from jobflow_desktop_app.search.companies.company_sources_enrichment import (  # noqa: E402
    fetch_job_details,
)
from jobflow_desktop_app.search.companies.sources_helpers import (  # noqa: E402
    has_job_signal,
    collect_careers_page_link_snapshots,
    normalize_company_job,
    normalize_job_page_coverage_state,
    select_company_jobs_for_coverage,
    select_listing_urls_for_processing,
    update_job_page_coverage_state,
)
from jobflow_desktop_app.search.output.final_output import normalize_job_url  # noqa: E402
from jobflow_desktop_app.prompt_assets import load_prompt_asset  # noqa: E402
from jobflow_desktop_app.ai.client import OpenAIResponsesError  # noqa: E402


class _FakeClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[dict] = []

    def create(self, payload: dict) -> dict:
        self.requests.append(payload)
        if not self.payloads:
            raise AssertionError("No fake payload left for client.create().")
        return self.payloads.pop(0)


class _FlakyClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []

    def create(self, payload: dict) -> dict:
        self.requests.append(payload)
        if not self.outcomes:
            raise AssertionError("No fake outcome left for client.create().")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class CompanySourcesTests(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "sources": {"maxJobsPerCompany": 1},
            "filters": {"maxPostAgeDays": 90},
            "fetch": {"timeoutMs": 30000},
            "analysis": {"jdFetchMaxJobsPerRun": 10},
            "search": {"model": "gpt-5-nano", "maxJobsPerQuery": 8},
            "companyDiscovery": {"model": "gpt-5-nano"},
            "candidate": {
                "targetRoles": [{"displayName": "Hydrogen durability engineer"}],
                "locationPreference": "Berlin / Remote",
                "scopeProfile": "",
            },
        }

    def test_detect_ats_from_url_matches_supported_hosts(self) -> None:
        self.assertEqual(
            detect_ats_from_url("https://boards.greenhouse.io/acme/jobs/123"),
            {"type": "greenhouse", "id": "acme"},
        )
        self.assertEqual(
            detect_ats_from_url("https://jobs.lever.co/beta/role"),
            {"type": "lever", "id": "beta"},
        )
        self.assertEqual(
            detect_ats_from_url("https://careers.smartrecruiters.com/gamma/job"),
            {"type": "smartrecruiters", "id": "gamma"},
        )

    def test_has_job_signal_accepts_official_workable_detail_page(self) -> None:
        self.assertTrue(
            has_job_signal(
                title="Senior Localization Program Manager",
                url="https://apply.workable.com/transifex/j/EE71278021",
                summary="Lead multilingual launches and TMS governance.",
            )
        )

    def test_normalize_job_url_preserves_greenhouse_job_query_id(self) -> None:
        self.assertEqual(
            normalize_job_url("https://lokalise.com/job?gh_jid=7681640003&utm_source=test"),
            "https://lokalise.com/job?gh_jid=7681640003",
        )

    def test_dedupe_jobs_by_normalized_url_keeps_distinct_greenhouse_query_urls(self) -> None:
        result = build_found_job_records(
            [
                {
                    "title": "Finance Operations Specialist",
                    "company": "Lokalise",
                    "url": "https://lokalise.com/job?gh_jid=7681640003",
                },
                {
                    "title": "Senior Software Engineer",
                    "company": "Lokalise",
                    "url": "https://lokalise.com/job?gh_jid=7673441003",
                },
            ],
            existing_jobs=[],
            config=self._config(),
        )
        self.assertEqual(len(result), 2)

    def test_has_job_signal_rejects_google_jobs_ui_subpages(self) -> None:
        self.assertFalse(
            has_job_signal(
                title="Job search",
                url="https://www.google.com/about/careers/applications/jobs/results/jobs/results",
                summary="Browse openings and refine your filters.",
            )
        )
        self.assertFalse(
            has_job_signal(
                title="How we hire",
                url="https://www.google.com/about/careers/applications/jobs/results/how-we-hire",
                summary="Learn about the hiring process.",
            )
        )
        self.assertFalse(
            has_job_signal(
                title="Know your rights: workplace discrimination is illegal",
                url="https://careers.google.com/jobs/dist/legal/EEOC_KnowYourRights_10_20.pdf",
                summary="Equal employment opportunity notice.",
            )
        )

    def test_collect_supported_company_source_jobs_updates_company_state(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            },
            {
                "name": "Existing Workday",
                "careersUrl": "https://company.myworkdayjobs.com/foo",
            },
        ]
        fetched_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Develop PEM durability test plans.",
            },
            {
                "title": "Electrolyzer Scientist",
                "location": "Munich",
                "url": "https://boards.greenhouse.io/acme/jobs/2",
                "datePosted": "2026-04-11T00:00:00Z",
                "summary": "Work on stack degradation analytics.",
            },
        ]

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
            return_value=fetched_jobs,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
            )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(len(result.processed_companies), 1)
        self.assertEqual(len(result.remaining_companies), 1)
        self.assertEqual(result.remaining_companies[0]["name"], "Existing Workday")
        self.assertEqual(len(result.jobs), 1)
        company = result.processed_companies[0]
        self.assertTrue(company["snapshotComplete"])
        self.assertEqual(company["lastJobsFoundCount"], 2)
        self.assertEqual(company["lastNewJobsCount"], 2)
        self.assertEqual(len(company["snapshotJobUrls"]), 2)
        self.assertEqual(company["atsType"], "greenhouse")
        self.assertEqual(company["atsId"], "acme")
        self.assertEqual(company["sourceDiagnostics"]["sourcePath"], "ats")
        self.assertEqual(company["sourceDiagnostics"]["reason"], "queued_jobs")

    def test_collect_supported_company_source_jobs_filters_noise_titles_from_ats_snapshot(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            }
        ]
        fetched_jobs = [
            {
                "title": "See all jobs",
                "location": "",
                "url": "https://boards.greenhouse.io/acme/jobs/noise",
                "datePosted": "2026-04-18T00:00:00Z",
                "summary": "",
            },
            {
                "title": "Fuel Cell Engineer",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/real",
                "datePosted": "2026-04-18T00:00:00Z",
                "summary": "Develop PEM durability plans.",
            },
        ]

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
            return_value=fetched_jobs,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
            )

        diagnostics = result.processed_companies[0]["sourceDiagnostics"]
        self.assertEqual(diagnostics["filteredNoiseTitle"], 1)
        self.assertEqual(diagnostics["snapshotJobs"], 1)

    def test_collect_supported_company_source_jobs_reuses_cached_prerank_scores(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            }
        ]
        fetched_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/real",
                "datePosted": "2026-04-18T00:00:00Z",
                "summary": "Develop PEM durability plans.",
            },
        ]
        existing_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "company": "Acme Hydrogen",
                "url": "https://boards.greenhouse.io/acme/jobs/real",
                "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/real",
                "aiPreRankScore": 88,
                "aiPreRankReason": "Direct hydrogen durability match.",
            }
        ]
        client = _FakeClient([])

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
            return_value=fetched_jobs,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                existing_jobs=existing_jobs,
                client=client,
            )

        self.assertEqual(client.requests, [])
        diagnostics = result.processed_companies[0]["sourceDiagnostics"]
        self.assertEqual(diagnostics["reusedPrerankJobs"], 1)

    def test_merge_company_source_jobs_and_found_records_preserve_analysis(self) -> None:
        existing_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "company": "Acme Hydrogen",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "analysis": {"overallScore": 72, "matchScore": 72, "fitTrack": "direct_fit"},
            }
        ]
        incoming_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "summary": "Develop PEM durability test plans.",
                "source": "company:Acme Hydrogen:greenhouse",
                "sourceType": "company",
                "companyTags": ["hydrogen"],
            }
        ]

        merged_jobs = merge_company_source_jobs(existing_jobs, incoming_jobs)
        self.assertEqual(len(merged_jobs), 1)
        self.assertEqual(merged_jobs[0]["analysis"]["matchScore"], 72)
        self.assertEqual(merged_jobs[0]["location"], "Berlin")

        found_records = build_found_job_records(
            incoming_jobs,
            existing_jobs=merged_jobs,
            config=self._config(),
        )
        self.assertEqual(len(found_records), 1)
        self.assertTrue(found_records[0]["alreadyAnalyzed"])
        self.assertEqual(found_records[0]["fitTrack"], "direct_fit")

    def test_collect_careers_page_job_candidates_prefers_json_ld(self) -> None:
        html = """
        <html>
          <head>
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Hydrogen Systems Engineer",
                "description": "<p>Develop hydrogen systems.</p>",
                "datePosted": "2026-04-12",
                "jobLocation": {
                  "@type": "Place",
                  "address": {
                    "@type": "PostalAddress",
                    "addressLocality": "Berlin",
                    "addressCountry": "DE"
                  }
                },
                "url": "https://example.com/jobs/123"
              }
            </script>
          </head>
          <body><a href="/jobs/123">Fallback link</a></body>
        </html>
        """

        jobs = collect_careers_page_job_candidates(html, "https://example.com/careers")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Hydrogen Systems Engineer")
        self.assertEqual(jobs[0]["location"], "Berlin, DE")
        self.assertEqual(jobs[0]["url"], "https://example.com/jobs/123")

    def test_select_company_jobs_for_coverage_excludes_low_ai_prerank_jobs(self) -> None:
        selection = select_company_jobs_for_coverage(
            company={"name": "Modelon AB"},
            jobs=[
                {
                    "title": "Senior backend engineer - AI platform & Agentic workflows",
                    "url": "https://example.com/jobs/backend",
                    "aiPreRankScore": 20,
                    "datePosted": "2026-04-18T00:00:00Z",
                },
                {
                    "title": "Technical Product Lead, Industry Applications & Physics",
                    "url": "https://example.com/jobs/product-lead",
                    "aiPreRankScore": 65,
                    "datePosted": "2026-04-18T00:00:00Z",
                },
            ],
            limit=5,
        )

        self.assertEqual(
            [job["title"] for job in selection["jobs"]],
            ["Technical Product Lead, Industry Applications & Physics"],
        )
        self.assertEqual(selection["excludedCompletedCount"], 0)
        self.assertEqual(selection["excludedLowPrerankCount"], 1)

    def test_select_company_jobs_for_coverage_defers_pending_ai_prerank_jobs(self) -> None:
        selection = select_company_jobs_for_coverage(
            company={"name": "Lionbridge"},
            jobs=[
                {
                    "title": "Localization Program Manager",
                    "url": "https://example.com/jobs/localization-pm",
                    "aiPreRankScore": 74,
                    "datePosted": "2026-04-18T00:00:00Z",
                },
                {
                    "title": "Localization Vendor Manager",
                    "url": "https://example.com/jobs/vendor-manager",
                    "aiPreRankPending": True,
                    "datePosted": "2026-04-19T00:00:00Z",
                },
            ],
            limit=5,
        )

        self.assertEqual(
            [job["url"] for job in selection["jobs"]],
            ["https://example.com/jobs/localization-pm"],
        )
        self.assertEqual(selection["excludedLowPrerankCount"], 0)
        self.assertEqual(selection["excludedPendingPrerankCount"], 1)

    def test_discover_company_careers_prefers_jobs_page_url_and_page_type(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "website": "https://www.servicenow.com",
                            "jobsPageUrl": "https://careers.servicenow.com/jobs",
                            "pageType": "jobs_listing",
                            "careersUrl": "https://www.servicenow.com/company/careers.html",
                            "sampleJobUrls": [
                                "https://careers.servicenow.com/jobs/744000090460686/senior-product-marketing-manager/"
                            ],
                        }
                    )
                }
            ]
        )

        discovered = discover_company_careers(
            client,
            config={"companyDiscovery": {"model": "gpt-5-nano"}},
            company_name="ServiceNow",
        )

        self.assertEqual(discovered["website"], "https://www.servicenow.com")
        self.assertEqual(discovered["jobsPageUrl"], "https://careers.servicenow.com/jobs")
        self.assertEqual(discovered["careersUrl"], "https://careers.servicenow.com/jobs")
        self.assertEqual(discovered["pageType"], "jobs_listing")
        self.assertEqual(
            discovered["sampleJobUrls"],
            ["https://careers.servicenow.com/jobs/744000090460686/senior-product-marketing-manager"],
        )

    def test_discover_company_careers_retries_once_after_transient_error(self) -> None:
        client = _FlakyClient(
            [
                RuntimeError("temporary timeout"),
                {
                    "output_text": json.dumps(
                        {
                            "website": "https://www.servicenow.com",
                            "jobsPageUrl": "https://careers.servicenow.com/jobs",
                            "pageType": "jobs_listing",
                            "careersUrl": "https://careers.servicenow.com/jobs",
                            "sampleJobUrls": [],
                        }
                    )
                },
            ]
        )

        discovered = discover_company_careers(
            client,
            config={"companyDiscovery": {"model": "gpt-5-nano"}},
            company_name="ServiceNow",
        )

        self.assertEqual(len(client.requests), 2)
        self.assertEqual(discovered["jobsPageUrl"], "https://careers.servicenow.com/jobs")

    def test_normalize_company_careers_discovery_cache_preserves_not_found_cache_shape(self) -> None:
        normalized = normalize_company_careers_discovery_cache(
            {
                "website": "",
                "jobsPageUrl": "",
                "pageType": "not_found",
                "careersUrl": "",
                "sampleJobUrls": [],
            }
        )

        self.assertEqual(
            normalized,
            {
                "website": "",
                "jobsPageUrl": "",
                "pageType": "not_found",
                "careersUrl": "",
                "sampleJobUrls": [],
            },
        )

    def test_fetch_careers_page_jobs_with_trace_prefers_ai_enumeration_over_followup_heuristics(self) -> None:
        landing_html = """
        <html>
          <body>
            <a href="https://careers.smartrecruiters.com/Acme">View Global Openings</a>
          </body>
        </html>
        """
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Localization Project Manager",
                                    "url": "https://careers.smartrecruiters.com/Acme/localization-project-manager",
                                    "location": "Berlin",
                                    "summary": "Lead multilingual launches.",
                                }
                            ],
                            "nextListingUrls": [],
                        }
                    )
                }
            ]
        )

        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                return_value=(landing_html, "https://example.com/careers"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[],
            ),
        ):
            trace = fetch_careers_page_jobs_with_trace(
                "https://example.com/careers",
                config=self._config(),
                timeout_seconds=15,
                client=client,
                company_name="Acme",
            )

        self.assertEqual(len(trace.jobs), 1)
        self.assertEqual(trace.source_path, "company_page_ai")
        self.assertEqual(trace.followed_links, [])

    def test_fetch_careers_page_jobs_with_trace_reuses_cached_listing_enumeration_when_fingerprint_matches(self) -> None:
        landing_html = """
        <html>
          <body>
            <a href="https://careers.smartrecruiters.com/Acme">View Global Openings</a>
          </body>
        </html>
        """
        cached_job = {
            "title": "Localization Project Manager",
            "url": "https://careers.smartrecruiters.com/Acme/localization-project-manager",
            "location": "Berlin",
            "summary": "Lead multilingual launches.",
        }
        first_client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [cached_job],
                            "nextListingUrls": [],
                        }
                    )
                }
            ]
        )
        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                return_value=(landing_html, "https://example.com/careers"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[],
            ),
        ):
            first_trace = fetch_careers_page_jobs_with_trace(
                "https://example.com/careers",
                config=self._config(),
                timeout_seconds=15,
                client=first_client,
                company_name="Acme",
            )
            second_client = _FakeClient([])
            second_trace = fetch_careers_page_jobs_with_trace(
                "https://example.com/careers",
                config=self._config(),
                timeout_seconds=15,
                client=second_client,
                company_name="Acme",
                listing_page_cache_entry=first_trace.listing_page_cache_entry,
            )

        self.assertEqual(len(first_client.requests), 1)
        self.assertEqual(second_client.requests, [])
        self.assertEqual(second_trace.source_path, "company_page_ai_cache")
        self.assertEqual(second_trace.jobs[0]["url"], cached_job["url"])

    def test_fetch_careers_page_jobs_with_trace_keeps_parser_jobs_when_ai_enumeration_times_out(self) -> None:
        landing_html = """
        <html>
          <body>
            <a href="https://careers.roblox.com/jobs/7142298">Staff Software Engineer, Search</a>
          </body>
        </html>
        """
        parser_job = {
            "title": "Staff Software Engineer, Search",
            "url": "https://careers.roblox.com/jobs/7142298",
            "location": "San Mateo",
            "datePosted": "",
            "summary": "Build search systems.",
        }

        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                return_value=(landing_html, "https://careers.roblox.com/jobs"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[parser_job],
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.enumerate_jobs_and_listing_hints_from_careers_page",
                side_effect=TimeoutError("OpenAI API request timed out."),
            ),
        ):
            trace = fetch_careers_page_jobs_with_trace(
                "https://careers.roblox.com/jobs",
                config=self._config(),
                timeout_seconds=15,
                client=_FakeClient([]),
                company_name="Roblox",
            )

        self.assertEqual(trace.source_path, "company_page")
        self.assertEqual([job["url"] for job in trace.jobs], [parser_job["url"]])
        self.assertEqual(trace.next_listing_urls, [])

    def test_fetch_careers_page_jobs_with_trace_uses_sample_jobs_when_ai_enumeration_times_out_and_parser_is_empty(self) -> None:
        landing_html = """
        <html>
          <body>
            <a href="https://duolingo.breezy.hr/">Openings</a>
          </body>
        </html>
        """
        sample_detail_html = """
        <html>
          <head><title>Urdu Localization Translator</title></head>
          <body>
            <h1>Urdu Localization Translator</h1>
            <p>Berlin, Germany</p>
          </body>
        </html>
        """

        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                side_effect=[
                    (landing_html, "https://duolingo.breezy.hr/"),
                    (sample_detail_html, "https://duolingo.breezy.hr/p/1234-urdu-localization-translator"),
                ],
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[],
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.enumerate_jobs_and_listing_hints_from_careers_page",
                side_effect=TimeoutError("OpenAI API request timed out."),
            ),
        ):
            trace = fetch_careers_page_jobs_with_trace(
                "https://duolingo.breezy.hr/",
                config=self._config(),
                timeout_seconds=15,
                sample_job_urls=["https://duolingo.breezy.hr/p/1234-urdu-localization-translator"],
                client=_FakeClient([]),
                company_name="Duolingo",
            )

        self.assertEqual(trace.source_path, "sample_job_urls")
        self.assertEqual(len(trace.jobs), 1)
        self.assertEqual(
            trace.jobs[0]["url"],
            "https://duolingo.breezy.hr/p/1234-urdu-localization-translator",
        )

    def test_update_job_page_coverage_state_persists_listing_page_cache(self) -> None:
        coverage = update_job_page_coverage_state(
            entry_url="https://example.com/careers",
            coverage_state={},
            processed_listing_urls=["https://example.com/careers"],
            discovered_listing_urls=["https://example.com/careers/page/2"],
            listing_page_cache_updates={
                "https://example.com/careers": {
                    "pageFingerprint": "abc123",
                    "jobs": [
                        {
                            "title": "Localization Project Manager",
                            "url": "https://example.com/jobs/123",
                            "location": "Berlin",
                            "summary": "Lead multilingual launches.",
                        }
                    ],
                    "nextListingUrls": ["https://example.com/careers/page/2"],
                }
            },
        )

        normalized = normalize_job_page_coverage_state(coverage)
        cache_entry = normalized["listingPageCache"]["https://example.com/careers"]
        self.assertEqual(cache_entry["pageFingerprint"], "abc123")
        self.assertEqual(cache_entry["jobs"][0]["url"], "https://example.com/jobs/123")
        self.assertEqual(
            cache_entry["nextListingUrls"],
            ["https://example.com/careers/page/2"],
        )

    def test_update_job_page_coverage_state_preserves_company_search_cache(self) -> None:
        coverage = update_job_page_coverage_state(
            entry_url="https://example.com/careers",
            coverage_state={
                "companySearchCache": {
                    "cache-key": {
                        "query": "site:example.com Example careers jobs",
                        "companyWebsite": "https://example.com",
                        "jobsPageUrl": "https://example.com/careers",
                        "pageType": "jobs_listing",
                        "sampleJobUrls": ["https://example.com/jobs/1"],
                        "jobs": [
                            {
                                "title": "Localization Project Manager",
                                "url": "https://example.com/jobs/1",
                                "location": "Berlin",
                                "summary": "Lead multilingual launches.",
                            }
                        ],
                    }
                }
            },
            processed_listing_urls=["https://example.com/careers"],
            discovered_listing_urls=[],
        )

        normalized = normalize_job_page_coverage_state(coverage)
        cache_entry = normalized["companySearchCache"]["cache-key"]
        self.assertEqual(cache_entry["query"], "site:example.com Example careers jobs")
        self.assertEqual(cache_entry["jobsPageUrl"], "https://example.com/careers")
        self.assertEqual(cache_entry["jobs"][0]["url"], "https://example.com/jobs/1")

    def test_enumerate_jobs_from_careers_page_can_return_additional_official_urls(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Strategic Account Manager - DACH region",
                                    "url": "https://modelon.careers.haileyhr.app/en-GB/job/real-role",
                                    "location": "Germany",
                                    "summary": "Drive DACH growth.",
                                },
                                {
                                    "title": "Senior Account Executive",
                                    "url": "https://modelon.careers.haileyhr.app/en-GB/job/senior-account-executive",
                                    "location": "Germany",
                                    "summary": "Own enterprise account growth across DACH.",
                                },
                                {
                                    "title": "Aggregator result",
                                    "url": "https://www.linkedin.com/jobs/view/123456",
                                    "location": "",
                                    "summary": "",
                                },
                            ],
                            "nextListingUrls": [],
                        }
                    )
                }
            ]
        )

        jobs = enumerate_jobs_from_careers_page(
            client,
            config=self._config(),
            company_name="Modelon AB",
            page_url="https://modelon.careers.haileyhr.app/en-GB/jobs",
            page_text="Open positions and growth roles across Europe.",
            links=[
                {
                    "text": "Strategic Account Manager - DACH region",
                    "url": "https://modelon.careers.haileyhr.app/en-GB/job/real-role",
                },
            ],
            sample_job_urls=["https://modelon.careers.haileyhr.app/en-GB/job/real-role"],
        )

        self.assertEqual(
            [job["url"] for job in jobs],
            [
                "https://modelon.careers.haileyhr.app/en-GB/job/real-role",
                "https://modelon.careers.haileyhr.app/en-GB/job/senior-account-executive",
                "https://www.linkedin.com/jobs/view/123456",
            ],
        )

    def test_careers_page_enumeration_prompt_targets_coverage_not_samples_only(self) -> None:
        from jobflow_desktop_app.prompt_assets import load_prompt_asset

        prompt = load_prompt_asset("search_ranking", "careers_page_job_enumeration_prompt.txt")
        self.assertIn("Aim for coverage, not just one or two examples.", prompt)
        self.assertIn("do not stop after only reproducing those examples", prompt)
        self.assertIn("Do not perform web search in this step.", prompt)
        self.assertIn("Concrete detail pages on professional job platforms are acceptable", prompt)
        self.assertIn("dashboard/profile pages", prompt)
        self.assertIn("how-we-hire/how-we-work pages", prompt)
        self.assertIn("title should read like a real job title", prompt)
        self.assertIn("Recommended jobs", prompt)
        self.assertIn("Job alerts", prompt)
        self.assertIn("explicit remaining frontier", prompt)
        self.assertIn("do not leave remaining frontier pages unstated", prompt)

    def test_collect_careers_page_link_snapshots_excludes_ui_navigation_pages(self) -> None:
        html = """
        <html>
          <body>
            <a href="/teams">Teams</a>
            <a href="/jobs/alerts">Job alerts</a>
            <a href="/jobs/results/123-software-engineer">Software Engineer</a>
          </body>
        </html>
        """

        snapshots = collect_careers_page_link_snapshots(
            html,
            "https://www.google.com/about/careers/applications/jobs/results/",
        )

        self.assertEqual(
            snapshots,
            [
                {
                    "text": "Software Engineer",
                    "url": "https://www.google.com/jobs/results/123-software-engineer",
                }
            ],
        )

    def test_enumerate_jobs_from_careers_page_filters_ui_navigation_results(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Teams",
                                    "url": "https://www.google.com/teams",
                                    "location": "",
                                    "summary": "",
                                },
                                {
                                    "title": "Software Engineer",
                                    "url": "https://www.google.com/jobs/results/123-software-engineer",
                                    "location": "Berlin",
                                    "summary": "Build search infrastructure.",
                                },
                            ],
                            "nextListingUrls": [],
                        }
                    )
                }
            ]
        )

        jobs = enumerate_jobs_from_careers_page(
            client,
            config=self._config(),
            company_name="Google",
            page_url="https://www.google.com/about/careers/applications/jobs/results/",
            page_text="Google careers search results page.",
            links=[
                {"text": "Teams", "url": "https://www.google.com/teams"},
                {
                    "text": "Software Engineer",
                    "url": "https://www.google.com/jobs/results/123-software-engineer",
                },
            ],
            sample_job_urls=[],
        )

        self.assertEqual(
            jobs,
            [
                {
                    "title": "Software Engineer",
                    "location": "Berlin",
                    "url": "https://www.google.com/jobs/results/123-software-engineer",
                    "datePosted": "",
                    "summary": "Build search infrastructure.",
                    "aiEnumerated": True,
                }
            ],
        )

    def test_enumerate_jobs_and_listing_hints_from_careers_page_returns_frontier_hints(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Localization Program Manager",
                                    "url": "https://example.com/jobs/localization-program-manager",
                                    "location": "Berlin",
                                    "summary": "Lead multilingual launches.",
                                }
                            ],
                            "nextListingUrls": [
                                "https://example.com/careers/page/2",
                                "https://example.com/careers/archive",
                            ],
                        }
                    )
                }
            ]
        )

        result = enumerate_jobs_and_listing_hints_from_careers_page(
            client,
            config=self._config(),
            company_name="Example Co",
            page_url="https://example.com/careers",
            page_text="Open roles and archive pages.",
            links=[
                {"text": "Page 2", "url": "https://example.com/careers/page/2"},
                {"text": "Archive", "url": "https://example.com/careers/archive"},
                {
                    "text": "Localization Program Manager",
                    "url": "https://example.com/jobs/localization-program-manager",
                },
            ],
            sample_job_urls=[],
        )

        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(
            result.next_listing_urls,
            [
                "https://example.com/careers/page/2",
                "https://example.com/careers/archive",
            ],
        )

    def test_enumerate_jobs_and_listing_hints_from_careers_page_does_not_enable_web_search(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [],
                            "nextListingUrls": [],
                        }
                    )
                }
            ]
        )

        enumerate_jobs_and_listing_hints_from_careers_page(
            client,
            config=self._config(),
            company_name="Example Co",
            page_url="https://example.com/careers",
            page_text="Open roles on this careers page.",
            links=[{"text": "Page 2", "url": "https://example.com/careers/page/2"}],
            sample_job_urls=[],
        )

        self.assertEqual(len(client.requests), 1)
        self.assertNotIn("tools", client.requests[0])

    def test_enumerate_jobs_and_listing_hints_from_careers_page_filters_seen_jobs_and_marks_revisit_when_batch_full(self) -> None:
        jobs = [
            {
                "title": f"Software Engineer {index}",
                "url": f"https://example.com/jobs/{index}",
                "location": "Berlin",
                "summary": "Engineering role",
            }
            for index in range(12)
        ]
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": jobs,
                            "nextListingUrls": ["https://example.com/careers/page/2"],
                        }
                    )
                }
            ]
        )

        result = enumerate_jobs_and_listing_hints_from_careers_page(
            client,
            config=self._config(),
            company_name="Example Co",
            page_url="https://example.com/careers",
            page_text="Open roles on this careers page.",
            links=[
                {"text": f"Software Engineer {index}", "url": f"https://example.com/jobs/{index}"}
                for index in range(12)
            ],
            sample_job_urls=[],
            seen_job_urls=["https://example.com/jobs/0", "https://example.com/jobs/1"],
        )

        self.assertEqual(len(result.jobs), 10)
        self.assertEqual(result.jobs[0]["url"], "https://example.com/jobs/2")
        self.assertTrue(result.revisit_current_page)
        self.assertEqual(
            result.next_listing_urls,
            ["https://example.com/careers/page/2"],
        )

    def test_job_page_coverage_state_marks_complete_when_no_new_jobs_and_no_new_frontier(self) -> None:
        next_state = update_job_page_coverage_state(
            entry_url="https://example.com/careers",
            coverage_state={
                "pendingListingUrls": ["https://example.com/careers/page/2"],
                "visitedListingUrls": ["https://example.com/careers"],
                "coverageComplete": False,
            },
            processed_listing_urls=["https://example.com/careers/page/2"],
            discovered_listing_urls=[],
        )

        self.assertEqual(
            next_state,
            {
                "pendingListingUrls": [],
                "visitedListingUrls": [
                    "https://example.com/careers",
                    "https://example.com/careers/page/2",
                ],
                "coverageComplete": True,
            },
        )

    def test_select_listing_urls_for_processing_prefers_pending_frontier(self) -> None:
        selected = select_listing_urls_for_processing(
            entry_url="https://example.com/careers",
            coverage_state={
                "pendingListingUrls": [
                    "https://example.com/careers/page/2",
                    "https://example.com/careers/page/3",
                ],
                "visitedListingUrls": ["https://example.com/careers"],
                "coverageComplete": False,
            },
            limit=2,
        )

        self.assertEqual(
            selected,
            [
                "https://example.com/careers/page/2",
                "https://example.com/careers/page/3",
            ],
        )

    def test_select_listing_urls_for_processing_reopens_completed_entry_when_no_jobs_materialized(self) -> None:
        selected = select_listing_urls_for_processing(
            entry_url="https://example.com/careers",
            coverage_state={
                "pendingListingUrls": [],
                "visitedListingUrls": ["https://example.com/careers"],
                "coverageComplete": True,
            },
            limit=2,
            allow_entry_retry_when_coverage_complete=True,
        )

        self.assertEqual(selected, ["https://example.com/careers"])

    def test_company_job_search_prompt_targets_company_level_enumeration(self) -> None:
        from jobflow_desktop_app.prompt_assets import load_prompt_asset

        prompt = load_prompt_asset("search_ranking", "company_job_search_prompt.txt")
        self.assertIn("company-level job enumeration task", prompt)
        self.assertIn("same company", prompt)
        self.assertIn("not a candidate-matching task", prompt)
        self.assertIn("Do not invent URLs", prompt)

    def test_fetch_careers_page_jobs_with_trace_uses_ai_enumeration_when_rule_parser_finds_nothing(self) -> None:
        landing_html = """
        <html>
          <body>
            <a href="https://modelon.careers.haileyhr.app/en-GB/job/real-role">Open position</a>
          </body>
        </html>
        """
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Strategic Account Manager - DACH region",
                                    "url": "https://modelon.careers.haileyhr.app/en-GB/job/additional-role",
                                    "location": "Germany",
                                    "summary": "Drive DACH growth.",
                                }
                            ],
                            "nextListingUrls": [],
                        }
                    )
                }
            ]
        )

        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                return_value=(landing_html, "https://modelon.com/company/careers/"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[],
            ),
        ):
            trace = fetch_careers_page_jobs_with_trace(
                "https://modelon.com/company/careers/",
                config=self._config(),
                timeout_seconds=15,
                client=client,
                company_name="Modelon AB",
            )

        self.assertEqual(len(trace.jobs), 1)
        self.assertEqual(trace.jobs[0]["url"], "https://modelon.careers.haileyhr.app/en-GB/job/additional-role")
        self.assertEqual(trace.source_path, "company_page_ai")

    def test_fetch_careers_page_jobs_with_trace_passes_stripped_text_to_ai_enumeration(self) -> None:
        landing_html = """
        <html>
          <head>
            <script>window.__BOOTSTRAP__ = {"noise": true}</script>
          </head>
          <body>
            <h1>Open positions</h1>
            <p>Localization Program Manager role in Berlin.</p>
            <a href="https://example.com/jobs/localization-program-manager">Open position</a>
          </body>
        </html>
        """
        observed_page_text: list[str] = []

        def fake_enumeration(*_args, **kwargs):
            observed_page_text.append(kwargs["page_text"])
            return type("EnumResult", (), {"jobs": [], "next_listing_urls": []})()

        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                return_value=(landing_html, "https://example.com/careers"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[],
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.enumerate_jobs_and_listing_hints_from_careers_page",
                side_effect=fake_enumeration,
            ),
        ):
            fetch_careers_page_jobs_with_trace(
                "https://example.com/careers",
                config=self._config(),
                timeout_seconds=15,
                client=_FakeClient([]),
                company_name="Acme",
            )

        self.assertEqual(len(observed_page_text), 1)
        self.assertIn("Open positions", observed_page_text[0])
        self.assertIn("Localization Program Manager role in Berlin.", observed_page_text[0])
        self.assertNotIn("window.__BOOTSTRAP__", observed_page_text[0])

    def test_fetch_careers_page_jobs_with_trace_merges_parser_and_ai_results_for_better_coverage(self) -> None:
        landing_html = """
        <html>
          <body>
            <a href="https://www.transifex.com/careers/">Careers</a>
          </body>
        </html>
        """
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Full-Stack Developer (Rigi)",
                                    "url": "https://apply.workable.com/transifex/j/EE71278021",
                                    "location": "Remote",
                                    "summary": "Build localization platform features.",
                                },
                                {
                                    "title": "Full-Stack Developer (VCC)",
                                    "url": "https://apply.workable.com/transifex/j/752E3BC4C3",
                                    "location": "Remote",
                                    "summary": "Build localization platform features.",
                                },
                            ],
                            "nextListingUrls": [],
                        }
                    )
                }
            ]
        )

        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                return_value=(landing_html, "https://www.transifex.com/careers/"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[
                    {
                        "title": "Localization Program Manager",
                        "location": "Germany",
                        "url": "https://apply.workable.com/transifex/j/1234567890",
                        "datePosted": "",
                        "summary": "Lead multilingual launches.",
                    }
                ],
            ),
        ):
            trace = fetch_careers_page_jobs_with_trace(
                "https://www.transifex.com/careers/",
                config=self._config(),
                timeout_seconds=15,
                client=client,
                company_name="Transifex",
            )

        self.assertEqual(trace.source_path, "company_page_ai")
        self.assertEqual(
            [job["url"] for job in trace.jobs],
            [
                "https://apply.workable.com/transifex/j/1234567890",
                "https://apply.workable.com/transifex/j/EE71278021",
                "https://apply.workable.com/transifex/j/752E3BC4C3",
            ],
        )

    def test_enumerate_jobs_from_careers_page_can_work_without_page_links(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Localization Program Manager",
                                    "url": "https://apply.workable.com/transifex/j/EE71278021",
                                    "location": "Germany",
                                    "summary": "Lead multilingual launches and localization workflows.",
                                }
                            ]
                        }
                    )
                }
            ]
        )

        jobs = enumerate_jobs_from_careers_page(
            client,
            config=self._config(),
            company_name="Transifex",
            page_url="https://www.transifex.com/careers/",
            page_text="Careers at Transifex. Join our localization technology team.",
            links=[],
            sample_job_urls=["https://apply.workable.com/transifex/j/EE71278021"],
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://apply.workable.com/transifex/j/EE71278021")

    def test_fetch_careers_page_jobs_with_trace_uses_sample_job_urls_when_listing_yields_nothing(self) -> None:
        landing_html = """
        <html>
          <body>
            <a href="https://apply.workable.com/transifex/">Careers</a>
          </body>
        </html>
        """
        sample_detail_html = """
        <html>
          <head><title>Senior Localization Program Manager</title></head>
          <body>
            <h1>Senior Localization Program Manager</h1>
            <p>Location: Germany</p>
            <p>Lead multilingual launches and TMS governance.</p>
          </body>
        </html>
        """

        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
                side_effect=[
                    (landing_html, "https://www.transifex.com/careers/"),
                    (sample_detail_html, "https://apply.workable.com/transifex/j/EE71278021"),
                ],
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.collect_careers_page_job_candidates",
                return_value=[],
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_careers.enumerate_jobs_and_listing_hints_from_careers_page",
                return_value=type(
                    "Enumeration",
                    (),
                    {
                        "jobs": [],
                        "next_listing_urls": [],
                    },
                )(),
            ),
        ):
            trace = fetch_careers_page_jobs_with_trace(
                "https://www.transifex.com/careers/",
                config=self._config(),
                timeout_seconds=15,
                sample_job_urls=["https://apply.workable.com/transifex/j/EE71278021"],
                company_name="Transifex",
            )

        self.assertEqual(trace.source_path, "sample_job_urls")
        self.assertEqual(len(trace.jobs), 1)
        self.assertEqual(trace.jobs[0]["url"], "https://apply.workable.com/transifex/j/EE71278021")
        self.assertEqual(trace.jobs[0]["title"], "Senior Localization Program Manager")
        self.assertTrue(trace.jobs[0]["location"].startswith("Germany"))

    def test_fetch_careers_page_jobs_with_trace_uses_sample_job_urls_when_listing_fetch_fails(self) -> None:
        sample_detail_html = """
        <html>
          <head><title>Senior Localization Program Manager</title></head>
          <body>
            <h1>Senior Localization Program Manager</h1>
            <p>Location: Germany</p>
            <p>Lead multilingual launches and TMS governance.</p>
          </body>
        </html>
        """

        with patch(
            "jobflow_desktop_app.search.companies.company_sources_careers.fetch_text",
            side_effect=[
                RuntimeError("temporary listing outage"),
                (sample_detail_html, "https://apply.workable.com/transifex/j/EE71278021"),
            ],
        ):
            trace = fetch_careers_page_jobs_with_trace(
                "https://www.transifex.com/careers/",
                config=self._config(),
                timeout_seconds=15,
                sample_job_urls=["https://apply.workable.com/transifex/j/EE71278021"],
                company_name="Transifex",
            )

        self.assertEqual(trace.source_path, "sample_job_urls")
        self.assertEqual(len(trace.jobs), 1)
        self.assertEqual(trace.jobs[0]["url"], "https://apply.workable.com/transifex/j/EE71278021")
        self.assertEqual(trace.jobs[0]["title"], "Senior Localization Program Manager")

    def test_collect_careers_page_job_candidates_no_longer_uses_sample_pattern_as_hard_filter(self) -> None:
        html = """
        <html>
          <body>
            <a href="/en/jobs/saved-jobs">Saved jobs</a>
            <a href="/en/jobs/jr310623/customer-success-manager/">Customer Success Manager</a>
            <a href="/en/jobs/interns">Interns</a>
          </body>
        </html>
        """

        jobs = collect_careers_page_job_candidates(
            html,
            "https://careers.salesforce.com/en/jobs/?search=%2F",
            sample_job_urls=[
                "https://careers.salesforce.com/en/jobs/jr333752/customer-success-manager-director-core-sales-service-clouds/"
            ],
        )

        self.assertEqual(
            [job["url"] for job in jobs],
            [
                "https://careers.salesforce.com/en/jobs/jr310623/customer-success-manager",
            ],
        )

    def test_collect_careers_page_job_candidates_accepts_pdf_job_spec_links(self) -> None:
        html = """
        <html>
          <body>
            <a href="/wp-content/uploads/2026/01/Hypermotive-Business-Development-Manager-Job-Specification-27-Jan-2026.pdf">
              Business Development Manager
            </a>
          </body>
        </html>
        """

        jobs = collect_careers_page_job_candidates(
            html,
            "https://www.hyper-motive.com/careers/",
            sample_job_urls=[
                "https://www.hyper-motive.com/wp-content/uploads/2026/01/Hypermotive-Business-Development-Manager-Job-Specification-27-Jan-2026.pdf"
            ],
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Business Development Manager")
        self.assertEqual(
            jobs[0]["url"],
            "https://www.hyper-motive.com/wp-content/uploads/2026/01/Hypermotive-Business-Development-Manager-Job-Specification-27-Jan-2026.pdf",
        )

    def test_fetch_job_details_extracts_text_from_pdf_job_spec(self) -> None:
        with (
            patch(
                "jobflow_desktop_app.search.companies.company_sources_enrichment.fetch_response",
                return_value=(
                    b"%PDF-1.4 fake",
                    "application/pdf",
                    "https://example.com/jobs/hydrogen-system-engineer-job-spec.pdf",
                ),
            ),
            patch(
                "jobflow_desktop_app.search.companies.company_sources_enrichment.extract_pdf_text_from_bytes",
                return_value="Hydrogen System Engineer\nLocation: Berlin\nResponsibilities include MBSE integration and validation.",
            ),
        ):
            details = fetch_job_details(
                "https://example.com/jobs/hydrogen-system-engineer-job-spec.pdf",
                config=self._config(),
                timeout_seconds=15,
            )

        self.assertTrue(details["ok"])
        self.assertEqual(details["contentType"], "application/pdf")
        self.assertEqual(
            details["extracted"]["description"],
            "Hydrogen System Engineer\nLocation: Berlin\nResponsibilities include MBSE integration and validation.",
        )
        self.assertEqual(details["extracted"]["location"], "Berlin")

    def test_fetch_job_details_treats_antibot_interstitial_as_failed_detail_fetch(self) -> None:
        html = """
        <html>
          <head><title>JavaScript is disabled</title></head>
          <body>
            JavaScript is disabled.
            In order to continue, we need to verify that you're not a robot.
            This requires JavaScript. Enable JavaScript and then reload the page.
          </body>
        </html>
        """
        with patch(
            "jobflow_desktop_app.search.companies.company_sources_enrichment.fetch_response",
            return_value=(
                html.encode("utf-8"),
                "text/html",
                "https://careers.example.com/jobs/localization-manager",
            ),
        ):
            details = fetch_job_details(
                "https://careers.example.com/jobs/localization-manager",
                config=self._config(),
                timeout_seconds=15,
            )

        self.assertFalse(details["ok"])
        self.assertEqual(details["rawText"], "")
        self.assertEqual(details["extracted"], {})
        self.assertIn("Anti-bot interstitial", details["error"])

    def test_company_search_fallback_helpers_follow_region_and_company_scope(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:JP"]
        company = {
            "name": "Acme Hydrogen",
            "tags": ["hydrogen", "region:JP"],
        }

        self.assertTrue(company_search_fallback_enabled(company, config))
        query = build_company_search_fallback_query(company, config)
        self.assertIn("Acme Hydrogen careers", query)
        self.assertIn("jobs", query.casefold())
        self.assertNotIn("Hydrogen durability engineer", query)
        self.assertNotIn("remote", query.casefold())

    def test_company_search_fallback_requires_region_match_without_keyword_override(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:JP"]
        company = {
            "name": "Acme Hydrogen",
            "tags": ["hydrogen", "region:DE"],
        }

        self.assertFalse(company_search_fallback_enabled(company, config))

    def test_build_company_search_fallback_query_stays_company_scoped(self) -> None:
        config = self._config()
        config["candidate"] = {
            "targetRoles": [
                {
                    "displayName": "Localization Project Manager|本地化项目经理",
                    "descriptionEn": "Lead multilingual content launches and vendor operations.",
                },
                {
                    "displayName": "Translation Operations Manager|翻译运营经理",
                },
            ],
            "locationPreference": "Berlin, Germany\nRemote",
            "semanticProfile": {
                "job_fit_core_terms": ["software localization", "translation management systems"],
                "job_fit_support_terms": ["linguistic quality assurance", "memoQ", "Smartling", "SQL"],
            },
        }
        company = {
            "name": "Lionbridge",
            "tags": ["region:DE"],
        }

        query = build_company_search_fallback_query(company, config)

        self.assertIn("Lionbridge careers", query)
        self.assertIn("jobs", query.casefold())
        self.assertNotIn("|", query)
        self.assertNotIn("Localization Project Manager", query)
        self.assertNotIn("software localization", query)
        self.assertNotIn("Translation Operations Manager", query)
        self.assertNotIn("remote", query.casefold())

    def test_build_company_search_fallback_query_prefers_jobs_host_site_constraint(self) -> None:
        config = self._config()
        config["candidate"] = {
            "targetRoles": [{"displayName": "Customer Success Manager"}],
            "locationPreference": "Berlin, Germany\nRemote EU",
            "semanticProfile": {
                "job_fit_core_terms": ["customer onboarding"],
                "job_fit_support_terms": ["client retention", "CRM"],
            },
        }
        company = {
            "name": "Salesforce",
            "website": "https://www.salesforce.com",
            "jobsPageUrl": "https://careers.salesforce.com/en/jobs/?search=%2F",
        }

        query = build_company_search_fallback_query(company, config)

        self.assertIn("site:careers.salesforce.com", query)
        self.assertIn("Salesforce careers", query)
        self.assertNotIn("Customer Success Manager", query)

    def test_collect_supported_company_source_jobs_uses_web_search_fallback_with_client(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:DE"]
        companies = [
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example.com",
                "tags": ["hydrogen", "region:DE"],
            }
        ]
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "website": "https://acme.example.com",
                            "jobsPageUrl": "",
                            "pageType": "not_found",
                            "careersUrl": "",
                            "sampleJobUrls": [],
                        }
                    )
                },
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Fuel Cell Engineer",
                                    "company": "Acme Hydrogen",
                                    "location": "Berlin",
                                    "url": "https://acme.example.com/jobs/123",
                                    "summary": "Develop PEM durability methods.",
                                    "datePosted": "2026-04-12",
                                    "availabilityHint": "active",
                                }
                            ]
                        }
                    )
                },
            ]
        )

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_job_details",
                return_value={
                    "ok": True,
                    "status": 200,
                    "finalUrl": "https://acme.example.com/jobs/123",
                    "redirected": False,
                    "fetchedAt": "2026-04-15T10:00:00+00:00",
                    "applyUrl": "https://acme.example.com/jobs/123/apply",
                    "rawText": "Detailed job description",
                    "locationHint": "Berlin",
                    "extracted": {
                        "title": "Fuel Cell Engineer",
                        "company": "Acme Hydrogen",
                        "location": "Berlin",
                        "datePosted": "2026-04-12",
                        "description": "Detailed job description for durability work.",
                    },
                },
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=client,
            )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(result.remaining_companies, [])
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["sourceType"], "company_search")
        self.assertIn("jd", result.jobs[0])
        self.assertEqual(result.jobs[0]["jd"]["applyUrl"], "https://acme.example.com/jobs/123/apply")
        self.assertEqual(result.processed_companies[0]["sourceDiagnostics"]["sourcePath"], "company_search")
        self.assertEqual(result.processed_companies[0]["sourceDiagnostics"]["reason"], "queued_jobs")

    def test_collect_supported_company_source_jobs_reuses_cached_company_search_results(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:DE"]
        company = {
            "name": "Acme Hydrogen",
            "website": "https://acme.example.com",
            "careersUrl": "https://acme.example.com/careers",
            "jobsPageUrl": "https://acme.example.com/careers",
            "jobsPageType": "jobs_listing",
            "sampleJobUrls": ["https://acme.example.com/jobs/123"],
            "tags": ["hydrogen", "region:DE"],
            "snapshotComplete": True,
        }
        cache_key = build_company_search_cache_key(
            company_name="Acme Hydrogen",
            company_website="https://acme.example.com",
            jobs_page_url="https://acme.example.com/careers",
            page_type="jobs_listing",
            sample_job_urls=["https://acme.example.com/jobs/123"],
            query="site:acme.example.com Acme Hydrogen careers jobs",
        )
        company["jobPageCoverage"] = {
            "companySearchCache": {
                cache_key: {
                    "query": "site:acme.example.com Acme Hydrogen careers jobs",
                    "companyWebsite": "https://acme.example.com",
                    "jobsPageUrl": "https://acme.example.com/careers",
                    "pageType": "jobs_listing",
                    "sampleJobUrls": ["https://acme.example.com/jobs/123"],
                    "jobs": [
                        {
                            "title": "Fuel Cell Engineer",
                            "company": "Acme Hydrogen",
                            "location": "Berlin",
                            "url": "https://acme.example.com/jobs/123",
                            "summary": "Develop PEM durability methods.",
                            "datePosted": "2026-04-12",
                            "availabilityHint": "active",
                            "source": "company_search:Acme Hydrogen",
                            "sourceType": "company_search",
                        }
                    ],
                }
            }
        }

        empty_trace = type(
            "Trace",
            (),
            {
                "jobs": [],
                "followed_links": [],
                "source_path": "company_page",
                "next_listing_urls": [],
                "listing_page_cache_entry": None,
            },
        )()

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
                return_value=empty_trace,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.openai_search_jobs",
                side_effect=AssertionError("company_search cache should avoid a fresh AI call"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_job_details",
                return_value={
                    "ok": True,
                    "status": 200,
                    "finalUrl": "https://acme.example.com/jobs/123",
                    "redirected": False,
                    "fetchedAt": "2026-04-15T10:00:00+00:00",
                    "applyUrl": "https://acme.example.com/jobs/123/apply",
                    "rawText": "Detailed job description",
                    "locationHint": "Berlin",
                    "extracted": {
                        "title": "Fuel Cell Engineer",
                        "company": "Acme Hydrogen",
                        "location": "Berlin",
                        "datePosted": "2026-04-12",
                        "description": "Detailed job description for durability work.",
                    },
                },
            ),
        ):
            result = collect_supported_company_source_jobs(
                [company],
                config=config,
                client=object(),
            )

        diagnostics = result.processed_companies[0]["sourceDiagnostics"]
        self.assertEqual(diagnostics["usedCompanySearch"], 1)
        self.assertEqual(diagnostics["cachedCompanySearchReused"], 1)
        self.assertEqual(result.jobs[0]["url"], "https://acme.example.com/jobs/123")

    def test_build_company_search_cache_key_changes_when_known_job_urls_change(self) -> None:
        base_kwargs = {
            "company_name": "Acme Hydrogen",
            "company_website": "https://acme.example.com",
            "jobs_page_url": "https://acme.example.com/careers",
            "page_type": "jobs_listing",
            "sample_job_urls": ["https://acme.example.com/jobs/123"],
            "query": "site:acme.example.com Acme Hydrogen careers jobs",
        }
        without_known = build_company_search_cache_key(**base_kwargs, known_job_urls=[])
        with_known = build_company_search_cache_key(
            **base_kwargs,
            known_job_urls=["https://acme.example.com/jobs/123"],
        )

        self.assertNotEqual(without_known, with_known)

    def test_openai_search_jobs_retries_once_and_includes_known_job_urls(self) -> None:
        client = _FlakyClient(
            [
                RuntimeError("temporary timeout"),
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Fuel Cell Engineer",
                                    "company": "Acme Hydrogen",
                                    "location": "Berlin",
                                    "url": "https://acme.example.com/jobs/456",
                                    "summary": "Develop PEM durability methods.",
                                    "datePosted": "2026-04-12",
                                    "availabilityHint": "active",
                                }
                            ]
                        }
                    )
                },
            ]
        )

        jobs = openai_search_jobs(
            client,
            config=self._config(),
            company_name="Acme Hydrogen",
            company_website="https://acme.example.com",
            jobs_page_url="https://acme.example.com/careers",
            page_type="jobs_listing",
            sample_job_urls=["https://acme.example.com/jobs/123"],
            known_job_urls=["https://acme.example.com/jobs/123"],
            query="site:acme.example.com Acme Hydrogen careers jobs",
        )

        self.assertEqual(len(client.requests), 2)
        request_payload = json.dumps(client.requests[-1], ensure_ascii=False)
        self.assertIn("https://acme.example.com/jobs/123", request_payload)
        self.assertEqual([job["url"] for job in jobs], ["https://acme.example.com/jobs/456"])

    def test_collect_supported_company_source_jobs_caps_company_search_timeout_per_company(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:DE"]
        companies = [
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example.com",
                "tags": ["hydrogen", "region:DE"],
            }
        ]
        client = _FakeClient([])
        client.timeout_seconds = 240
        observed_timeouts: list[int] = []

        def fake_openai_search_jobs(_client, **kwargs):
            observed_timeouts.append(int(getattr(_client, "timeout_seconds", 0)))
            return [
                {
                    "title": "Fuel Cell Engineer",
                    "company": "Acme Hydrogen",
                    "location": "Berlin",
                    "url": "https://acme.example.com/jobs/123",
                    "summary": "Develop PEM durability methods.",
                    "datePosted": "2026-04-12",
                    "availabilityHint": "active",
                }
            ]

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.discover_company_careers",
                return_value={
                    "website": "https://acme.example.com",
                    "jobsPageUrl": "",
                    "pageType": "not_found",
                    "careersUrl": "",
                    "sampleJobUrls": [],
                },
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.openai_search_jobs",
                side_effect=fake_openai_search_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_job_details",
                return_value={
                    "ok": True,
                    "status": 200,
                    "finalUrl": "https://acme.example.com/jobs/123",
                    "redirected": False,
                    "fetchedAt": "2026-04-15T10:00:00+00:00",
                    "applyUrl": "https://acme.example.com/jobs/123/apply",
                    "rawText": "Detailed job description",
                    "locationHint": "Berlin",
                    "extracted": {
                        "title": "Fuel Cell Engineer",
                        "company": "Acme Hydrogen",
                        "location": "Berlin",
                        "datePosted": "2026-04-12",
                        "description": "Detailed job description for durability work.",
                    },
                },
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=client,
                timeout_seconds=240,
            )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(observed_timeouts, [COMPANY_SEARCH_FALLBACK_TIMEOUT_SECONDS])
        self.assertEqual(client.timeout_seconds, 240)

    def test_collect_supported_company_source_jobs_propagates_stage_timeout_to_ats_fetch_and_detail_enrichment(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            }
        ]
        observed_fetch_timeouts: list[int | None] = []
        observed_detail_timeouts: list[int | None] = []
        fetched_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Develop PEM durability test plans.",
            }
        ]

        def fake_fetch_supported_ats_jobs(_ats_type, _ats_id, **kwargs):
            observed_fetch_timeouts.append(kwargs.get("timeout_seconds"))
            return list(fetched_jobs)

        def fake_enrich_selected_jobs_with_details(jobs, **kwargs):
            observed_detail_timeouts.append(kwargs.get("timeout_seconds"))
            return list(jobs), kwargs.get("already_fetched_count", 0)

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
                side_effect=fake_fetch_supported_ats_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.enrich_selected_jobs_with_details",
                side_effect=fake_enrich_selected_jobs_with_details,
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                timeout_seconds=9,
        )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(observed_fetch_timeouts, [9])
        self.assertEqual(len(observed_detail_timeouts), 1)
        self.assertGreaterEqual(observed_detail_timeouts[0], 1)
        self.assertLessEqual(observed_detail_timeouts[0], 9)

    def test_collect_supported_company_source_jobs_propagates_stage_timeout_to_generic_ai_calls(self) -> None:
        client = _FakeClient([])
        client.timeout_seconds = 240
        companies = [
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example.com",
                "tags": ["hydrogen"],
            }
        ]
        observed_discovery_timeouts: list[int] = []
        observed_listing_timeouts: list[tuple[int, int | None]] = []
        observed_prerank_timeouts: list[int] = []

        def fake_discover_company_careers(_client, **_kwargs):
            observed_discovery_timeouts.append(int(getattr(_client, "timeout_seconds", 0)))
            return {
                "website": "https://acme.example.com",
                "jobsPageUrl": "https://acme.example.com/careers",
                "pageType": "jobs_listing",
                "careersUrl": "https://acme.example.com/careers",
                "sampleJobUrls": [],
            }

        def fake_fetch_careers_page_jobs_with_trace(_url, **kwargs):
            observed_listing_timeouts.append(
                (
                    int(getattr(kwargs.get("client"), "timeout_seconds", 0)),
                    kwargs.get("timeout_seconds"),
                )
            )
            return CareersPageFetchTrace(
                jobs=[
                    {
                        "title": "Localization Program Manager",
                        "company": "Acme Hydrogen",
                        "location": "Berlin",
                        "url": "https://acme.example.com/jobs/1",
                        "summary": "Lead multilingual launches.",
                        "datePosted": "2026-04-12",
                    }
                ],
                followed_links=[],
                source_path="company_page",
                next_listing_urls=[],
            )

        def fake_prerank_company_jobs_for_candidate(_client, **kwargs):
            observed_prerank_timeouts.append(int(getattr(_client, "timeout_seconds", 0)))
            return list(kwargs["jobs"])

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.discover_company_careers",
                side_effect=fake_discover_company_careers,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
                side_effect=fake_fetch_careers_page_jobs_with_trace,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=fake_prerank_company_jobs_for_candidate,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.enrich_selected_jobs_with_details",
                side_effect=lambda jobs, **kwargs: (list(jobs), kwargs.get("already_fetched_count", 0)),
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                client=client,
                timeout_seconds=9,
            )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(observed_discovery_timeouts, [9])
        self.assertEqual(observed_listing_timeouts, [(9, 9)])
        self.assertEqual(len(observed_prerank_timeouts), 1)
        self.assertGreaterEqual(observed_prerank_timeouts[0], 1)
        self.assertLessEqual(observed_prerank_timeouts[0], 9)
        self.assertEqual(client.timeout_seconds, 240)

    def test_collect_supported_company_source_jobs_passes_stage_budget_to_company_search_fallback(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:DE"]
        companies = [
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example.com",
                "tags": ["hydrogen", "region:DE"],
            }
        ]
        observed_budgets: list[bool] = []

        def fake_company_fallback(**kwargs):
            budget = kwargs.get("budget")
            observed_budgets.append(
                budget is not None and hasattr(budget, "remaining_timeout") and hasattr(budget, "capped_timeout")
            )
            return [], {"cacheHit": False}

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.discover_company_careers",
                return_value={
                    "website": "https://acme.example.com",
                    "jobsPageUrl": "",
                    "pageType": "not_found",
                    "careersUrl": "",
                    "sampleJobUrls": [],
                },
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources._search_jobs_with_company_fallback",
                side_effect=fake_company_fallback,
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=_FakeClient([]),
                timeout_seconds=30,
            )

        self.assertEqual(result.jobs, [])
        self.assertEqual(observed_budgets, [True])

    def test_collect_supported_company_source_jobs_reopens_completed_non_ats_entry_when_no_jobs_materialized(self) -> None:
        companies = [
            {
                "name": "Duolingo",
                "website": "https://careers.duolingo.com",
                "jobsPageUrl": "https://duolingo.breezy.hr/",
                "careersUrl": "https://duolingo.breezy.hr/",
                "jobsPageType": "jobs_listing",
                "sampleJobUrls": [
                    "https://duolingo.breezy.hr/p/3d7ea54d18ea-creator-community-program-manager-contract"
                ],
                "snapshotComplete": False,
                "jobPageCoverage": {
                    "pendingListingUrls": [],
                    "visitedListingUrls": ["https://duolingo.breezy.hr/"],
                    "coverageComplete": True,
                },
            }
        ]
        fake_trace = CareersPageFetchTrace(
            jobs=[
                {
                    "title": "Localization Program Manager",
                    "company": "Duolingo",
                    "location": "Remote",
                    "url": "https://duolingo.breezy.hr/p/3d7ea54d18ea-creator-community-program-manager-contract",
                    "summary": "Lead multilingual launches.",
                    "datePosted": "",
                }
            ],
            followed_links=[],
            source_path="sample_job_urls",
            next_listing_urls=[],
        )

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
                return_value=fake_trace,
            ) as fetch_trace,
            patch(
                "jobflow_desktop_app.search.companies.sources.openai_search_jobs",
            ) as company_search,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                client=None,
            )

        self.assertEqual(fetch_trace.call_count, 1)
        company_search.assert_not_called()
        diagnostics = result.processed_companies[0]["sourceDiagnostics"]
        self.assertEqual(diagnostics["listingPagesProcessed"], 1)
        self.assertEqual(diagnostics["rawJobsFetched"], 1)
        self.assertEqual(diagnostics["sourcePath"], "sample_job_urls")

    def test_collect_supported_company_source_jobs_reuses_cached_careers_discovery_result(self) -> None:
        config = self._config()
        company = {
            "name": "Acme Hydrogen",
            "careersDiscoveryCache": {
                "website": "https://acme.example.com",
                "jobsPageUrl": "https://acme.example.com/careers",
                "pageType": "jobs_listing",
                "careersUrl": "https://acme.example.com/careers",
                "sampleJobUrls": ["https://acme.example.com/jobs/123"],
            },
        }
        trace = type(
            "Trace",
            (),
            {
                "jobs": [
                    {
                        "title": "Fuel Cell Engineer",
                        "company": "Acme Hydrogen",
                        "location": "Berlin",
                        "url": "https://acme.example.com/jobs/123",
                        "summary": "Develop PEM durability methods.",
                        "datePosted": "2026-04-12",
                        "availabilityHint": "active",
                        "aiEnumerated": True,
                    }
                ],
                "followed_links": [],
                "source_path": "company_page_ai",
                "next_listing_urls": [],
                "listing_page_cache_entry": None,
            },
        )()

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.discover_company_careers",
                side_effect=AssertionError("cached careers discovery should avoid a fresh AI call"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
                return_value=trace,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_job_details",
                return_value={
                    "ok": True,
                    "status": 200,
                    "finalUrl": "https://acme.example.com/jobs/123",
                    "redirected": False,
                    "fetchedAt": "2026-04-15T10:00:00+00:00",
                    "applyUrl": "https://acme.example.com/jobs/123/apply",
                    "rawText": "Detailed job description",
                    "locationHint": "Berlin",
                    "extracted": {
                        "title": "Fuel Cell Engineer",
                        "company": "Acme Hydrogen",
                        "location": "Berlin",
                        "datePosted": "2026-04-12",
                        "description": "Detailed job description for durability work.",
                    },
                },
            ),
        ):
            result = collect_supported_company_source_jobs(
                [company],
                config=config,
                client=object(),
            )

        processed = result.processed_companies[0]
        diagnostics = processed["sourceDiagnostics"]
        self.assertEqual(diagnostics["cachedCareersDiscoveryReused"], 1)
        self.assertEqual(processed["website"], "https://acme.example.com")
        self.assertEqual(processed["careersUrl"], "https://acme.example.com/careers")

    def test_collect_supported_company_source_jobs_defer_and_then_complete_listing_frontier(self) -> None:
        company = {
            "name": "Example Co",
            "website": "https://example.com",
            "careersUrl": "https://example.com/careers",
        }

        first_trace = type(
            "Trace",
            (),
            {
                "jobs": [],
                "followed_links": [],
                "source_path": "company_page_ai",
                "next_listing_urls": ["https://example.com/careers/page/2"],
            },
        )()
        second_trace = type(
            "Trace",
            (),
            {
                "jobs": [],
                "followed_links": [],
                "source_path": "company_page_ai",
                "next_listing_urls": [],
            },
        )()

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
            return_value=first_trace,
        ):
            first_result = collect_supported_company_source_jobs(
                [company],
                config=self._config(),
                client=None,
            )

        self.assertEqual(first_result.jobs, [])
        self.assertEqual(first_result.companies_handled_count, 0)
        self.assertEqual(len(first_result.remaining_companies), 1)
        self.assertFalse(first_result.remaining_companies[0]["snapshotComplete"])
        self.assertEqual(
            first_result.remaining_companies[0]["jobPageCoverage"],
            {
                "pendingListingUrls": ["https://example.com/careers/page/2"],
                "visitedListingUrls": ["https://example.com/careers"],
                "coverageComplete": False,
            },
        )
        self.assertEqual(
            first_result.remaining_companies[0]["sourceDiagnostics"]["reason"],
            "listing_frontier_pending",
        )

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
            return_value=second_trace,
        ):
            second_result = collect_supported_company_source_jobs(
                first_result.remaining_companies,
                config=self._config(),
                client=None,
            )

        self.assertEqual(second_result.jobs, [])
        self.assertEqual(second_result.remaining_companies, [])
        self.assertEqual(second_result.companies_handled_count, 1)
        self.assertTrue(second_result.processed_companies[0]["snapshotComplete"])
        self.assertEqual(
            second_result.processed_companies[0]["jobPageCoverage"],
            {
                "pendingListingUrls": [],
                "visitedListingUrls": [
                    "https://example.com/careers",
                    "https://example.com/careers/page/2",
                ],
                "coverageComplete": True,
            },
        )

    def test_collect_supported_company_source_jobs_records_attempt_fields_when_snapshot_filters_to_empty(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example.com",
                "jobsPageUrl": "https://acme.example.com/careers",
                "careersUrl": "https://acme.example.com/careers",
                "jobsPageType": "jobs_listing",
                "snapshotJobUrls": ["https://acme.example.com/jobs/old-role"],
            }
        ]
        fake_trace = CareersPageFetchTrace(
            jobs=[
                {
                    "title": "Saved Jobs",
                    "company": "Acme Hydrogen",
                    "location": "",
                    "url": "https://acme.example.com/jobs/saved",
                    "summary": "Saved jobs page.",
                    "datePosted": "",
                }
            ],
            followed_links=[],
            source_path="company_page",
            next_listing_urls=[],
        )

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
            return_value=fake_trace,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                timeout_seconds=30,
            )

        self.assertEqual(result.jobs, [])
        self.assertEqual(result.companies_handled_count, 1)
        company = result.processed_companies[0]
        self.assertIn("lastSearchedAt", company)
        self.assertEqual(company["lastJobsFoundCount"], 0)
        self.assertEqual(company["lastNewJobsCount"], 0)
        self.assertNotIn("snapshotJobUrls", company)
        self.assertTrue(company["snapshotComplete"])
        self.assertEqual(company["sourceDiagnostics"]["reason"], "all_jobs_filtered")

    def test_collect_supported_company_source_jobs_filters_company_search_results_by_sample_urls(self) -> None:
        config = self._config()
        config["candidate"] = {
            "targetRoles": [{"displayName": "Customer Success Manager"}],
            "locationPreference": "Berlin / Remote EU",
            "semanticProfile": {
                "job_fit_core_terms": ["customer onboarding"],
                "job_fit_support_terms": ["client retention", "CRM"],
            },
        }
        companies = [
            {
                "name": "Salesforce",
                "website": "https://www.salesforce.com",
                "sampleJobUrls": [
                    "https://careers.salesforce.com/en/jobs/jr333752/customer-success-manager-director-core-sales-service-clouds/"
                ],
                "tags": ["region:DE"],
            }
        ]
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "website": "https://www.salesforce.com",
                            "jobsPageUrl": "",
                            "pageType": "not_found",
                            "careersUrl": "",
                            "sampleJobUrls": [],
                        }
                    )
                },
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Customer Success Manager",
                                    "company": "Salesforce",
                                    "location": "Berlin",
                                    "url": "https://careers.salesforce.com/en/jobs/jr310623/customer-success-manager/",
                                    "summary": "Drive onboarding and customer adoption.",
                                    "datePosted": "2026-04-12",
                                    "availabilityHint": "active",
                                },
                                {
                                    "title": "Customer Success Manager",
                                    "company": "Salesforce",
                                    "location": "Berlin",
                                    "url": "https://careers.salesforce.com/en/jobs/saved-jobs/customer-success-manager/",
                                    "summary": "Saved jobs page",
                                    "datePosted": "2026-04-12",
                                    "availabilityHint": "active",
                                },
                            ]
                        }
                    )
                },
            ]
        )

        with patch(
            "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
            side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
        ), patch(
            "jobflow_desktop_app.search.companies.sources.fetch_job_details",
            return_value={
                "ok": True,
                "status": 200,
                "finalUrl": "https://careers.salesforce.com/en/jobs/jr310623/customer-success-manager/",
                "redirected": False,
                "fetchedAt": "2026-04-15T10:00:00+00:00",
                "applyUrl": "https://careers.salesforce.com/en/jobs/jr310623/customer-success-manager/apply",
                "rawText": "Customer Success Manager role",
                "locationHint": "Berlin",
                "extracted": {
                    "title": "Customer Success Manager",
                    "company": "Salesforce",
                    "location": "Berlin",
                    "datePosted": "2026-04-12",
                    "description": "Drive onboarding and customer adoption.",
                },
            },
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=client,
            )

        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(
            result.jobs[0]["url"],
            "https://careers.salesforce.com/en/jobs/jr310623/customer-success-manager",
        )

    def test_collect_supported_company_source_jobs_prefers_ai_discovered_jobs_page_url(self) -> None:
        config = self._config()
        companies = [
            {
                "name": "ServiceNow",
                "website": "",
                "careersUrl": "",
                "tags": ["region:DE"],
            }
        ]
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "website": "https://www.servicenow.com",
                            "jobsPageUrl": "https://careers.servicenow.com/jobs",
                            "pageType": "jobs_listing",
                            "careersUrl": "https://careers.servicenow.com/jobs",
                            "sampleJobUrls": [
                                "https://careers.servicenow.com/jobs/744000090460686/hydrogen-systems-engineer/"
                            ],
                        }
                    )
                }
            ]
        )

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
                return_value=type(
                    "Trace",
                    (),
                    {
                        "jobs": [
                            {
                                "title": "Hydrogen Systems Engineer",
                                "location": "Berlin",
                                "url": "https://careers.servicenow.com/jobs/744000090460686/hydrogen-systems-engineer/",
                                "datePosted": "2026-04-18T00:00:00Z",
                                "summary": "Lead hydrogen durability validation and systems integration.",
                            }
                        ],
                        "followed_links": [],
                        "source_path": "jobs_listing",
                        "next_listing_urls": [],
                    },
                )(),
            ) as fetch_trace,
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=client,
            )

        fetch_trace.assert_called_once()
        self.assertEqual(fetch_trace.call_args.args[0], "https://careers.servicenow.com/jobs")
        processed_company = result.processed_companies[0]
        self.assertEqual(processed_company["careersUrl"], "https://careers.servicenow.com/jobs")
        self.assertEqual(processed_company["jobsPageType"], "jobs_listing")
        self.assertEqual(
            processed_company["sampleJobUrls"],
            ["https://careers.servicenow.com/jobs/744000090460686/hydrogen-systems-engineer"],
        )
        self.assertEqual(processed_company["sourceDiagnostics"]["jobsPageType"], "jobs_listing")

    def test_collect_supported_company_source_jobs_records_ats_filter_diagnostics(self) -> None:
        companies = [
            {
                "name": "Acme Careers",
                "careersUrl": "https://boards.greenhouse.io/acme",
            }
        ]
        fetched_jobs = [
            {
                "title": "Localization Program Manager",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Lead localization launches.",
            },
            {
                "title": "Old Job",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/2",
                "datePosted": "2020-01-01T00:00:00Z",
                "summary": "Outdated opening.",
            },
            {
                "title": "Localization Program Manager",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Duplicate entry.",
            },
            {
                "title": "Closed Job",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/3",
                "datePosted": "2026-04-10T00:00:00Z",
                "summary": "Already closed.",
                "availabilityHint": "closed",
            },
        ]

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
            return_value=fetched_jobs,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
            )

        self.assertEqual(result.companies_handled_count, 1)
        company = result.processed_companies[0]
        diagnostics = company["sourceDiagnostics"]
        self.assertEqual(diagnostics["rawJobsFetched"], 4)
        self.assertEqual(diagnostics["filteredStale"], 1)
        self.assertEqual(diagnostics["filteredDuplicate"], 1)
        self.assertEqual(diagnostics["filteredUnavailable"], 1)
        self.assertEqual(diagnostics["snapshotJobs"], 1)
        self.assertEqual(diagnostics["reason"], "queued_jobs")

    def test_collect_supported_company_source_jobs_marks_no_careers_page_reason(self) -> None:
        companies = [
            {
                "name": "No Careers Inc",
                "website": "",
                "careersUrl": "",
                "tags": ["region:DE"],
            }
        ]

        result = collect_supported_company_source_jobs(
            companies,
            config=self._config(),
            client=None,
        )

        self.assertEqual(result.companies_handled_count, 1)
        company = result.processed_companies[0]
        self.assertEqual(company["sourceDiagnostics"]["reason"], "no_careers_page")
        self.assertEqual(company["lastJobsFoundCount"], 0)

    def test_collect_supported_company_source_jobs_marks_transient_lookup_failure(self) -> None:
        companies = [
            {
                "name": "Lookup Error Inc",
                "website": "https://lookup-error.example.com",
                "careersUrl": "",
                "tags": ["region:DE"],
            }
        ]

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.discover_company_careers",
                side_effect=RuntimeError("temporary company lookup error"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources._search_jobs_with_company_fallback",
                side_effect=RuntimeError("temporary fallback error"),
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                client=object(),
            )

        self.assertEqual(result.companies_handled_count, 0)
        self.assertEqual(len(result.remaining_companies), 1)
        self.assertEqual(
            result.remaining_companies[0]["sourceDiagnostics"]["reason"],
            "transient_fetch_error",
        )

    def test_collect_supported_company_source_jobs_skips_fully_analyzed_ats_snapshot_jobs(self) -> None:
        companies = [
            {
                "name": "Acme Careers",
                "careersUrl": "https://boards.greenhouse.io/acme",
            }
        ]
        fetched_jobs = [
            {
                "title": "Localization Program Manager",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Lead localization launches.",
            }
        ]
        existing_jobs = [
            {
                "title": "Localization Program Manager",
                "company": "Acme Careers",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "analysis": {"overallScore": 74, "recommend": False},
            }
        ]

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
            return_value=fetched_jobs,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                existing_jobs=existing_jobs,
            )

        self.assertEqual(result.jobs, [])
        self.assertEqual(result.companies_handled_count, 1)
        company = result.processed_companies[0]
        diagnostics = company["sourceDiagnostics"]
        self.assertEqual(diagnostics["snapshotJobs"], 1)
        self.assertEqual(diagnostics["completedJobsExcluded"], 1)
        self.assertEqual(diagnostics["selectedJobs"], 0)
        self.assertEqual(diagnostics["reason"], "all_snapshot_jobs_already_analyzed")

    def test_collect_supported_company_source_jobs_records_missing_url_and_title_filters(self) -> None:
        companies = [
            {
                "name": "Acme Careers",
                "careersUrl": "https://boards.greenhouse.io/acme",
            }
        ]
        fetched_jobs = [
            {
                "title": "",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/missing-title",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Bad row missing title.",
            },
            {
                "title": "Missing URL",
                "location": "Berlin",
                "url": "",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Bad row missing url.",
            },
            {
                "title": "Localization Program Manager",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Lead localization launches.",
            },
        ]

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
            return_value=fetched_jobs,
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
            )

        self.assertEqual(result.companies_handled_count, 1)
        diagnostics = result.processed_companies[0]["sourceDiagnostics"]
        self.assertEqual(diagnostics["filteredMissingTitle"], 1)
        self.assertEqual(diagnostics["filteredMissingUrl"], 1)
        self.assertEqual(diagnostics["snapshotJobs"], 1)
        self.assertEqual(diagnostics["reason"], "queued_jobs")

    def test_collect_supported_company_source_jobs_prefers_careers_page_before_web_search_fallback(self) -> None:
        config = self._config()
        config["candidate"] = {
            "targetRoles": [{"displayName": "Localization Project Manager"}],
            "locationPreference": "Berlin / Remote Germany",
            "semanticProfile": {
                "job_fit_core_terms": ["software localization"],
                "job_fit_support_terms": ["vendor workflows", "memoQ"],
            },
        }
        companies = [
            {
                "name": "Lokalise",
                "website": "https://lokalise.com",
                "careersUrl": "https://lokalise.com/careers",
                "tags": ["source:web", "region:DE"],
                "sourceEvidence": {"webSearch": {"query": "Localization platforms employers"}},
            }
        ]
        client = _FakeClient([])

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
                return_value=type(
                    "Trace",
                    (),
                    {
                        "jobs": [
                            {
                                "title": "Localization Project Manager",
                                "company": "Lokalise",
                                "location": "Berlin",
                                "url": "https://lokalise.com/careers/jobs/123",
                                "summary": "Lead localization operations and vendor workflows.",
                                "datePosted": "2026-04-12",
                                "availabilityHint": "active",
                            }
                        ],
                        "followed_links": [],
                        "source_path": "jobs_listing",
                        "next_listing_urls": [],
                    },
                )(),
            ) as fetch_trace,
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.openai_search_jobs",
                side_effect=AssertionError("company_search should only run as a last fallback"),
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=client,
            )

        fetch_trace.assert_called_once()
        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["company"], "Lokalise")
        self.assertEqual(
            result.processed_companies[0]["sourceDiagnostics"]["sourcePath"],
            "jobs_listing",
        )

    def test_collect_supported_company_source_jobs_falls_back_when_careers_links_only_yield_filtered_noise(self) -> None:
        config = self._config()
        companies = [
            {
                "name": "Salesforce",
                "website": "https://www.salesforce.com",
                "careersUrl": "https://careers.salesforce.com/en/jobs",
                "jobsPageUrl": "https://careers.salesforce.com/en/jobs",
                "jobsPageType": "jobs_listing",
                "tags": ["region:DE"],
                "sourceEvidence": {"webSearch": {"query": "enterprise software employers"}},
            }
        ]
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "jobs": [
                                {
                                    "title": "Customer Success Manager",
                                    "company": "Salesforce",
                                    "location": "Berlin",
                                    "url": "https://careers.salesforce.com/en/jobs/jr312342/customer-success-manager-core-clouds-public-sector-nonprofit/",
                                    "summary": "Drive onboarding and customer adoption for enterprise customers.",
                                    "datePosted": "2026-04-12",
                                    "availabilityHint": "active",
                                }
                            ]
                        }
                    )
                }
            ]
        )

        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
            return_value=type(
                "Trace",
                (),
                {
                    "jobs": [
                        {
                            "title": "Saved jobs",
                            "company": "Salesforce",
                            "location": "",
                            "url": "https://careers.salesforce.com/en/jobs/saved-jobs",
                            "datePosted": "2026-04-18T00:00:00Z",
                            "summary": "",
                            "availabilityHint": "",
                        }
                    ],
                    "followed_links": [],
                    "source_path": "jobs_listing",
                    "next_listing_urls": [],
                },
            )(),
        ), patch(
            "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
            side_effect=lambda _client, **kwargs: list(kwargs["jobs"]),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=client,
            )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["title"], "Customer Success Manager")
        self.assertEqual(
            result.processed_companies[0]["sourceDiagnostics"]["sourcePath"],
            "company_search",
        )

    def test_collect_supported_company_source_jobs_preserves_supported_ats_failures_for_retry(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            }
        ]

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
                side_effect=RuntimeError("temporary ats outage"),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs_with_trace",
                return_value=type(
                    "Trace",
                    (),
                    {
                        "jobs": [],
                        "followed_links": [],
                        "source_path": "company_page",
                        "next_listing_urls": [],
                    },
                )(),
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                client=object(),
            )

        self.assertEqual(result.jobs, [])
        self.assertEqual(result.companies_handled_count, 0)
        self.assertEqual(len(result.remaining_companies), 1)
        self.assertEqual(result.remaining_companies[0]["name"], "Acme Hydrogen")
        self.assertFalse(result.remaining_companies[0]["snapshotComplete"])

    def test_collect_supported_company_source_jobs_defers_company_when_ai_prerank_times_out(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            }
        ]
        fetched_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Develop PEM durability test plans.",
            }
        ]

        remaining_call_count = {"count": 0}

        def fake_remaining_seconds(_deadline):
            remaining_call_count["count"] += 1
            return 30 if remaining_call_count["count"] <= 3 else 0

        client = SimpleNamespace(timeout_seconds=999)
        observed_timeout: dict[str, int] = {}

        def _raise_timeout(current_client, **kwargs):
            del kwargs
            observed_timeout["value"] = int(current_client.timeout_seconds)
            raise TimeoutError("OpenAI API request timed out.")

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
                return_value=fetched_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=_raise_timeout,
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                client=client,
                search_run_id=77,
            )

        self.assertEqual(result.jobs, [])
        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(len(result.remaining_companies), 0)
        self.assertEqual(observed_timeout["value"], COMPANY_JOB_PRERANK_TIMEOUT_SECONDS)
        self.assertEqual(client.timeout_seconds, 999)
        company = result.processed_companies[0]
        self.assertEqual(company["name"], "Acme Hydrogen")
        self.assertTrue(company["snapshotComplete"])
        self.assertEqual(company["sourceDiagnostics"]["reason"], "ai_job_prerank_pending_retry")
        self.assertNotIn("aiRankingError", company["sourceDiagnostics"])
        self.assertEqual(company["sourceWorkState"]["technicalFailureCount"], 1)
        self.assertEqual(company["sourceWorkState"]["suspendedRunId"], 77)

    def test_collect_supported_company_source_jobs_keeps_scored_jobs_when_some_prerank_results_are_pending(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            }
        ]
        fetched_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Develop PEM durability test plans.",
            },
            {
                "title": "Vendor Manager",
                "location": "Remote",
                "url": "https://boards.greenhouse.io/acme/jobs/2",
                "datePosted": "2026-04-13T00:00:00Z",
                "summary": "Coordinate external supplier programs.",
            },
        ]

        def _partial_prerank(_client, **kwargs):
            jobs = [dict(item) for item in kwargs["jobs"]]
            jobs[0]["aiPreRankScore"] = 88
            jobs[0]["aiPreRankReason"] = "Strong match."
            jobs[1]["aiPreRankPending"] = True
            return jobs

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
                return_value=fetched_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.prerank_company_jobs_for_candidate",
                side_effect=_partial_prerank,
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                client=object(),
            )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["url"], "https://boards.greenhouse.io/acme/jobs/1")
        company = result.processed_companies[0]
        self.assertEqual(company["sourceDiagnostics"]["reason"], "queued_jobs")
        self.assertEqual(company["sourceDiagnostics"]["pendingPrerankJobsDeferred"], 1)

    def test_collect_supported_company_source_jobs_keeps_partial_results_when_stage_budget_runs_out(self) -> None:
        companies = [
            {
                "name": "Acme Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/acme",
                "tags": ["hydrogen"],
            },
            {
                "name": "Beta Hydrogen",
                "careersUrl": "https://boards.greenhouse.io/beta",
                "tags": ["hydrogen"],
            },
        ]
        fetched_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "location": "Berlin",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "summary": "Develop PEM durability test plans.",
            }
        ]
        remaining_call_count = {"count": 0}

        def fake_remaining_seconds(_deadline):
            remaining_call_count["count"] += 1
            return 30 if remaining_call_count["count"] <= 3 else 0

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.fetch_supported_ats_jobs",
                return_value=fetched_jobs,
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources.enrich_selected_jobs_with_details",
                side_effect=lambda jobs, **_: (list(jobs), 0),
            ),
            patch(
                "jobflow_desktop_app.search.companies.sources._remaining_seconds",
                side_effect=fake_remaining_seconds,
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=self._config(),
                timeout_seconds=30,
            )

        self.assertEqual(result.companies_handled_count, 1)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(len(result.processed_companies), 1)
        self.assertEqual(result.processed_companies[0]["name"], "Acme Hydrogen")
        self.assertEqual(len(result.remaining_companies), 1)
        self.assertEqual(result.remaining_companies[0]["name"], "Beta Hydrogen")

    def test_collect_supported_company_source_jobs_preserves_generic_transient_failures_with_client(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:DE"]
        companies = [
            {
                "name": "Acme Hydrogen",
                "website": "https://acme.example.com",
                "tags": ["hydrogen", "region:DE"],
            }
        ]

        with (
            patch(
                "jobflow_desktop_app.search.companies.sources.discover_company_careers",
                side_effect=RuntimeError("temporary search outage"),
            ),
        ):
            result = collect_supported_company_source_jobs(
                companies,
                config=config,
                client=object(),
            )

        self.assertEqual(result.jobs, [])
        self.assertEqual(result.companies_handled_count, 0)
        self.assertEqual(len(result.remaining_companies), 1)
        self.assertEqual(result.remaining_companies[0]["name"], "Acme Hydrogen")
        self.assertFalse(result.remaining_companies[0]["snapshotComplete"])

    def test_enrich_job_with_details_populates_jd_payload(self) -> None:
        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_job_details",
            return_value={
                "ok": True,
                "status": 200,
                "finalUrl": "https://example.com/jobs/1",
                "redirected": False,
                "fetchedAt": "2026-04-15T10:00:00+00:00",
                "applyUrl": "https://example.com/jobs/1/apply",
                "rawText": "Detailed job description",
                "locationHint": "Berlin",
                "extracted": {
                    "title": "Hydrogen Test Engineer",
                    "company": "Acme",
                    "location": "Berlin",
                    "datePosted": "2026-04-12",
                    "description": "Detailed job description for AST and validation.",
                },
            },
        ):
            enriched = enrich_job_with_details(
                {
                    "title": "Engineer",
                    "company": "Acme",
                    "url": "https://example.com/jobs/1",
                    "summary": "",
                },
                config=self._config(),
                timeout_seconds=30,
            )

        self.assertEqual(enriched["title"], "Hydrogen Test Engineer")
        self.assertEqual(enriched["jd"]["applyUrl"], "https://example.com/jobs/1/apply")
        self.assertIn("AST", enriched["summary"])

    def test_enrich_job_with_details_keeps_original_title_when_detail_fetch_hits_antibot_gate(self) -> None:
        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_job_details",
            return_value={
                "ok": False,
                "status": 200,
                "finalUrl": "https://careers.example.com/jobs/localization-manager",
                "redirected": False,
                "fetchedAt": "2026-04-20T20:45:37+00:00",
                "applyUrl": "",
                "rawText": "",
                "locationHint": "",
                "error": "Anti-bot interstitial page instead of job details.",
                "extracted": {},
            },
        ):
            enriched = enrich_job_with_details(
                {
                    "title": "[Gaming] Korean into Thai Translators",
                    "company": "Lionbridge",
                    "url": "https://careers.example.com/jobs/localization-manager",
                    "summary": "Gaming translation role for Korean into Thai.",
                },
                config=self._config(),
                timeout_seconds=30,
            )

        self.assertEqual(enriched["title"], "[Gaming] Korean into Thai Translators")
        self.assertEqual(enriched["summary"], "Gaming translation role for Korean into Thai.")
        self.assertFalse(enriched["jd"]["ok"])

    def test_enrich_job_with_details_uses_ai_rescue_after_failed_detail_fetch(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "recovered": True,
                            "isConcreteEmployeeRole": True,
                            "title": "Localization Program Manager",
                            "company": "Smartling",
                            "location": "Remote, US",
                            "datePosted": "2026-04-10",
                            "description": "Lead localization programs, vendor management, and multilingual release delivery.",
                            "applyUrl": "https://job-boards.greenhouse.io/smartling/jobs/12345/apply",
                            "reason": "Recovered official employee requisition details.",
                        }
                    )
                }
            ]
        )
        with patch(
            "jobflow_desktop_app.search.companies.sources.fetch_job_details",
            return_value={
                "ok": False,
                "status": 200,
                "finalUrl": "https://job-boards.greenhouse.io/smartling/jobs/12345",
                "redirected": False,
                "fetchedAt": "2026-04-20T20:45:37+00:00",
                "applyUrl": "",
                "rawText": "",
                "locationHint": "",
                "error": "Anti-bot interstitial page instead of job details.",
                "extracted": {},
            },
        ):
            enriched = enrich_job_with_details(
                {
                    "title": "Localization Program Manager",
                    "company": "Smartling",
                    "url": "https://job-boards.greenhouse.io/smartling/jobs/12345",
                    "summary": "",
                },
                config=self._config(),
                client=client,
                timeout_seconds=30,
            )

        self.assertTrue(enriched["jd"]["ok"])
        self.assertTrue(enriched["jd"]["rescuedByAI"])
        self.assertEqual(enriched["title"], "Localization Program Manager")
        self.assertIn("vendor management", enriched["summary"])
        self.assertEqual(
            enriched["jd"]["applyUrl"],
            "https://job-boards.greenhouse.io/smartling/jobs/12345/apply",
        )

    def test_job_detail_rescue_prompt_requires_exact_role_recovery(self) -> None:
        prompt = load_prompt_asset("search_ranking", "job_detail_rescue_prompt.txt")
        self.assertIn("This is not a company-discovery task", prompt)
        self.assertIn("Do not substitute a different role", prompt)
        self.assertIn("non-employment flows are not concrete employee requisitions", prompt)

    def test_sources_helpers_normalize_and_select_company_jobs(self) -> None:
        normalized = normalize_company_job(
            {
                "title": "Hydrogen Systems Engineer",
                "location": "Berlin",
                "url": "https://example.com/jobs/1",
                "summary": "Work on PEM systems.",
            },
            company_name="Acme Hydrogen",
            ats_type="greenhouse",
            company_tags=["hydrogen"],
            config=self._config(),
            discovered_at="2026-04-15T10:00:00Z",
        )
        self.assertEqual(normalized["company"], "Acme Hydrogen")
        self.assertEqual(normalized["source"], "company:Acme Hydrogen:greenhouse")
        self.assertEqual(normalized["regionTag"], "")

        selection = select_company_jobs_for_coverage(
            company={"jobLinkCoverage": {"cursor": 1, "recentSeenJobUrls": ["https://example.com/jobs/2"]}},
            jobs=[
                {"title": "Hydrogen Systems Engineer", "url": "https://example.com/jobs/1"},
                {"title": "Hydrogen Test Engineer", "url": "https://example.com/jobs/2"},
                {"title": "Duplicate", "url": "https://example.com/jobs/1"},
            ],
            limit=2,
        )
        self.assertEqual([job["url"] for job in selection["jobs"]], ["https://example.com/jobs/1", "https://example.com/jobs/2"])
        self.assertEqual(selection["poolSize"], 2)
        self.assertEqual(selection["jobLinkCoverage"], {})

    def test_select_company_jobs_for_coverage_prefers_ai_prerank_over_keyword_shape(self) -> None:
        jobs = [
            {
                "title": "See engineering team positions",
                "summary": "Explore backend platform and developer tooling roles.",
                "url": "https://example.com/jobs/engineering",
                "aiPreRankScore": 8,
            },
            {
                "title": "Localization Project Manager",
                "summary": "Lead multilingual launches and translation vendor operations.",
                "url": "https://example.com/jobs/localization-pm",
                "aiPreRankScore": 92,
            },
            {
                "title": "Translation Operations Specialist",
                "summary": "Own translation workflows in the TMS and multilingual content QA.",
                "url": "https://example.com/jobs/translation-ops",
                "aiPreRankScore": 81,
            },
        ]

        selection = select_company_jobs_for_coverage(
            company={},
            jobs=jobs,
            limit=2,
        )

        self.assertEqual(
            [job["url"] for job in selection["jobs"]],
            [
                "https://example.com/jobs/localization-pm",
                "https://example.com/jobs/translation-ops",
            ],
        )
        self.assertEqual(selection["poolSize"], 2)

    def test_select_company_jobs_for_coverage_skips_completed_jobs_and_uses_unified_schedule_score(self) -> None:
        jobs = [
            {
                "title": "Localization Project Manager",
                "url": "https://example.com/jobs/1",
                "datePosted": "2026-04-12T00:00:00Z",
                "aiPreRankScore": 70,
            },
            {
                "title": "Translation Operations Manager",
                "url": "https://example.com/jobs/2",
                "datePosted": "2026-04-15T00:00:00Z",
                "aiPreRankScore": 65,
            },
            {
                "title": "Vendor Manager",
                "url": "https://example.com/jobs/3",
                "datePosted": "2026-04-17T00:00:00Z",
                "aiPreRankScore": 55,
            },
        ]

        selection = select_company_jobs_for_coverage(
            company={"jobLinkCoverage": {"recentSeenJobUrls": ["https://example.com/jobs/2"], "cursor": 0}},
            jobs=jobs,
            limit=2,
            completed_job_urls={"https://example.com/jobs/1"},
        )

        self.assertEqual(
            [job["url"] for job in selection["jobs"]],
            ["https://example.com/jobs/2", "https://example.com/jobs/3"],
        )
        self.assertEqual(selection["excludedCompletedCount"], 1)
        self.assertEqual(selection["excludedLowPrerankCount"], 0)
        self.assertEqual(selection["poolSize"], 2)

    def test_select_company_jobs_for_coverage_prefers_ai_prerank_score(self) -> None:
        jobs = [
            {
                "title": "Staff Technical Program Manager",
                "url": "https://example.com/jobs/tpm",
                "datePosted": "2026-04-18T00:00:00Z",
                "aiPreRankScore": 12,
            },
            {
                "title": "Sr Manager, Talent Acquisition",
                "url": "https://example.com/jobs/ta",
                "datePosted": "2026-03-01T00:00:00Z",
                "aiPreRankScore": 94,
            },
        ]

        selection = select_company_jobs_for_coverage(
            company={},
            jobs=jobs,
            limit=1,
        )

        self.assertEqual([job["url"] for job in selection["jobs"]], ["https://example.com/jobs/ta"])
        self.assertEqual(selection["excludedLowPrerankCount"], 1)

    def test_select_company_jobs_for_coverage_drops_low_ai_prerank_irrelevant_pages(self) -> None:
        jobs = [
            {
                "title": "Saved jobs",
                "url": "https://example.com/jobs/saved",
                "aiPreRankScore": 10,
            },
            {
                "title": "Talent Acquisition Manager",
                "url": "https://example.com/jobs/ta",
                "aiPreRankScore": 86,
            },
        ]

        selection = select_company_jobs_for_coverage(
            company={},
            jobs=jobs,
            limit=2,
        )

        self.assertEqual([job["url"] for job in selection["jobs"]], ["https://example.com/jobs/ta"])
        self.assertEqual(selection["poolSize"], 1)

    def test_has_job_signal_rejects_navigation_titles_even_with_job_like_urls(self) -> None:
        self.assertFalse(
            has_job_signal(
                title="Explore Internal Open Positions",
                url="https://example.com/jobs/internal-open-positions",
                summary="",
            )
        )
        self.assertFalse(
            has_job_signal(
                title="Internal Open Positions",
                url="https://example.com/jobs/internal-open-positions",
                summary="",
            )
        )
        self.assertFalse(
            has_job_signal(
                title="See engineering team positions",
                url="https://example.com/jobs/engineering-team",
                summary="",
            )
        )
        self.assertTrue(
            has_job_signal(
                title="Localization Project Manager",
                url="https://example.com/jobs/localization-project-manager",
                summary="Lead multilingual launches and vendor workflows.",
            )
        )


if __name__ == "__main__":
    unittest.main()

