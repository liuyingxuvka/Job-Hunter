from __future__ import annotations

import re
from pathlib import Path

from ...db.connection import Database
from ...db.repositories.search_runtime import JobReviewStateRepository, SearchRunRepository


def candidate_id_from_run_dir(run_dir: Path) -> int | None:
    match = re.fullmatch(r"candidate_(\d+)", run_dir.name.strip())
    if match is None:
        return None
    return int(match.group(1))


def project_root_from_run_dir(run_dir: Path) -> Path | None:
    try:
        return run_dir.resolve().parents[2]
    except IndexError:
        return None


def runtime_db_path(project_root: Path) -> Path:
    return Path(project_root) / "runtime" / "data" / "jobflow_desktop.db"


def search_run_repository_for_run_dir(run_dir: Path) -> SearchRunRepository | None:
    project_root = project_root_from_run_dir(run_dir)
    if project_root is None:
        return None
    db_path = runtime_db_path(project_root)
    if not db_path.exists():
        return None
    return SearchRunRepository(Database(db_path))


def job_review_state_repository_for_run_dir(run_dir: Path) -> JobReviewStateRepository | None:
    project_root = project_root_from_run_dir(run_dir)
    if project_root is None:
        return None
    db_path = runtime_db_path(project_root)
    if not db_path.exists():
        return None
    return JobReviewStateRepository(Database(db_path))


__all__ = [
    "candidate_id_from_run_dir",
    "job_review_state_repository_for_run_dir",
    "project_root_from_run_dir",
    "runtime_db_path",
    "search_run_repository_for_run_dir",
]
