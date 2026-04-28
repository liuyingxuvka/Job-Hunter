from __future__ import annotations

import os
import shutil
from pathlib import Path

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
    paths.updates_dir.mkdir(parents=True, exist_ok=True)
    (paths.runtime_dir / "backups").mkdir(parents=True, exist_ok=True)
    (paths.runtime_dir / "search_runs").mkdir(parents=True, exist_ok=True)


def _copy_missing_tree_contents(source: Path, destination: Path) -> None:
    if not source.exists() or not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for source_item in source.iterdir():
        destination_item = destination / source_item.name
        if source_item.is_dir():
            _copy_missing_tree_contents(source_item, destination_item)
            continue
        if destination_item.exists():
            continue
        destination_item.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_item, destination_item)


def migrate_packaged_runtime_if_needed(paths: AppPaths) -> None:
    if not paths.is_packaged:
        return
    bundled_runtime_dir = paths.bundled_runtime_dir
    if not bundled_runtime_dir or not bundled_runtime_dir.exists():
        return
    try:
        if bundled_runtime_dir.resolve() == paths.runtime_dir.resolve():
            return
    except OSError:
        return

    for relative_dir in ("data", "exports", "logs", "backups", "search_runs"):
        _copy_missing_tree_contents(
            bundled_runtime_dir / relative_dir,
            paths.runtime_dir / relative_dir,
        )


def ensure_working_directory(paths: AppPaths) -> None:
    os.chdir(paths.project_root)


def recover_interrupted_runtime_state(context: AppContext) -> list[int]:
    runtime_mirror = SearchRuntimeMirror(context.database)
    return recover_interrupted_search_runs(runtime_mirror)


def bootstrap_application() -> AppContext:
    paths = build_app_paths()
    ensure_runtime_directories(paths)
    migrate_packaged_runtime_if_needed(paths)
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
