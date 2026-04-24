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
from jobflow_desktop_app.app.pages import search_results as search_results_module
from jobflow_desktop_app.app.theme import apply_theme
from jobflow_desktop_app.paths import AppPaths
from jobflow_desktop_app.search.orchestration import JobSearchResult, SearchRunResult
from jobflow_desktop_app.search.state.search_progress_state import SearchProgress, SearchStats


OUTPUT_DIR = REPO_ROOT / "docs" / "images"


def _demo_jobs() -> list[JobSearchResult]:
    return [
        JobSearchResult(
            title="Hydrogen Systems Integration Engineer",
            title_zh="氢能系统集成工程师",
            title_en="Hydrogen Systems Integration Engineer",
            company="H2Flow Systems",
            location="Munich, Germany",
            location_zh="德国慕尼黑",
            location_en="Munich, Germany",
            url="https://example.com/jobs/hydrogen-systems-integration",
            final_url="https://example.com/jobs/hydrogen-systems-integration",
            link_status="final",
            date_found="2026-04-22T06:57:27+00:00",
            match_score=92,
            overall_match_score=92,
            bound_target_role_score=92,
            recommend=True,
            fit_level_cn="高推荐",
            fit_track="core",
            adjacent_direction_cn="氢能系统",
            bound_target_role_name_zh="氢能系统集成工程师（电解槽/燃料电池）",
            bound_target_role_name_en="Hydrogen Systems Integration Engineer",
            bound_target_role_display_name="氢能系统集成工程师（电解槽/燃料电池）",
            bound_target_role_text="Hydrogen Systems Integration Engineer",
        ),
        JobSearchResult(
            title="MBSE Requirements Verification Lead",
            title_zh="MBSE需求验证负责人",
            title_en="MBSE Requirements Verification Lead",
            company="GridTwin Labs",
            location="Berlin, Germany",
            location_zh="德国柏林",
            location_en="Berlin, Germany",
            url="https://example.com/jobs/mbse-verification-lead",
            final_url="https://example.com/jobs/mbse-verification-lead",
            link_status="final",
            date_found="2026-04-22T05:41:10+00:00",
            match_score=87,
            overall_match_score=87,
            bound_target_role_score=87,
            recommend=True,
            fit_level_cn="高推荐",
            fit_track="core",
            adjacent_direction_cn="系统工程",
            bound_target_role_name_zh="MBSE与需求验证工程师（能源系统）",
            bound_target_role_name_en="MBSE & Requirements Verification Engineer",
            bound_target_role_display_name="MBSE与需求验证工程师（能源系统）",
            bound_target_role_text="MBSE & Requirements Verification Engineer",
        ),
        JobSearchResult(
            title="Digital Twin Reliability Engineer",
            title_zh="数字孪生可靠性工程师",
            title_en="Digital Twin Reliability Engineer",
            company="Electrolyzer Systems Europe",
            location="Remote · EU",
            location_zh="远程 · 欧盟",
            location_en="Remote · EU",
            url="https://example.com/jobs/digital-twin-reliability",
            final_url="https://example.com/jobs/digital-twin-reliability",
            link_status="final",
            date_found="2026-04-21T15:20:44+00:00",
            match_score=81,
            overall_match_score=81,
            bound_target_role_score=81,
            recommend=True,
            fit_level_cn="中推荐",
            fit_track="adjacent",
            adjacent_direction_cn="可靠性与PHM",
            bound_target_role_name_zh="数字孪生与PHM工程师（能源装备）",
            bound_target_role_name_en="Digital Twin & PHM Engineer",
            bound_target_role_display_name="数字孪生与PHM工程师（能源装备）",
            bound_target_role_text="Digital Twin & PHM Engineer",
        ),
        JobSearchResult(
            title="Energy Equipment Validation Engineer",
            title_zh="能源装备验证工程师",
            title_en="Energy Equipment Validation Engineer",
            company="VoltBridge Engineering",
            location="Hamburg, Germany",
            location_zh="德国汉堡",
            location_en="Hamburg, Germany",
            url="https://example.com/jobs/equipment-validation",
            final_url="https://example.com/jobs/equipment-validation",
            link_status="final",
            date_found="2026-04-21T09:18:02+00:00",
            match_score=76,
            overall_match_score=76,
            bound_target_role_score=76,
            recommend=True,
            fit_level_cn="中推荐",
            fit_track="adjacent",
            adjacent_direction_cn="验证测试",
            bound_target_role_name_zh="MBSE与需求验证工程师（能源系统）",
            bound_target_role_name_en="MBSE & Requirements Verification Engineer",
            bound_target_role_display_name="MBSE与需求验证工程师（能源系统）",
            bound_target_role_text="MBSE & Requirements Verification Engineer",
        ),
    ]


class _ReadmeScreenshotRunner:
    def __init__(self, _project_root: Path) -> None:
        self.jobs = _demo_jobs()
        self.stats = SearchStats(
            candidate_company_pool_count=12,
            main_discovered_job_count=18,
            main_scored_job_count=8,
            recommended_job_count=len(self.jobs),
            main_pending_analysis_count=0,
        )
        self.progress = SearchProgress()
        self.runtime_mirror = None

    def set_job_display_i18n_context_provider(self, _provider) -> None:
        return None

    def load_recommended_jobs(self, _candidate_id: int) -> list[JobSearchResult]:
        return list(self.jobs)

    def load_live_jobs(self, _candidate_id: int) -> list[JobSearchResult]:
        return list(self.jobs)

    def load_search_stats(self, _candidate_id: int) -> SearchStats:
        return self.stats

    def load_search_progress(self, _candidate_id: int) -> SearchProgress:
        return self.progress

    def run_search(self, **_kwargs: object) -> SearchRunResult:
        return SearchRunResult(
            success=True,
            exit_code=0,
            message="Demo screenshot data only.",
            stdout_tail="",
            stderr_tail="",
            run_dir=DESKTOP_ROOT,
        )


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

    context.settings.save_ui_language("zh")
    icon = _resolve_app_icon(context)
    if icon is not None:
        app.setWindowIcon(icon)
    original_runner = search_results_module.JobSearchRunner
    try:
        search_results_module.JobSearchRunner = _ReadmeScreenshotRunner
        apply_theme(app)
        window = MainWindow(context)
    finally:
        search_results_module.JobSearchRunner = original_runner
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
            window.workspace_compact_page._set_step(2)
            window._set_ai_status("ready", model_name="gpt-5-nano")
            if window.current_candidate_id is not None:
                window.workspace_compact_page.results_step._reload_existing_results(
                    int(window.current_candidate_id)
                )
            _process_events(app, loops=12)
            window.grab().save(str(output_path))
            _resize_image(output_path, target_width=1600)
        finally:
            window.workspace_compact_page.shutdown_background_work(wait_ms=1000)
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
