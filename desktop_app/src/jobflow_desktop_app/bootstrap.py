from __future__ import annotations

import os

from .db.bootstrap import initialize_database
from .db.connection import Database
from .db.repositories.candidates import CandidateRepository
from .db.repositories.overview import OverviewRepository
from .db.repositories.profiles import SearchProfileRepository
from .db.repositories.settings import AppSettingsRepository
from .paths import AppPaths, build_app_paths
from .app.context import AppContext
from .db.seeds.demo_candidate import ensure_demo_candidate_seeded
from .search.state.runtime_db_mirror import SearchRuntimeMirror
from .search.state.runtime_recovery import recover_interrupted_search_runs


def ensure_runtime_directories(paths: AppPaths) -> None:
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.exports_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    (paths.runtime_dir / "backups").mkdir(parents=True, exist_ok=True)
    (paths.runtime_dir / "search_runs").mkdir(parents=True, exist_ok=True)


def ensure_working_directory(paths: AppPaths) -> None:
    os.chdir(paths.project_root)


def recover_interrupted_runtime_state(context: AppContext) -> list[int]:
    runtime_mirror = SearchRuntimeMirror(context.database)
    return recover_interrupted_search_runs(runtime_mirror)


def bootstrap_application() -> AppContext:
    paths = build_app_paths()
    ensure_runtime_directories(paths)
    ensure_working_directory(paths)
    database = Database(paths.db_path)
    initialize_database(database, paths.schema_path)
    context = AppContext(
        paths=paths,
        database=database,
        candidates=CandidateRepository(database),
        profiles=SearchProfileRepository(database),
        settings=AppSettingsRepository(database),
        overview=OverviewRepository(database),
    )
    recover_interrupted_runtime_state(context)
    ensure_demo_candidate_seeded(context)
    return context
