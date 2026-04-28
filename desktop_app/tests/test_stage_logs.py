from __future__ import annotations

import unittest

try:
    from ._helpers import create_candidate, make_temp_context
except ImportError:  # pragma: no cover - direct discovery fallback
    from _helpers import create_candidate, make_temp_context  # type: ignore

from jobflow_desktop_app.db.repositories.search_runtime import SearchRunRepository
from jobflow_desktop_app.db.repositories.stage_logs import SearchStageLogRepository


class SearchStageLogRepositoryTests(unittest.TestCase):
    def test_stage_logs_round_trip_counts_and_redact_sensitive_text(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            run_id = SearchRunRepository(context.database).create_run(
                candidate_id=candidate_id,
                run_dir="runtime/search_runs/1",
            )
            repository = SearchStageLogRepository(context.database)

            log_id = repository.start(
                search_run_id=run_id,
                candidate_id=candidate_id,
                round_number=2,
                stage_name="direct_job_discovery",
                message="starting with Z:/PrivateFixture/resume.pdf",
                metadata={
                    "rotationSeed": 42,
                    "apiKey": "sess-sensitive-fixture-token",
                    "resumePath": "Z:/PrivateFixture/resume.pdf",
                },
            )
            repository.finish(
                log_id,
                status="soft_failed",
                exit_code=1,
                message="failed at Z:/PrivateFixture/resume.pdf",
                error_summary="token sess-sensitive-fixture-token at Z:/PrivateFixture/resume.pdf",
                counts={
                    "rawJobs": 10,
                    "skippedExisting": 4,
                    "verifiedJobs": 2,
                },
                duration_ms=123,
            )

            rows = repository.list_for_run(run_id)

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row.search_run_id, run_id)
            self.assertEqual(row.candidate_id, candidate_id)
            self.assertEqual(row.round_number, 2)
            self.assertEqual(row.stage_name, "direct_job_discovery")
            self.assertEqual(row.status, "soft_failed")
            self.assertEqual(row.exit_code, 1)
            self.assertEqual(row.duration_ms, 123)
            self.assertEqual(row.counts["rawJobs"], 10)
            self.assertEqual(row.counts["skippedExisting"], 4)
            self.assertNotIn("Z:/PrivateFixture", row.message)
            self.assertNotIn("sess-sensitive-fixture", row.error_summary)
            self.assertEqual(row.metadata["apiKey"], "[redacted]")
            self.assertEqual(row.metadata["resumePath"], "[redacted]")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
