from __future__ import annotations

import unittest

from jobflow_desktop_app.app.pages import search_results_controls_state


class SearchResultsControlsStateTests(unittest.TestCase):
    def test_selected_search_duration_seconds_uses_minimum_and_fallback(self) -> None:
        self.assertEqual(
            search_results_controls_state.selected_search_duration_seconds(120),
            3600,
        )
        self.assertEqual(
            search_results_controls_state.selected_search_duration_seconds("1800"),
            3600,
        )
        self.assertEqual(
            search_results_controls_state.selected_search_duration_seconds("bad"),
            3600,
        )

    def test_selected_search_duration_label_uses_default_when_missing(self) -> None:
        self.assertEqual(
            search_results_controls_state.selected_search_duration_label("zh", ""),
            "1 小时",
        )
        self.assertEqual(
            search_results_controls_state.selected_search_duration_label("en", "30 minutes"),
            "30 minutes",
        )

    def test_remaining_countdown_seconds_and_search_button_text(self) -> None:
        remaining = search_results_controls_state.remaining_countdown_seconds(
            owns_running_search=True,
            owns_queued_restart=False,
            started_monotonic=100.0,
            duration_seconds=600,
            now_monotonic=lambda: 160.0,
        )
        self.assertEqual(remaining, 540)
        self.assertEqual(
            search_results_controls_state.remaining_countdown_seconds(
                owns_running_search=False,
                owns_queued_restart=False,
                started_monotonic=100.0,
                duration_seconds=600,
                now_monotonic=lambda: 160.0,
            ),
            0,
        )
        self.assertEqual(
            search_results_controls_state.search_button_text("zh", running=True),
            "停止搜索",
        )
        self.assertEqual(
            search_results_controls_state.search_button_text("en", running=False, queued=False),
            "Start Search",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
