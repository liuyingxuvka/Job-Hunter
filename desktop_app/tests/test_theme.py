from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleOptionViewItem

from jobflow_desktop_app.app.pages.target_direction import TargetDirectionStep
from jobflow_desktop_app.app.pages.target_direction_role_list_delegate import (
    TargetRoleListDelegate,
    is_checked_state,
)
from jobflow_desktop_app.app.theme import APP_STYLESHEET, apply_theme

try:
    from ._helpers import (
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
    )
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import (  # type: ignore
        create_candidate,
        create_profile,
        get_qapp,
        make_temp_context,
        process_events,
    )


class ThemeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = get_qapp()
        apply_theme(self.app)

    def test_target_role_list_uses_delegate_owned_checkbox_rendering(self) -> None:
        self.assertNotIn("checkbox_unchecked.svg", APP_STYLESHEET)
        self.assertNotIn("checkbox_checked_black.svg", APP_STYLESHEET)
        self.assertNotIn("QListWidget#TargetRoleList::indicator", APP_STYLESHEET)

    def test_delegate_checked_state_helper_accepts_qt_model_int_values(self) -> None:
        self.assertTrue(is_checked_state(2))
        self.assertTrue(is_checked_state(Qt.Checked))
        self.assertFalse(is_checked_state(0))
        self.assertFalse(is_checked_state(Qt.Unchecked))

    def test_target_role_delegate_selection_highlight_wins_over_checked_state(self) -> None:
        selected = TargetRoleListDelegate.row_colors(is_selected=True, is_checked=False, is_enabled=True)
        checked_only = TargetRoleListDelegate.row_colors(is_selected=False, is_checked=True, is_enabled=True)
        selected_and_checked = TargetRoleListDelegate.row_colors(is_selected=True, is_checked=True, is_enabled=True)

        self.assertEqual(selected["bg"].name(), "#0f7b6c")
        self.assertEqual(selected_and_checked["bg"].name(), "#0f7b6c")
        self.assertIsNone(checked_only["bg"])
        self.assertEqual(selected_and_checked["text"].name(), "#ffffff")
        self.assertEqual(checked_only["text"].name(), "#1f2933")

    def test_target_role_items_stay_compact_under_theme(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Theme Candidate", base_location="Berlin")
            create_profile(context, candidate_id, name="Localization Operations", is_active=True)

            page = TargetDirectionStep(context, ui_language="zh")
            self.addCleanup(page.deleteLater)

            page.resize(1200, 800)
            page.set_candidate(candidate_id)
            page.show()
            process_events()

            self.assertGreater(page.direction_list.count(), 0)
            rect = page.direction_list.visualItemRect(page.direction_list.item(0))
            self.assertGreater(rect.height(), 0)
            self.assertLessEqual(rect.height(), 44)

    def test_target_role_list_uses_explicit_delegate_and_checkbox_toggle(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Theme Candidate", base_location="Berlin")
            create_profile(context, candidate_id, name="Localization Operations", is_active=True)

            page = TargetDirectionStep(context, ui_language="zh")
            self.addCleanup(page.deleteLater)

            page.resize(1200, 800)
            page.set_candidate(candidate_id)
            page.show()
            process_events()

            delegate = page.direction_list.itemDelegate()
            self.assertIsInstance(delegate, TargetRoleListDelegate)

            item = page.direction_list.item(0)
            self.assertIsNotNone(item)
            self.assertEqual(item.checkState(), Qt.Checked)
            page.direction_list.setCurrentRow(0)
            process_events()
            page.direction_list.setFocus()
            QTest.keyClick(page.direction_list, Qt.Key_Space)
            process_events()

            self.assertEqual(item.checkState(), Qt.Unchecked)

    def test_target_role_list_mouse_click_toggles_checkbox(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context, name="Theme Candidate", base_location="Berlin")
            create_profile(context, candidate_id, name="Localization Operations", is_active=True)

            page = TargetDirectionStep(context, ui_language="zh")
            self.addCleanup(page.deleteLater)

            page.resize(1200, 800)
            page.set_candidate(candidate_id)
            page.show()
            process_events()

            item = page.direction_list.item(0)
            self.assertIsNotNone(item)
            delegate = page.direction_list.itemDelegate()
            self.assertIsInstance(delegate, TargetRoleListDelegate)

            option = QStyleOptionViewItem()
            option.rect = page.direction_list.visualItemRect(item)
            click_point = delegate.checkbox_rect(option).center()
            QTest.mouseClick(page.direction_list.viewport(), Qt.LeftButton, Qt.NoModifier, click_point)
            process_events()

            self.assertEqual(item.checkState(), Qt.Unchecked)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
