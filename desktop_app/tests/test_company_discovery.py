from __future__ import annotations

from copy import deepcopy
import json
import random
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
    discover_companies_from_query,
    sample_weighted_discovery_queries,
)
from jobflow_desktop_app.ai.role_recommendations import CandidateSemanticProfile  # noqa: E402
from jobflow_desktop_app.search.orchestration.candidate_search_signals import (  # noqa: E402
    CandidateInputSignals,
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


class CompanyDiscoveryTests(unittest.TestCase):
    def test_discover_companies_from_query_uses_web_search_and_normalizes_results(self) -> None:
        client = _FakeClient(
            [
                {
                    "output_text": json.dumps(
                        {
                            "companies": [
                                {
                                    "name": "Acme Energy",
                                    "website": "https://acme.example/careers#jobs",
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

        companies = discover_companies_from_query(
            client,
            model="gpt-5-nano",
            query="hydrogen durability companies",
            excluded_companies=["Existing Co"],
            adjacent_scope=False,
        )

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["name"], "Acme Energy")
        self.assertIn("source:web", companies[0]["tags"])
        self.assertIn("region:DE", companies[0]["tags"])
        self.assertIn("web_search", companies[0]["discoverySources"])
        self.assertEqual(client.requests[0]["tools"], [{"type": "web_search"}])

    def test_auto_discover_companies_in_pool_updates_companies_and_query_stats(self) -> None:
        config = {
            "candidate": {"scopeProfile": "hydrogen_mainline"},
            "companyDiscovery": {
                "enableAutoDiscovery": True,
                "model": "gpt-5-nano",
                "queries": ["hydrogen companies", "battery companies"],
                "maxNewCompaniesPerRun": 2,
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
            query_stats={},
            query_budget=1,
            max_new_companies=1,
            rng=random.Random(0),
            progress_callback=progress.append,
        )

        self.assertEqual(result["added"], 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(progress), 1)
        self.assertEqual(len(result["companies"]), 2)
        self.assertEqual(result["companies"][0]["repeatCount"], 1)
        self.assertFalse(result["queryStats"])
        new_company = result["companies"][1]
        self.assertIn("domain:acme.example", build_company_identity_keys(new_company))

    def test_sample_weighted_discovery_queries_dedupes_and_respects_limit(self) -> None:
        queries = sample_weighted_discovery_queries(
            ["A", "a", "B", "C"],
            {"B": 3},
            2,
            rng=random.Random(1),
            used_queries={"C"},
        )
        self.assertEqual(len(queries), 2)
        self.assertEqual(len({item.casefold() for item in queries}), 2)
        self.assertNotIn("C", queries)

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
                core_business_areas=("placeholder",),
            )
            signals = CandidateSearchSignals(
                semantic=SemanticSearchSignals(
                    summary="Hydrogen profile",
                    target_direction_keywords=["fuel cell diagnostics"],
                    background_keywords=["hydrogen systems"],
                    core_business_areas=["electrochemical durability"],
                    strong_capabilities=["degradation analytics"],
                    adjacent_business_areas=["reliability engineering"],
                    exploration_business_areas=["industrial technology"],
                    avoid_business_areas=[],
                ),
                candidate=CandidateInputSignals(target_directions=[], notes=[]),
                profile=ProfileSearchSignals(
                    role_names=[],
                    target_roles=[],
                    keyword_focus=[],
                    company_focus=[],
                    company_keyword_focus=[],
                    queries=[],
                ),
            )

            plan = build_discovery_anchor_plan(
                scope_profile="hydrogen_mainline",
                signals=signals,
            )

            self.assertEqual(plan.core[:4], [
                "fuel cell diagnostics",
                "hydrogen systems",
                "electrochemical durability",
                "degradation analytics",
            ])
            self.assertIn("reliability engineering", plan.adjacent)
            self.assertIn("industrial technology", plan.explore)

    def test_discovery_query_bucket_order_favors_core_without_extra_weight_math(self) -> None:
        self.assertEqual(
            discovery_query_bucket_order(7),
            ["core", "adjacent", "core", "explore", "core", "adjacent", "core"],
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
                target_direction_keywords=("fuel cell diagnostics",),
                background_keywords=("hydrogen systems",),
                core_business_areas=("electrochemical durability",),
                adjacent_business_areas=(),
                exploration_business_areas=(),
                strong_capabilities=("degradation analytics",),
            )

            signals = collect_candidate_search_signals(
                candidate=candidate,
                profiles=profiles,
                semantic_profile=semantic_profile,
            )
            plan = build_discovery_anchor_plan(
                scope_profile="hydrogen_mainline",
                signals=signals,
            )

            self.assertIn("digital twin and PHM", plan.adjacent)
            self.assertIn("industrial gas platforms", plan.explore)

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
                target_direction_keywords=("fuel cell diagnostics",),
                background_keywords=("hydrogen systems",),
                core_business_areas=("electrochemical durability",),
            )

            signals = collect_candidate_search_signals(
                candidate=candidate,
                profiles=profiles,
                semantic_profile=semantic_profile,
            )
            plan = build_discovery_anchor_plan(
                scope_profile="hydrogen_mainline",
                signals=signals,
            )

            self.assertIn("advanced ceramics platforms", plan.adjacent)

    def test_build_company_discovery_queries_from_anchor_plan_uses_only_provided_anchors(self) -> None:
        queries = build_company_discovery_queries_from_anchor_plan(
            anchor_plan=DiscoveryAnchorPlan(
                core=["hydrogen systems", "fuel cells"],
                adjacent=["reliability engineering"],
                explore=[],
            ),
            rotation_seed=0,
        )

        self.assertEqual(len(queries), 6)
        self.assertEqual(len({item.casefold() for item in queries}), 6)
        self.assertTrue(any("reliability engineering" in item for item in queries))
        self.assertFalse(any("energy infrastructure platforms" in item for item in queries))

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
                target_direction_keywords=("fuel cell diagnostics",),
                background_keywords=("hydrogen systems",),
                core_business_areas=("electrochemical durability",),
                avoid_business_areas=("battery", "automotive"),
            )

            signals = collect_candidate_search_signals(
                candidate=candidate,
                profiles=profiles,
                semantic_profile=semantic_profile,
            )
            plan = build_discovery_anchor_plan(
                scope_profile="hydrogen_mainline",
                signals=signals,
            )

            self.assertFalse(any("battery" in item.casefold() for item in plan.explore))
            self.assertFalse(any("automotive" in item.casefold() for item in plan.explore))

    def test_build_discovery_anchor_plan_uses_feedback_keywords_even_with_semantic_profile(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Feedback Candidate")
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
                target_direction_keywords=("fuel cell diagnostics",),
                background_keywords=("hydrogen systems",),
                core_business_areas=("electrochemical durability",),
            )

            signals = collect_candidate_search_signals(
                candidate=candidate,
                profiles=profiles,
                semantic_profile=semantic_profile,
            )
            plan = build_discovery_anchor_plan(
                scope_profile="hydrogen_mainline",
                signals=signals,
                feedback_keywords=["digital twin asset health"],
            )

            self.assertIn("energy digitalization and PHM", plan.adjacent)

    def test_phrase_limit_independently_caps_semantic_anchor_pool(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="Hydrogen diagnostics and reliability profile",
                target_direction_keywords=["fuel cell diagnostics", "electrolyzer durability"],
                background_keywords=["hydrogen systems", "lifetime prediction"],
                core_business_areas=[
                    "electrochemical durability",
                    "stack balance of plant",
                    "MEA membrane materials",
                ],
                strong_capabilities=["degradation analytics", "reliability engineering"],
                adjacent_business_areas=["digital twin asset health"],
                exploration_business_areas=["industrial gas platforms"],
                avoid_business_areas=[],
            ),
            candidate=CandidateInputSignals(target_directions=[], notes=[]),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=[],
                keyword_focus=[],
                company_focus=[],
                company_keyword_focus=[],
                queries=[],
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
                scope_profile="hydrogen_mainline",
                signals=signals,
            )
        finally:
            discovery_module.DISCOVERY_BUCKET_RULES.clear()
            discovery_module.DISCOVERY_BUCKET_RULES.update(original_rules)

        self.assertEqual(plan.core, ["fuel cell diagnostics", "electrolyzer durability"])

    def test_minimum_anchor_count_backfills_sparse_buckets_independently(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="",
                target_direction_keywords=[],
                background_keywords=[],
                core_business_areas=[],
                strong_capabilities=[],
                adjacent_business_areas=[],
                exploration_business_areas=[],
                avoid_business_areas=[],
            ),
            candidate=CandidateInputSignals(target_directions=[], notes=[]),
            profile=ProfileSearchSignals(
                role_names=[],
                target_roles=[],
                keyword_focus=[],
                company_focus=[],
                company_keyword_focus=[],
                queries=[],
            ),
        )

        import jobflow_desktop_app.search.orchestration.company_discovery_queries as discovery_module
        original_rules = deepcopy(discovery_module.DISCOVERY_BUCKET_RULES)
        try:
            baseline = build_discovery_anchor_plan(
                scope_profile="hydrogen_mainline",
                signals=signals,
            )
            discovery_module.DISCOVERY_BUCKET_RULES["core"]["minimum_anchor_count"] = 3
            discovery_module.DISCOVERY_BUCKET_RULES["adjacent"]["minimum_anchor_count"] = 2
            expanded = build_discovery_anchor_plan(
                scope_profile="hydrogen_mainline",
                signals=signals,
            )
        finally:
            discovery_module.DISCOVERY_BUCKET_RULES.clear()
            discovery_module.DISCOVERY_BUCKET_RULES.update(original_rules)

        self.assertEqual(baseline.core, ["hydrogen systems", "fuel cells"])
        self.assertEqual(expanded.core, ["hydrogen systems", "fuel cells", "electrolyzers"])
        self.assertEqual(baseline.adjacent, ["system validation and testing"])
        self.assertEqual(expanded.adjacent, ["system validation and testing", "energy digitalization and PHM"])

    def test_query_limit_independently_changes_query_budget_and_bucket_mix(self) -> None:
        anchor_plan = DiscoveryAnchorPlan(
            core=["hydrogen systems", "fuel cells", "electrochemical diagnostics"],
            adjacent=["reliability engineering", "digital twin asset health"],
            explore=["industrial gas platforms", "battery aging diagnostics"],
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

        self.assertEqual(len(baseline_queries), 7)
        self.assertEqual(baseline_order.count("core"), 4)
        self.assertEqual(len(reduced_queries), 5)
        self.assertEqual(reduced_order.count("core"), 2)
        self.assertIn("explore", reduced_order)


if __name__ == "__main__":
    unittest.main()

