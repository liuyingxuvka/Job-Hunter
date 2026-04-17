from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.companies.sources import (  # noqa: E402
    build_found_job_records,
    build_company_search_fallback_query,
    collect_supported_company_source_jobs,
    collect_careers_page_job_candidates,
    company_search_fallback_enabled,
    detect_ats_from_url,
    enrich_job_with_details,
    merge_company_source_jobs,
)
from jobflow_desktop_app.search.companies.sources_helpers import (  # noqa: E402
    normalize_company_job,
    select_company_jobs_for_coverage,
)


class _FakeClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[dict] = []

    def create(self, payload: dict) -> dict:
        self.requests.append(payload)
        if not self.payloads:
            raise AssertionError("No fake payload left for client.create().")
        return self.payloads.pop(0)


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
                "targetRole": "Hydrogen durability engineer",
                "locationPreference": "Berlin / Remote",
                "scopeProfile": "hydrogen_mainline",
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

    def test_merge_company_source_jobs_and_found_records_preserve_analysis(self) -> None:
        existing_jobs = [
            {
                "title": "Fuel Cell Engineer",
                "company": "Acme Hydrogen",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "analysis": {"overallScore": 72, "matchScore": 72, "fitTrack": "hydrogen_core"},
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
        self.assertEqual(found_records[0]["fitTrack"], "hydrogen_core")

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

    def test_company_search_fallback_helpers_follow_region_and_keywords(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:JP"]
        company = {
            "name": "Acme Hydrogen",
            "tags": ["hydrogen", "region:JP"],
        }

        self.assertTrue(company_search_fallback_enabled(company, config))
        self.assertIn("採用", build_company_search_fallback_query(company, config))

    def test_company_search_fallback_keywords_can_force_enable_without_region_match(self) -> None:
        config = self._config()
        config["sources"]["fallbackSearchRegions"] = ["region:JP"]
        config["sources"]["majorCompanyKeywords"] = ["acme"]
        company = {
            "name": "Acme Hydrogen",
            "tags": ["hydrogen", "region:DE"],
        }

        self.assertTrue(company_search_fallback_enabled(company, config))

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
                            "careersUrl": "",
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
                "jobflow_desktop_app.search.companies.sources.discover_careers_from_website",
                return_value="",
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
                "jobflow_desktop_app.search.companies.sources.fetch_careers_page_jobs",
                return_value=[],
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
                "jobflow_desktop_app.search.companies.sources.discover_careers_from_website",
                side_effect=RuntimeError("temporary website outage"),
            ),
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


if __name__ == "__main__":
    unittest.main()

