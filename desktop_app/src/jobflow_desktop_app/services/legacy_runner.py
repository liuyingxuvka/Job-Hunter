from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..db.repositories.candidates import CandidateRecord
from ..db.repositories.profiles import SearchProfileRecord
from ..db.repositories.settings import OpenAISettings
from .location_structured import candidate_location_preference_text
from .role_recommendations import description_query_lines, role_name_query_lines


@dataclass(frozen=True)
class LegacyRunResult:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    run_dir: Path
    config_path: Path
    recommended_json_path: Path
    cancelled: bool = False


@dataclass(frozen=True)
class LegacyJobResult:
    title: str
    company: str
    location: str
    url: str
    date_found: str
    match_score: int | None
    recommend: bool
    fit_level_cn: str
    fit_track: str
    adjacent_direction_cn: str
    source_url: str = ""
    final_url: str = ""
    link_status: str = "source"


@dataclass(frozen=True)
class LegacySearchStats:
    discovered_job_count: int = 0
    discovered_company_count: int = 0
    scored_job_count: int = 0
    recommended_job_count: int = 0
    pending_resume_count: int = 0
    candidate_company_pool_count: int = 0
    signal_hit_job_count: int = 0
    main_discovered_job_count: int = 0
    main_scored_job_count: int = 0
    displayable_result_count: int = 0
    main_pending_analysis_count: int = 0


@dataclass(frozen=True)
class LegacySearchProgress:
    status: str = "idle"
    stage: str = "idle"
    message: str = ""
    last_event: str = ""
    started_at: str = ""
    updated_at: str = ""
    elapsed_seconds: int = 0


@dataclass(frozen=True)
class LegacyStageRunResult:
    success: bool
    exit_code: int
    message: str
    stdout_tail: str
    stderr_tail: str
    cancelled: bool = False


