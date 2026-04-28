from __future__ import annotations

import os
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
    install_root: Path | None = None
    user_data_root: Path | None = None
    updates_dir: Path | None = None
    bundled_runtime_dir: Path | None = None
    is_packaged: bool = False

    def __post_init__(self) -> None:
        if self.install_root is None:
            object.__setattr__(self, "install_root", self.project_root)
        if self.user_data_root is None:
            object.__setattr__(self, "user_data_root", self.runtime_dir.parent)
        if self.updates_dir is None:
            object.__setattr__(self, "updates_dir", self.runtime_dir.parent / "updates")
        if self.bundled_runtime_dir is None:
            object.__setattr__(self, "bundled_runtime_dir", self.runtime_dir)


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


def _resolve_user_data_root() -> Path:
    override = os.environ.get("JOBFLOW_USER_DATA_ROOT", "").strip()
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Job-Hunter"

    return Path.home() / "AppData" / "Local" / "Job-Hunter"


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
    install_root = _resolve_bundle_root()
    project_root = _resolve_project_root()
    is_packaged = bool(getattr(sys, "frozen", False))
    user_data_root = _resolve_user_data_root() if is_packaged else project_root
    runtime_dir = (user_data_root / "runtime") if is_packaged else (project_root / "runtime")
    data_dir = runtime_dir / "data"
    exports_dir = runtime_dir / "exports"
    logs_dir = runtime_dir / "logs"
    db_path = data_dir / "jobflow_desktop.db"
    schema_path = _resolve_schema_path(project_root)
    updates_dir = (user_data_root / "updates") if is_packaged else (runtime_dir / "updates")
    bundled_runtime_dir = project_root / "runtime"
    return AppPaths(
        install_root=install_root,
        project_root=project_root,
        runtime_dir=runtime_dir,
        data_dir=data_dir,
        exports_dir=exports_dir,
        logs_dir=logs_dir,
        db_path=db_path,
        schema_path=schema_path,
        user_data_root=user_data_root,
        updates_dir=updates_dir,
        bundled_runtime_dir=bundled_runtime_dir,
        is_packaged=is_packaged,
    )
