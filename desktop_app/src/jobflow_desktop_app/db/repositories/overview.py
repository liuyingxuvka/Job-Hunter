from __future__ import annotations

from dataclasses import dataclass

from ..connection import Database


@dataclass(frozen=True)
class OverviewStats:
    candidate_count: int
    profile_count: int
    job_count: int
    run_count: int


class OverviewRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def load_stats(self) -> OverviewStats:
        with self.database.session() as connection:
            candidate_count = int(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0])
            profile_count = int(connection.execute("SELECT COUNT(*) FROM search_profiles").fetchone()[0])
            job_count = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            run_count = int(connection.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0])
        return OverviewStats(
            candidate_count=candidate_count,
            profile_count=profile_count,
            job_count=job_count,
            run_count=run_count,
        )
