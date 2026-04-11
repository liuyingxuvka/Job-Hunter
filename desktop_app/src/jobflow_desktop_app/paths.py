from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    project_root: Path
    runtime_dir: Path
    data_dir: Path
    exports_dir: Path
    logs_dir: Path
    db_path: Path
    schema_path: Path


def build_app_paths() -> AppPaths:
    project_root = Path(__file__).resolve().parents[2]
    runtime_dir = project_root / "runtime"
    data_dir = runtime_dir / "data"
    exports_dir = runtime_dir / "exports"
    logs_dir = runtime_dir / "logs"
    db_path = data_dir / "jobflow_desktop.db"
    schema_path = project_root / "src" / "jobflow_desktop_app" / "db" / "schema.sql"
    return AppPaths(
        project_root=project_root,
        runtime_dir=runtime_dir,
        data_dir=data_dir,
        exports_dir=exports_dir,
        logs_dir=logs_dir,
        db_path=db_path,
        schema_path=schema_path,
    )