class LegacyJobflowRunner:
    PIPELINE_MODE_COMPANY_ONLY = "company_only"
    PIPELINE_MODE_COMPANY_PLUS_WEB_SIGNAL = "company_plus_web_signal"

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.legacy_root = self.project_root.parent / "legacy_jobflow_reference"
        self.runtime_root = self.project_root / "runtime" / "legacy_runs"

    def run_search(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        settings: OpenAISettings | None = None,
        api_base_url: str = "",
        max_queries: int = 6,
        max_companies: int = 16,
        timeout_seconds: int = 900,
        cancel_event: threading.Event | None = None,
    ) -> LegacyRunResult:
        if candidate.candidate_id is None:
            raise ValueError("Candidate ID is required.")
        run_dir = self._candidate_run_dir(candidate.candidate_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = run_dir / "config.generated.json"
        web_signal_config_path = run_dir / "config.generated.web_signal.json"
        resume_config_path = run_dir / "config.generated.resume.json"
        recommended_json_path = run_dir / "jobs_recommended.json"
        progress_started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        current_stage = "preparing"
        progress_lock = threading.Lock()

        def write_progress(
            *,
            status: str,
            stage: str | None = None,
            message: str = "",
            last_event: str = "",
        ) -> None:
            with progress_lock:
                self._write_search_progress(
                    run_dir,
                    status=status,
                    stage=stage or current_stage,
                    message=message,
                    last_event=last_event,
                    started_at=progress_started_at,
                )

        def error_result(message: str, stdout_tail: str = "", stderr_tail: str = "") -> LegacyRunResult:
            detail = self._tail(stderr_tail or stdout_tail or message, max_lines=8, max_chars=1200)
            write_progress(status="error", message=message, last_event=detail)
            return self._error_result(
                candidate.candidate_id,
                message,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )

        def cancelled_result(message: str, stdout_tail: str = "", stderr_tail: str = "") -> LegacyRunResult:
            detail = self._tail(stderr_tail or stdout_tail or message, max_lines=8, max_chars=1200)
            write_progress(status="cancelled", stage="completed", message=message, last_event=detail)
            return LegacyRunResult(
                success=False,
                exit_code=-2,
                message=message,
                stdout_tail=self._tail(stdout_tail),
                stderr_tail=self._tail(stderr_tail),
                run_dir=run_dir,
                config_path=config_path,
                recommended_json_path=recommended_json_path,
                cancelled=True,
            )

        write_progress(
            status="running",
            stage=current_stage,
            message="Preparing search runtime and validating dependencies.",
            last_event="Initializing search workspace.",
        )
        if cancel_event is not None and cancel_event.is_set():
            return cancelled_result("Search cancelled before start.")
        if not self.legacy_root.exists():
            return error_result("legacy_jobflow_reference directory not found.")

        node_bin = self._resolve_node_binary()
        if not node_bin:
            return error_result(
                "Node.js is not available. Set JOBFLOW_NODE_PATH or place node.exe under runtime/tools/node/",
            )
        pipeline_mode = self._resolve_pipeline_mode()
        source_companies_path: Path | None = None
        analysis_budget = 0
        stage_stdout: list[tuple[str, str]] = []
        stage_stderr: list[tuple[str, str]] = []

        try:
            base_config = self._load_base_config()
            source_companies_path = self._prepare_candidate_companies_path(
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
            )
            runtime_config = self._build_runtime_config(
                base_config=base_config,
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
                model_override=self._resolve_model_override(settings),
                source_companies_path=source_companies_path,
                pipeline_stage="main",
            )
            config_path.write_text(
                json.dumps(runtime_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            analysis_budget = max(
                1,
                int(
                    self._ensure_dict(runtime_config, "analysis").get(
                        "maxJobsToAnalyzePerRun",
                        4,
                    )
                ),
            )

            resume_config = self._build_runtime_config(
                base_config=base_config,
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
                model_override=self._resolve_model_override(settings),
                source_companies_path=source_companies_path,
                pipeline_stage="resume_pending",
            )
            resume_config_path.write_text(
                json.dumps(resume_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if pipeline_mode == self.PIPELINE_MODE_COMPANY_PLUS_WEB_SIGNAL:
                web_signal_config = self._build_runtime_config(
                    base_config=base_config,
                    candidate=candidate,
                    profiles=profiles,
                    run_dir=run_dir,
                    model_override=self._resolve_model_override(settings),
                    source_companies_path=source_companies_path,
                    pipeline_stage="web_signal",
                )
                web_signal_config_path.write_text(
                    json.dumps(web_signal_config, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:
            return error_result(f"Failed to generate runtime config: {exc}")

        env = self._build_runtime_env(settings=settings, api_base_url=api_base_url)
        if not env.get("OPENAI_API_KEY", "").strip():
            return error_result(
                "OpenAI API key is missing. Set OPENAI_API_KEY or save it in AI settings.",
            )
        dependency_check = self._ensure_legacy_dependencies(node_bin=node_bin)
        if not dependency_check.success:
            return error_result(
                dependency_check.message,
                stdout_tail=dependency_check.stdout_tail,
                stderr_tail=dependency_check.stderr_tail,
            )
        if cancel_event is not None and cancel_event.is_set():
            return cancelled_result("Search cancelled before execution.")

        stage_notes: list[str] = []
        try:
            resume_pending_count = self._write_resume_pending_jobs(run_dir)
        except Exception as exc:
                stage_notes.append(f"Resume queue refresh skipped: {exc}")
        else:
            if resume_pending_count > 0:
                stage_notes.append(
                    f"Resume queue: {resume_pending_count} unfinished jobs will be processed before discovery continues."
                )

        def run_resume_stage(message: str, start_event: str) -> LegacyStageRunResult | None:
            nonlocal current_stage
            if not resume_config_path.exists():
                return None
            current_stage = "resume_pending"
            write_progress(
                status="running",
                message=message,
                last_event=start_event,
            )
            return self._run_legacy_stage(
                command=[
                    node_bin,
                    "jobflow.mjs",
                    "--config",
                    str(resume_config_path),
                    "--max-queries",
                    str(max(1, int(max_queries))),
                    "--max-companies",
                    str(max(1, int(max_companies))),
                ],
                env=env,
                timeout_seconds=max(240, min(timeout_seconds, 900)),
                cancel_event=cancel_event,
                progress_callback=lambda line: write_progress(
                    status="running",
                    message=message,
                    last_event=line,
                ),
            )

        auto_resume_threshold = max(1, min(4, analysis_budget))
        if resume_pending_count > 0:
            resume_result = run_resume_stage(
                "Completing unfinished main-stage jobs from the last run before new discovery.",
                "Starting pending-job resume pass.",
            )
            if resume_result is not None:
                if resume_result.stdout_tail:
                    stage_stdout.append(("resume_pending", resume_result.stdout_tail))
                if resume_result.stderr_tail:
                    stage_stderr.append(("resume_pending", resume_result.stderr_tail))
                if resume_result.cancelled:
                    return cancelled_result(
                        "Search cancelled while resuming unfinished jobs.",
                        stdout_tail=resume_result.stdout_tail,
                        stderr_tail=resume_result.stderr_tail,
                    )
                if resume_result.success:
                    try:
                        resume_pending_count = self._write_resume_pending_jobs(run_dir)
                    except Exception as exc:
                        stage_notes.append(f"Resume queue finalize skipped: {exc}")
                    else:
                        if resume_pending_count > 0:
                            stage_notes.append(
                                f"Resume-only stage left {resume_pending_count} unfinished job(s)."
                            )
                        else:
                            stage_notes.append("Resume-only stage completed all unfinished jobs.")
                else:
                    stage_notes.append(
                        f"Resume-only stage failed (exit {resume_result.exit_code}); continue with discovery pipeline."
                    )

        signal_stdout = ""
        signal_stderr = ""
        if (
            pipeline_mode == self.PIPELINE_MODE_COMPANY_PLUS_WEB_SIGNAL
            and source_companies_path is not None
            and web_signal_config_path.exists()
        ):
            current_stage = "web_signal"
            write_progress(
                status="running",
                message="Running web signal discovery before main-stage job analysis.",
                last_event="Starting web signal queries.",
            )
            signal_command = [
                node_bin,
                "jobflow.mjs",
                "--config",
                str(web_signal_config_path),
                "--max-queries",
                str(self._signal_max_queries(max_queries)),
                "--max-companies",
                str(max(1, min(int(max_companies), 120))),
                "--dry-run",
            ]
            signal_result = self._run_legacy_stage(
                command=signal_command,
                env=env,
                timeout_seconds=max(300, min(timeout_seconds, 900)),
                cancel_event=cancel_event,
                progress_callback=lambda line: write_progress(
                    status="running",
                    message="Running web signal discovery before main-stage job analysis.",
                    last_event=line,
                ),
            )
            if signal_result.cancelled:
                return cancelled_result(
                    "Search cancelled during web signal stage.",
                    stdout_tail=signal_result.stdout_tail,
                    stderr_tail=signal_result.stderr_tail,
                )
            signal_stdout = signal_result.stdout_tail
            signal_stderr = signal_result.stderr_tail
            if signal_stdout:
                stage_stdout.append(("web_signal", signal_stdout))
            if signal_stderr:
                stage_stderr.append(("web_signal", signal_stderr))
            if signal_result.success:
                try:
                    added = self._ingest_web_signal_companies(
                        run_dir=run_dir,
                        companies_path=source_companies_path,
                    )
                    if added > 0:
                        stage_notes.append(f"Web signal intake: +{added} companies.")
                    write_progress(
                        status="running",
                        message="Web signal discovery finished; candidate company pool updated.",
                        last_event=f"Web signal intake completed with {added} added companies.",
                    )
                except Exception as exc:
                    stage_notes.append(f"Web signal intake skipped: {exc}")
                    write_progress(
                        status="running",
                        message="Web signal discovery finished; candidate company pool update skipped.",
                        last_event=f"Web signal intake skipped: {exc}",
                    )
            else:
                stage_notes.append(
                    f"Web signal stage failed (exit {signal_result.exit_code}); continue with company-first stage."
                )
                write_progress(
                    status="running",
                    message="Web signal stage failed; falling back to main-stage company processing.",
                    last_event=signal_result.stderr_tail or signal_result.stdout_tail or signal_result.message,
                )
        if cancel_event is not None and cancel_event.is_set():
            return cancelled_result("Search cancelled before main-stage analysis.")

        current_stage = "main"
        write_progress(
            status="running",
            message="Running main-stage company search, scoring, and post-verification.",
            last_event="Starting main-stage job collection.",
        )
        main_command = [
            node_bin,
            "jobflow.mjs",
            "--config",
            str(config_path),
            "--max-queries",
            str(max(1, int(max_queries))),
            "--max-companies",
            str(max(1, int(max_companies))),
        ]
        main_result = self._run_legacy_stage(
            command=main_command,
            env=env,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
            progress_callback=lambda line: write_progress(
                status="running",
                message="Running main-stage company search, scoring, and post-verification.",
                last_event=line,
            ),
        )
        if main_result.cancelled:
            return cancelled_result(
                "Search cancelled during main-stage analysis.",
                stdout_tail=main_result.stdout_tail,
                stderr_tail=main_result.stderr_tail,
            )
        if main_result.stdout_tail:
            stage_stdout.append(("main", main_result.stdout_tail))
        if main_result.stderr_tail:
            stage_stderr.append(("main", main_result.stderr_tail))
        pending_after_main = 0
        try:
            pending_after_main = self._write_resume_pending_jobs(run_dir)
        except Exception as exc:
            stage_notes.append(f"Resume queue finalize skipped: {exc}")
        else:
            if (
                main_result.success
                and pending_after_main > 0
                and pending_after_main <= auto_resume_threshold
            ):
                resume_result = run_resume_stage(
                    "Finishing the last few unfinished main-stage jobs before wrapping up.",
                    "Starting final pending-job pass.",
                )
                if resume_result is not None:
                    if resume_result.stdout_tail:
                        stage_stdout.append(("resume_finalize", resume_result.stdout_tail))
                    if resume_result.stderr_tail:
                        stage_stderr.append(("resume_finalize", resume_result.stderr_tail))
                    if resume_result.cancelled:
                        return cancelled_result(
                            "Search cancelled while finalizing unfinished jobs.",
                            stdout_tail=resume_result.stdout_tail,
                            stderr_tail=resume_result.stderr_tail,
                        )
                    if resume_result.success:
                        try:
                            pending_after_main = self._write_resume_pending_jobs(run_dir)
                        except Exception as exc:
                            stage_notes.append(f"Resume queue final check skipped: {exc}")
                        else:
                            if pending_after_main > 0:
                                stage_notes.append(
                                    f"Final pending pass still left {pending_after_main} unfinished job(s)."
                                )
                            else:
                                stage_notes.append("Final pending pass cleared the remaining unfinished jobs.")
                    else:
                        stage_notes.append(
                            f"Final pending pass failed (exit {resume_result.exit_code}); remaining unfinished jobs stay queued."
                        )
        success = main_result.success
        if success:
            message = "Legacy engine completed."
        else:
            message = main_result.message or "Legacy engine failed."
        if stage_notes:
            message = f"{message} {' '.join(stage_notes)}"

        combined_stdout = [f"[{label}]\n{text}" for label, text in stage_stdout if text]
        combined_stderr = [f"[{label}]\n{text}" for label, text in stage_stderr if text]
        write_progress(
            status="success" if success else "error",
            stage="completed" if success else "main",
            message=message,
            last_event=main_result.stderr_tail or main_result.stdout_tail or message,
        )
        return LegacyRunResult(
            success=success,
            exit_code=main_result.exit_code,
            message=message,
            stdout_tail=self._tail("\n\n".join(combined_stdout)),
            stderr_tail=self._tail("\n\n".join(combined_stderr)),
            run_dir=run_dir,
            config_path=config_path,
            recommended_json_path=recommended_json_path,
        )

    def load_recommended_jobs(self, candidate_id: int) -> list[LegacyJobResult]:
        run_dir = self._candidate_run_dir(candidate_id)
        recommended = self._filter_review_ready_jobs(self._load_jobs_from_file(run_dir / "jobs_recommended.json"))
        jobs: list[dict] = recommended

        if not jobs:
            all_jobs = self._load_jobs_from_file(run_dir / "jobs.json")
            if all_jobs:
                ready_all_jobs = self._filter_review_ready_jobs(all_jobs)
                recommended_in_all = [
                    item
                    for item in all_jobs
                    if bool((item.get("analysis") or {}).get("recommend"))
                ]
                recommended_ready = self._filter_review_ready_jobs(recommended_in_all)
                if recommended_ready:
                    jobs = recommended_ready
                else:
                    ready_all_jobs.sort(
                        key=lambda item: int(self._extract_match_score(item.get("analysis")) or 0),
                        reverse=True,
                    )
                    jobs = ready_all_jobs[:20]

        return self._build_job_records(jobs)

    def load_live_jobs(self, candidate_id: int) -> list[LegacyJobResult]:
        run_dir = self._candidate_run_dir(candidate_id)
        jobs = self._merge_job_items_from_paths(
            [
                run_dir / "jobs_found.signal.json",
                run_dir / "jobs.signal.json",
                run_dir / "jobs_found.json",
                run_dir / "jobs.json",
                run_dir / "jobs_recommended.json",
            ]
        )
        return self._build_job_records(self._filter_review_ready_jobs(jobs))

    def load_search_stats(self, candidate_id: int) -> LegacySearchStats:
        run_dir = self._candidate_run_dir(candidate_id)
        candidate_companies_payload = self._load_companies_payload(run_dir / "companies.candidate.json")
        # Keep signal-stage hits and main-stage jobs separate so the UI can
        # show what was only discovered by web signal versus what reached the
        # company-first analysis pipeline.
        signal_paths = [
            run_dir / "jobs_found.signal.json",
            run_dir / "jobs.signal.json",
        ]
        main_paths = [
            run_dir / "jobs_found.json",
            run_dir / "jobs.json",
            run_dir / "jobs_recommended.json",
        ]
        signal_jobs = self._merge_job_items_from_paths(signal_paths)
        main_jobs = self._merge_job_items_from_paths(main_paths)
        merged_jobs = self._merge_job_items_from_paths(signal_paths + main_paths)
        discovered_companies: set[str] = set()
        for item in merged_jobs:
            company = self._clean_company_name(item.get("company"))
            if company:
                discovered_companies.add(company.casefold())

        pending_jobs = self._load_resume_pending_jobs(run_dir)
        recommended_jobs = self.load_recommended_jobs(candidate_id)
        scored_jobs = self._filter_review_ready_jobs(merged_jobs)
        main_scored_jobs = self._filter_review_ready_jobs(main_jobs)

        candidate_company_pool_count = self._count_unique_companies_in_payload(candidate_companies_payload)
        signal_hit_job_count = len(signal_jobs)
        main_discovered_job_count = len(main_jobs)
        main_scored_job_count = len(main_scored_jobs)
        displayable_result_count = len(recommended_jobs)
        main_pending_analysis_count = len(pending_jobs)
        return LegacySearchStats(
            discovered_job_count=len(merged_jobs),
            discovered_company_count=len(discovered_companies),
            scored_job_count=len(scored_jobs),
            recommended_job_count=displayable_result_count,
            pending_resume_count=main_pending_analysis_count,
            candidate_company_pool_count=candidate_company_pool_count,
            signal_hit_job_count=signal_hit_job_count,
            main_discovered_job_count=main_discovered_job_count,
            main_scored_job_count=main_scored_job_count,
            displayable_result_count=displayable_result_count,
            main_pending_analysis_count=main_pending_analysis_count,
        )

    def load_search_progress(self, candidate_id: int) -> LegacySearchProgress:
        return self._load_search_progress_from_run_dir(self._candidate_run_dir(candidate_id))

    @classmethod
    def _merge_job_items_from_paths(cls, paths: list[Path]) -> list[dict]:
        merged_jobs: dict[str, dict] = {}
        for path in paths:
            for item in cls._load_jobs_from_file(path):
                key = cls._job_item_key(item)
                if not key:
                    continue
                existing = merged_jobs.get(key)
                if existing is None:
                    merged_jobs[key] = dict(item)
                    continue
                merged_jobs[key] = cls._merge_job_item(existing, item)
        return list(merged_jobs.values())

    @classmethod
    def _count_unique_companies_in_payload(cls, payload: dict) -> int:
        companies = payload.get("companies", [])
        if not isinstance(companies, list):
            return 0
        seen_keys: set[str] = set()
        unique_count = 0
        for raw in companies:
            if not isinstance(raw, dict):
                continue
            company_keys: list[str] = []
            name = cls._clean_company_name(raw.get("name"))
            if name:
                company_keys.append(f"name:{name.casefold()}")
            for url in (str(raw.get("website") or "").strip(), str(raw.get("careersUrl") or "").strip()):
                domain = cls._domain_from_url(url)
                if domain:
                    company_keys.append(f"domain:{domain}")
            if not company_keys:
                continue
            if any(key in seen_keys for key in company_keys):
                seen_keys.update(company_keys)
                continue
            unique_count += 1
            seen_keys.update(company_keys)
        return unique_count

    @staticmethod
    def _load_jobs_from_file(path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list):
            return []
        return [item for item in jobs if isinstance(item, dict)]

    @staticmethod
    def _job_item_key(item: dict) -> str:
        url = str(item.get("url") or item.get("canonicalUrl") or "").strip()
        if url:
            return url.casefold()
        title = str(item.get("title") or "").strip().casefold()
        company = str(item.get("company") or "").strip().casefold()
        date_found = str(item.get("dateFound") or "").strip()
        return f"{title}|{company}|{date_found}"

    @staticmethod
    def _merge_job_item(existing: dict, incoming: dict) -> dict:
        merged = dict(existing)
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                nested = dict(merged.get(key) or {})
                for nested_key, nested_value in value.items():
                    if nested_value not in ("", None, [], {}):
                        nested[nested_key] = nested_value
                merged[key] = nested
                continue
            if value not in ("", None, [], {}):
                merged[key] = value
        return merged

    @staticmethod
    def _extract_match_score(analysis: object) -> int | None:
        if not isinstance(analysis, dict):
            return None
        raw_score = analysis.get("matchScore")
        if isinstance(raw_score, bool):
            return None
        if isinstance(raw_score, int):
            return raw_score
        if isinstance(raw_score, float) and raw_score.is_integer():
            return int(raw_score)
        try:
            text = str(raw_score or "").strip()
            if not text:
                return None
            return int(text)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _filter_review_ready_jobs(cls, jobs: list[dict]) -> list[dict]:
        ready_jobs: list[dict] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            analysis = item.get("analysis", {})
            if cls._extract_match_score(analysis) is None:
                continue
            ready_jobs.append(item)
        return ready_jobs

    @classmethod
    def _analysis_completed(cls, analysis: object) -> bool:
        if not isinstance(analysis, dict):
            return False
        if cls._extract_match_score(analysis) is not None:
            return True
        return bool(analysis.get("prefilterRejected"))

    @classmethod
    def _collect_resume_pending_jobs(cls, run_dir: Path) -> list[dict]:
        merged_jobs: dict[str, dict] = {}
        paths = [
            run_dir / "jobs_found.json",
            run_dir / "jobs.json",
            run_dir / "jobs_recommended.json",
        ]
        for path in paths:
            for item in cls._load_jobs_from_file(path):
                key = cls._job_item_key(item)
                if not key:
                    continue
                existing = merged_jobs.get(key)
                if existing is None:
                    merged_jobs[key] = dict(item)
                    continue
                merged_jobs[key] = cls._merge_job_item(existing, item)

        pending_jobs: list[dict] = []
        for item in merged_jobs.values():
            if not isinstance(item, dict):
                continue
            job = dict(item)
            job_url = str(job.get("url") or job.get("canonicalUrl") or "").strip()
            if not job_url:
                continue
            job["url"] = job_url
            if cls._analysis_completed(job.get("analysis")):
                continue
            pending_jobs.append(job)

        pending_jobs.sort(
            key=lambda item: (
                str(item.get("dateFound") or ""),
                str(item.get("company") or "").casefold(),
                str(item.get("title") or "").casefold(),
                str(item.get("url") or "").casefold(),
            )
        )
        return pending_jobs

    @classmethod
    def _merge_resume_pending_job_lists(cls, run_dir: Path, *job_lists: list[dict]) -> list[dict]:
        merged_jobs: dict[str, dict] = {}
        for jobs in job_lists:
            normalized_jobs = cls._normalize_resume_pending_jobs(
                jobs if isinstance(jobs, list) else [],
                run_dir,
            )
            for item in normalized_jobs:
                key = cls._job_item_key(item)
                if not key:
                    continue
                existing = merged_jobs.get(key)
                if existing is None:
                    merged_jobs[key] = dict(item)
                    continue
                merged_jobs[key] = cls._merge_job_item(existing, item)
        return cls._normalize_resume_pending_jobs(list(merged_jobs.values()), run_dir)

    @classmethod
    def _load_resume_pending_payload(cls, run_dir: Path) -> dict:
        payload_path = run_dir / "jobs_resume_pending.json"
        if not payload_path.exists():
            return {}
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _normalize_resume_pending_jobs(cls, jobs: list[dict], run_dir: Path) -> list[dict]:
        merged_details = {
            cls._job_item_key(item): item
            for item in cls._merge_job_items_from_paths(
                [
                    run_dir / "jobs_found.json",
                    run_dir / "jobs.json",
                    run_dir / "jobs_recommended.json",
                ]
            )
            if cls._job_item_key(item)
        }
        pending_jobs: list[dict] = []
        for raw in jobs:
            if not isinstance(raw, dict):
                continue
            job = dict(raw)
            key = cls._job_item_key(job)
            if key:
                detail = merged_details.get(key)
                if detail is not None:
                    job = cls._merge_job_item(job, detail)
            job_url = str(job.get("url") or job.get("canonicalUrl") or "").strip()
            if not job_url:
                continue
            job["url"] = job_url
            if cls._analysis_completed(job.get("analysis")):
                continue
            pending_jobs.append(job)

        pending_jobs.sort(
            key=lambda item: (
                str(item.get("dateFound") or ""),
                str(item.get("company") or "").casefold(),
                str(item.get("title") or "").casefold(),
                str(item.get("url") or "").casefold(),
            )
        )
        return pending_jobs

    @classmethod
    def _load_resume_pending_jobs(cls, run_dir: Path, include_fallback: bool = True) -> list[dict]:
        payload = cls._load_resume_pending_payload(run_dir)
        payload_jobs = payload.get("jobs", [])
        if isinstance(payload_jobs, list) and not include_fallback:
            return cls._normalize_resume_pending_jobs(payload_jobs, run_dir)
        if not include_fallback:
            return []
        current_jobs = cls._collect_resume_pending_jobs(run_dir)
        return cls._merge_resume_pending_job_lists(
            run_dir,
            current_jobs,
            payload_jobs if isinstance(payload_jobs, list) else [],
        )

    @classmethod
    def _build_resume_pending_payload(cls, run_dir: Path) -> dict:
        existing_payload = cls._load_resume_pending_payload(run_dir)
        payload_jobs = existing_payload.get("jobs", [])
        pending_jobs = cls._merge_resume_pending_job_lists(
            run_dir,
            cls._collect_resume_pending_jobs(run_dir),
            payload_jobs if isinstance(payload_jobs, list) else [],
        )
        existing_version = existing_payload.get("version")
        try:
            version = max(2, int(existing_version))
        except (TypeError, ValueError):
            version = 2

        preserved_meta = {
            key: value
            for key, value in existing_payload.items()
            if key not in {"version", "generatedAt", "jobs", "summary"}
        }
        payload = {
            **preserved_meta,
            "version": version,
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "jobs": pending_jobs,
        }
        existing_summary = existing_payload.get("summary")
        if isinstance(existing_summary, dict):
            summary = dict(existing_summary)
            summary["mainStagePendingCount"] = len(pending_jobs)
            payload["summary"] = summary
        return payload

    @classmethod
    def _write_resume_pending_jobs(cls, run_dir: Path) -> int:
        payload = cls._build_resume_pending_payload(run_dir)
        output_path = run_dir / "jobs_resume_pending.json"
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(payload["jobs"])

    @staticmethod
    def _build_job_records(jobs: list[dict]) -> list[LegacyJobResult]:
        records: list[LegacyJobResult] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            analysis = item.get("analysis", {})
            if not isinstance(analysis, dict):
                analysis = {}
            recommend = bool(analysis.get("recommend"))
            score = LegacyJobflowRunner._extract_match_score(analysis)
            source_url, final_url, link_status = LegacyJobflowRunner._resolve_job_links(item)
            canonical_url = str(item.get("canonicalUrl") or "").strip()

            records.append(
                LegacyJobResult(
                    title=str(item.get("title") or "").strip(),
                    company=str(item.get("company") or "").strip(),
                    location=str(item.get("location") or "").strip(),
                    url=source_url or canonical_url,
                    date_found=str(item.get("dateFound") or "").strip(),
                    match_score=score,
                    recommend=recommend,
                    fit_level_cn=str(analysis.get("fitLevelCn") or "").strip(),
                    fit_track=str(analysis.get("fitTrack") or "").strip(),
                    adjacent_direction_cn=str(analysis.get("adjacentDirectionCn") or "").strip(),
                    source_url=source_url,
                    final_url=final_url,
                    link_status=link_status,
                )
            )
        return records

    @staticmethod
    def _resolve_job_links(item: dict) -> tuple[str, str, str]:
        source_url = str(item.get("url") or "").strip()
        canonical_url = str(item.get("canonicalUrl") or "").strip()
        analysis = item.get("analysis")
        if not isinstance(analysis, dict):
            analysis = {}
        post_verify = analysis.get("postVerify")
        if not isinstance(post_verify, dict):
            post_verify = {}
        jd = item.get("jd")
        if not isinstance(jd, dict):
            jd = {}

        verified_final_url = str(post_verify.get("finalUrl") or "").strip()
        if verified_final_url and LegacyJobflowRunner._coerce_bool(post_verify.get("isValidJobPage")):
            return source_url, verified_final_url, "verified_final"

        apply_url = str(jd.get("applyUrl") or item.get("applyUrl") or "").strip()
        if apply_url:
            return source_url, apply_url, "apply"

        final_url = str(jd.get("finalUrl") or item.get("finalUrl") or "").strip()
        if final_url:
            return source_url, final_url, "final"

        if canonical_url:
            return source_url, canonical_url, "canonical"

        if source_url:
            return source_url, source_url, "source"

        return "", "", "source"

    @staticmethod
    def _coerce_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value or "").strip().casefold()
        return text in {"1", "true", "yes", "y", "on"}

    def _load_base_config(self) -> dict:
        candidates = [
            self.legacy_root / "config.json",
            self.legacy_root / "config.example.json",
        ]
        for path in candidates:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError("No legacy config file found.")

    def _build_runtime_env(self, settings: OpenAISettings | None, api_base_url: str) -> dict[str, str]:
        env = os.environ.copy()
        use_env_only = (
            settings is not None and str(settings.api_key_source or "").strip().lower() == "env"
        )
        settings_key = settings.api_key.strip() if settings is not None else ""
        if settings_key:
            env["OPENAI_API_KEY"] = settings_key
        elif not use_env_only and not env.get("OPENAI_API_KEY", "").strip():
            azure_key = env.get("AZURE_OPENAI_API_KEY", "").strip()
            if azure_key:
                env["OPENAI_API_KEY"] = azure_key

        settings_model = settings.model.strip() if settings is not None else ""
        if settings_model:
            env["JOBFLOW_OPENAI_MODEL"] = settings_model
        elif not env.get("JOBFLOW_OPENAI_MODEL", "").strip():
            azure_model = (
                env.get("AZURE_OPENAI_MODEL", "").strip()
                or env.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
            )
            if azure_model:
                env["JOBFLOW_OPENAI_MODEL"] = azure_model

        if not env.get("JOBFLOW_OPENAI_MODEL", "").strip():
            env["JOBFLOW_OPENAI_MODEL"] = "gpt-5"

        if not (env.get("OPENAI_BASE_URL", "").strip() or env.get("OPENAI_API_BASE", "").strip()):
            azure_endpoint = env.get("AZURE_OPENAI_ENDPOINT", "").strip()
            if azure_endpoint:
                normalized = azure_endpoint.rstrip("/")
                if normalized.endswith("/openai/v1"):
                    derived = normalized
                elif normalized.endswith("/openai"):
                    derived = f"{normalized}/v1"
                elif normalized.endswith("/v1"):
                    derived = f"{normalized}/openai/v1"
                else:
                    derived = f"{normalized}/openai/v1"
                env["OPENAI_BASE_URL"] = derived
                env["OPENAI_API_BASE"] = derived
        if api_base_url.strip() and not (
            env.get("OPENAI_BASE_URL", "").strip() or env.get("OPENAI_API_BASE", "").strip()
        ):
            env["OPENAI_BASE_URL"] = api_base_url.strip()
            env["OPENAI_API_BASE"] = api_base_url.strip()
        return env

    def _resolve_node_binary(self) -> str:
        from_path = shutil.which("node")
        if from_path:
            return from_path

        custom = os.getenv("JOBFLOW_NODE_PATH", "").strip()
        if custom and Path(custom).exists():
            return str(Path(custom))

        candidates = [
            self.project_root / "runtime" / "tools" / "node" / "node.exe",
            self.project_root / "runtime" / "tools" / "nodejs" / "node.exe",
            self.project_root / "runtime" / "tools" / "node-v22-win-x64" / "node.exe",
            self.project_root / "runtime" / "tools" / "node-v20-win-x64" / "node.exe",
        ]
        for path in candidates:
            if path.exists():
                return str(path)

        tools_dir = self.project_root / "runtime" / "tools"
        if tools_dir.exists():
            for folder in sorted(tools_dir.glob("node-v*-win-x64"), reverse=True):
                binary = folder / "node.exe"
                if binary.exists():
                    return str(binary)
        return ""

    def _resolve_npm_binary(self, node_bin: str = "") -> str:
        for candidate in ("npm.cmd", "npm", "npm.exe"):
            from_path = shutil.which(candidate)
            if from_path:
                return from_path

        custom = os.getenv("JOBFLOW_NPM_PATH", "").strip()
        if custom and Path(custom).exists():
            return str(Path(custom))

        if node_bin:
            node_path = Path(node_bin)
            sibling_candidates = (
                node_path.with_name("npm.cmd"),
                node_path.with_name("npm"),
                node_path.with_name("npm.exe"),
            )
            for path in sibling_candidates:
                if path.exists():
                    return str(path)
        return ""

    def _ensure_legacy_dependencies(self, node_bin: str) -> LegacyStageRunResult:
        if self._legacy_dependencies_ready():
            return LegacyStageRunResult(
                success=True,
                exit_code=0,
                message="Legacy Node dependencies are ready.",
                stdout_tail="",
                stderr_tail="",
            )

        npm_bin = self._resolve_npm_binary(node_bin=node_bin)
        if not npm_bin:
            return LegacyStageRunResult(
                success=False,
                exit_code=-1,
                message=(
                    "Legacy Node dependencies are missing, and npm is not available. "
                    "Install dependencies in legacy_jobflow_reference or provide npm via PATH / JOBFLOW_NPM_PATH."
                ),
                stdout_tail="",
                stderr_tail="",
            )

        install_result = self._install_legacy_dependencies(npm_bin=npm_bin)
        if not install_result.success:
            return install_result
        if self._legacy_dependencies_ready():
            return LegacyStageRunResult(
                success=True,
                exit_code=0,
                message="Legacy Node dependencies are ready.",
                stdout_tail=install_result.stdout_tail,
                stderr_tail=install_result.stderr_tail,
            )
        return LegacyStageRunResult(
            success=False,
            exit_code=-1,
            message=(
                "Legacy Node dependencies were installed, but required packages are still missing. "
                "Please check legacy_jobflow_reference/node_modules."
            ),
            stdout_tail=install_result.stdout_tail,
            stderr_tail=install_result.stderr_tail,
        )

    def _install_legacy_dependencies(self, npm_bin: str) -> LegacyStageRunResult:
        package_lock = self.legacy_root / "package-lock.json"
        commands: list[list[str]] = []
        if package_lock.exists():
            commands.append([npm_bin, "ci", "--no-audit", "--no-fund"])
        commands.append([npm_bin, "install", "--no-audit", "--no-fund"])

        last_result: LegacyStageRunResult | None = None
        for command in commands:
            result = self._run_process_stage(
                command=command,
                cwd=self.legacy_root,
                env=os.environ.copy(),
                timeout_seconds=600,
                success_message="Legacy dependency installation completed.",
                failure_message="Legacy dependency installation failed.",
            )
            if result.success:
                return result
            last_result = result
        return last_result or LegacyStageRunResult(
            success=False,
            exit_code=-1,
            message="Legacy dependency installation failed.",
            stdout_tail="",
            stderr_tail="",
        )

    def _legacy_dependencies_ready(self) -> bool:
        node_modules_dir = self.legacy_root / "node_modules"
        if not node_modules_dir.exists():
            return False

        required_packages = self._legacy_dependency_names()
        if not required_packages:
            return True
        for package_name in required_packages:
            if not (node_modules_dir / package_name / "package.json").exists():
                return False
        return True

    def _legacy_dependency_names(self) -> list[str]:
        package_json_path = self.legacy_root / "package.json"
        fallback = ["cheerio", "exceljs", "openai", "p-limit"]
        if not package_json_path.exists():
            return fallback
        try:
            payload = json.loads(package_json_path.read_text(encoding="utf-8"))
        except Exception:
            return fallback
        dependencies = payload.get("dependencies")
        if not isinstance(dependencies, dict):
            return fallback
        names = [str(name or "").strip() for name in dependencies.keys()]
        return [name for name in names if name] or fallback

    def _build_runtime_config(
        self,
        base_config: dict,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        run_dir: Path,
        model_override: str = "",
        source_companies_path: Path | None = None,
        pipeline_stage: str = "main",
    ) -> dict:
        config = copy.deepcopy(base_config)
        candidate_config = self._ensure_dict(config, "candidate")
        search_config = self._ensure_dict(config, "search")
        sources_config = self._ensure_dict(config, "sources")
        output_config = self._ensure_dict(config, "output")
        company_discovery_config = self._ensure_dict(config, "companyDiscovery")
        analysis_config = self._ensure_dict(config, "analysis")
        translation_config = self._ensure_dict(config, "translation")

        resume_path = self._resolve_resume_path(candidate, run_dir)
        scope_profile = self._resolve_scope_profile(profiles)
        target_role = self._resolve_target_role(candidate, profiles)
        location_preference = candidate_location_preference_text(
            base_location_struct=candidate.base_location_struct,
            preferred_locations_struct=candidate.preferred_locations_struct,
            base_location_text=candidate.base_location,
            preferred_locations_text=candidate.preferred_locations,
        )

        candidate_config["resumePath"] = resume_path
        candidate_config["scopeProfile"] = scope_profile
        candidate_config["targetRole"] = target_role
        candidate_config["locationPreference"] = location_preference

        queries = self._build_queries(candidate, profiles, run_dir=run_dir)
        search_config["queries"] = queries
        if pipeline_stage == "web_signal":
            search_config["maxJobsPerQuery"] = min(
                35,
                max(10, int(search_config.get("maxJobsPerQuery", 20))),
            )
        else:
            search_config["maxJobsPerQuery"] = min(
                50,
                max(10, int(search_config.get("maxJobsPerQuery", 30))),
            )
        if model_override:
            search_config["model"] = model_override

        if source_companies_path is None:
            source_companies_path = self._prepare_candidate_companies_path(
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
            )
        company_discovery_queries = self._build_company_discovery_queries(
            candidate=candidate,
            profiles=profiles,
            search_queries=queries,
            run_dir=run_dir,
            companies_path=source_companies_path,
        )
        sources_config["companiesPath"] = str(source_companies_path.resolve())

        if model_override:
            company_discovery_config["model"] = model_override
            analysis_config["model"] = model_override
            analysis_config["postVerifyModel"] = model_override
            translation_config["model"] = model_override

        analysis_config["preFilterEnabled"] = False
        analysis_config["recommendScoreThreshold"] = min(
            50,
            max(35, int(analysis_config.get("recommendScoreThreshold", 50))),
        )
        analysis_config["minTransferableScore"] = min(
            45,
            max(30, int(analysis_config.get("minTransferableScore", 40))),
        )
        analysis_config["maxJobsToAnalyzePerRun"] = min(
            80,
            max(20, int(analysis_config.get("maxJobsToAnalyzePerRun", 60))),
        )

        if pipeline_stage == "resume_pending":
            # Resume-only stage processes unfinished main-stage jobs before
            # launching any fresh discovery work.
            sources_config["enableWebSearch"] = False
            sources_config["enableCompanySources"] = False
            sources_config["requireCompanyDiscovery"] = False
            sources_config["enableCompanySearchFallback"] = False
            company_discovery_config["enableAutoDiscovery"] = False
            company_discovery_config["queries"] = []
            analysis_config["scoringUseWebSearch"] = False
            analysis_config["postVerifyEnabled"] = True
            analysis_config["postVerifyUseWebSearch"] = True
            analysis_config["postVerifyRequireChecked"] = True

            output_config["foundJsonPath"] = "./jobs_found.resume_pending.json"
            return config

        if pipeline_stage == "web_signal":
            # Web signal stage only collects potential hiring company hints from web job hits.
            sources_config["enableWebSearch"] = True
            sources_config["enableCompanySources"] = False
            sources_config["requireCompanyDiscovery"] = False
            sources_config["enableCompanySearchFallback"] = False
            company_discovery_config["enableAutoDiscovery"] = False
            company_discovery_config["queries"] = []
            # This stage is always executed with '--dry-run'. strictScoring must be disabled,
            # otherwise legacy jobflow throws and aborts before signal ingestion.
            analysis_config["strictScoring"] = False
            analysis_config["scoringUseWebSearch"] = False
            analysis_config["postVerifyEnabled"] = False
            analysis_config["postVerifyUseWebSearch"] = False
            analysis_config["postVerifyRequireChecked"] = False
            translation_config["enable"] = False

            output_config["trackerXlsxPath"] = "./jobs_recommended.signal.xlsx"
            output_config["xlsxPath"] = "./jobs.signal.xlsx"
            output_config["jsonPath"] = "./jobs.signal.json"
            output_config["foundJsonPath"] = "./jobs_found.signal.json"
            output_config["recommendedXlsxPath"] = "./jobs_recommended.signal.xlsx"
            output_config["recommendedJsonPath"] = "./jobs_recommended.signal.json"
            output_config["cnEuropeJsonPath"] = "./jobs_cn_europe.signal.json"
            output_config["resumePendingPath"] = "./jobs_resume_pending.json"
            return config

        # Main stage: company pool first, company sources as final job channel.
        adaptive_search_config = self._ensure_dict(config, "adaptiveSearch")
        fetch_config = self._ensure_dict(config, "fetch")

        sources_config["enableWebSearch"] = False
        sources_config["enableCompanySources"] = True
        sources_config["requireCompanyDiscovery"] = True
        sources_config["maxCompaniesPerRun"] = 16
        sources_config["maxJobsPerCompany"] = 5
        sources_config["maxJobLinksPerCompany"] = 12
        sources_config["enableCompanySearchFallback"] = True
        sources_config["maxCompanySearchFallbacksPerRun"] = 2

        company_discovery_config["enableAutoDiscovery"] = True
        company_discovery_config["queries"] = company_discovery_queries
        company_discovery_config["maxNewCompaniesPerRun"] = 6
        analysis_config["scoringUseWebSearch"] = False
        # Ask AI to post-verify link validity/expiry signals before final recommendation.
        analysis_config["postVerifyEnabled"] = True
        analysis_config["postVerifyUseWebSearch"] = True
        analysis_config["postVerifyRequireChecked"] = True
        analysis_config["maxJobsToAnalyzePerRun"] = 8
        analysis_config["jdFetchMaxJobsPerRun"] = 10
        analysis_config["postVerifyMaxJobsPerRun"] = 5
        fetch_config["timeoutMs"] = 12000
        adaptive_search_config.update(
            {
                "enabled": True,
                "targetNewJobs": 3,
                "minNewJobsToContinue": 3,
                "baseBudgetSeconds": 300,
                "baseRoundSeconds": 300,
                "extendBudgetSeconds": 480,
                "extendRoundSeconds": 480,
                "deepBudgetSeconds": 720,
                "deepRoundSeconds": 720,
                "baseExistingCompanies": 8,
                "baseExistingCompanyBatchSize": 8,
                "extendCompanyBatchSize": 4,
                "extendExistingCompanyBatchSize": 4,
                "maxExistingCompaniesPerRun": 12,
                "coldDiscoveryQueryBudget": 6,
                "coldStartQueryBudget": 6,
                "warmDiscoveryQueryBudget": 4,
                "deepSearchQueryBudget": 4,
                "coldDiscoveryCompanyBudget": 6,
                "coldStartMaxNewCompanies": 6,
                "warmDiscoveryCompanyBudget": 4,
                "deepSearchMaxNewCompanies": 4,
                "processDiscoveredCompaniesPerRound": 4,
                "coldStartImmediateProcessBatchSize": 4,
                "deepSearchImmediateProcessBatchSize": 4,
                "companyCooldownDaysNoJobs": 7,
                "companyCooldownDaysNoNewJobs": 3,
                "companyCooldownDaysSomeJobsNoNew": 3,
                "companyCooldownDaysWithNewJobs": 2,
                "companyCooldownDaysWithNew": 2,
            }
        )
        translation_config["enable"] = False

        output_config["trackerXlsxPath"] = "./jobs_recommended.xlsx"
        output_config["xlsxPath"] = "./jobs.xlsx"
        output_config["jsonPath"] = "./jobs.json"
        output_config["foundJsonPath"] = "./jobs_found.json"
        output_config["recommendedXlsxPath"] = "./jobs_recommended.xlsx"
        output_config["recommendedJsonPath"] = "./jobs_recommended.json"
        output_config["cnEuropeJsonPath"] = "./jobs_cn_europe.json"
        output_config["resumePendingPath"] = "./jobs_resume_pending.json"
        return config

    def _prepare_candidate_companies_path(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        run_dir: Path,
    ) -> Path:
        if candidate.candidate_id is None:
            raise ValueError("Candidate ID is required.")
        candidate_companies_path = run_dir / "companies.candidate.json"
        existing_payload = self._load_companies_payload(candidate_companies_path)
        if candidate_companies_path.exists():
            payload = existing_payload
        else:
            # Cold start: the desktop app should begin from an empty company pool.
            payload = {"companies": []}
        candidate_companies_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return candidate_companies_path.resolve()

    @staticmethod
    def _load_companies_payload(path: Path) -> dict:
        if not path.exists():
            return {"companies": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"companies": []}
        if not isinstance(payload, dict):
            return {"companies": []}
        companies = payload.get("companies")
        if not isinstance(companies, list):
            payload["companies"] = []
        return payload

    @staticmethod
    def _merge_companies_payloads(primary: dict, secondary: dict) -> dict:
        merged = dict(secondary or {})
        merged_companies: list[dict] = []
        key_to_index: dict[str, int] = {}

        def normalize_company(raw: dict) -> dict:
            if not isinstance(raw, dict):
                return {}
            item = {str(k): v for k, v in raw.items()}
            item["name"] = str(item.get("name") or "").strip()
            item["website"] = str(item.get("website") or "").strip()
            item["careersUrl"] = str(item.get("careersUrl") or "").strip()
            tags = item.get("tags")
            if isinstance(tags, list):
                item["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
            else:
                item["tags"] = []
            item["source"] = str(item.get("source") or "").strip()
            if isinstance(item.get("signalCount"), int):
                item["signalCount"] = int(item.get("signalCount") or 0)
            else:
                item.pop("signalCount", None)
            item["lastSeen"] = str(item.get("lastSeen") or "").strip()
            return item

        def company_keys(item: dict) -> list[str]:
            keys: list[str] = []
            name = str(item.get("name") or "").strip().casefold()
            if name:
                keys.append(f"name:{name}")
            for url in (str(item.get("website") or "").strip(), str(item.get("careersUrl") or "").strip()):
                domain = LegacyJobflowRunner._domain_from_url(url)
                if domain:
                    keys.append(f"domain:{domain}")
            return keys

        def choose_text(primary_text: str, fallback_text: str) -> str:
            primary_clean = str(primary_text or "").strip()
            if primary_clean:
                return primary_clean
            return str(fallback_text or "").strip()

        def merge_tags(existing_tags: list, incoming_tags: list) -> list[str]:
            seen: set[str] = set()
            merged_list: list[str] = []
            for tag in list(existing_tags) + list(incoming_tags):
                text = str(tag or "").strip()
                if not text:
                    continue
                key = text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                merged_list.append(text)
            return merged_list

        def parse_signal_count(value: object) -> int:
            if isinstance(value, int):
                return max(0, value)
            try:
                parsed = int(str(value or "").strip())
            except Exception:
                return 0
            return max(0, parsed)

        def pick_latest_timestamp(existing_ts: str, incoming_ts: str) -> str:
            candidates = [str(existing_ts or "").strip(), str(incoming_ts or "").strip()]
            valid = [value for value in candidates if value]
            if not valid:
                return ""
            return sorted(valid)[-1]

        def merge_company(existing: dict, incoming: dict) -> dict:
            out = dict(existing)
            for key, value in incoming.items():
                current = out.get(key)
                if current in (None, "", [], {}):
                    out[key] = value

            out["name"] = choose_text(existing.get("name", ""), incoming.get("name", ""))
            out["website"] = choose_text(existing.get("website", ""), incoming.get("website", ""))
            out["careersUrl"] = choose_text(existing.get("careersUrl", ""), incoming.get("careersUrl", ""))
            out["source"] = choose_text(existing.get("source", ""), incoming.get("source", ""))

            existing_tags = existing.get("tags") if isinstance(existing.get("tags"), list) else []
            incoming_tags = incoming.get("tags") if isinstance(incoming.get("tags"), list) else []
            out["tags"] = merge_tags(existing_tags, incoming_tags)

            signal_count = parse_signal_count(existing.get("signalCount")) + parse_signal_count(
                incoming.get("signalCount")
            )
            if signal_count > 0:
                out["signalCount"] = signal_count
            else:
                out.pop("signalCount", None)

            out["lastSeen"] = pick_latest_timestamp(
                str(existing.get("lastSeen") or ""),
                str(incoming.get("lastSeen") or ""),
            )

            if signal_count >= 3:
                tags = list(out.get("tags") or [])
                if "stage:core" not in [str(tag).strip().casefold() for tag in tags]:
                    tags.append("stage:core")
                out["tags"] = merge_tags(tags, [])
            return out

        def add_company(raw: dict) -> None:
            item = normalize_company(raw)
            if not item.get("name") and not item.get("website") and not item.get("careersUrl"):
                return
            keys = company_keys(item)
            index = None
            for key in keys:
                if key in key_to_index:
                    index = key_to_index[key]
                    break
            if index is None:
                merged_companies.append(item)
                index = len(merged_companies) - 1
            else:
                merged_companies[index] = merge_company(merged_companies[index], item)
            for key in company_keys(merged_companies[index]):
                key_to_index[key] = index

        for raw in (primary or {}).get("companies", []) or []:
            add_company(raw)
        for raw in (secondary or {}).get("companies", []) or []:
            add_company(raw)

        merged["companies"] = merged_companies
        return merged

    @staticmethod
    def _domain_from_url(url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return ""
        candidate = text if "://" in text else f"https://{text}"
        try:
            parsed = urlparse(candidate)
        except Exception:
            return ""
        return str(parsed.netloc or "").replace("www.", "").strip().casefold()

    def _resolve_resume_path(self, candidate: CandidateRecord, run_dir: Path) -> str:
        raw_path = candidate.active_resume_path.strip()
        if raw_path:
            path = Path(raw_path)
            if path.exists() and path.is_file():
                return str(path.resolve())

        generated_resume = run_dir / "resume.generated.md"
        lines = [
            f"# Candidate: {candidate.name}",
            "",
            f"- Base Location: {candidate.base_location or 'N/A'}",
            "- Preferred Locations:",
            candidate.preferred_locations.strip() or "N/A",
            "",
            "- Target Directions:",
            candidate.target_directions.strip() or "N/A",
            "",
            "- Notes:",
            candidate.notes.strip() or "N/A",
            "",
        ]
        generated_resume.write_text("\n".join(lines), encoding="utf-8")
        return str(generated_resume.resolve())

    def _resolve_scope_profile(self, profiles: list[SearchProfileRecord]) -> str:
        active_profiles = [profile for profile in profiles if profile.is_active]
        source = active_profiles if active_profiles else profiles
        for profile in source:
            if profile.scope_profile == "adjacent_mbse":
                return "adjacent_mbse"
        for profile in source:
            if profile.scope_profile.strip():
                return profile.scope_profile.strip()
        return "hydrogen_mainline"

    def _resolve_target_role(self, candidate: CandidateRecord, profiles: list[SearchProfileRecord]) -> str:
        active_profiles = [profile for profile in profiles if profile.is_active]
        source = active_profiles if active_profiles else profiles
        chunks: list[str] = []
        seen: set[str] = set()
        for profile in source:
            for raw in role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name):
                text = str(raw or "").strip()
                if not text:
                    continue
                key = text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                chunks.append(text)
            target_role = str(profile.target_role or "").strip()
            if target_role:
                key = target_role.casefold()
                if key not in seen:
                    seen.add(key)
                    chunks.append(target_role)
        if chunks:
            return " ; ".join(chunks[:8])
        if candidate.target_directions.strip():
            return candidate.target_directions.strip()
        return "Systems Engineer"

    def _build_queries(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        run_dir: Path | None = None,
    ) -> list[str]:
        active_profiles = [profile for profile in profiles if profile.is_active]
        source = active_profiles if active_profiles else profiles
        raw_queries: list[str] = []

        for profile in source:
            raw_queries.extend(profile.queries)
            raw_queries.extend(role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name))
            raw_queries.append(profile.target_role)
            raw_queries.append(profile.name)
            raw_queries.extend(description_query_lines(profile.keyword_focus))
            raw_queries.extend(self._split_multivalue_text(profile.company_focus))
            raw_queries.extend(self._split_multivalue_text(profile.company_keyword_focus))

        raw_queries.extend(candidate.target_directions.splitlines())
        feedback = self._load_run_feedback(run_dir)
        for item in feedback.get("companies", [])[:10]:
            raw_queries.append(f"{item} careers")
        for item in feedback.get("keywords", [])[:12]:
            raw_queries.append(item)

        normalized: list[str] = []
        seen: set[str] = set()
        for query in raw_queries:
            text = self._normalize_query(query)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)

        if not normalized:
            normalized = ["systems engineer job", "verification engineer job"]
        return normalized[:80]

    def _build_company_discovery_queries(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        search_queries: list[str],
        run_dir: Path | None = None,
        companies_path: Path | None = None,
    ) -> list[str]:
        active_profiles = [profile for profile in profiles if profile.is_active]
        source = active_profiles if active_profiles else profiles

        role_terms: list[str] = []
        for profile in source:
            role_terms.extend(role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name))
            role_terms.append(profile.name)
            role_terms.append(profile.target_role)
            role_terms.extend(description_query_lines(profile.keyword_focus))
            role_terms.extend(self._split_multivalue_text(profile.company_focus))
            role_terms.extend(self._split_multivalue_text(profile.company_keyword_focus))
        role_terms.extend(candidate.target_directions.splitlines())

        normalized_roles: list[str] = []
        seen_roles: set[str] = set()
        for raw in role_terms:
            text = str(raw or "").strip()
            if not text:
                continue
            if len(text) > 120:
                continue
            if len(text) > 80 and re.search(r"[,.，。;；:：]", text):
                continue
            key = text.casefold()
            if key in seen_roles:
                continue
            seen_roles.add(key)
            normalized_roles.append(text)

        feedback = self._load_run_feedback(run_dir)
        feedback_companies = list(feedback.get("companies", []))
        feedback_keywords = list(feedback.get("keywords", []))
        if companies_path is not None:
            payload = self._load_companies_payload(companies_path)
            for company in payload.get("companies", []) or []:
                if not isinstance(company, dict):
                    continue
                name = str(company.get("name") or "").strip()
                if name:
                    feedback_companies.append(name)
        feedback_companies = self._dedup_text(feedback_companies, limit=120)
        feedback_keywords = self._dedup_text(feedback_keywords, limit=80)

        seed_queries: list[str] = []
        for raw in search_queries:
            text = str(raw or "").strip()
            if not text:
                continue
            base = re.sub(r"\bjobs?\b", "", text, flags=re.IGNORECASE)
            base = re.sub(r"\bcareer(s)?\b", "", base, flags=re.IGNORECASE).strip()
            if not base:
                continue
            if len(base) > 120:
                continue
            if len(base) > 80 and re.search(r"[,.，。;；:：]", base):
                continue
            seed_queries.append(f"{base} companies careers")

        generated: list[str] = []
        for role in normalized_roles[:6]:
            generated.append(f"{role} companies careers")
            generated.append(f"{role} employers")
        for company in feedback_companies[:8]:
            generated.append(f"{company} careers")
        for keyword in feedback_keywords[:6]:
            generated.append(f"{keyword} companies careers")
        generated.extend(seed_queries[:6])
        if not generated:
            generated = ["systems engineer companies careers", "verification engineer companies careers"]

        dedup: list[str] = []
        seen: set[str] = set()
        for raw in generated:
            text = str(raw or "").strip()
            if not text:
                continue
            if len(text) > 240:
                text = text[:240].strip()
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(text)
        return dedup[:36]

    def _resolve_pipeline_mode(self) -> str:
        raw = str(os.getenv("JOBFLOW_PIPELINE_MODE", "") or "").strip().lower()
        key = raw.replace("-", "_").replace(" ", "_")
        if key in {
            "",
            "default",
            "company_plus_web_signal",
            "company_plus_signal",
            "company_and_web_signal",
            "hybrid",
        }:
            return self.PIPELINE_MODE_COMPANY_PLUS_WEB_SIGNAL
        if key in {"company_only", "company_first"}:
            return self.PIPELINE_MODE_COMPANY_ONLY
        return self.PIPELINE_MODE_COMPANY_PLUS_WEB_SIGNAL

    @staticmethod
    def _signal_max_queries(max_queries: int) -> int:
        return max(4, min(24, int(max_queries)))

    def _run_legacy_stage(
        self,
        command: list[str],
        env: dict[str, str],
        timeout_seconds: int,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> LegacyStageRunResult:
        return self._run_process_stage(
            command=command,
            cwd=self.legacy_root,
            env=env,
            timeout_seconds=timeout_seconds,
            success_message="Legacy stage completed.",
            failure_message="Legacy stage failed.",
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

    def _run_process_stage(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int,
        success_message: str,
        failure_message: str,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> LegacyStageRunResult:
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def reader(pipe: object, sink: list[str], prefix: str = "") -> None:
            stream = pipe
            if stream is None:
                return
            try:
                while True:
                    line = stream.readline()
                    if line == "":
                        break
                    sink.append(line)
                    text = str(line or "").strip()
                    if text and progress_callback is not None:
                        try:
                            progress_callback(f"{prefix}{text}")
                        except Exception:
                            pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                bufsize=1,
            )
        except Exception as exc:
            return LegacyStageRunResult(
                success=False,
                exit_code=-1,
                message=f"Failed to run legacy stage: {exc}",
                stdout_tail="",
                stderr_tail="",
            )

        stdout_thread = threading.Thread(target=reader, args=(process.stdout, stdout_lines, ""), daemon=True)
        stderr_thread = threading.Thread(
            target=reader,
            args=(process.stderr, stderr_lines, "[stderr] "),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        deadline = time.monotonic() + max(1, int(timeout_seconds))
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._terminate_process_tree(process)
                    stdout_thread.join(timeout=2)
                    stderr_thread.join(timeout=2)
                    return LegacyStageRunResult(
                        success=False,
                        exit_code=-2,
                        message="Legacy stage cancelled.",
                        stdout_tail=self._tail("".join(stdout_lines)),
                        stderr_tail=self._tail("".join(stderr_lines)),
                        cancelled=True,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
                try:
                    return_code = process.wait(timeout=min(0.5, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            return LegacyStageRunResult(
                success=False,
                exit_code=-1,
                message=f"Legacy stage timed out after {timeout_seconds} seconds.",
                stdout_tail=self._tail("".join(stdout_lines)),
                stderr_tail=self._tail("".join(stderr_lines)),
            )

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        success = return_code == 0
        return LegacyStageRunResult(
            success=success,
            exit_code=return_code,
            message=success_message if success else failure_message,
            stdout_tail=self._tail("".join(stdout_lines)),
            stderr_tail=self._tail("".join(stderr_lines)),
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
            return
        except Exception:
            pass
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                process.wait(timeout=2)
                return
            except Exception:
                pass
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            pass

    def _ingest_web_signal_companies(self, run_dir: Path, companies_path: Path) -> int:
        signal_jobs = self._load_jobs_from_file(run_dir / "jobs_found.signal.json")
        if not signal_jobs:
            signal_jobs = self._load_jobs_from_file(run_dir / "jobs.signal.json")
        if not signal_jobs:
            return 0

        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        candidates: list[dict] = []
        for job in signal_jobs:
            source = str(job.get("source") or "").strip().casefold()
            if not source.startswith("web_search"):
                continue
            company_name = self._clean_company_name(job.get("company"))
            if not company_name:
                continue

            job_url = str(job.get("canonicalUrl") or job.get("url") or "").strip()
            domain = self._domain_from_url(job_url)
            website = ""
            careers_url = ""
            if domain and not self._is_aggregator_domain(domain):
                website = f"https://{domain}"
                careers_url = job_url

            tags = ["pool:signal", "stage:signal", "signal:web_search"]
            region_tag = str(job.get("regionTag") or "").strip().upper()
            if region_tag:
                tags.append(f"region:{region_tag}")

            candidates.append(
                {
                    "name": company_name,
                    "website": website,
                    "careersUrl": careers_url,
                    "tags": tags,
                    "source": "web_signal",
                    "signalCount": 1,
                    "lastSeen": now_iso,
                }
            )

        if not candidates:
            return 0

        existing_payload = self._load_companies_payload(companies_path)
        before = self._company_identity_keys(existing_payload)
        merged_payload = self._merge_companies_payloads({"companies": candidates}, existing_payload)
        after = self._company_identity_keys(merged_payload)
        companies_path.write_text(
            json.dumps(merged_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return max(0, len(after) - len(before))

    @staticmethod
    def _company_identity_keys(payload: dict) -> set[str]:
        keys: set[str] = set()
        for raw in (payload or {}).get("companies", []) or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip().casefold()
            if name:
                keys.add(f"name:{name}")
            for raw_url in (
                str(raw.get("website") or "").strip(),
                str(raw.get("careersUrl") or "").strip(),
            ):
                domain = LegacyJobflowRunner._domain_from_url(raw_url)
                if domain:
                    keys.add(f"domain:{domain}")
        return keys

    @staticmethod
    def _split_multivalue_text(raw: str) -> list[str]:
        text = str(raw or "")
        if not text.strip():
            return []
        tokens = re.split(r"[\n,;|]+", text)
        values: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            item = re.sub(r"\s+", " ", str(token or "").strip())
            if not item:
                continue
            if len(item) > 140:
                item = item[:140].strip()
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(item)
        return values

    def _load_run_feedback(self, run_dir: Path | None) -> dict[str, list[str]]:
        if run_dir is None:
            return {"companies": [], "keywords": []}

        paths = [
            run_dir / "jobs_recommended.json",
            run_dir / "jobs.json",
            run_dir / "jobs_found.signal.json",
        ]
        company_candidates: list[str] = []
        keyword_candidates: list[str] = []
        for path in paths:
            jobs = self._load_jobs_from_file(path)
            for job in jobs:
                analysis = job.get("analysis", {})
                if not isinstance(analysis, dict):
                    analysis = {}
                score = analysis.get("matchScore")
                recommend = bool(analysis.get("recommend"))
                accepted = path.name in {"jobs_recommended.json", "jobs_found.signal.json"} or recommend or (
                    isinstance(score, int) and score >= 70
                )
                if not accepted:
                    continue

                company = self._clean_company_name(job.get("company"))
                if company:
                    company_candidates.append(company)
                title = re.sub(r"\s+", " ", str(job.get("title") or "").strip())
                if title:
                    keyword_candidates.extend(self._extract_feedback_keywords(title))

        companies = self._dedup_text(company_candidates, limit=40)
        keywords = self._dedup_text(keyword_candidates, limit=40)
        return {"companies": companies, "keywords": keywords}

    @staticmethod
    def _extract_feedback_keywords(title: str) -> list[str]:
        text = re.sub(r"\s+", " ", str(title or "").strip())
        if not text:
            return []
        candidates: list[str] = []
        if len(text) <= 90:
            candidates.append(text)

        stop = {
            "senior",
            "junior",
            "lead",
            "principal",
            "staff",
            "manager",
            "engineer",
            "specialist",
            "intern",
        }
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+/-]{2,}", text)
        for token in tokens:
            lower = token.casefold()
            if lower in stop:
                continue
            if len(lower) < 4:
                continue
            candidates.append(token)
        return candidates[:8]

    @staticmethod
    def _dedup_text(values: list[str], limit: int = 40) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in values:
            text = re.sub(r"\s+", " ", str(raw or "").strip())
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _clean_company_name(raw: object) -> str:
        text = re.sub(r"\s+", " ", str(raw or "").strip())
        if not text:
            return ""
        text = re.sub(r"\([^)]*listed via[^)]*\)", "", text, flags=re.IGNORECASE).strip(" -|,;")
        if not text:
            return ""
        lowered = text.casefold()
        if lowered in {"n/a", "unknown", "company", "confidential"}:
            return ""
        if len(text) > 120:
            text = text[:120].strip()
        return text

    @staticmethod
    def _is_aggregator_domain(domain: str) -> bool:
        host = str(domain or "").strip().casefold()
        if not host:
            return True
        blocked_markers = (
            "linkedin.",
            "indeed.",
            "glassdoor.",
            "monster.",
            "ziprecruiter.",
            "smartrecruiters.",
            "greenhouse.",
            "lever.co",
            "workday.",
            "jobboard",
            "wellfound.",
            "careerbuilder.",
            "job-boards.",
            "boards.",
        )
        return any(marker in host for marker in blocked_markers)

    @staticmethod
    def _normalize_query(raw: str) -> str:
        text = re.sub(r"\s+", " ", str(raw or "").strip())
        if not text:
            return ""
        punctuation_hits = sum(text.count(mark) for mark in ("。", ".", "!", "?", ";", "；"))
        if len(text) > 120 and punctuation_hits >= 2:
            return ""
        if len(text) > 200:
            text = text[:200].strip()
        lower = text.lower()
        if len(text.split()) > 18 and not any(
            token in lower for token in ("job", "jobs", "career", "careers", "recruit", "hiring")
        ):
            return ""
        if any(token in lower for token in (" job", " jobs", "career", "careers", "招聘", "岗位", "职位")):
            return text
        return f"{text} job"

    @staticmethod
    def _ensure_dict(container: dict, key: str) -> dict:
        value = container.get(key)
        if isinstance(value, dict):
            return value
        container[key] = {}
        return container[key]

    @staticmethod
    def _resolve_model_override(settings: OpenAISettings | None) -> str:
        env_model = os.getenv("JOBFLOW_OPENAI_MODEL", "").strip()
        if env_model:
            return env_model
        azure_model = (
            os.getenv("AZURE_OPENAI_MODEL", "").strip()
            or os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
        )
        if azure_model:
            return azure_model
        if settings is None:
            return ""
        return settings.model.strip()

    def _candidate_run_dir(self, candidate_id: int) -> Path:
        return self.runtime_root / f"candidate_{candidate_id}"

    @staticmethod
    def _search_progress_path(run_dir: Path) -> Path:
        return run_dir / "search.progress.json"

    @classmethod
    def _load_search_progress_from_run_dir(cls, run_dir: Path) -> LegacySearchProgress:
        path = cls._search_progress_path(run_dir)
        if not path.exists():
            return LegacySearchProgress()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return LegacySearchProgress()
        if not isinstance(payload, dict):
            return LegacySearchProgress()
        status = str(payload.get("status") or "idle").strip() or "idle"
        started_at = str(payload.get("startedAt") or "").strip()
        elapsed_seconds = max(0, int(payload.get("elapsedSeconds") or 0))
        if status == "running" and started_at:
            try:
                started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                elapsed_seconds = max(0, int((now_dt - started_dt).total_seconds()))
            except Exception:
                elapsed_seconds = max(0, int(payload.get("elapsedSeconds") or 0))
        return LegacySearchProgress(
            status=status,
            stage=str(payload.get("stage") or "idle").strip() or "idle",
            message=str(payload.get("message") or "").strip(),
            last_event=str(payload.get("lastEvent") or "").strip(),
            started_at=started_at,
            updated_at=str(payload.get("updatedAt") or "").strip(),
            elapsed_seconds=elapsed_seconds,
        )

    @classmethod
    def _write_search_progress(
        cls,
        run_dir: Path,
        *,
        status: str,
        stage: str,
        message: str = "",
        last_event: str = "",
        started_at: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        started = str(started_at or "").strip() or now
        elapsed_seconds = 0
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
            elapsed_seconds = max(0, int((now_dt - started_dt).total_seconds()))
        except Exception:
            elapsed_seconds = 0
        payload = {
            "status": str(status or "idle").strip() or "idle",
            "stage": str(stage or "idle").strip() or "idle",
            "message": str(message or "").strip(),
            "lastEvent": str(last_event or "").strip(),
            "startedAt": started,
            "updatedAt": now,
            "elapsedSeconds": elapsed_seconds,
        }
        cls._search_progress_path(run_dir).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _error_result(
        self,
        candidate_id: int,
        message: str,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> LegacyRunResult:
        run_dir = self._candidate_run_dir(candidate_id)
        config_path = run_dir / "config.generated.json"
        recommended_json_path = run_dir / "jobs_recommended.json"
        return LegacyRunResult(
            success=False,
            exit_code=-1,
            message=message,
            stdout_tail=self._tail(stdout_tail),
            stderr_tail=self._tail(stderr_tail),
            run_dir=run_dir,
            config_path=config_path,
            recommended_json_path=recommended_json_path,
        )

    @staticmethod
    def _tail(text: str, max_lines: int = 40, max_chars: int = 4000) -> str:
        lines = str(text or "").splitlines()
        clipped = "\n".join(lines[-max_lines:])
        if len(clipped) <= max_chars:
            return clipped
        return clipped[-max_chars:]

