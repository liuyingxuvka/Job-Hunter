from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.ai.role_recommendations import CandidateSemanticProfile  # noqa: E402
from jobflow_desktop_app.search.orchestration import runtime_config_builder  # noqa: E402
from jobflow_desktop_app.search.orchestration.company_discovery_queries import (  # noqa: E402
    DiscoveryAnchorPlan,
)


class _FakeRunner:
    runtime_mirror = None
    signals = object()


class RuntimeConfigBuilderTests(unittest.TestCase):
    def test_resolve_effective_max_companies_prefers_runtime_cap_when_present(self) -> None:
        self.assertEqual(
            runtime_config_builder.resolve_effective_max_companies(
                requested_max_companies=5,
                runtime_config={"sources": {"maxCompaniesPerRun": 3}},
            ),
            3,
        )
        self.assertEqual(
            runtime_config_builder.resolve_effective_max_companies(
                requested_max_companies=5,
                runtime_config={"sources": {}},
            ),
            5,
        )
        self.assertEqual(
            runtime_config_builder.resolve_effective_max_companies(
                requested_max_companies=None,
                runtime_config={"sources": {"maxCompaniesPerRun": 4}},
            ),
            4,
        )

    def test_populate_runtime_config_common_reuses_one_candidate_signal_payload(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Demo Candidate",
                notes="Hydrogen degradation and diagnostics",
            )
            create_profile(
                context,
                candidate_id,
                name="Hydrogen Systems Engineer",
                scope_profile="hydrogen_mainline",
                keyword_focus="hydrogen diagnostics",
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            runtime_mirror = None
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            semantic_profile = CandidateSemanticProfile(
                summary="Hydrogen reliability profile",
                background_keywords=("hydrogen systems",),
                target_direction_keywords=("fuel cell diagnostics",),
                core_business_areas=("electrochemical durability",),
                adjacent_business_areas=("reliability engineering",),
                exploration_business_areas=("industrial technology",),
                strong_capabilities=("degradation analytics",),
            )

            discovery_anchor_plan = DiscoveryAnchorPlan(
                core=["hydrogen systems"],
                adjacent=["reliability"],
                explore=["industrial technology"],
            )
            with patch(
                "jobflow_desktop_app.search.orchestration.runtime_config_builder.company_discovery_queries_module.build_discovery_anchor_plan",
                return_value=discovery_anchor_plan,
            ) as build_anchor_plan, patch(
                "jobflow_desktop_app.search.orchestration.runtime_config_builder.company_discovery_queries_module.build_company_discovery_queries_from_anchor_plan",
                return_value=["hydrogen systems companies"],
            ) as build_queries:
                company_discovery_queries = runtime_config_builder.populate_runtime_config_common(
                    runtime_mirror,
                    candidate_config={},
                    search_config={},
                    sources_config={},
                    company_discovery_config={},
                    analysis_config={},
                    translation_config={},
                    fetch_config={},
                    candidate=candidate,
                    profiles=profiles,
                    run_dir=run_dir,
                    query_rotation_seed=17,
                    semantic_profile=semantic_profile,
                    model_override="",
                    signals=_FakeRunner.signals,
                )

            self.assertEqual(company_discovery_queries, ["hydrogen systems companies"])
            self.assertIs(build_anchor_plan.call_args.kwargs["signals"], _FakeRunner.signals)
            self.assertEqual(
                build_queries.call_args.kwargs["anchor_plan"],
                discovery_anchor_plan,
            )

    def test_build_runtime_candidate_context_reuses_injected_signals_once(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Context Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            runtime_mirror = None
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)

            discovery_anchor_plan = DiscoveryAnchorPlan(core=["hydrogen systems"], adjacent=[], explore=[])
            with patch(
                "jobflow_desktop_app.search.orchestration.runtime_config_builder.company_discovery_queries_module.build_discovery_anchor_plan",
                return_value=discovery_anchor_plan,
            ):
                built = runtime_config_builder.build_runtime_candidate_context(
                    runtime_mirror,
                    candidate=candidate,
                    profiles=profiles,
                    run_dir=run_dir,
                    semantic_profile=None,
                    signals=_FakeRunner.signals,
                )

            self.assertIs(built.signals, _FakeRunner.signals)
            self.assertEqual(built.resume_path, str((run_dir / "resume.generated.md").resolve()))
            self.assertIs(built.discovery_anchor_plan, discovery_anchor_plan)

    def test_build_runtime_config_reuses_provided_candidate_context(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Context Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            candidate_context = runtime_config_builder.RuntimeCandidateConfigContext(
                candidate_inputs=runtime_config_builder.RuntimeCandidateInputPrep(
                    resume_path=str((run_dir / "resume.generated.md").resolve()),
                    resume_text="",
                    scope_profile="hydrogen_mainline",
                    target_role="Systems Engineer",
                    target_roles=[],
                ),
                signals=_FakeRunner.signals,
                discovery_anchor_plan=DiscoveryAnchorPlan(core=["hydrogen systems"], adjacent=[], explore=[]),
            )

            with patch.object(
                runtime_config_builder,
                "build_runtime_candidate_context",
                side_effect=AssertionError("candidate context should be reused"),
            ), patch.object(
                runtime_config_builder,
                "build_candidate_context_company_discovery_queries",
                return_value=["hydrogen systems companies"],
            ):
                runtime_config = runtime_config_builder.build_runtime_config(
                    None,
                    base_config={},
                    candidate=candidate,
                    profiles=profiles,
                    run_dir=run_dir,
                    query_rotation_seed=7,
                    candidate_context=candidate_context,
                )

            self.assertEqual(
                runtime_config["companyDiscovery"]["queries"],
                ["hydrogen systems companies"],
            )
            self.assertIn("maxJobsPerQuery", runtime_config["search"])
            self.assertIn("maxCompaniesPerRun", runtime_config["sources"])
            self.assertIn("maxJobsPerCompany", runtime_config["sources"])
            self.assertIn("maxJobLinksPerCompany", runtime_config["sources"])
            self.assertIn("companyRotationIntervalDays", runtime_config["sources"])
            self.assertIn("companyRotationSeed", runtime_config["sources"])
            self.assertIn("maxNewCompaniesPerRun", runtime_config["companyDiscovery"])
            self.assertIn("maxCompaniesPerQuery", runtime_config["companyDiscovery"])
            self.assertIn("maxJobsToAnalyzePerRun", runtime_config["analysis"])
            self.assertIn("jdFetchMaxJobsPerRun", runtime_config["analysis"])
            self.assertIn("postVerifyMaxJobsPerRun", runtime_config["analysis"])
            self.assertEqual(
                runtime_config["fetch"]["timeoutMs"],
                runtime_config_builder.HTTP_REQUEST_TIMEOUT_MS,
            )

    def test_build_runtime_config_resume_pending_injects_analysis_and_fetch_defaults(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Resume Pending Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)
            candidate_context = runtime_config_builder.RuntimeCandidateConfigContext(
                candidate_inputs=runtime_config_builder.RuntimeCandidateInputPrep(
                    resume_path=str((run_dir / "resume.generated.md").resolve()),
                    resume_text="",
                    scope_profile="hydrogen_mainline",
                    target_role="Systems Engineer",
                    target_roles=[],
                ),
                signals=_FakeRunner.signals,
                discovery_anchor_plan=DiscoveryAnchorPlan(core=["hydrogen systems"], adjacent=[], explore=[]),
            )

            with patch.object(
                runtime_config_builder,
                "build_candidate_context_company_discovery_queries",
                return_value=["hydrogen systems companies"],
            ):
                runtime_config = runtime_config_builder.build_runtime_config(
                    None,
                    base_config={},
                    candidate=candidate,
                    profiles=profiles,
                    run_dir=run_dir,
                    pipeline_stage="resume_pending",
                    candidate_context=candidate_context,
                )

            self.assertEqual(
                runtime_config["fetch"]["timeoutMs"],
                runtime_config_builder.HTTP_REQUEST_TIMEOUT_MS,
            )
            self.assertEqual(runtime_config["analysis"]["maxJobsToAnalyzePerRun"], 60)
            self.assertEqual(runtime_config["analysis"]["jdFetchMaxJobsPerRun"], 60)
            self.assertEqual(runtime_config["analysis"]["postVerifyMaxJobsPerRun"], 60)
            self.assertFalse(runtime_config["sources"]["enableCompanySources"])
            self.assertFalse(runtime_config["companyDiscovery"]["enableAutoDiscovery"])

    def test_build_candidate_context_company_discovery_queries_uses_anchor_plan(self) -> None:
        candidate_context = runtime_config_builder.RuntimeCandidateConfigContext(
            candidate_inputs=runtime_config_builder.RuntimeCandidateInputPrep(
                resume_path="resume.md",
                resume_text="resume text",
                scope_profile="hydrogen_mainline",
                target_role="Systems Engineer",
                target_roles=[],
            ),
            signals=_FakeRunner.signals,
            discovery_anchor_plan=DiscoveryAnchorPlan(core=["hydrogen systems"], adjacent=[], explore=[]),
        )

        with patch(
            "jobflow_desktop_app.search.orchestration.runtime_config_builder.company_discovery_queries_module.build_company_discovery_queries_from_anchor_plan",
            return_value=["hydrogen systems companies"],
        ) as build_queries:
            queries = runtime_config_builder.build_candidate_context_company_discovery_queries(
                candidate_context,
                query_rotation_seed=11,
            )

        self.assertEqual(queries, ["hydrogen systems companies"])
        self.assertEqual(
            build_queries.call_args.kwargs["anchor_plan"],
            candidate_context.discovery_anchor_plan,
        )

    def test_build_runtime_candidate_context_from_inputs_uses_prepared_inputs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Prepared Context Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            prepared_inputs = runtime_config_builder.RuntimeCandidateInputPrep(
                resume_path="resume.md",
                resume_text="resume text",
                scope_profile="hydrogen_mainline",
                target_role="Systems Engineer",
                target_roles=[],
            )
            with patch(
                "jobflow_desktop_app.search.orchestration.runtime_config_builder.company_discovery_queries_module.build_discovery_anchor_plan",
                return_value=DiscoveryAnchorPlan(core=["hydrogen systems"], adjacent=[], explore=[]),
            ) as build_anchor_plan:
                built = runtime_config_builder.build_runtime_candidate_context_from_inputs(
                    candidate=candidate,
                    profiles=profiles,
                    semantic_profile=None,
                    candidate_inputs=prepared_inputs,
                    signals=_FakeRunner.signals,
                    feedback_keywords=["pem", "durability"],
                )

            self.assertIs(built.signals, _FakeRunner.signals)
            self.assertIs(build_anchor_plan.call_args.kwargs["signals"], _FakeRunner.signals)
            self.assertEqual(build_anchor_plan.call_args.kwargs["resume_text"], "resume text")
            self.assertEqual(
                build_anchor_plan.call_args.kwargs["feedback_keywords"],
                ["pem", "durability"],
            )

    def test_prepare_runtime_candidate_inputs_collects_once(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Prepared Candidate",
                notes="Hydrogen systems diagnostics",
            )
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)

            prepared = runtime_config_builder.prepare_runtime_candidate_inputs(
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
            )

            self.assertEqual(prepared.scope_profile, "adjacent_mbse")
            self.assertEqual(prepared.target_role, "Systems Engineer")
            self.assertTrue(prepared.resume_path.endswith("resume.generated.md"))
            self.assertIn("Prepared Candidate", prepared.resume_text)

    def test_refresh_runtime_candidate_context_reloads_feedback_keywords(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Refresh Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            candidate_context = runtime_config_builder.RuntimeCandidateConfigContext(
                candidate_inputs=runtime_config_builder.RuntimeCandidateInputPrep(
                    resume_path="resume.md",
                    resume_text="resume text",
                    scope_profile="hydrogen_mainline",
                    target_role="Systems Engineer",
                    target_roles=[],
                ),
                signals=_FakeRunner.signals,
                discovery_anchor_plan=DiscoveryAnchorPlan(core=["hydrogen systems"], adjacent=[], explore=[]),
            )

            runtime_mirror = SimpleNamespace(
                load_latest_run_feedback=lambda candidate_id: {"keywords": ["digital twin", "phm"]},
            )
            with patch(
                "jobflow_desktop_app.search.orchestration.runtime_config_builder.company_discovery_queries_module.build_discovery_anchor_plan",
                return_value=DiscoveryAnchorPlan(core=["digital twin"], adjacent=["phm"], explore=[]),
            ) as build_anchor_plan:
                refreshed = runtime_config_builder.refresh_runtime_candidate_context(
                    runtime_mirror,
                    candidate=candidate,
                    profiles=profiles,
                    semantic_profile=None,
                    candidate_context=candidate_context,
                )

            self.assertIsNot(refreshed, candidate_context)
            self.assertEqual(
                build_anchor_plan.call_args.kwargs["feedback_keywords"],
                ["digital twin", "phm"],
            )

    def test_apply_runtime_candidate_context_keeps_search_priorities_without_semantic_profile(self) -> None:
        candidate = SimpleNamespace(
            base_location_struct=None,
            preferred_locations_struct=None,
            base_location="Berlin",
            preferred_locations="Remote",
        )
        candidate_config: dict = {}
        search_config: dict = {}
        candidate_context = runtime_config_builder.RuntimeCandidateConfigContext(
            candidate_inputs=runtime_config_builder.RuntimeCandidateInputPrep(
                resume_path="resume.md",
                resume_text="resume text",
                scope_profile="hydrogen_mainline",
                target_role="Systems Engineer",
                target_roles=[],
            ),
            signals=_FakeRunner.signals,
            discovery_anchor_plan=DiscoveryAnchorPlan(core=["hydrogen systems"], adjacent=[], explore=[]),
        )

        runtime_config_builder.apply_runtime_candidate_context(
            candidate_config=candidate_config,
            search_config=search_config,
            candidate=candidate,
            candidate_context=candidate_context,
            semantic_profile=None,
        )

        self.assertNotIn("searchPriorities", candidate_config)

    def test_runtime_config_sections_returns_named_sections_object(self) -> None:
        sections = runtime_config_builder.runtime_config_sections({})

        self.assertTrue(hasattr(sections, "candidate"))
        self.assertTrue(hasattr(sections, "analysis"))
        self.assertIsInstance(sections.search, dict)

    def test_load_base_config_omits_runtime_derived_fields(self) -> None:
        config = runtime_config_builder.load_base_config()

        self.assertNotIn("maxJobsPerQuery", config["search"])
        self.assertNotIn("queries", config["search"])
        self.assertNotIn("maxCompaniesPerRun", config["sources"])
        self.assertNotIn("maxJobsPerCompany", config["sources"])
        self.assertNotIn("maxJobLinksPerCompany", config["sources"])
        self.assertNotIn("companyRotationIntervalDays", config["sources"])
        self.assertNotIn("enableWebSearch", config["sources"])
        self.assertNotIn("enableCompanySearchFallback", config["sources"])
        self.assertNotIn("enableAutoDiscovery", config["companyDiscovery"])
        self.assertNotIn("queries", config["companyDiscovery"])
        self.assertNotIn("maxNewCompaniesPerRun", config["companyDiscovery"])
        self.assertNotIn("maxCompaniesPerQuery", config["companyDiscovery"])
        self.assertNotIn("maxJobsToAnalyzePerRun", config["analysis"])
        self.assertNotIn("postVerifyMaxJobsPerRun", config["analysis"])

    def test_build_runtime_env_keeps_env_mode_api_key_from_named_variable(self) -> None:
        settings = SimpleNamespace(
            api_key_source="env",
            api_key="",
            api_key_env_var="JOBFLOW_TEST_OPENAI_KEY",
            model="",
        )
        original = os.environ.get("JOBFLOW_TEST_OPENAI_KEY")
        os.environ["JOBFLOW_TEST_OPENAI_KEY"] = "env-secret"
        try:
            env = runtime_config_builder.build_runtime_env(settings, api_base_url="")
        finally:
            if original is None:
                os.environ.pop("JOBFLOW_TEST_OPENAI_KEY", None)
            else:
                os.environ["JOBFLOW_TEST_OPENAI_KEY"] = original

        self.assertEqual(env["OPENAI_API_KEY"], "env-secret")


if __name__ == "__main__":
    unittest.main()
