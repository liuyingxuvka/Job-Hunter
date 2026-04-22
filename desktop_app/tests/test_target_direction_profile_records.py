from __future__ import annotations

import unittest

from jobflow_desktop_app.app.pages.target_direction_profile_records import (
    build_new_profile_record,
    build_updated_profile_record,
    prepare_profile_content,
)
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord


class TargetDirectionProfileRecordsTests(unittest.TestCase):
    def test_prepare_profile_content_normalizes_names_and_flags_generic_titles(self) -> None:
        prepared = prepare_profile_content(
            role_name_zh="系统工程师",
            role_name_en="Engineer",
            description_zh="负责系统集成",
            description_en="Owns systems integration",
            fallback_name="系统工程师",
            untitled_label="未命名岗位",
            canonical_role_name=lambda role_name_i18n, fallback_name: "Engineer",
            is_generic_role_name=lambda canonical_name: canonical_name == "Engineer",
        )

        self.assertEqual(prepared.canonical_name, "Engineer")
        self.assertTrue(prepared.is_generic)
        self.assertIn("Owns systems integration", prepared.keyword_focus)

    def test_build_new_and_updated_profile_records_preserve_expected_fields(self) -> None:
        prepared = prepare_profile_content(
            role_name_zh="燃料电池测试工程师",
            role_name_en="Fuel Cell Test Engineer",
            description_zh="负责测试",
            description_en="Leads test work",
            fallback_name="Fuel Cell Test Engineer",
            untitled_label="Untitled Role",
            canonical_role_name=lambda role_name_i18n, fallback_name: fallback_name,
            is_generic_role_name=lambda canonical_name: False,
        )
        new_record = build_new_profile_record(
            candidate=CandidateRecord(
                candidate_id=11,
                name="Test Candidate",
                email="",
                base_location="",
                preferred_locations="Berlin, Germany",
                target_directions="",
                notes="",
                active_resume_path="",
                created_at="",
                updated_at="",
            ),
            scope_profile="adjacent",
            prepared=prepared,
            is_active=True,
        )

        self.assertEqual(new_record.profile_id, None)
        self.assertEqual(new_record.target_role, "Fuel Cell Test Engineer")
        self.assertEqual(new_record.location_preference, "Berlin, Germany")

        existing = SearchProfileRecord(
            profile_id=3,
            candidate_id=11,
            name="Old Name",
            scope_profile="core",
            target_role="Old Name",
            location_preference="Munich, Germany",
        )
        updated_record = build_updated_profile_record(
            profile_id=3,
            candidate=CandidateRecord(
                candidate_id=11,
                name="Test Candidate",
                email="",
                base_location="",
                preferred_locations="Berlin, Germany",
                target_directions="",
                notes="",
                active_resume_path="",
                created_at="",
                updated_at="",
            ),
            existing_profile=existing,
            prepared=prepared,
            is_active=False,
        )

        self.assertEqual(updated_record.scope_profile, "core")
        self.assertEqual(updated_record.location_preference, "Munich, Germany")
        self.assertFalse(updated_record.is_active)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
