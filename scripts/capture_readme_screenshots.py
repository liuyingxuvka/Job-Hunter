from __future__ import annotations

import tempfile
import time
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "desktop_app"
SRC_ROOT = DESKTOP_ROOT / "src"

import sys

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app import bootstrap as bootstrap_module
from jobflow_desktop_app.app.main_window import MainWindow, _resolve_app_icon
from jobflow_desktop_app.app.theme import apply_theme
from jobflow_desktop_app.paths import AppPaths


OUTPUT_DIR = REPO_ROOT / "docs" / "images"


def _process_events(app: QApplication, loops: int = 20, delay_seconds: float = 0.06) -> None:
    for _ in range(max(1, int(loops))):
        app.processEvents()
        time.sleep(max(0.0, float(delay_seconds)))


def _resize_image(path: Path, target_width: int = 1600) -> None:
    image = Image.open(path)
    if image.width <= target_width:
        image.save(path, optimize=True)
        return
    target_height = round(image.height * target_width / image.width)
    resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    resized.save(path, optimize=True)


def _temp_paths(runtime_root: Path) -> AppPaths:
    data_dir = runtime_root / "data"
    logs_dir = runtime_root / "logs"
    exports_dir = runtime_root / "exports"
    return AppPaths(
        project_root=DESKTOP_ROOT,
        runtime_dir=runtime_root,
        data_dir=data_dir,
        exports_dir=exports_dir,
        logs_dir=logs_dir,
        db_path=data_dir / "jobflow_desktop.db",
        schema_path=DESKTOP_ROOT / "src" / "jobflow_desktop_app" / "db" / "schema.sql",
    )


def _build_window(app: QApplication, runtime_root: Path, *, width: int, height: int) -> MainWindow:
    original_build_app_paths = bootstrap_module.build_app_paths
    try:
        bootstrap_module.build_app_paths = lambda: _temp_paths(runtime_root)
        context = bootstrap_module.bootstrap_application()
    finally:
        bootstrap_module.build_app_paths = original_build_app_paths

    context.settings.save_ui_language("en")
    icon = _resolve_app_icon(context)
    if icon is not None:
        app.setWindowIcon(icon)
    apply_theme(app)
    window = MainWindow(context)
    if icon is not None:
        window.setWindowIcon(icon)
    window.statusBar().hide()
    window.resize(width, height)
    window.show()
    window.raise_()
    window.activateWindow()
    _process_events(app, loops=24)
    return window


def _capture_candidate_entry(app: QApplication) -> None:
    output_path = OUTPUT_DIR / "readme-screenshot-directory.png"
    with tempfile.TemporaryDirectory(prefix="job-hunter-readme-") as runtime_root_raw:
        window = _build_window(app, Path(runtime_root_raw), width=1440, height=980)
        try:
            window.grab().save(str(output_path))
            _resize_image(output_path, target_width=1600)
        finally:
            window.close()
            _process_events(app, loops=4, delay_seconds=0.03)


def _capture_search_workspace(app: QApplication) -> None:
    output_path = OUTPUT_DIR / "readme-screenshot-search-results.png"
    with tempfile.TemporaryDirectory(prefix="job-hunter-readme-") as runtime_root_raw:
        window = _build_window(app, Path(runtime_root_raw), width=1440, height=1260)
        try:
            window._open_workspace(window.current_candidate_id)
            _process_events(app, loops=16)
            window.workspace_page._set_step(2)
            window._set_ai_status("ready", model_name="gpt-5-nano")
            _process_events(app, loops=12)
            window.grab().save(str(output_path))
            _resize_image(output_path, target_width=1600)
        finally:
            window.workspace_page.shutdown_background_work(wait_ms=1000)
            window.close()
            _process_events(app, loops=4, delay_seconds=0.03)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    _capture_candidate_entry(app)
    _capture_search_workspace(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
