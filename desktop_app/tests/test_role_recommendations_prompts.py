from __future__ import annotations

import unittest

from jobflow_desktop_app.ai.role_recommendations_models import CandidateSemanticProfile, ResumeReadResult
from jobflow_desktop_app.ai.role_recommendations_prompts import (
    CANDIDATE_SEMANTIC_PROFILE_PROMPT,
    MANUAL_ROLE_ENRICH_PROMPT,
    SYSTEM_PROMPT,
    build_candidate_semantic_profile_prompt,
    build_manual_role_enrich_prompt,
    build_role_recommendation_prompt,
    compact_role_recommendation_semantic_profile_lines,
)
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord


class RoleRecommendationsPromptsTests(unittest.TestCase):
    def _candidate(self) -> CandidateRecord:
        return CandidateRecord(
            candidate_id=1,
            name="Demo Candidate",
            email="demo@example.com",
            base_location="Munich, Germany",
            preferred_locations="Munich\nBerlin",
            target_directions="hydrogen systems",
            notes="Strong systems integration background",
            active_resume_path="resume.txt",
            created_at="",
            updated_at="",
        )

    def test_build_candidate_semantic_profile_prompt_includes_resume_and_manual_background(self) -> None:
        prompt = build_candidate_semantic_profile_prompt(
            self._candidate(),
            resume_result=ResumeReadResult(text="resume excerpt", source_type=".txt"),
        )
        self.assertIn("Candidate name: Demo Candidate", prompt)
        self.assertIn("Professional background summary (manual):", prompt)
        self.assertIn("resume excerpt", prompt)

    def test_candidate_semantic_profile_prompt_asset_rejects_naked_support_phrases(self) -> None:
        self.assertIn("must stay domain-qualified", CANDIDATE_SEMANTIC_PROFILE_PROMPT)
        self.assertIn("Do NOT output naked support/process phrases", CANDIDATE_SEMANTIC_PROFILE_PROMPT)

    def test_build_role_recommendation_prompt_includes_compact_semantic_profile_lines(self) -> None:
        profile = CandidateSemanticProfile(
            summary="Hydrogen systems focus",
            career_and_education_history="Graduate education and industry energy systems work.",
            company_discovery_primary_anchors=("hydrogen systems",),
            job_fit_core_terms=("fuel cell systems",),
            job_fit_support_terms=("systems integration", "requirements traceability"),
        )
        compact_lines = compact_role_recommendation_semantic_profile_lines(profile)
        prompt = build_role_recommendation_prompt(
            self._candidate(),
            existing_roles=[("Fuel Cell Systems Engineer", "Existing role")],
            resume_result=ResumeReadResult(text="resume excerpt", source_type=".txt"),
            semantic_profile=profile,
        )

        self.assertIn("AI semantic summary:", compact_lines)
        self.assertIn("Career and education history:", compact_lines)
        self.assertNotIn("preferred role levels", "\n".join(compact_lines))
        self.assertNotIn("role types to downgrade", "\n".join(compact_lines))
        self.assertIn("existing roles (must not repeat):", prompt)
        self.assertIn("Fuel Cell Systems Engineer", prompt)
        self.assertIn("hydrogen systems", prompt)

    def test_build_role_recommendation_prompt_asks_for_market_facing_job_titles(self) -> None:
        prompt = build_role_recommendation_prompt(
            self._candidate(),
            resume_result=ResumeReadResult(text="resume excerpt", source_type=".txt"),
        )

        self.assertIn("search lenses for finding real, currently open jobs", prompt)
        self.assertIn("not a personalized label", prompt)
        self.assertIn("Think like a recruiter choosing job families", prompt)
        self.assertIn("market-facing job-board title", prompt)
        self.assertIn("prefer 3-6 meaningful words", prompt)
        self.assertIn("Put technical specificity in role.description_zh and role.description_en", prompt)
        self.assertIn("cover genuinely different market hiring lanes", prompt)
        self.assertIn("market_search_rationale", prompt)
        self.assertIn("distinctness_check", prompt)
        self.assertIn("Scope labels describe search radius and career distance", prompt)
        self.assertIn("not a simple job-function taxonomy", prompt)
        self.assertIn("Decide the natural scope before satisfying the requested mix", prompt)
        self.assertIn("do not change the label", prompt)
        self.assertIn("Do not demote a close-evidence role from core", prompt)
        self.assertIn("Do not promote a distant role to core", prompt)
        self.assertIn("Exploratory does not mean unrelated", prompt)
        self.assertIn("Do not apply global industry bans", prompt)
        self.assertIn("Scope decision rubric:", prompt)
        self.assertIn("changed practical work setting can still be core", prompt)
        self.assertIn("different hiring lane", prompt)
        self.assertIn("different cluster of real job postings", prompt)
        self.assertIn("job-board query lane", prompt)
        self.assertNotIn("battery-only", prompt)
        self.assertNotIn("LT-PEM Fuel Cell", prompt)

    def test_role_recommendation_prompt_strengthens_existing_role_distinctness(self) -> None:
        prompt = build_role_recommendation_prompt(
            self._candidate(),
            existing_roles=[
                ("Specialized System Dynamics Engineer", "Existing narrow role"),
                ("Domain Performance Engineer", "Existing market-facing role"),
            ],
            resume_result=ResumeReadResult(text="resume excerpt", source_type=".txt"),
        )

        self.assertIn("internally group the existing roles by broad job family", prompt)
        self.assertIn("small wording change, seniority change, acronym swap", prompt)
        self.assertIn("over-specific or AI-synthetic", prompt)
        self.assertIn("treat that broader title as already covered", prompt)
        self.assertIn("cleaner market-facing rewrite of an existing role", prompt)
        self.assertIn("materially different set of real job postings", prompt)
        self.assertIn("Role-mix and count targets are subordinate to distinctness and scope correctness", prompt)
        self.assertIn("do not return a broad rewrite of that same lane", prompt)
        self.assertIn("Do not propose a broader title when existing roles already cover the same hiring intent", prompt)
        self.assertIn("distinctness_check must name the different hiring lane", prompt)

    def test_system_prompt_frames_roles_as_market_search_lenses(self) -> None:
        self.assertIn("downstream search lenses", SYSTEM_PROMPT)
        self.assertIn("not a personalized summary", SYSTEM_PROMPT)
        self.assertIn("keep the title market-facing first", SYSTEM_PROMPT)
        self.assertIn("clearly new market lanes", SYSTEM_PROMPT)
        self.assertIn("distinctness wins", SYSTEM_PROMPT)
        self.assertIn("wording-only difference is not enough", SYSTEM_PROMPT)
        self.assertIn("real job-board query lane", SYSTEM_PROMPT)
        self.assertIn("Scope labels describe search radius and career distance", SYSTEM_PROMPT)
        self.assertIn("Never relabel a role as core, adjacent, or exploratory just to satisfy the requested mix", SYSTEM_PROMPT)
        self.assertIn("Do not apply global industry bans", SYSTEM_PROMPT)
        self.assertNotIn("role names should reflect that specificity instead of broad job families", SYSTEM_PROMPT)

    def test_scope_prompt_assets_avoid_domain_specific_example_anchors(self) -> None:
        prompt_text = "\n".join([SYSTEM_PROMPT, MANUAL_ROLE_ENRICH_PROMPT])

        forbidden_anchor_text = (
            "Fuel Cell",
            "Hydrogen Systems",
            "LT-PEM",
            "battery-only",
            "Systems Integration & Test Engineer",
            "HIL/SIL",
            "Good role.name_en shape examples",
            "Bad role.name_en examples",
        )
        for forbidden in forbidden_anchor_text:
            self.assertNotIn(forbidden, prompt_text)

    def test_build_manual_role_enrich_prompt_includes_required_scope_profile(self) -> None:
        prompt = build_manual_role_enrich_prompt(
            self._candidate(),
            role_name="Localization Strategy Manager",
            rough_description="Supports vendor strategy.",
            desired_scope_profile="exploratory",
            resume_result=ResumeReadResult(text="resume excerpt", source_type=".txt"),
        )

        self.assertIn("Required scope_profile: exploratory", prompt)
        self.assertIn("must stay inside that requested scope_profile", prompt)
        self.assertIn("Scope decision rubric:", prompt)
        self.assertIn("make the selected search radius visible", prompt)
        self.assertIn("preserve it or add only one short qualifier", prompt)
        self.assertIn("Put that evidence in the descriptions", prompt)
        self.assertIn("Do not apply global industry bans", prompt)

    def test_manual_role_enrich_prompt_asset_defines_visible_scope_fit(self) -> None:
        self.assertIn("downstream search lens", MANUAL_ROLE_ENRICH_PROMPT)
        self.assertIn("user has already chosen the role type", MANUAL_ROLE_ENRICH_PROMPT)
        self.assertIn("preserve it or add only one short qualifier", MANUAL_ROLE_ENRICH_PROMPT)
        self.assertIn("usually 3-7 meaningful words", MANUAL_ROLE_ENRICH_PROMPT)
        self.assertIn("Scope labels describe search radius and career distance", MANUAL_ROLE_ENRICH_PROMPT)
        self.assertIn("changed practical work setting can still be core", MANUAL_ROLE_ENRICH_PROMPT)
        self.assertIn("nearby transferable domain", MANUAL_ROLE_ENRICH_PROMPT)
        self.assertIn("larger career narrative shift than adjacent", MANUAL_ROLE_ENRICH_PROMPT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
