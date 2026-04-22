from __future__ import annotations

from copy import deepcopy
import json
import sys
import unittest
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.companies.discovery import (  # noqa: E402
    auto_discover_companies_in_pool,
    build_company_identity_keys,
    discover_companies_for_candidate,
)
from jobflow_desktop_app.ai.client import OpenAIResponsesError  # noqa: E402
from jobflow_desktop_app.ai.role_recommendations import (  # noqa: E402
    CandidateSemanticProfile,
    encode_bilingual_description,
    encode_bilingual_role_name,
)
from jobflow_desktop_app.search.orchestration.candidate_search_signals import (  # noqa: E402
    CandidateSearchSignals,
    ProfileSearchSignals,
    SemanticSearchSignals,
    collect_candidate_search_signals,
)
from jobflow_desktop_app.search.orchestration.company_discovery_queries import (  # noqa: E402
    DiscoveryAnchorPlan,
    build_company_discovery_queries_from_anchor_plan,
    build_discovery_anchor_plan,
    discovery_query_bucket_order,
)
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord  # noqa: E402

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore


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
            raise AssertionError("No outcome left for client.create().")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class CompanyDiscoveryTests(unittest.TestCase):
    def test_discover_companies_for_candidate_uses_web_search_and_normalizes_results(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "name": "Acme Energy",
                                    "website": "https://acme.example/careers#jobs",
                                    "businessSummary": "Hydrogen systems employer with fuel-cell platform operations.",
                                    "tags": ["hydrogen"],
                                    "region": "de",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            ]
        )

        companies = discover_companies_for_candidate(
            client,
            model="gpt-5-nano",
            candidate_context={
                "summary": "Hydrogen systems employer search.",
                "targetRoles": ["Hydrogen Systems Engineer"],
                "desiredWorkDirections": ["fuel cell platforms"],
                "avoidBusinessAreas": ["staffing firms"],
            },
            company_count=6,
            existing_companies=["Existing Co"],
        )

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["name"], "Acme Energy")
        self.assertEqual(
            companies[0]["businessSummary"],
            "Hydrogen systems employer with fuel-cell platform operations.",
        )
        self.assertIn("source:web", companies[0]["tags"])
        self.assertIn("region:DE", companies[0]["tags"])
        self.assertIn("web_search", companies[0]["discoverySources"])
        self.assertEqual(client.requests[0]["tools"], [{"type": "web_search"}])
        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("desiredCompanyCount", user_text)
        self.assertIn("Existing Co", user_text)
        self.assertIn("Hydrogen Systems Engineer", user_text)
        self.assertIn("Do not repeat any company listed in existingCompanies.", user_text)

    def test_discover_companies_for_candidate_accepts_empty_required_fields(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "name": "Lokalise",
                                    "website": "",
                                    "businessSummary": "",
                                    "tags": [],
                                    "region": "",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            ]
        )

        companies = discover_companies_for_candidate(
            client,
            model="gpt-5-nano",
            candidate_context={
                "summary": "Localization operations candidate.",
                "targetRoles": ["Localization Program Manager"],
                "desiredWorkDirections": ["translation management"],
                "avoidBusinessAreas": [],
            },
            company_count=6,
        )

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["name"], "Lokalise")
        self.assertEqual(companies[0]["website"], "")
        self.assertEqual(companies[0]["businessSummary"], "")
        self.assertIn("source:web", companies[0]["tags"])

    def test_discover_companies_for_candidate_includes_candidate_context(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps({"companies": []}, ensure_ascii=False)
                }
            ]
        )

        discover_companies_for_candidate(
            client,
            model="gpt-5-nano",
            candidate_context={
                "summary": "Localization leader seeking in-house platform employers.",
                "targetRoles": ["Localization Program Manager"],
                "desiredWorkDirections": ["localization operations", "translation management"],
                "avoidBusinessAreas": ["agency-only roles"],
            },
            company_count=10,
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("candidateContext", user_text)
        self.assertIn("Localization Program Manager", user_text)
        self.assertIn("desiredCompanyCount", user_text)

    def test_discover_companies_for_candidate_retries_once_on_timeout(self) -> None:
        client = _FlakyClient(
            [
                OpenAIResponsesError("OpenAI API request timed out."),
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "name": "Smartling",
                                    "website": "https://www.smartling.com",
                                    "businessSummary": "Localization software platform for multilingual content teams.",
                                    "tags": ["localization", "tms"],
                                    "region": "US",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                },
            ]
        )

        progress: list[str] = []
        companies = discover_companies_for_candidate(
            client,
            model="gpt-5-nano",
            candidate_context={
                "summary": "Localization operations candidate.",
                "targetRoles": ["Localization Program Manager"],
                "desiredWorkDirections": ["translation management", "multilingual content"],
                "avoidBusinessAreas": [],
            },
            company_count=10,
            progress_callback=progress.append,
        )

        self.assertEqual([item["name"] for item in companies], ["Smartling"])
        self.assertEqual(len(client.requests), 2)
        self.assertIn("Python direct company discovery timed out; retrying once.", progress)

    def test_auto_discover_companies_in_pool_updates_companies_and_query_stats(self) -> None:
        config = {
            "candidate": {"scopeProfile": "hydrogen_mainline"},
            "companyDiscovery": {
                "enableAutoDiscovery": True,
                "model": "gpt-5-nano",
                "companyDiscoveryInput": {
                    "summary": "Hydrogen systems candidate.",
                    "targetRoles": ["Hydrogen Systems Engineer"],
                    "desiredWorkDirections": ["hydrogen systems", "fuel-cell platforms"],
                    "avoidBusinessAreas": [],
                },
                "maxCompaniesPerCall": 6,
            },
        }
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "name": "Acme Energy",
                                    "website": "https://acme.example",
                                    "tags": ["hydrogen"],
                                    "region": "DE",
                                }
                            ]
                        }
                    )
                }
            ]
        )
        progress: list[str] = []

        result = auto_discover_companies_in_pool(
            client,
            config=config,
            companies=[
                {
                    "name": "Repeat Co",
                    "website": "https://repeat.example",
                    "repeatCount": 2,
                }
            ],
            progress_callback=progress.append,
        )

        self.assertEqual(result["added"], 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(progress), 1)
        self.assertEqual(len(result["companies"]), 2)
        self.assertEqual(result["companies"][0]["repeatCount"], 1)
        new_company = result["companies"][1]
        self.assertIn("domain:acme.example", build_company_identity_keys(new_company))

    def test_auto_discover_companies_in_pool_returns_exhausted_when_query_set_missing(self) -> None:
        config = {
            "candidate": {},
            "companyDiscovery": {
                "enableAutoDiscovery": True,
                "model": "gpt-5-nano",
            },
        }
        client = _FakeClient([])

        result = auto_discover_companies_in_pool(
            client,
            config=config,
            companies=[],
        )

        self.assertEqual(result["added"], 0)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["newCompanies"], [])
        self.assertEqual(client.requests, [])

    def test_auto_discover_companies_in_pool_processes_a_single_query_per_call(self) -> None:
        config = {
            "candidate": {},
            "companyDiscovery": {
                "enableAutoDiscovery": True,
                "model": "gpt-5-nano",
                "companyDiscoveryInput": {
                    "summary": "Risk candidate.",
                    "targetRoles": ["Risk Manager"],
                    "desiredWorkDirections": ["risk operations"],
                    "avoidBusinessAreas": [],
                },
                "maxCompaniesPerCall": 2,
            },
        }
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "name": "Acme Risk",
                                    "website": "https://risk.example",
                                    "tags": ["risk"],
                                    "region": "DE",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                },
            ],
        )

        result = auto_discover_companies_in_pool(
            client,
            config=config,
            companies=[],
        )

        self.assertEqual(result["added"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["newCompanies"][0]["name"], "Acme Risk")

    def test_auto_discover_companies_in_pool_passes_existing_company_names_to_direct_discovery(self) -> None:
        config = {
            "candidate": {},
            "companyDiscovery": {
                "enableAutoDiscovery": True,
                "model": "gpt-5-nano",
                "companyDiscoveryInput": {
                    "summary": "Localization candidate.",
                    "targetRoles": ["Localization Program Manager"],
                    "desiredWorkDirections": ["translation management"],
                    "avoidBusinessAreas": [],
                },
                "maxCompaniesPerCall": 10,
            },
        }
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps({"companies": []}, ensure_ascii=False)
                }
            ]
        )

        auto_discover_companies_in_pool(
            client,
            config=config,
            companies=[
                {"name": "Smartling", "website": "https://www.smartling.com"},
                {"name": "Lokalise", "website": "https://lokalise.com"},
            ],
        )

        user_text = client.requests[0]["input"][1]["content"][0]["text"]
        self.assertIn("Smartling", user_text)
        self.assertIn("Lokalise", user_text)

    def test_auto_discover_companies_in_pool_processes_one_query_and_advances_cursor(self) -> None:
        config = {
            "candidate": {
                "targetRoles": [{"displayName": "Localization Program Manager"}],
                "semanticProfile": {
                    "summary": "In-house localization leader for multilingual product teams.",
                    "company_discovery_primary_anchors": ["localization operations"],
                    "company_discovery_secondary_anchors": ["translation management"],
                    "avoid_business_areas": ["agency-only roles"],
                },
            },
            "companyDiscovery": {
                "enableAutoDiscovery": True,
                "model": "gpt-5-nano",
                "companyDiscoveryInput": {
                    "summary": "In-house localization leader for multilingual product teams.",
                    "targetRoles": ["Localization Program Manager"],
                    "desiredWorkDirections": [
                        "localization operations",
                        "translation management",
                    ],
                    "avoidBusinessAreas": ["agency-only roles"],
                },
                "maxCompaniesPerCall": 3,
            },
        }
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "name": "Microsoft",
                                    "website": "https://www.microsoft.com",
                                    "businessSummary": "Global technology company.",
                                    "tags": [],
                                    "region": "GLOBAL",
                                },
                                {
                                    "name": "Lokalise",
                                    "website": "https://lokalise.com",
                                    "businessSummary": "Localization platform.",
                                    "tags": [],
                                    "region": "GLOBAL",
                                },
                                {
                                    "name": "Smartling",
                                    "website": "https://www.smartling.com",
                                    "businessSummary": "Localization platform.",
                                    "tags": [],
                                    "region": "GLOBAL",
                                },
                            ]
                        },
                        ensure_ascii=False,
                    )
                },
            ]
        )

        result = auto_discover_companies_in_pool(
            client,
            config=config,
            companies=[],
        )

        names = [company["name"] for company in result["newCompanies"]]
        self.assertEqual(result["added"], 3)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(names, ["Microsoft", "Lokalise", "Smartling"])

    def test_build_discovery_anchor_plan_uses_injected_signals_for_semantic_path(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            create_profile(
                context,
                candidate_id,
                name="Hydrogen Systems Engineer",
                scope_profile="hydrogen_mainline",
                keyword_focus="hydrogen diagnostics",
                is_active=True,
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            semantic_profile = CandidateSemanticProfile(
                summary="Hydrogen profile",
                job_fit_core_terms=("placeholder",),
            )
            signals = CandidateSearchSignals(
                semantic=SemanticSearchSignals(
                    summary="Hydrogen profile",
                    company_discovery_primary_anchors=["fuel cell diagnostics"],
                    company_discovery_secondary_anchors=["reliability engineering", "industrial technology"],
                    job_fit_core_terms=["hydrogen systems", "electrochemical durability"],
                    job_fit_support_terms=["degradation analytics"],
                    avoid_business_areas=[],
                ),
                profile=ProfileSearchSignals(
                    role_names=[],
                    target_roles=[],
                ),
            )

            plan = build_discovery_anchor_plan(
                scope_profiles=["hydrogen_mainline"],
                signals=signals,
            )

            self.assertEqual(plan.core[:3], [
                "fuel cell diagnostics",
                "hydrogen systems",
                "electrochemical durability",
            ])
            self.assertNotIn("degradation analytics", plan.core)
            self.assertIn("reliability engineering", plan.adjacent)
            self.assertIn("industrial technology", plan.adjacent)

    def test_discovery_query_bucket_order_favors_core_without_extra_weight_math(self) -> None:
        self.assertEqual(
            discovery_query_bucket_order(7),
            ["core", "adjacent", "core", "core", "adjacent", "core"],
        )
        self.assertEqual(
            discovery_query_bucket_order(3),
            ["core", "adjacent", "core"],
        )

    def test_build_discovery_anchor_plan_keeps_explicit_business_hints_in_semantic_path(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Demo Candidate",
                notes="digital twin and PHM; industrial gas platforms",
            )
            create_profile(
                context,
                candidate_id,
                name="Hydrogen Systems Engineer",
                scope_profile="hydrogen_mainline",
                keyword_focus="hydrogen diagnostics",
                is_active=True,
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            semantic_profile = CandidateSemanticProfile(
                summary="Hydrogen profile",
                company_discovery_primary_anchors=("fuel cell diagnostics",),
                company_discovery_secondary_anchors=("digital twin and PHM", "industrial gas platforms"),
                job_fit_core_terms=("hydrogen systems", "electrochemical durability"),
                job_fit_support_terms=("degradation analytics",),
            )

            signals = collect_candidate_search_signals(
                profiles=profiles,
                semantic_profile=semantic_profile,
            )
            plan = build_discovery_anchor_plan(
                scope_profiles=["hydrogen_mainline"],
                signals=signals,
            )

            self.assertIn("digital twin and PHM", plan.adjacent)
            self.assertIn("industrial gas platforms", plan.adjacent)

    def test_build_discovery_anchor_plan_classifies_unmatched_business_hints_as_adjacent(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Adjacent Candidate",
                notes="advanced ceramics platforms",
            )
            create_profile(
                context,
                candidate_id,
                name="Hydrogen Systems Engineer",
                scope_profile="hydrogen_mainline",
                is_active=True,
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            semantic_profile = CandidateSemanticProfile(
                summary="Hydrogen profile",
                company_discovery_primary_anchors=("fuel cell diagnostics",),
                company_discovery_secondary_anchors=("advanced ceramics platforms",),
                job_fit_core_terms=("hydrogen systems", "electrochemical durability"),
            )

            signals = collect_candidate_search_signals(
                profiles=profiles,
                semantic_profile=semantic_profile,
            )
            plan = build_discovery_anchor_plan(
                scope_profiles=["hydrogen_mainline"],
                signals=signals,
            )

            self.assertIn("advanced ceramics platforms", plan.adjacent)

    def test_build_company_discovery_queries_from_anchor_plan_uses_only_provided_anchors(self) -> None:
        queries = build_company_discovery_queries_from_anchor_plan(
            anchor_plan=DiscoveryAnchorPlan(
                core=["hydrogen systems", "fuel cells"],
                adjacent=["reliability engineering"],
            ),
            rotation_seed=0,
        )

        self.assertEqual(len(queries), 6)
        self.assertEqual(len({item.casefold() for item in queries}), 6)
        self.assertTrue(any("reliability engineering" in item for item in queries))
        self.assertFalse(any("energy infrastructure platforms" in item for item in queries))
        self.assertFalse(any("industrial technology companies" in item for item in queries))

    def test_build_company_discovery_queries_preserve_anchor_priority_under_rotation(self) -> None:
        queries = build_company_discovery_queries_from_anchor_plan(
            anchor_plan=DiscoveryAnchorPlan(
                core=["localization platforms", "translation management systems"],
                adjacent=["language services providers"],
            ),
            rotation_seed=987654,
        )

        self.assertTrue(queries)
        self.assertTrue(any("localization platforms" in item for item in queries[:2]))
        self.assertTrue(any("translation management systems" in item for item in queries[:3]))

    def test_build_company_discovery_queries_prefer_ai_discovery_anchors_over_broad_manual_focus(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="Sustainability and environmental compliance profile",
                company_discovery_primary_anchors=[
                    "environmental compliance",
                    "sustainability reporting",
                ],
                company_discovery_secondary_anchors=[
                    "product stewardship",
                ],
                job_fit_core_terms=["ESG governance"],
            ),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=["Environmental Compliance Manager"],
            ),
        )

        plan = build_discovery_anchor_plan(
            scope_profiles=[],
            signals=signals,
        )
        queries = build_company_discovery_queries_from_anchor_plan(
            anchor_plan=plan,
            rotation_seed=0,
        )

        self.assertEqual(plan.core[:2], ["environmental compliance", "sustainability reporting"])
        self.assertTrue(any("environmental compliance" in item for item in queries[:2]))
        self.assertTrue(any("sustainability reporting" in item for item in queries[:3]))
        self.assertFalse(any(item == "regulated industry employers" for item in queries))
        self.assertFalse(any(item == "life sciences employers" for item in queries))

    def test_build_company_discovery_queries_contextualize_generic_adjacent_anchors(self) -> None:
        queries = build_company_discovery_queries_from_anchor_plan(
            anchor_plan=DiscoveryAnchorPlan(
                core=["in-house localization"],
                adjacent=["vendor performance management"],
            ),
            rotation_seed=0,
        )

        self.assertTrue(
            any("in-house localization vendor performance management" in item for item in queries)
        )
        self.assertFalse(any(item == "vendor performance management employers" for item in queries))

    def test_build_company_discovery_queries_contextualize_overlap_with_only_generic_support_tokens(self) -> None:
        queries = build_company_discovery_queries_from_anchor_plan(
            anchor_plan=DiscoveryAnchorPlan(
                core=["localization vendor management"],
                adjacent=["vendor performance management"],
            ),
            rotation_seed=0,
        )

        self.assertTrue(
            any("localization vendor performance management" in item for item in queries)
        )
        self.assertFalse(any(item == "vendor performance management employers" for item in queries))

    def test_build_discovery_anchor_plan_without_scope_profiles_keeps_buckets_empty(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="",
                company_discovery_primary_anchors=[],
                company_discovery_secondary_anchors=[],
                job_fit_core_terms=[],
                job_fit_support_terms=[],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=[],
            ),
        )

        plan = build_discovery_anchor_plan(
            scope_profiles=[],
            signals=signals,
        )

        self.assertEqual(plan.core, [])
        self.assertEqual(plan.adjacent, [])

    def test_build_discovery_anchor_plan_ignores_partial_scope_defaults(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="",
                company_discovery_primary_anchors=[],
                company_discovery_secondary_anchors=[],
                job_fit_core_terms=["banking risk analytics"],
                job_fit_support_terms=[],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=[],
            ),
        )

        plan = build_discovery_anchor_plan(
            scope_profiles=["adjacent_mbse", ""],
            signals=signals,
        )

        self.assertEqual(plan.core, ["banking risk analytics"])
        self.assertFalse(any("systems engineering" in item.casefold() for item in plan.core))

    def test_collect_candidate_search_signals_decodes_bilingual_keyword_focus(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Finance Candidate",
                notes="Risk analytics and regulatory reporting",
            )
            context.profiles.save(
                SearchProfileRecord(
                    profile_id=None,
                    candidate_id=candidate_id,
                    name="Regulatory Reporting Automation Engineer",
                    scope_profile="",
                    target_role="Regulatory Reporting Automation Engineer",
                    location_preference="Frankfurt\nBerlin\nRemote Germany",
                    role_name_i18n=encode_bilingual_role_name(
                        "监管报送自动化工程师",
                        "Regulatory Reporting Automation Engineer",
                    ),
                    keyword_focus=encode_bilingual_description(
                        "监管报送自动化",
                        "Regulatory reporting automation",
                    ),
                    is_active=True,
                )
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            signals = collect_candidate_search_signals(
                profiles=profiles,
                semantic_profile=None,
            )

            self.assertIn("Regulatory Reporting Automation Engineer", signals.profile.role_names)
            self.assertIn("Regulatory Reporting Automation Engineer", signals.profile.target_roles)
            self.assertIn("Regulatory reporting automation", signals.profile.keyword_focus_terms)

    def test_build_discovery_anchor_plan_uses_profile_keyword_focus_when_semantic_profile_missing(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Localization Candidate",
                notes="Prefer in-house localization roles.",
            )
            create_profile(
                context,
                candidate_id,
                name="Localization Program Manager",
                keyword_focus=(
                    "localization operations\n"
                    "translation management\n"
                    "multilingual content\n"
                    "vendor management"
                ),
                is_active=True,
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            signals = collect_candidate_search_signals(
                profiles=profiles,
                semantic_profile=None,
            )
            plan = build_discovery_anchor_plan(
                scope_profiles=[],
                signals=signals,
            )

            self.assertIn("localization operations", signals.profile.keyword_focus_terms)
            self.assertIn("translation management", plan.core)
            self.assertIn("localization operations", plan.core)

    def test_build_discovery_anchor_plan_filters_tool_only_company_hints(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="",
                company_discovery_primary_anchors=[],
                company_discovery_secondary_anchors=[],
                job_fit_core_terms=[],
                job_fit_support_terms=["Python", "SQL", "Basel reporting"],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=[],
            ),
        )

        plan = build_discovery_anchor_plan(
            scope_profiles=[],
            signals=signals,
        )

        self.assertIn("Basel reporting", plan.adjacent)
        self.assertNotIn("Python", plan.adjacent)
        self.assertNotIn("SQL", plan.adjacent)

    def test_build_discovery_anchor_plan_normalizes_profile_queries_into_business_phrases(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="",
                company_discovery_primary_anchors=[],
                company_discovery_secondary_anchors=[],
                job_fit_core_terms=["software localization"],
                job_fit_support_terms=[],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=["Translation Management Specialist"],
                target_roles=[],
            ),
        )

        plan = build_discovery_anchor_plan(
            scope_profiles=[],
            signals=signals,
        )
        queries = build_company_discovery_queries_from_anchor_plan(
            anchor_plan=plan,
            rotation_seed=0,
        )

        self.assertIn("software localization", plan.core)
        self.assertFalse(any("employers employers" in item for item in queries))
        self.assertFalse(any("careers employers" in item for item in queries))

    def test_build_discovery_anchor_plan_filters_avoided_defaults(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Avoid Candidate")
            create_profile(
                context,
                candidate_id,
                name="Hydrogen Systems Engineer",
                scope_profile="hydrogen_mainline",
                is_active=True,
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            semantic_profile = CandidateSemanticProfile(
                summary="Hydrogen profile",
                company_discovery_primary_anchors=("fuel cell diagnostics",),
                job_fit_core_terms=("hydrogen systems", "electrochemical durability"),
                avoid_business_areas=("battery", "automotive"),
            )

            signals = collect_candidate_search_signals(
                profiles=profiles,
                semantic_profile=semantic_profile,
            )
            plan = build_discovery_anchor_plan(
                scope_profiles=["hydrogen_mainline"],
                signals=signals,
            )

            self.assertFalse(any("battery" in item.casefold() for item in plan.adjacent))
            self.assertFalse(any("automotive" in item.casefold() for item in plan.adjacent))

    def test_phrase_limit_independently_caps_semantic_anchor_pool(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="Hydrogen diagnostics and reliability profile",
                company_discovery_primary_anchors=["fuel cell diagnostics", "electrolyzer durability"],
                company_discovery_secondary_anchors=["digital twin asset health"],
                job_fit_core_terms=[
                    "electrochemical durability",
                    "stack balance of plant",
                    "MEA membrane materials",
                ],
                job_fit_support_terms=["hydrogen systems", "lifetime prediction", "degradation analytics", "reliability engineering", "industrial gas platforms"],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=[],
            ),
        )

        original_rules = deepcopy(__import__(
            "jobflow_desktop_app.search.orchestration.company_discovery_queries",
            fromlist=["DISCOVERY_BUCKET_RULES"],
        ).DISCOVERY_BUCKET_RULES)
        import jobflow_desktop_app.search.orchestration.company_discovery_queries as discovery_module

        try:
            discovery_module.DISCOVERY_BUCKET_RULES["core"]["phrase_limit"] = 2
            plan = build_discovery_anchor_plan(
                scope_profiles=["hydrogen_mainline"],
                signals=signals,
            )
        finally:
            discovery_module.DISCOVERY_BUCKET_RULES.clear()
            discovery_module.DISCOVERY_BUCKET_RULES.update(original_rules)

        self.assertEqual(plan.core, ["fuel cell diagnostics", "electrolyzer durability"])

    def test_minimum_anchor_count_no_longer_backfills_implicit_scope_defaults(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="",
                company_discovery_primary_anchors=[],
                company_discovery_secondary_anchors=[],
                job_fit_core_terms=[],
                job_fit_support_terms=[],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=[],
            ),
        )

        import jobflow_desktop_app.search.orchestration.company_discovery_queries as discovery_module
        original_rules = deepcopy(discovery_module.DISCOVERY_BUCKET_RULES)
        try:
            baseline = build_discovery_anchor_plan(
                scope_profiles=["hydrogen_mainline"],
                signals=signals,
            )
            discovery_module.DISCOVERY_BUCKET_RULES["core"]["minimum_anchor_count"] = 3
            discovery_module.DISCOVERY_BUCKET_RULES["adjacent"]["minimum_anchor_count"] = 2
            expanded = build_discovery_anchor_plan(
                scope_profiles=["hydrogen_mainline"],
                signals=signals,
            )
        finally:
            discovery_module.DISCOVERY_BUCKET_RULES.clear()
            discovery_module.DISCOVERY_BUCKET_RULES.update(original_rules)

        self.assertEqual(baseline.core, [])
        self.assertEqual(expanded.core, [])
        self.assertEqual(baseline.adjacent, [])
        self.assertEqual(expanded.adjacent, [])

    def test_query_limit_independently_changes_query_budget_and_bucket_mix(self) -> None:
        anchor_plan = DiscoveryAnchorPlan(
            core=["hydrogen systems", "fuel cells", "electrochemical diagnostics"],
            adjacent=["reliability engineering", "digital twin asset health"],
        )

        import jobflow_desktop_app.search.orchestration.company_discovery_queries as discovery_module
        original_rules = deepcopy(discovery_module.DISCOVERY_BUCKET_RULES)
        try:
            baseline_order = discovery_query_bucket_order(discovery_module.discovery_query_limit())
            baseline_queries = build_company_discovery_queries_from_anchor_plan(
                anchor_plan=anchor_plan,
                rotation_seed=0,
            )
            discovery_module.DISCOVERY_BUCKET_RULES["core"]["query_limit"] = 2
            reduced_order = discovery_query_bucket_order(discovery_module.discovery_query_limit())
            reduced_queries = build_company_discovery_queries_from_anchor_plan(
                anchor_plan=anchor_plan,
                rotation_seed=0,
            )
        finally:
            discovery_module.DISCOVERY_BUCKET_RULES.clear()
            discovery_module.DISCOVERY_BUCKET_RULES.update(original_rules)

        self.assertEqual(len(baseline_queries), 6)
        self.assertEqual(baseline_order.count("core"), 4)
        self.assertEqual(len(reduced_queries), 4)
        self.assertEqual(reduced_order.count("core"), 2)
        self.assertNotIn("explore", reduced_order)


if __name__ == "__main__":
    unittest.main()

