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
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord  # noqa: E402
from jobflow_desktop_app.search.orchestration import runtime_config_builder  # noqa: E402
from jobflow_desktop_app.search.orchestration.candidate_search_signals import (  # noqa: E402
    CandidateSearchSignals,
    ProfileSearchSignals,
    SemanticSearchSignals,
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
                company_discovery_primary_anchors=("hydrogen systems",),
                company_discovery_secondary_anchors=("reliability engineering",),
                job_fit_core_terms=("fuel cell diagnostics", "electrochemical durability"),
                job_fit_support_terms=("degradation analytics",),
            )

            company_discovery_config: dict = {}
            candidate_context = runtime_config_builder.populate_runtime_config_common(
                runtime_mirror,
                candidate_config={},
                search_config={},
                sources_config={},
                company_discovery_config=company_discovery_config,
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

            self.assertIs(candidate_context.signals, _FakeRunner.signals)
            self.assertEqual(
                company_discovery_config["companyDiscoveryInput"]["summary"],
                "Hydrogen reliability profile",
            )
            self.assertIn(
                "hydrogen systems",
                company_discovery_config["companyDiscoveryInput"]["desiredWorkDirections"],
            )

    def test_build_runtime_candidate_context_reuses_injected_signals_once(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Context Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            run_dir = context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}"
            run_dir.mkdir(parents=True, exist_ok=True)

            built = runtime_config_builder.build_runtime_candidate_context(
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
                semantic_profile=None,
                signals=_FakeRunner.signals,
            )

            self.assertIs(built.signals, _FakeRunner.signals)
            self.assertEqual(built.resume_path, str((run_dir / "resume.generated.md").resolve()))
            self.assertIn("Systems Engineer", built.discovery_query_input["targetRoles"])

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
                    scope_profiles=("hydrogen_mainline",),
                    target_roles=[],
                ),
                signals=_FakeRunner.signals,
                discovery_query_input={
                    "summary": "Hydrogen systems engineer",
                    "targetRoles": ["Systems Engineer"],
                    "desiredWorkDirections": ["hydrogen systems"],
                    "avoidBusinessAreas": [],
                    "locationPreference": "",
                },
            )

            with patch.object(
                runtime_config_builder,
                "build_runtime_candidate_context",
                side_effect=AssertionError("candidate context should be reused"),
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
                runtime_config["companyDiscovery"]["companyDiscoveryInput"],
                candidate_context.discovery_query_input,
            )
            self.assertEqual(runtime_config["companyDiscovery"]["maxCompaniesPerCall"], 5)
            self.assertIn("maxJobsPerQuery", runtime_config["search"])
            self.assertIn("maxCompaniesPerRun", runtime_config["sources"])
            self.assertIn("maxJobsPerCompany", runtime_config["sources"])
            self.assertIn("maxJobLinksPerCompany", runtime_config["sources"])
            self.assertIn("companyRotationIntervalDays", runtime_config["sources"])
            self.assertIn("maxCompaniesPerCall", runtime_config["companyDiscovery"])
            self.assertIn("maxJobsToAnalyzePerRun", runtime_config["analysis"])
            self.assertIn("jdFetchMaxJobsPerRun", runtime_config["analysis"])
            self.assertIn("postVerifyMaxJobsPerRun", runtime_config["analysis"])
            self.assertFalse(runtime_config["analysis"]["postVerifyEnabled"])
            self.assertFalse(runtime_config["analysis"]["postVerifyUseWebSearch"])
            self.assertFalse(runtime_config["analysis"]["postVerifyRequireChecked"])
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
                    scope_profiles=("hydrogen_mainline",),
                    target_roles=[],
                ),
                signals=_FakeRunner.signals,
                discovery_query_input={
                    "summary": "Hydrogen systems engineer",
                    "targetRoles": ["Systems Engineer"],
                    "desiredWorkDirections": ["hydrogen systems"],
                    "avoidBusinessAreas": [],
                    "locationPreference": "",
                },
            )

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
            self.assertFalse(runtime_config["companyDiscovery"]["enableAutoDiscovery"])
            self.assertNotIn("queries", runtime_config["companyDiscovery"])

    def test_build_candidate_context_company_discovery_query_input_uses_semantic_and_profile_context(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Localization Candidate",
                notes="Interested in multilingual product operations.",
            )
            create_profile(
                context,
                candidate_id,
                name="Localization Program Manager",
                keyword_focus="translation management\nvendor management",
                is_active=True,
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)
            semantic_profile = CandidateSemanticProfile(
                summary="In-house localization leader for multilingual product teams.",
                company_discovery_primary_anchors=("localization operations",),
                company_discovery_secondary_anchors=("translation management",),
                job_fit_core_terms=("terminology governance",),
                job_fit_support_terms=("multilingual content operations", "Python"),
                avoid_business_areas=("agency-only work",),
            )
            candidate_inputs = runtime_config_builder.RuntimeCandidateInputPrep(
                resume_path="resume.md",
                scope_profiles=("general_search",),
                target_roles=[{"displayName": "Localization Program Manager"}],
            )

            query_input = runtime_config_builder.build_candidate_context_company_discovery_query_input(
                candidate=candidate,
                profiles=profiles,
                semantic_profile=semantic_profile,
                candidate_inputs=candidate_inputs,
            )

        self.assertEqual(
            query_input["summary"],
            "In-house localization leader for multilingual product teams.",
        )
        self.assertEqual(query_input["targetRoles"], ["Localization Program Manager"])
        self.assertIn("localization operations", query_input["desiredWorkDirections"])
        self.assertIn("translation management", query_input["desiredWorkDirections"])
        self.assertIn("vendor management", query_input["desiredWorkDirections"])
        self.assertIn("terminology governance", query_input["desiredWorkDirections"])
        self.assertIn("multilingual content operations", query_input["desiredWorkDirections"])
        self.assertNotIn("Python", query_input["desiredWorkDirections"])
        self.assertIn("agency-only work", query_input["avoidBusinessAreas"])

    def test_build_candidate_context_company_fit_terms_prioritizes_business_terms_over_tools(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="",
                company_discovery_primary_anchors=[],
                company_discovery_secondary_anchors=[],
                job_fit_core_terms=["software localization", "translation management systems"],
                job_fit_support_terms=["linguistic quality assurance", "Python", "SQL", "memoQ"],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=["Localization Project Manager"],
                target_roles=["Localization Program Manager"],
                keyword_focus_terms=["localization operations", "translation management"],
            ),
        )
        candidate_context = runtime_config_builder.RuntimeCandidateConfigContext(
            candidate_inputs=runtime_config_builder.RuntimeCandidateInputPrep(
                resume_path="resume.md",
                scope_profiles=("general_search",),
                target_roles=[],
            ),
            signals=signals,
            discovery_query_input={},
        )

        terms = runtime_config_builder.build_candidate_context_company_fit_terms(candidate_context)

        self.assertEqual(
            terms["core"][:4],
            [
                "Localization Program Manager",
                "localization operations",
                "translation management",
                "software localization",
            ],
        )
        self.assertIn("linguistic quality assurance", terms["support"])
        self.assertIn("Localization Project Manager", terms["support"])

    def test_build_target_roles_payload_prefers_target_role_over_profile_name_placeholder(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Localization Candidate")
            context.profiles.save(
                SearchProfileRecord(
                    profile_id=None,
                    candidate_id=candidate_id,
                    name="Localization Search",
                    scope_profile="general_search",
                    target_role="Localization Program Manager",
                    location_preference="Berlin, Germany\nRemote",
                    role_name_i18n="",
                    keyword_focus="software localization\ntranslation management systems",
                    is_active=True,
                )
            )
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            payload = runtime_config_builder.build_target_roles_payload(candidate, profiles)

            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["displayName"], "Localization Program Manager")
            self.assertEqual(payload[0]["targetRoleText"], "Localization Program Manager")

    def test_build_runtime_candidate_context_from_inputs_uses_prepared_inputs(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Prepared Context Candidate")
            create_profile(context, candidate_id, name="Systems Engineer", is_active=True)
            candidate = context.candidates.get(candidate_id)
            profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertIsNotNone(candidate)

            prepared_inputs = runtime_config_builder.RuntimeCandidateInputPrep(
                resume_path="resume.md",
                scope_profiles=("hydrogen_mainline",),
                target_roles=[],
            )
            built = runtime_config_builder.build_runtime_candidate_context_from_inputs(
                candidate=candidate,
                profiles=profiles,
                semantic_profile=None,
                candidate_inputs=prepared_inputs,
                signals=_FakeRunner.signals,
            )

            self.assertIs(built.signals, _FakeRunner.signals)
            self.assertEqual(built.discovery_query_input["targetRoles"], [])

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

            self.assertEqual(prepared.scope_profiles, ())
            self.assertTrue(prepared.target_roles)
            self.assertTrue(prepared.resume_path.endswith("resume.generated.md"))
            self.assertIn(
                "Prepared Candidate",
                Path(prepared.resume_path).read_text(encoding="utf-8"),
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
                    scope_profiles=("hydrogen_mainline",),
                    target_roles=[],
                ),
            signals=_FakeRunner.signals,
            discovery_query_input={},
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
        self.assertNotIn("maxCompaniesPerCall", config["companyDiscovery"])
        self.assertNotIn("maxJobsToAnalyzePerRun", config["analysis"])
        self.assertNotIn("postVerifyMaxJobsPerRun", config["analysis"])
        self.assertFalse(config["analysis"]["postVerifyEnabled"])
        self.assertFalse(config["analysis"]["postVerifyUseWebSearch"])
        self.assertFalse(config["analysis"]["postVerifyRequireChecked"])

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
