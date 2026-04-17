from __future__ import annotations

import unittest

from jobflow_desktop_app.app.pages.search_results_prerequisites import (
    active_profiles,
    blocked_ai_issue,
    search_owner_name,
    search_prerequisite_issue,
)
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord


class SearchResultsPrerequisitesTests(unittest.TestCase):
    def test_active_profiles_filters_inactive_rows(self) -> None:
        profiles = [
            SearchProfileRecord(
                profile_id=1,
                candidate_id=10,
                name="Active",
                scope_profile="",
                target_role="Active",
                location_preference="",
                is_active=True,
            ),
            SearchProfileRecord(
                profile_id=2,
                candidate_id=10,
                name="Inactive",
                scope_profile="",
                target_role="Inactive",
                location_preference="",
                is_active=False,
            ),
        ]
        filtered = active_profiles(profiles)
        self.assertEqual([profile.profile_id for profile in filtered], [1])

    def test_blocked_ai_issue_returns_default_message_for_blocking_level(self) -> None:
        issue = blocked_ai_issue(
            "zh",
            ai_validation_level="warning",
            ai_validation_message="",
            blocked_ai_levels={"warning", "error"},
        )
        self.assertIn("AI 状态未通过验证", issue)

    def test_search_owner_name_uses_resolver_and_handles_missing_id(self) -> None:
        self.assertEqual(search_owner_name(None, resolve_candidate_name=lambda _: "ignored"), "")
        self.assertEqual(search_owner_name(7, resolve_candidate_name=lambda _: "Candidate A"), "Candidate A")

    def test_search_prerequisite_issue_covers_other_running_candidate_and_missing_profiles(self) -> None:
        issue_running_other = search_prerequisite_issue(
            "zh",
            target_candidate_id=3,
            target_ai_busy=False,
            target_ai_busy_message="",
            ai_issue="",
            current_candidate_running=False,
            any_candidate_running=True,
            owner_name="Candidate A",
            has_active_profiles=True,
        )
        self.assertIn("Candidate A", issue_running_other)

        issue_missing_profiles = search_prerequisite_issue(
            "zh",
            target_candidate_id=3,
            target_ai_busy=False,
            target_ai_busy_message="",
            ai_issue="",
            current_candidate_running=False,
            any_candidate_running=False,
            owner_name="",
            has_active_profiles=False,
        )
        self.assertIn("还没有任何已启用的目标岗位", issue_missing_profiles)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
