from __future__ import annotations

import sys
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


def _resolve_bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resolve_project_root() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    if not getattr(sys, "frozen", False):
        return source_root

    bundle_root = _resolve_bundle_root()
    packaged_desktop_root = bundle_root / "desktop_app"
    if packaged_desktop_root.exists():
        return packaged_desktop_root
    return bundle_root


def _resolve_schema_path(project_root: Path) -> Path:
    schema_path = project_root / "src" / "jobflow_desktop_app" / "db" / "schema.sql"
    if schema_path.exists():
        return schema_path

    bundle_temp = getattr(sys, "_MEIPASS", "")
    if bundle_temp:
        bundled_schema_path = Path(bundle_temp) / "jobflow_desktop_app" / "db" / "schema.sql"
        if bundled_schema_path.exists():
            return bundled_schema_path

    return schema_path


def build_app_paths() -> AppPaths:
    project_root = _resolve_project_root()
    runtime_dir = project_root / "runtime"
    data_dir = runtime_dir / "data"
    exports_dir = runtime_dir / "exports"
    logs_dir = runtime_dir / "logs"
    db_path = data_dir / "jobflow_desktop.db"
    schema_path = _resolve_schema_path(project_root)
    return AppPaths(
        project_root=project_root,
        runtime_dir=runtime_dir,
        data_dir=data_dir,
        exports_dir=exports_dir,
        logs_dir=logs_dir,
        db_path=db_path,
        schema_path=schema_path,
    )
