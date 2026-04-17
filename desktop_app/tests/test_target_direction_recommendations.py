from __future__ import annotations

import unittest

from jobflow_desktop_app.ai.role_recommendations import (
    TargetRoleSuggestion,
    encode_bilingual_description,
)
from jobflow_desktop_app.app.pages.target_direction_recommendations import (
    apply_role_suggestions,
    build_existing_role_context,
)
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord


class TargetDirectionRecommendationsTests(unittest.TestCase):
    def test_build_existing_role_context_filters_empty_canonical_names(self) -> None:
        profiles = [
            SearchProfileRecord(
                profile_id=1,
                candidate_id=7,
                name="Hydrogen Systems Engineer",
                scope_profile="core",
                target_role="Hydrogen Systems Engineer",
                location_preference="Munich, Germany",
                role_name_i18n="zh::氢能系统工程师||en::Hydrogen Systems Engineer",
                keyword_focus=encode_bilingual_description("氢能系统集成", "hydrogen systems integration"),
            ),
            SearchProfileRecord(
                profile_id=2,
                candidate_id=7,
                name="Ignored",
                scope_profile="",
                target_role="Ignored",
                location_preference="",
                role_name_i18n="",
                keyword_focus="zh::忽略||en::ignored",
            ),
        ]

        context = build_existing_role_context(
            profiles,
            canonical_role_name=lambda role_name_i18n, fallback_name: (
                fallback_name if "Hydrogen" in fallback_name else ""
            ),
        )

        self.assertEqual(context, [("Hydrogen Systems Engineer", "ZH: 氢能系统集成\nEN: hydrogen systems integration")])

    def test_apply_role_suggestions_dedupes_existing_and_newly_added_names(self) -> None:
        existing_profiles = [
            SearchProfileRecord(
                profile_id=1,
                candidate_id=7,
                name="Hydrogen Systems Engineer",
                scope_profile="core",
                target_role="Hydrogen Systems Engineer",
                location_preference="Munich, Germany",
                role_name_i18n="zh::氢能系统工程师||en::Hydrogen Systems Engineer",
                keyword_focus="",
            )
        ]
        suggestions = [
            TargetRoleSuggestion(
                name="Hydrogen Systems Engineer",
                name_zh="氢能系统工程师",
                name_en="Hydrogen Systems Engineer",
                description_zh="描述一",
                description_en="desc one",
                scope_profile="core",
            ),
            TargetRoleSuggestion(
                name="Fuel Cell Test Engineer",
                name_zh="燃料电池测试工程师",
                name_en="Fuel Cell Test Engineer",
                description_zh="描述二",
                description_en="desc two",
                scope_profile="adjacent",
            ),
            TargetRoleSuggestion(
                name="Fuel Cell Test Engineer",
                name_zh="燃料电池测试工程师",
                name_en="Fuel Cell Test Engineer",
                description_zh="描述三",
                description_en="desc three",
                scope_profile="adjacent",
            ),
        ]

        saved_records: list[SearchProfileRecord] = []

        def save_profile(profile: SearchProfileRecord) -> int:
            saved_records.append(profile)
            return 100 + len(saved_records)

        applied = apply_role_suggestions(
            suggestions,
            candidate=CandidateRecord(
                candidate_id=7,
                name="Candidate",
                email="",
                base_location="",
                preferred_locations="Munich, Germany",
                target_directions="",
                notes="",
                active_resume_path="",
                created_at="",
                updated_at="",
            ),
            existing_profiles=existing_profiles,
            save_profile=save_profile,
            canonical_role_name=lambda role_name_i18n, fallback_name: fallback_name,
            ui_language="zh",
        )

        self.assertEqual(applied.added_names, ("燃料电池测试工程师",))
        self.assertEqual(applied.last_profile_id, 101)
        self.assertEqual(len(saved_records), 1)
        self.assertEqual(saved_records[0].candidate_id, 7)
        self.assertEqual(saved_records[0].location_preference, "Munich, Germany")
        self.assertEqual(saved_records[0].target_role, "Fuel Cell Test Engineer")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
