from __future__ import annotations

import unittest

from jobflow_desktop_app.ai.role_recommendations import (
    RoleRecommendationMixPlan,
    TargetRoleSuggestion,
    build_role_recommendation_prompt,
    encode_bilingual_description,
)
from jobflow_desktop_app.app.pages.target_direction_recommendations import (
    apply_role_suggestions,
    build_existing_role_context,
    build_role_recommendation_mix_plan,
)
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord


class TargetDirectionRecommendationsTests(unittest.TestCase):
    def _profile(
        self,
        *,
        profile_id: int,
        name: str,
        scope_profile: str,
    ) -> SearchProfileRecord:
        return SearchProfileRecord(
            profile_id=profile_id,
            candidate_id=7,
            name=name,
            scope_profile=scope_profile,
            target_role=name,
            location_preference="Munich, Germany",
            role_name_i18n=f"zh::{name}||en::{name}",
            keyword_focus="",
        )

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

    def test_build_role_recommendation_mix_plan_fills_base_ratio_first(self) -> None:
        plan = build_role_recommendation_mix_plan(
            [
                self._profile(profile_id=1, name="Core A", scope_profile="core"),
                self._profile(profile_id=2, name="Core B", scope_profile="core"),
            ]
        )

        self.assertEqual(plan.current_total, 2)
        self.assertEqual((plan.current_core, plan.current_adjacent, plan.current_exploratory), (2, 0, 0))
        self.assertEqual((plan.request_core, plan.request_adjacent, plan.request_exploratory), (1, 2, 1))
        self.assertEqual(plan.request_total, 4)

    def test_build_role_recommendation_mix_plan_extends_to_second_ratio_after_first_stage(self) -> None:
        plan = build_role_recommendation_mix_plan(
            [
                self._profile(profile_id=1, name="Core A", scope_profile="core"),
                self._profile(profile_id=2, name="Core B", scope_profile="core"),
                self._profile(profile_id=3, name="Core C", scope_profile="core"),
                self._profile(profile_id=4, name="Adjacent A", scope_profile="adjacent"),
                self._profile(profile_id=5, name="Adjacent B", scope_profile="adjacent"),
                self._profile(profile_id=6, name="Explore A", scope_profile="exploratory"),
            ]
        )

        self.assertEqual(plan.current_total, 6)
        self.assertEqual((plan.request_core, plan.request_adjacent, plan.request_exploratory), (3, 2, 1))
        self.assertEqual(plan.request_total, 6)

    def test_build_role_recommendation_mix_plan_respects_total_cap(self) -> None:
        profiles = [
            self._profile(profile_id=index, name=f"Role {index}", scope_profile="core")
            for index in range(1, 13)
        ]
        plan = build_role_recommendation_mix_plan(profiles)

        self.assertEqual(plan.current_total, 12)
        self.assertEqual(plan.remaining_capacity, 0)
        self.assertEqual(plan.request_total, 0)

    def test_build_role_recommendation_prompt_includes_mix_plan_and_existing_roles(self) -> None:
        candidate = CandidateRecord(
            candidate_id=7,
            name="Candidate",
            email="",
            base_location="Berlin, Germany",
            preferred_locations="Berlin, Germany\nRemote",
            target_directions="Localization Program Manager",
            notes="",
            active_resume_path="",
            created_at="",
            updated_at="",
        )
        prompt = build_role_recommendation_prompt(
            candidate,
            existing_roles=[("Localization Program Manager", "ZH: 本地化项目经理\nEN: localization program manager")],
            mix_plan=RoleRecommendationMixPlan(
                total_cap=12,
                current_total=4,
                current_core=2,
                current_adjacent=1,
                current_exploratory=1,
                request_core=1,
                request_adjacent=1,
                request_exploratory=0,
            ),
        )

        self.assertIn("total saved roles: 4 / 12", prompt)
        self.assertIn("core to add this round: 1", prompt)
        self.assertIn("adjacent to add this round: 1", prompt)
        self.assertIn("exploratory to add this round: 0", prompt)
        self.assertIn("scope_profile as one of: core, adjacent, exploratory", prompt)
        self.assertIn("existing roles (must not repeat):", prompt)
        self.assertIn("Localization Program Manager", prompt)

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
