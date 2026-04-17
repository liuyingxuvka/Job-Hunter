from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jobflow_desktop_app.ai.role_recommendations_resume import (
    build_missing_background_error,
    load_resume_excerpt_result,
)
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord


class RoleRecommendationsResumeTests(unittest.TestCase):
    def test_load_resume_excerpt_result_reads_text_and_applies_truncation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            resume_path = Path(temp_dir) / "resume.txt"
            resume_path.write_text("Line 1\n\n\nLine 2\n", encoding="utf-8")

            result = load_resume_excerpt_result(str(resume_path), max_chars=6)

            self.assertEqual(result.source_type, ".txt")
            self.assertTrue(result.text.startswith("Line 1"))
            self.assertIn("...[truncated]", result.text)

    def test_build_missing_background_error_requires_resume_or_manual_summary(self) -> None:
        candidate = CandidateRecord(
            candidate_id=1,
            name="Demo Candidate",
            email="demo@example.com",
            base_location="Munich, Germany",
            preferred_locations="Munich\nBerlin",
            target_directions="systems engineering",
            notes="",
            active_resume_path="resume.pdf",
            created_at="",
            updated_at="",
        )

        error_text = build_missing_background_error(
            action_name="AI role recommendations",
            candidate=candidate,
            resume_result=load_resume_excerpt_result(""),
        )
        self.assertIn("AI role recommendations needs usable candidate background information.", error_text)
        self.assertIn("Professional Background / 专业摘要", error_text)

        candidate_with_notes = CandidateRecord(
            candidate_id=1,
            name="Demo Candidate",
            email="demo@example.com",
            base_location="Munich, Germany",
            preferred_locations="Munich\nBerlin",
            target_directions="systems engineering",
            notes="Strong MBSE and verification background",
            active_resume_path="",
            created_at="",
            updated_at="",
        )
        self.assertEqual(
            build_missing_background_error(
                action_name="AI role recommendations",
                candidate=candidate_with_notes,
                resume_result=load_resume_excerpt_result(""),
            ),
            "",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
