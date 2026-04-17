from __future__ import annotations

from dataclasses import replace
import unittest

from jobflow_desktop_app.app.pages import search_results_live_state

try:
    from ._helpers import make_job
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import make_job  # type: ignore


class SearchResultsLiveStateTests(unittest.TestCase):
    def test_visible_jobs_sorts_newest_first_and_filters_hidden_keys(self) -> None:
        newest = make_job(
            title="Newest",
            url="https://example.com/jobs/newest",
            date_found="2026-04-15T10:00:00Z",
        )
        hidden = make_job(
            title="Hidden",
            url="https://example.com/jobs/hidden",
            date_found="2026-04-14T10:00:00Z",
        )
        oldest = make_job(
            title="Oldest",
            url="https://example.com/jobs/oldest",
            date_found="2026-04-13T10:00:00Z",
        )

        visible = search_results_live_state.visible_jobs(
            [oldest, hidden, newest],
            {"https://example.com/jobs/hidden"},
        )

        self.assertEqual([job.title for job in visible], ["Newest", "Oldest"])

    def test_job_key_falls_back_to_identity_when_url_missing(self) -> None:
        job = make_job(
            title="Systems Engineer",
            company="Acme Robotics",
            url="",
            date_found="2026-04-14T12:00:00Z",
        )

        self.assertEqual(
            search_results_live_state.job_key(job),
            "systems engineer|acme robotics|2026-04-14t12:00:00z",
        )

    def test_job_render_signature_tracks_link_and_score_fields(self) -> None:
        job = replace(
            make_job(
            url="https://example.com/jobs/1",
            match_score=82,
            ),
            bound_target_role_score=91,
            source_url="https://source.example.com/post/1",
            final_url="https://apply.example.com/roles/1",
            link_status="verified",
        )

        signature = search_results_live_state.job_render_signature(job)

        self.assertEqual(signature[0], "https://example.com/jobs/1")
        self.assertEqual(signature[1], "https://source.example.com/post/1")
        self.assertEqual(signature[2], "https://apply.example.com/roles/1")
        self.assertEqual(signature[3], "verified")
        self.assertEqual(signature[-1], 91)

    def test_jobs_signature_preserves_row_order(self) -> None:
        first = make_job(title="First", url="https://example.com/jobs/1")
        second = make_job(title="Second", url="https://example.com/jobs/2")

        signature = search_results_live_state.jobs_signature([first, second])

        self.assertEqual(len(signature), 2)
        self.assertEqual(signature[0][4], "First")
        self.assertEqual(signature[1][4], "Second")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
