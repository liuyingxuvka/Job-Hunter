from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from jobflow_desktop_app.db.bootstrap import initialize_database  # noqa: E402
from jobflow_desktop_app.db.connection import Database  # noqa: E402
from jobflow_desktop_app.db.repositories.candidates import (  # noqa: E402
    CandidateRecord,
    CandidateRepository,
)
from jobflow_desktop_app.db.repositories.overview import OverviewRepository  # noqa: E402
from jobflow_desktop_app.db.repositories.profiles import (  # noqa: E402
    SearchProfileRecord,
    SearchProfileRepository,
)
from jobflow_desktop_app.db.repositories.settings import (  # noqa: E402
    AppSettingsRepository,
    OpenAISettings,
)
from jobflow_desktop_app.app.context import AppContext  # noqa: E402
from jobflow_desktop_app.paths import AppPaths  # noqa: E402
from jobflow_desktop_app.search.orchestration.job_search_runner import (  # noqa: E402
    JobSearchResult,
    SearchRunResult,
)
from jobflow_desktop_app.search.state.search_progress_state import (  # noqa: E402
    SearchProgress,
    SearchStats,
)


def get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def process_events() -> None:
    app = get_qapp()
    app.processEvents()
    app.processEvents()


@contextmanager
def make_temp_context() -> Iterator[AppContext]:
    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        runtime_dir = temp_root / "runtime"
        data_dir = runtime_dir / "data"
        exports_dir = runtime_dir / "exports"
        logs_dir = runtime_dir / "logs"
        for path in (runtime_dir, data_dir, exports_dir, logs_dir):
            path.mkdir(parents=True, exist_ok=True)

        paths = AppPaths(
            project_root=DESKTOP_ROOT,
            runtime_dir=runtime_dir,
            data_dir=data_dir,
            exports_dir=exports_dir,
            logs_dir=logs_dir,
            db_path=data_dir / "jobflow_desktop.db",
            schema_path=SRC_ROOT / "jobflow_desktop_app" / "db" / "schema.sql",
        )
        database = Database(paths.db_path)
        initialize_database(database, paths.schema_path)
        yield AppContext(
            paths=paths,
            database=database,
            candidates=CandidateRepository(database),
            profiles=SearchProfileRepository(database),
            settings=AppSettingsRepository(database),
            overview=OverviewRepository(database),
        )


def create_candidate(
    context: AppContext,
    *,
    name: str = "Demo Candidate",
    base_location: str = "Munich, Germany",
    preferred_locations: str = "Munich\nBerlin\nRemote",
    notes: str = "Systems engineering background",
) -> int:
    return context.candidates.save(
        CandidateRecord(
            candidate_id=None,
            name=name,
            email="demo@example.com",
            base_location=base_location,
            preferred_locations=preferred_locations,
            target_directions="",
            notes=notes,
            active_resume_path="",
            created_at="",
            updated_at="",
        )
    )


def create_profile(
    context: AppContext,
    candidate_id: int,
    *,
    name: str = "Systems Engineer",
    scope_profile: str = "",
    keyword_focus: str = "requirements and validation",
    is_active: bool = True,
    role_name_i18n: str = "",
) -> int:
    return context.profiles.save(
        SearchProfileRecord(
            profile_id=None,
            candidate_id=candidate_id,
            name=name,
            scope_profile=scope_profile,
            target_role=name,
            location_preference="Munich\nBerlin\nRemote",
            role_name_i18n=role_name_i18n,
            keyword_focus=keyword_focus,
            is_active=is_active,
        )
    )


def save_openai_settings(
    context: AppContext,
    *,
    api_key: str = "test-key",
    model: str = "gpt-5-nano",
) -> None:
    context.settings.save_openai_settings(
        OpenAISettings(
            api_key=api_key,
            model=model,
            api_key_source="direct",
            api_key_env_var="",
        )
    )


class FakeJobSearchRunner:
    def __init__(self) -> None:
        self.jobs: list[JobSearchResult] = []
        self.stats = SearchStats()
        self.progress = SearchProgress()
        self.run_calls: list[dict[str, object]] = []
        self._run_result = SearchRunResult(
            success=True,
            exit_code=0,
            message="Search completed.",
            stdout_tail="",
            stderr_tail="",
            run_dir=DESKTOP_ROOT,
        )

    def set_jobs(self, jobs: list[JobSearchResult], stats: SearchStats | None = None) -> None:
        self.jobs = list(jobs)
        if stats is not None:
            self.stats = stats

    def set_progress(self, progress: SearchProgress) -> None:
        self.progress = progress

    def set_run_result(self, result: SearchRunResult) -> None:
        self._run_result = result

    def load_recommended_jobs(self, candidate_id: int) -> list[JobSearchResult]:
        return list(self.jobs)

    def load_search_stats(self, candidate_id: int) -> SearchStats:
        return self.stats

    def load_search_progress(self, candidate_id: int) -> SearchProgress:
        return self.progress

    def run_search(self, **kwargs: object) -> SearchRunResult:
        self.run_calls.append(dict(kwargs))
        return self._run_result


def make_job(
    *,
    title: str = "Systems Engineer",
    company: str = "Acme Robotics",
    location: str = "Munich, Germany",
    url: str = "https://example.com/jobs/1",
    date_found: str = "2026-04-14T12:00:00Z",
    match_score: int = 78,
    bound_target_role_name_en: str = "Systems Engineer",
) -> JobSearchResult:
    return JobSearchResult(
        title=title,
        company=company,
        location=location,
        url=url,
        date_found=date_found,
        match_score=match_score,
        recommend=True,
        fit_level_cn="高推荐",
        fit_track="adjacent_mbse",
        adjacent_direction_cn="系统工程",
        overall_match_score=match_score,
        bound_target_role_name_en=bound_target_role_name_en,
        bound_target_role_display_name=bound_target_role_name_en,
        bound_target_role_text=bound_target_role_name_en,
        final_url=url,
        link_status="final",
    )


@contextmanager
def patch_run_busy_task_sync() -> Iterator[None]:
    def _run_busy_task_sync(
        owner,
        *,
        title,
        message,
        task,
        on_success,
        on_error=None,
        on_finally=None,
        timeout_ms=None,
        show_dialog=True,
    ) -> bool:
        try:
            result = task()
        except Exception as exc:  # pragma: no cover - defensive
            if on_error is not None:
                on_error(exc)
        else:
            on_success(result)
        finally:
            if on_finally is not None:
                on_finally()
        return True

    with ExitStack() as stack:
        stack.enter_context(
            patch("jobflow_desktop_app.app.pages.target_direction.run_busy_task", new=_run_busy_task_sync)
        )
        stack.enter_context(
            patch("jobflow_desktop_app.app.pages.search_results.run_busy_task", new=_run_busy_task_sync)
        )
        yield


@contextmanager
def suppress_message_boxes(question_answer: int = QMessageBox.Yes) -> Iterator[None]:
    with (
        patch.object(QMessageBox, "information", return_value=QMessageBox.Ok),
        patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok),
        patch.object(QMessageBox, "question", return_value=question_answer),
    ):
        yield
