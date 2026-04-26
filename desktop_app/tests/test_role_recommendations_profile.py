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
          "career_and_education_history": "  PhD-level energy systems researcher with industry validation experience.  ",
          "company_discovery_primary_anchors": ["fuel cell durability", "electrolyzer reliability"],
          "company_discovery_secondary_anchors": ["industrial gas decarbonization"],
          "job_fit_core_terms": ["energy systems", "energy systems", " validation ", "fuel cell systems"],
          "job_fit_support_terms": ["requirements traceability"],
          "avoid_business_areas": ["generic software roles"]
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
        self.assertEqual(
            profile.job_fit_core_terms,
            ("energy systems", "validation", "fuel cell systems"),
        )
        self.assertEqual(
            profile.company_discovery_primary_anchors,
            ("fuel cell durability", "electrolyzer reliability"),
        )
        self.assertEqual(
            profile.career_and_education_history,
            "PhD-level energy systems researcher with industry validation experience.",
        )

    def test_signature_and_cache_round_trip(self) -> None:
        candidate = self._candidate()
        signature = build_candidate_semantic_profile_source_signature(
            candidate,
            ResumeReadResult(text="resume text", source_type=".txt"),
        )
        profile = CandidateSemanticProfile(
            source_signature=signature,
            summary="Energy systems focus",
            career_and_education_history="Graduate education and industry energy systems work.",
            company_discovery_primary_anchors=("hydrogen systems",),
            job_fit_core_terms=("fuel cell systems",),
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
