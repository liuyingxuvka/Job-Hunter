from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from jobflow_desktop_app.app.pages.target_direction import TargetDirectionStep

try:
    from ._helpers import create_candidate, get_qapp, make_temp_context, process_events
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, get_qapp, make_temp_context, process_events  # type: ignore


class TargetDirectionStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()

    def test_manual_add_save_without_api_and_reload_does_not_write_back_bilingual_rows(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Demo Candidate")
            fake_recommender = Mock()

            with patch(
                "jobflow_desktop_app.app.pages.target_direction.OpenAIRoleRecommendationService",
                return_value=fake_recommender,
            ):
                page = TargetDirectionStep(context, ui_language="zh")
            self.addCleanup(page.deleteLater)

            page.set_candidate(candidate_id)
            process_events()

            self.assertEqual(page.direction_list.count(), 0)

            fake_dialog = Mock()
            fake_dialog.exec.return_value = QDialog.Accepted
            fake_dialog.values.return_value = (
                "系统集成与验证工程师",
                "聚焦系统集成、验证和需求追溯",
            )

            with patch(
                "jobflow_desktop_app.app.pages.target_direction.ManualRoleInputDialog",
                return_value=fake_dialog,
            ):
                QTest.mouseClick(page.add_direction_button, Qt.LeftButton)
            process_events()

            self.assertEqual(page.direction_list.count(), 1)
            saved_profiles = context.profiles.list_for_candidate(candidate_id)
            self.assertEqual(len(saved_profiles), 1)
            saved_profile = saved_profiles[0]
            self.assertEqual(saved_profile.name, "系统集成与验证工程师")
            self.assertEqual(saved_profile.target_role, "系统集成与验证工程师")
            self.assertIn("验证", saved_profile.keyword_focus)

            save_spy = Mock(wraps=context.profiles.save)
            with patch.object(context.profiles, "save", save_spy):
                page.set_candidate(candidate_id)
                process_events()

            save_spy.assert_not_called()
            self.assertEqual(page.direction_name_input.text(), "系统集成与验证工程师")
            self.assertEqual(page.direction_reason_input.toPlainText(), "聚焦系统集成、验证和需求追溯")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
