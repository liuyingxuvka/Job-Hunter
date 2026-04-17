from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from jobflow_desktop_app.app.pages.target_direction import TargetDirectionStep
from jobflow_desktop_app.app.pages.workspace import CandidateWorkspacePage

try:
    from ._helpers import (
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
        save_openai_settings,
    )
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        FakeJobSearchRunner,
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
        save_openai_settings,
    )


class TargetDirectionRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def _make_target_step(self, context) -> TargetDirectionStep:
        with patch(
                "jobflow_desktop_app.app.pages.target_direction.OpenAIRoleRecommendationService",
            return_value=Mock(),
        ):
            step = TargetDirectionStep(context, ui_language="zh")
        self.addCleanup(step.deleteLater)
        return step

    def _make_workspace_page(self, context) -> CandidateWorkspacePage:
        fake_runner = FakeJobSearchRunner()
        fake_runner.set_jobs([])
        with patch("jobflow_desktop_app.app.pages.search_results.JobSearchRunner", return_value=fake_runner):
            page = CandidateWorkspacePage(context, ui_language="zh", on_data_changed=Mock())
        self.addCleanup(page.deleteLater)
        return page

    @staticmethod
    def _select_profile(step: TargetDirectionStep, profile_id: int) -> None:
        for row in range(step.direction_list.count()):
            item = step.direction_list.item(row)
            if item is not None and item.data(Qt.UserRole) == profile_id:
                step.direction_list.setCurrentRow(row)
                process_events()
                return
        raise AssertionError(f"Profile id {profile_id} not found in direction list")

    def test_empty_scope_profile_stays_empty_through_save_and_reload(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Scope Candidate")
            profile_id = create_profile(
                context,
                candidate_id,
                name="Systems Validation Lead",
                scope_profile="",
                keyword_focus="validation and integration",
                is_active=True,
            )

            step = self._make_target_step(context)
            step.set_candidate(candidate_id)
            process_events()
            self._select_profile(step, profile_id)

            step.direction_name_input.setText("Systems Validation Lead")
            step.direction_reason_input.setPlainText("Validation and integration ownership")
            process_events()

            QTest.mouseClick(step.save_direction_button, Qt.LeftButton)
            process_events()

            saved_profile = context.profiles.get(profile_id)
            self.assertIsNotNone(saved_profile)
            self.assertEqual(saved_profile.scope_profile, "")
            self.assertEqual(step.current_profile_id, profile_id)
            self.assertEqual(step.direction_list.currentItem().data(Qt.UserRole), profile_id)

    def test_async_save_preserves_current_selection_when_user_switches_profiles_before_callback(self) -> None:
        with make_temp_context() as context:
            save_openai_settings(context, api_key="test-key", model="gpt-5-nano")
            candidate_id = create_candidate(context, name="Async Save Candidate")
            profile_a_id = create_profile(
                context,
                candidate_id,
                name="Alpha Validation Lead",
                scope_profile="",
                keyword_focus="alpha validation",
                is_active=True,
            )
            profile_b_id = create_profile(
                context,
                candidate_id,
                name="Zulu Systems Lead",
                scope_profile="",
                keyword_focus="zulu systems",
                is_active=True,
            )

            captured: dict[str, object] = {}

            def fake_run_busy_task(_owner, **kwargs):
                captured.update(kwargs)
                return True

            step = self._make_target_step(context)
            step.set_candidate(candidate_id)
            step.set_ai_validation_state("Validation passed", "ready")
            process_events()
            self._select_profile(step, profile_a_id)
            self.assertEqual(step.current_profile_id, profile_a_id)

            with (
                patch("jobflow_desktop_app.app.pages.target_direction.run_busy_task", side_effect=fake_run_busy_task),
                patch.object(step, "_complete_role_name_pair", return_value=("Alpha Validation Lead", "Alpha Validation Lead")),
                patch.object(step, "_complete_description_pair", return_value=("alpha validation", "alpha validation")),
            ):
                step.direction_name_input.setText("Alpha Validation Lead")
                step.direction_reason_input.setPlainText("alpha validation")
                QTest.mouseClick(step.save_direction_button, Qt.LeftButton)
                process_events()

                self.assertIn("task", captured)
                self.assertIn("on_success", captured)
                self.assertIn("on_finally", captured)

                self._select_profile(step, profile_b_id)
                self.assertEqual(step.current_profile_id, profile_b_id)

                result = captured["task"]()
                captured["on_success"](result)
                captured["on_finally"]()
                process_events()

            self.assertEqual(step.current_profile_id, profile_b_id)
            self.assertEqual(step.direction_list.currentItem().data(Qt.UserRole), profile_b_id)
            self.assertIsNotNone(context.profiles.get(profile_a_id))
            self.assertIsNotNone(context.profiles.get(profile_b_id))

    def test_ai_busy_state_is_isolated_by_candidate(self) -> None:
        with make_temp_context() as context:
            save_openai_settings(context, api_key="test-key", model="gpt-5-nano")
            candidate_a_id = create_candidate(context, name="Candidate A")
            candidate_b_id = create_candidate(context, name="Candidate B")
            create_profile(context, candidate_a_id, name="Systems Engineer", scope_profile="", is_active=True)
            create_profile(context, candidate_b_id, name="Validation Engineer", scope_profile="", is_active=True)

            page = self._make_workspace_page(context)
            page.set_candidate(candidate_a_id)
            process_events()
            self.assertTrue(page.results_step.refresh_button.isEnabled())

            page.target_direction_step._set_ai_busy_state(True, "Step 2 busy for A", candidate_id=candidate_a_id)
            process_events()

            self.assertTrue(page.target_direction_step.is_ai_busy_for(candidate_a_id))
            self.assertFalse(page.target_direction_step.is_ai_busy_for(candidate_b_id))
            self.assertEqual(page.target_direction_step.ai_busy_message_for(candidate_a_id), "Step 2 busy for A")
            self.assertEqual(page.target_direction_step.ai_busy_message_for(candidate_b_id), "")
            self.assertFalse(page.results_step.refresh_button.isEnabled())
            self.assertIn("Step 2 busy for A", page.results_step.refresh_button.toolTip())

            page.set_candidate(candidate_b_id)
            process_events()

            self.assertFalse(page.target_direction_step.is_ai_busy_for(candidate_b_id))
            self.assertTrue(page.results_step.refresh_button.isEnabled())
            self.assertNotIn("Step 2 busy for A", page.results_step.refresh_button.toolTip())

    def test_generate_button_shows_visible_reason_when_ai_is_busy(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Busy Candidate")

            step = self._make_target_step(context)
            step.set_candidate(candidate_id)
            step.set_ai_validation_state("Validation passed", "ready")
            process_events()

            self.assertTrue(step.generate_directions_button.isEnabled())
            self.assertTrue(step.generate_issue_label.isHidden())

            step._set_ai_busy_state(True, "Step 2 busy right now", candidate_id=candidate_id)
            process_events()

            self.assertFalse(step.generate_directions_button.isEnabled())
            self.assertEqual(step.generate_directions_button.toolTip(), "Step 2 busy right now")
            self.assertFalse(step.generate_issue_label.isHidden())
            self.assertEqual(step.generate_issue_label.text(), "Step 2 busy right now")

            step._set_ai_busy_state(False, "", candidate_id=candidate_id)
            process_events()

            self.assertTrue(step.generate_directions_button.isEnabled())
            self.assertTrue(step.generate_issue_label.isHidden())

    def test_candidate_switch_preserves_selection_only_for_same_candidate(self) -> None:
        with make_temp_context() as context:
            candidate_a_id = create_candidate(context, name="Candidate A")
            candidate_b_id = create_candidate(context, name="Candidate B")
            profile_a1_id = create_profile(context, candidate_a_id, name="Alpha Role", scope_profile="", is_active=True)
            profile_a2_id = create_profile(context, candidate_a_id, name="Zulu Role", scope_profile="", is_active=True)
            profile_b_id = create_profile(context, candidate_b_id, name="Beta Role", scope_profile="", is_active=True)

            page = self._make_workspace_page(context)
            page.set_candidate(candidate_a_id)
            process_events()

            self._select_profile(page.target_direction_step, profile_a2_id)
            self.assertEqual(page.target_direction_step.current_profile_id, profile_a2_id)

            page.set_candidate(candidate_a_id)
            process_events()

            self.assertEqual(page.target_direction_step.current_candidate_id, candidate_a_id)
            self.assertEqual(page.target_direction_step.current_profile_id, profile_a2_id)
            self.assertEqual(page.target_direction_step.direction_list.currentItem().data(Qt.UserRole), profile_a2_id)

            page.set_candidate(candidate_b_id)
            process_events()

            self.assertEqual(page.target_direction_step.current_candidate_id, candidate_b_id)
            self.assertEqual(page.target_direction_step.current_profile_id, profile_b_id)
            self.assertEqual(page.target_direction_step.direction_list.currentItem().data(Qt.UserRole), profile_b_id)
            self.assertNotEqual(page.target_direction_step.current_profile_id, profile_a1_id)
            self.assertNotEqual(page.target_direction_step.current_profile_id, profile_a2_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
