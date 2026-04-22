from __future__ import annotations

import unittest

from jobflow_desktop_app.ai.role_recommendations_models import CandidateSemanticProfile, ResumeReadResult
from jobflow_desktop_app.ai.role_recommendations_prompts import (
    CANDIDATE_SEMANTIC_PROFILE_PROMPT,
    build_candidate_semantic_profile_prompt,
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
        self.assertIn("existing roles (must not repeat):", prompt)
        self.assertIn("Fuel Cell Systems Engineer", prompt)
        self.assertIn("hydrogen systems", prompt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
