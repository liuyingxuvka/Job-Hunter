from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.search.state.runtime_run_feedback import (  # noqa: E402
    SearchRunFeedbackStore,
)


class RuntimeRunFeedbackTests(unittest.TestCase):
    def test_load_latest_run_feedback_filters_noise_and_collects_keywords(self) -> None:
        artifacts = SimpleNamespace(
            load_latest_bucket_jobs=lambda *, candidate_id, job_bucket: (
                [
                    {
                        "company": "Acme Energy (listed via board)",
                        "title": "Hydrogen Durability Engineer",
                        "analysis": {
                            "recommend": True,
                            "isJobPosting": True,
                            "overallScore": 88,
                        },
                    }
                ]
                if job_bucket == "recommended"
                else [
                    {
                        "company": "Noise Co",
                        "title": "Landing Page",
                        "analysis": {
                            "landingPageNoise": True,
                            "recommend": False,
                        },
                    },
                    {
                        "company": "Second Co",
                        "title": "Fuel Cell Diagnostics Scientist",
                        "analysis": {
                            "recommend": False,
                            "isJobPosting": True,
                            "overallScore": 72,
                        },
                    },
                ]
            )
        )
        store = SearchRunFeedbackStore(artifacts=artifacts)

        feedback = store.load_latest_run_feedback(candidate_id=7)

        self.assertEqual(feedback["companies"], ["Acme Energy", "Second Co"])
        self.assertIn("Hydrogen Durability Engineer", feedback["keywords"])
        self.assertIn("Fuel Cell Diagnostics Scientist", feedback["keywords"])
        self.assertNotIn("Noise Co", feedback["companies"])

    def test_load_latest_run_feedback_falls_back_to_recent_run_with_usable_feedback(self) -> None:
        artifacts = SimpleNamespace(
            search_runs=SimpleNamespace(
                recent_for_candidate=lambda candidate_id, limit=20: [
                    SimpleNamespace(search_run_id=11),
                    SimpleNamespace(search_run_id=10),
                ]
            ),
            run_jobs=SimpleNamespace(
                load_bucket_jobs=lambda *, search_run_id, job_bucket: (
                    []
                    if search_run_id == 11
                    else (
                        [
                            {
                                "company": "Stable Energy",
                                "title": "Hydrogen Systems Engineer",
                                "analysis": {
                                    "recommend": True,
                                    "isJobPosting": True,
                                    "overallScore": 85,
                                },
                            }
                        ]
                        if job_bucket == "recommended"
                        else []
                    )
                )
            ),
            load_latest_bucket_jobs=lambda **kwargs: [],
        )
        store = SearchRunFeedbackStore(artifacts=artifacts)

        feedback = store.load_latest_run_feedback(candidate_id=7)

        self.assertEqual(feedback["companies"], ["Stable Energy"])
        self.assertIn("Hydrogen Systems Engineer", feedback["keywords"])

    def test_load_latest_run_feedback_prefers_successful_recent_run_over_newer_error_run(self) -> None:
        artifacts = SimpleNamespace(
            search_runs=SimpleNamespace(
                recent_for_candidate=lambda candidate_id, limit=20: [
                    SimpleNamespace(search_run_id=12, status="error", current_stage="sources"),
                    SimpleNamespace(search_run_id=11, status="success", current_stage="done"),
                ]
            ),
            run_jobs=SimpleNamespace(
                load_bucket_jobs=lambda *, search_run_id, job_bucket: (
                    (
                        [
                            {
                                "company": "Stable Energy",
                                "title": "Hydrogen Systems Engineer",
                                "analysis": {
                                    "recommend": True,
                                    "isJobPosting": True,
                                    "overallScore": 85,
                                },
                            }
                        ]
                        if search_run_id == 11 and job_bucket == "recommended"
                        else []
                    )
                    if search_run_id == 11
                    else (
                        [
                            {
                                "company": "Partial Run Co",
                                "title": "Energy Engineer",
                                "analysis": {
                                    "recommend": True,
                                    "isJobPosting": True,
                                    "overallScore": 72,
                                },
                            }
                        ]
                        if job_bucket == "recommended"
                        else []
                    )
                )
            ),
            load_latest_bucket_jobs=lambda **kwargs: [],
        )
        store = SearchRunFeedbackStore(artifacts=artifacts)

        feedback = store.load_latest_run_feedback(candidate_id=7)

        self.assertEqual(feedback["companies"], ["Stable Energy"])
        self.assertIn("Hydrogen Systems Engineer", feedback["keywords"])

    def test_load_latest_run_feedback_looks_past_first_five_recent_runs(self) -> None:
        artifacts = SimpleNamespace(
            search_runs=SimpleNamespace(
                recent_for_candidate=lambda candidate_id, limit=20: [
                    *[
                        SimpleNamespace(search_run_id=30 - index, status="error", current_stage="sources")
                        for index in range(6)
                    ],
                    SimpleNamespace(search_run_id=24, status="success", current_stage="done"),
                ]
            ),
            run_jobs=SimpleNamespace(
                load_bucket_jobs=lambda *, search_run_id, job_bucket: (
                    [
                        {
                            "company": "Deep History Energy",
                            "title": "Fuel Cell Diagnostics Scientist",
                            "analysis": {
                                "recommend": True,
                                "isJobPosting": True,
                                "overallScore": 81,
                            },
                        }
                    ]
                    if search_run_id == 24 and job_bucket == "recommended"
                    else []
                )
            ),
            load_latest_bucket_jobs=lambda **kwargs: [],
        )
        store = SearchRunFeedbackStore(artifacts=artifacts)

        feedback = store.load_latest_run_feedback(candidate_id=7)

        self.assertEqual(feedback["companies"], ["Deep History Energy"])
        self.assertIn("Fuel Cell Diagnostics Scientist", feedback["keywords"])


if __name__ == "__main__":
    unittest.main()
