from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jobflow_desktop_app.ai.role_recommendations_models import CandidateSemanticProfile, ResumeReadResult
from jobflow_desktop_app.ai.role_recommendations_profile import (
    build_candidate_semantic_profile_source_signature,
    load_candidate_semantic_profile_cache,
    parse_candidate_semantic_profile,
    save_candidate_semantic_profile_cache,
)
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord


class RoleRecommendationsProfileTests(unittest.TestCase):
    def _candidate(self) -> CandidateRecord:
        return CandidateRecord(
            candidate_id=1,
            name="Demo Candidate",
            email="demo@example.com",
            base_location="Munich, Germany",
            preferred_locations="Munich\nBerlin",
            target_directions="energy systems",
            notes="Strong systems integration background",
            active_resume_path="resume.txt",
            created_at="",
            updated_at="",
        )

    def test_parse_candidate_semantic_profile_normalizes_usable_payload(self) -> None:
        payload = """
        {
          "summary": "  Focused on energy systems and reliability.  ",
          "background_keywords": ["energy systems", "energy systems", " validation "],
          "target_direction_keywords": ["hydrogen systems"],
          "core_business_areas": ["fuel cell systems"],
          "adjacent_business_areas": [],
          "exploration_business_areas": [],
          "avoid_business_areas": ["generic software roles"],
          "strong_capabilities": ["requirements traceability"],
          "seniority_signals": ["senior"]
        }
        """.strip()

        profile = parse_candidate_semantic_profile(
            payload,
            source_signature="sig-1",
            extract_json_object_text=lambda text: text,
        )

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.source_signature, "sig-1")
        self.assertEqual(profile.summary, "Focused on energy systems and reliability.")
        self.assertEqual(profile.background_keywords, ("energy systems", "validation"))

    def test_signature_and_cache_round_trip(self) -> None:
        candidate = self._candidate()
        signature = build_candidate_semantic_profile_source_signature(
            candidate,
            ResumeReadResult(text="resume text", source_type=".txt"),
        )
        profile = CandidateSemanticProfile(
            source_signature=signature,
            summary="Energy systems focus",
            target_direction_keywords=("hydrogen systems",),
            core_business_areas=("fuel cell systems",),
        )

        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "semantic_profile.json"
            save_candidate_semantic_profile_cache(cache_path, profile)
            loaded = load_candidate_semantic_profile_cache(
                cache_path,
                source_signature=signature,
                extract_json_object_text=lambda text: text,
            )

        self.assertEqual(loaded, profile)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
