from __future__ import annotations

from .db.bootstrap import initialize_database
from .db.connection import Database
from .db.repositories.candidates import CandidateRepository
from .db.repositories.overview import OverviewRepository
from .db.repositories.profiles import SearchProfileRepository
from .db.repositories.settings import AppSettingsRepository
from .paths import AppPaths, build_app_paths
from .services.app_context import AppContext
from .services.demo_seed import ensure_demo_candidate_seeded


def ensure_runtime_directories(paths: AppPaths) -> None:
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.exports_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)


def bootstrap_application() -> AppContext:
    paths = build_app_paths()
    ensure_runtime_directories(paths)
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
    ensure_demo_candidate_seeded(context)
    return context
