from __future__ import annotations

from dataclasses import replace
import unittest

from jobflow_desktop_app.app.pages import search_results_rendering

try:
    from ._helpers import make_job
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import make_job  # type: ignore


class SearchResultsRenderingTests(unittest.TestCase):
    def test_display_target_role_prefers_localized_fields_and_unbound_fallback(self) -> None:
        job = replace(
            make_job(bound_target_role_name_en=""),
            bound_target_role_name_zh="系统工程师",
            bound_target_role_display_name="Systems Engineer",
        )

        self.assertEqual(search_results_rendering.display_target_role("zh", job), "系统工程师")
        self.assertEqual(search_results_rendering.display_target_role("en", job), "Systems Engineer")
        self.assertEqual(
            search_results_rendering.display_target_role("en", make_job(bound_target_role_name_en="")),
            "Unbound",
        )

    def test_format_score_uses_bound_role_score_then_match_score_and_handles_missing(self) -> None:
        high = replace(make_job(match_score=78), bound_target_role_score=92)
        medium = make_job(match_score=73)
        no_score = replace(make_job(match_score=0), bound_target_role_score=None, match_score=None)

        self.assertEqual(search_results_rendering.format_score("zh", high), "92 / 100（高推荐）")
        self.assertEqual(search_results_rendering.format_score("en", medium), "73 / 100 (Medium)")
        self.assertEqual(search_results_rendering.format_score("en", no_score), "No score")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
