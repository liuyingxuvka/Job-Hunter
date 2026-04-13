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
from .role_recommendations import (
    CandidateSemanticProfile,
    OpenAIRoleRecommendationService,
    RoleRecommendationError,
    description_query_lines,
    load_resume_excerpt_result,
    role_name_query_lines,
)


BUSINESS_QUERY_WEIGHTS = {
    "core": 0.60,
    "adjacent": 0.25,
    "explore": 0.15,
}

PHRASE_LIBRARY_LIMITS = {
    "core": 45,
    "adjacent": 30,
    "explore": 15,
}

ROUND_PHRASE_SAMPLE_COUNTS = {
    "core": 5,
    "adjacent": 2,
    "explore": 1,
}

SEARCH_TRACK_KEYS = (
    "hydrogen_core",
    "energy_digitalization",
    "battery_ess_powertrain",
    "test_validation_reliability",
)

MAINLINE_SEARCH_TRACK_PATTERNS = {
    "hydrogen_core": re.compile(
        r"\b(fuel cell|fuel-cell|electrolyzer|electrolysis|hydrogen|h2|electrochemical|pem|lt-?pem|ht-?pem|mea|membrane electrode|catalyst|anode|cathode)\b|燃料电池|电解槽|氢能|电化学|膜电极|催化剂",
        flags=re.IGNORECASE,
    ),
    "energy_digitalization": re.compile(
        r"\b(digital twin|digital-twin|phm|prognostics|health management|condition monitoring|state monitoring|asset health|predictive maintenance|remaining useful life|rul|model[- ]based|mbse|systems engineering)\b|数字孪生|状态监测|健康管理|寿命预测|模型驱动|系统工程",
        flags=re.IGNORECASE,
    ),
    "battery_ess_powertrain": re.compile(
        r"\b(battery|bms|state of health|soh|state of charge|soc|energy storage|ess|pack|cell|module|powertrain|e-?mobility|ev|thermal runaway|inverter|motor control)\b|电池|储能|电驱|热失控|BMS",
        flags=re.IGNORECASE,
    ),
    "test_validation_reliability": re.compile(
        r"\b(test data|test bench|validation|verification|v&v|ast|accelerated stress test|durability|reliability|lifetime|parameter identification|parameter estimation|system identification|calibration|doe)\b|测试数据|试验台架|验证|方法学|加速应力测试|可靠性|寿命|参数辨识|参数识别|标定",
        flags=re.IGNORECASE,
    ),
}

ADJACENT_SCOPE_TRACK_PATTERNS = {
    "hydrogen_core": re.compile(
        r"\b(mbse|systems engineering|system engineer|sysml|requirements|traceability|technical interface|owner engineer)\b|系统工程|需求|可追溯|技术接口|业主工程",
        flags=re.IGNORECASE,
    ),
    "energy_digitalization": re.compile(
        r"\b(digital twin|phm|condition monitoring|asset health|predictive maintenance|rul)\b|数字孪生|状态监测|健康管理|寿命预测",
        flags=re.IGNORECASE,
    ),
    "battery_ess_powertrain": re.compile(
        r"\b(automotive|vehicle|powertrain|battery|bms|drivetrain|ev)\b|汽车|动力总成|电池|电驱",
        flags=re.IGNORECASE,
    ),
    "test_validation_reliability": re.compile(
        r"\b(verification|validation|v&v|integration|qualification|reliability|durability|test engineer|failure analysis)\b|验证|集成|鉴定|可靠性|耐久|测试|故障分析",
        flags=re.IGNORECASE,
    ),
}

SCOPE_TRACK_MIX_FALLBACKS = {
    "hydrogen_mainline": {
        "hydrogen_core": 0.60,
        "energy_digitalization": 0.10,
        "battery_ess_powertrain": 0.15,
        "test_validation_reliability": 0.15,
    },
    "adjacent_mbse": {
        "hydrogen_core": 0.45,
        "energy_digitalization": 0.20,
        "battery_ess_powertrain": 0.15,
        "test_validation_reliability": 0.20,
    },
}

MAINLINE_BUSINESS_ANCHORS = {
    "core": [
        ("hydrogen systems", r"\bhydrogen\b|\bh2\b|氢能|氢系统"),
        ("fuel cells", r"\bfuel cell\b|fuel-cell|燃料电池"),
        ("electrolyzers", r"\belectroly[sz]er\b|electrolysis|电解槽|制氢"),
        ("electrochemical diagnostics", r"electrochem|electrochemical|电化学|diagnostic|diagnostics|诊断"),
        ("degradation and aging", r"degradation|aging|老化|降解"),
        ("durability and reliability", r"durability|reliability|耐久|可靠性"),
        ("lifetime prediction", r"lifetime|remaining useful life|寿命预测|rul"),
        ("stack and balance-of-plant", r"\bstack\b|balance of plant|bop|堆|系统"),
        ("MEA and membrane materials", r"\bmea\b|membrane electrode|membrane|膜电极|膜|催化剂|catalyst"),
    ],
    "adjacent": [
        ("system validation and testing", r"validation|verification|test bench|testing|试验|验证|测试"),
        ("energy digitalization and PHM", r"digital twin|phm|condition monitoring|asset health|predictive maintenance|数字孪生|状态监测|健康管理"),
        ("industrial controls and automation", r"controls?|control systems?|automation|自动化|控制"),
        ("systems engineering and MBSE", r"mbse|systems engineering|sysml|requirements|traceability|系统工程|需求|可追溯"),
        ("technical diagnostics and monitoring", r"monitoring|diagnostics?|health management|监测|诊断|健康管理"),
    ],
    "explore": [
        ("battery aging and diagnostics", r"\bbattery\b|\bbms\b|储能|电池|soh|soc"),
        ("complex equipment reliability", r"complex equipment|industrial equipment|equipment reliability|高端装备|装备"),
        ("model-based diagnostics", r"model-based|simulation|parameter identification|calibration|建模|参数辨识|标定"),
        ("energy infrastructure platforms", r"energy infrastructure|industrial gas|grid|能源基础设施|工业气体"),
    ],
}

ADJACENT_SCOPE_BUSINESS_ANCHORS = {
    "core": [
        ("systems engineering", r"systems engineering|system engineer|系统工程"),
        ("MBSE and digital thread", r"mbse|sysml|digital thread|模型驱动|数字线程"),
        ("requirements and traceability", r"requirements|traceability|需求|可追溯"),
        ("verification and validation", r"verification|validation|v&v|验证|确认"),
        ("reliability and durability", r"reliability|durability|可靠性|耐久"),
        ("integration and qualification", r"integration|qualification|集成|鉴定"),
    ],
    "adjacent": [
        ("digital twin and PHM", r"digital twin|phm|condition monitoring|状态监测|健康管理"),
        ("industrial automation and controls", r"industrial automation|automation|controls?|工业自动化|控制"),
        ("complex equipment platforms", r"complex equipment|industrial equipment|装备|平台"),
        ("technical diagnostics", r"diagnostic|diagnostics|故障分析|诊断"),
    ],
    "explore": [
        ("automotive and powertrain systems", r"automotive|vehicle|powertrain|battery|bms|汽车|动力总成|电池"),
        ("energy infrastructure systems", r"energy infrastructure|grid|utility|能源基础设施|电网"),
        ("aerospace and high-end manufacturing", r"aerospace|manufacturing|航空航天|高端制造"),
    ],
}

DEFAULT_BUSINESS_ANCHORS = {
    "hydrogen_mainline": {
        "core": [
            "hydrogen systems",
            "fuel cells",
            "electrolyzers",
            "electrochemical diagnostics",
            "durability and reliability",
        ],
        "adjacent": [
            "system validation and testing",
            "energy digitalization and PHM",
            "industrial controls and automation",
        ],
        "explore": [
            "battery aging and diagnostics",
            "model-based diagnostics",
            "energy infrastructure platforms",
        ],
    },
    "adjacent_mbse": {
        "core": [
            "systems engineering",
            "MBSE and digital thread",
            "requirements and traceability",
            "verification and validation",
        ],
        "adjacent": [
            "digital twin and PHM",
            "industrial automation and controls",
            "technical diagnostics",
        ],
        "explore": [
            "automotive and powertrain systems",
            "energy infrastructure systems",
            "aerospace and high-end manufacturing",
        ],
    },
}

BUSINESS_COMPANY_QUERY_TEMPLATES = {
    "core": [
        "{anchor} companies",
        "{anchor} industrial technology companies",
    ],
    "adjacent": [
        "{anchor} companies",
        "{anchor} industrial technology companies",
    ],
    "explore": [
        "{anchor} companies",
        "{anchor} industrial technology companies",
    ],
}


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


@dataclass(frozen=True)
class DiscoveryAnchorPlan:
    core: list[str]
    adjacent: list[str]
    explore: list[str]


class LegacyJobflowRunner:
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
        max_companies: int = 20,
        timeout_seconds: int = 900,
        cancel_event: threading.Event | None = None,
    ) -> LegacyRunResult:
        if candidate.candidate_id is None:
            raise ValueError("Candidate ID is required.")
        run_dir = self._candidate_run_dir(candidate.candidate_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = run_dir / "config.generated.json"
        resume_config_path = run_dir / "config.generated.resume.json"
        recommended_json_path = run_dir / "jobs_recommended.json"
        progress_started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        query_rotation_seed = time.time_ns()
        current_stage = "preparing"
        progress_lock = threading.Lock()
        resume_pending_count = 0
        previous_run_requires_resume = self._previous_run_requires_resume(run_dir)

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
            self._refresh_resume_pending_jobs(run_dir)
            write_progress(status="error", message=message, last_event=detail)
            return self._error_result(
                candidate.candidate_id,
                message,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )

        def cancelled_result(message: str, stdout_tail: str = "", stderr_tail: str = "") -> LegacyRunResult:
            detail = self._tail(stderr_tail or stdout_tail or message, max_lines=8, max_chars=1200)
            self._refresh_resume_pending_jobs(run_dir)
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
        source_companies_path: Path | None = None
        semantic_profile: CandidateSemanticProfile | None = None
        effective_max_companies = max(1, int(max_companies))
        model_override = self._resolve_model_override(settings)
        stage_stdout: list[tuple[str, str]] = []
        stage_stderr: list[tuple[str, str]] = []

        try:
            base_config = self._load_base_config()
            source_companies_path = self._prepare_candidate_companies_path(
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
            )
            semantic_profile = self._load_candidate_semantic_profile_for_run(
                candidate=candidate,
                settings=settings,
                api_base_url=api_base_url,
                run_dir=run_dir,
            )
            if semantic_profile is not None and semantic_profile.is_usable():
                write_progress(
                    status="running",
                    stage=current_stage,
                    message="Preparing search runtime with AI semantic profile.",
                    last_event="AI-derived business profile loaded for company discovery and query planning.",
                )
            runtime_config = self._build_runtime_config(
                base_config=base_config,
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
                query_rotation_seed=query_rotation_seed,
                semantic_profile=semantic_profile,
                model_override=model_override,
                source_companies_path=source_companies_path,
                pipeline_stage="main",
            )
            config_path.write_text(
                json.dumps(runtime_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            effective_max_companies = max(
                1,
                min(
                    effective_max_companies,
                    int(
                        self._ensure_dict(runtime_config, "sources").get(
                            "maxCompaniesPerRun",
                            effective_max_companies,
                        )
                    ),
                ),
            )
            adaptive_runtime_config = self._ensure_dict(runtime_config, "adaptiveSearch")
            session_empty_rounds_before_pause = max(
                1,
                int(
                    adaptive_runtime_config.get(
                        "sessionEmptyRoundsBeforePause",
                        3,
                    )
                ),
            )
            session_idle_pause_seconds = max(
                30,
                int(adaptive_runtime_config.get("sessionIdlePauseSeconds", 300)),
            )
            session_pass_timeout_seconds = max(
                240,
                int(
                    adaptive_runtime_config.get(
                        "sessionPassTimeoutSeconds",
                        600,
                    )
                ),
            )

            resume_config = self._build_runtime_config(
                base_config=base_config,
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
                query_rotation_seed=query_rotation_seed,
                semantic_profile=semantic_profile,
                model_override=model_override,
                source_companies_path=source_companies_path,
                pipeline_stage="resume_pending",
            )
            resume_config_path.write_text(
                json.dumps(resume_config, ensure_ascii=False, indent=2),
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
        search_session_deadline = time.monotonic() + max(60, int(timeout_seconds))

        def write_main_runtime_config(rotation_seed: int) -> dict:
            nonlocal effective_max_companies
            runtime_config = self._build_runtime_config(
                base_config=base_config,
                candidate=candidate,
                profiles=profiles,
                run_dir=run_dir,
                query_rotation_seed=rotation_seed,
                semantic_profile=semantic_profile,
                model_override=model_override,
                source_companies_path=source_companies_path,
                pipeline_stage="main",
            )
            config_path.write_text(
                json.dumps(runtime_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            effective_max_companies = max(
                1,
                min(
                    max(1, int(max_companies)),
                    int(
                        self._ensure_dict(runtime_config, "sources").get(
                            "maxCompaniesPerRun",
                            max(1, int(max_companies)),
                        )
                    ),
                ),
            )
            return runtime_config

        def remaining_search_session_seconds() -> int:
            return max(0, int(search_session_deadline - time.monotonic()))

        def wait_for_idle_retry(wait_seconds: int) -> None:
            nonlocal current_stage
            current_stage = "idle_wait"
            write_progress(
                status="running",
                message=(
                    "No new companies were discovered for consecutive rounds. "
                    "Pausing briefly before the next discovery attempt."
                ),
                last_event=(
                    f"Waiting {wait_seconds} second(s) before the next timed search round."
                ),
            )
            wait_deadline = time.monotonic() + max(1, int(wait_seconds))
            while time.monotonic() < wait_deadline:
                if cancel_event is not None and cancel_event.is_set():
                    break
                if time.monotonic() >= search_session_deadline:
                    break
                sleep_seconds = min(
                    1.0,
                    wait_deadline - time.monotonic(),
                    max(0.1, search_session_deadline - time.monotonic()),
                )
                time.sleep(max(0.1, sleep_seconds))

        if previous_run_requires_resume:
            try:
                resume_pending_count = self._write_resume_pending_jobs(
                    run_dir,
                    include_found_fallback=True,
                )
            except Exception as exc:
                stage_notes.append(f"Resume queue refresh skipped: {exc}")
            else:
                if resume_pending_count > 0:
                    stage_notes.append(
                        f"Resume queue: {resume_pending_count} unfinished jobs will be processed before discovery continues."
                    )
        else:
            self._clear_resume_pending_jobs(run_dir)

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
            resume_timeout_seconds = max(240, min(session_pass_timeout_seconds, 900))
            return self._run_legacy_stage(
                command=[
                    node_bin,
                    "jobflow.mjs",
                    "--config",
                    str(resume_config_path),
                    "--max-queries",
                    str(max(1, int(max_queries))),
                    "--max-companies",
                    str(effective_max_companies),
                ],
                env=env,
                timeout_seconds=resume_timeout_seconds,
                progress_callback=lambda line: write_progress(
                    status="running",
                    message=message,
                    last_event=line,
                ),
            )

        def run_main_stage(message: str, start_event: str, timeout_override_seconds: int) -> LegacyStageRunResult:
            nonlocal current_stage
            current_stage = "main"
            write_progress(
                status="running",
                message=message,
                last_event=start_event,
            )
            main_command = [
                node_bin,
                "jobflow.mjs",
                "--config",
                str(config_path),
                "--max-queries",
                str(max(1, int(max_queries))),
                "--max-companies",
                str(effective_max_companies),
            ]
            return self._run_legacy_stage(
                command=main_command,
                env=env,
                timeout_seconds=timeout_override_seconds,
                progress_callback=lambda line: write_progress(
                    status="running",
                    message=message,
                    last_event=line,
                ),
            )

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

        if cancel_event is not None and cancel_event.is_set():
            return cancelled_result("Search cancelled after the pending-job round completed.")

        main_pass_index = 0
        idle_pause_count = 0
        consecutive_no_new_company_rounds = 0
        completed_pending_finalize_count = 0
        incomplete_pending_finalize_count = 0
        pending_after_main = 0
        main_result: LegacyStageRunResult | None = None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return cancelled_result("Search cancelled before starting the next search round.")
            remaining_budget_seconds = remaining_search_session_seconds()
            if remaining_budget_seconds <= 0:
                stage_notes.append("Timed search session reached its configured duration.")
                break
            if main_pass_index > 0 and consecutive_no_new_company_rounds >= session_empty_rounds_before_pause:
                idle_pause_count += 1
                wait_for_idle_retry(session_idle_pause_seconds)
                consecutive_no_new_company_rounds = 0
                if cancel_event is not None and cancel_event.is_set():
                    return cancelled_result("Search cancelled during the idle wait.")
                if remaining_search_session_seconds() <= 0:
                    stage_notes.append("Timed search session ended during the idle wait.")
                    break

            pass_query_rotation_seed = query_rotation_seed + (main_pass_index * 104729)
            if main_pass_index > 0:
                try:
                    write_main_runtime_config(pass_query_rotation_seed)
                except Exception as exc:
                    stage_notes.append(
                        f"Timed search session stopped because refreshed company discovery queries could not be prepared: {exc}"
                    )
                    break

            search_stats_before_round = self.load_search_stats(candidate.candidate_id)
            company_pool_count_before_round = search_stats_before_round.candidate_company_pool_count
            current_round_number = main_pass_index + 1
            main_result = run_main_stage(
                (
                    f"Running timed search round {current_round_number}; this round will finish cleanly before the next stop check."
                    if current_round_number > 1
                    else "Running the first timed search round."
                ),
                (
                    f"Starting timed search round {current_round_number} with refreshed company discovery queries."
                    if current_round_number > 1
                    else "Starting timed search round 1."
                ),
                session_pass_timeout_seconds,
            )
            stage_label = "main" if main_pass_index <= 0 else f"main_round_{current_round_number}"

            if main_result.stdout_tail:
                stage_stdout.append((stage_label, main_result.stdout_tail))
            if main_result.stderr_tail:
                stage_stderr.append((stage_label, main_result.stderr_tail))

            pending_after_main = 0
            try:
                pending_after_main = self._write_resume_pending_jobs(run_dir)
            except Exception as exc:
                stage_notes.append(f"Resume queue finalize skipped: {exc}")
            else:
                if main_result.success and pending_after_main > 0:
                    last_pending_before_pass: int | None = None

                    while pending_after_main > 0:
                        if (
                            last_pending_before_pass is not None
                            and pending_after_main >= last_pending_before_pass
                        ):
                            stage_notes.append(
                                "Auto-resume stopped because the pending queue did not shrink further."
                            )
                            break

                        last_pending_before_pass = pending_after_main
                        resume_result = run_resume_stage(
                            "Finishing remaining discovered jobs before wrapping up.",
                            "Starting final pending-job pass.",
                        )
                        if resume_result is None:
                            break
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
                        if not resume_result.success:
                            stage_notes.append(
                                f"Final pending pass failed (exit {resume_result.exit_code}); remaining unfinished jobs stay queued."
                            )
                            break
                        try:
                            pending_after_main = self._write_resume_pending_jobs(run_dir)
                        except Exception as exc:
                            stage_notes.append(f"Resume queue final check skipped: {exc}")
                            break

                    if pending_after_main > 0:
                        incomplete_pending_finalize_count += 1
                    else:
                        completed_pending_finalize_count += 1
            search_stats_after_round = self.load_search_stats(candidate.candidate_id)
            company_pool_growth = max(
                0,
                search_stats_after_round.candidate_company_pool_count - company_pool_count_before_round,
            )
            if company_pool_growth > 0:
                consecutive_no_new_company_rounds = 0
            else:
                consecutive_no_new_company_rounds += 1

            main_pass_index += 1
            if not main_result.success or pending_after_main > 0:
                break

            if cancel_event is not None and cancel_event.is_set():
                return cancelled_result(
                    "Search stopped after the current round completed.",
                    stdout_tail=main_result.stdout_tail,
                    stderr_tail=main_result.stderr_tail,
                )

        success = (main_result is None or main_result.success) and pending_after_main <= 0
        if success:
            message = "Timed search session completed."
            self._clear_resume_pending_jobs(run_dir)
        elif main_result is not None and main_result.success and pending_after_main > 0:
            message = "Timed search session stopped before finishing all discovered jobs."
        else:
            message = (main_result.message if main_result is not None else "") or "Timed search session failed."
        if completed_pending_finalize_count > 0:
            stage_notes.append(
                f"Pending jobs were finalized successfully in {completed_pending_finalize_count} round(s)."
            )
        if incomplete_pending_finalize_count > 0:
            stage_notes.append(
                f"Pending jobs still remained after finalization in {incomplete_pending_finalize_count} round(s)."
            )
        if idle_pause_count > 0:
            stage_notes.append(
                f"The session paused {idle_pause_count} time(s) after {session_empty_rounds_before_pause} consecutive rounds without new companies."
            )
        if stage_notes:
            message = f"{message} {' '.join(stage_notes)}"

        combined_stdout = [f"[{label}]\n{text}" for label, text in stage_stdout if text]
        combined_stderr = [f"[{label}]\n{text}" for label, text in stage_stderr if text]
        write_progress(
            status="success" if success else "error",
            stage="completed" if success else "main",
            message=message,
            last_event=(
                (main_result.stderr_tail if main_result is not None else "")
                or (main_result.stdout_tail if main_result is not None else "")
                or message
            ),
        )
        return LegacyRunResult(
            success=success,
            exit_code=main_result.exit_code if main_result is not None else 0,
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
                recommended_in_all = [
                    item
                    for item in all_jobs
                    if bool((item.get("analysis") or {}).get("recommend"))
                ]
                recommended_ready = self._filter_review_ready_jobs(recommended_in_all)
                if recommended_ready:
                    jobs = recommended_ready

        return self._build_job_records(jobs)

    def load_live_jobs(self, candidate_id: int) -> list[LegacyJobResult]:
        run_dir = self._candidate_run_dir(candidate_id)
        jobs = self._merge_job_items_from_paths(
            [
                run_dir / "jobs_found.json",
                run_dir / "jobs.json",
                run_dir / "jobs_recommended.json",
            ]
        )
        return self._build_job_records(self._filter_review_ready_jobs(jobs))

    def load_search_stats(self, candidate_id: int) -> LegacySearchStats:
        run_dir = self._candidate_run_dir(candidate_id)
        candidate_companies_payload = self._load_companies_payload(run_dir / "companies.candidate.json")
        main_paths = [
            run_dir / "jobs_found.json",
            run_dir / "jobs.json",
            run_dir / "jobs_recommended.json",
        ]
        main_jobs = self._merge_job_items_from_paths(main_paths)
        merged_jobs = list(main_jobs)
        discovered_companies: set[str] = set()
        for item in merged_jobs:
            company = self._clean_company_name(item.get("company"))
            if company:
                discovered_companies.add(company.casefold())

        pending_jobs = self._load_resume_pending_jobs(run_dir, include_fallback=False)
        recommended_jobs = self.load_recommended_jobs(candidate_id)
        scored_jobs = self._filter_review_ready_jobs(merged_jobs)
        main_scored_jobs = self._filter_review_ready_jobs(main_jobs)

        candidate_company_pool_count = self._count_unique_companies_in_payload(candidate_companies_payload)
        signal_hit_job_count = 0
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
        return LegacyJobflowRunner._job_identity_key(item)

    @staticmethod
    def _job_identity_key(item: dict) -> str:
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
    def _resume_pending_detail_paths(cls, run_dir: Path, include_found: bool = False) -> list[Path]:
        paths = [
            run_dir / "jobs.json",
            run_dir / "jobs_recommended.json",
        ]
        if include_found:
            paths.insert(0, run_dir / "jobs_found.json")
        return paths

    @classmethod
    def _collect_resume_pending_jobs(cls, run_dir: Path, include_found: bool = False) -> list[dict]:
        merged_jobs: dict[str, dict] = {}
        for path in cls._resume_pending_detail_paths(run_dir, include_found=include_found):
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
    def _merge_resume_pending_job_lists(
        cls,
        run_dir: Path,
        *job_lists: list[dict],
        include_found_details: bool = False,
    ) -> list[dict]:
        merged_jobs: dict[str, dict] = {}
        for jobs in job_lists:
            normalized_jobs = cls._normalize_resume_pending_jobs(
                jobs if isinstance(jobs, list) else [],
                run_dir,
                include_found_details=include_found_details,
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
    def _previous_run_requires_resume(cls, run_dir: Path) -> bool:
        progress = cls._load_search_progress_from_run_dir(run_dir)
        status = str(progress.status or "").strip().lower()
        if status in {"running", "cancelled", "error"}:
            return True
        if status in {"success", "idle"}:
            return False
        payload = cls._load_resume_pending_payload(run_dir)
        payload_jobs = payload.get("jobs", [])
        return isinstance(payload_jobs, list) and len(payload_jobs) > 0

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
    def _normalize_resume_pending_jobs(
        cls,
        jobs: list[dict],
        run_dir: Path,
        include_found_details: bool = False,
    ) -> list[dict]:
        merged_detail_items = cls._merge_job_items_from_paths(
            cls._resume_pending_detail_paths(run_dir, include_found=include_found_details)
        )
        merged_details = {
            cls._job_item_key(item): item
            for item in merged_detail_items
            if cls._job_item_key(item)
        }
        merged_details_by_identity = {
            cls._job_identity_key(item): item
            for item in merged_detail_items
            if cls._job_identity_key(item)
        }
        pending_jobs: list[dict] = []
        for raw in jobs:
            if not isinstance(raw, dict):
                continue
            job = dict(raw)
            key = cls._job_item_key(job)
            detail = merged_details.get(key) if key else None
            if detail is None:
                identity_key = cls._job_identity_key(job)
                if identity_key:
                    detail = merged_details_by_identity.get(identity_key)
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
        if isinstance(payload_jobs, list):
            return cls._normalize_resume_pending_jobs(payload_jobs, run_dir)
        if not include_fallback:
            return []
        current_jobs = cls._collect_resume_pending_jobs(run_dir)
        if current_jobs:
            return cls._merge_resume_pending_job_lists(
                run_dir,
                current_jobs,
                payload_jobs if isinstance(payload_jobs, list) else [],
            )
        current_jobs = cls._collect_resume_pending_jobs(run_dir, include_found=True)
        return cls._merge_resume_pending_job_lists(
            run_dir,
            current_jobs,
            payload_jobs if isinstance(payload_jobs, list) else [],
            include_found_details=True,
        )

    @classmethod
    def _build_resume_pending_payload(cls, run_dir: Path, include_found_fallback: bool = False) -> dict:
        existing_payload = cls._load_resume_pending_payload(run_dir)
        payload_jobs = existing_payload.get("jobs", [])
        pending_jobs = cls._merge_resume_pending_job_lists(
            run_dir,
            cls._collect_resume_pending_jobs(run_dir),
            payload_jobs if isinstance(payload_jobs, list) else [],
        )
        if include_found_fallback and not pending_jobs:
            pending_jobs = cls._merge_resume_pending_job_lists(
                run_dir,
                cls._collect_resume_pending_jobs(run_dir, include_found=True),
                payload_jobs if isinstance(payload_jobs, list) else [],
                include_found_details=True,
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
    def _write_resume_pending_jobs(cls, run_dir: Path, include_found_fallback: bool = False) -> int:
        payload = cls._build_resume_pending_payload(
            run_dir,
            include_found_fallback=include_found_fallback,
        )
        output_path = run_dir / "jobs_resume_pending.json"
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(payload["jobs"])

    @classmethod
    def _clear_resume_pending_jobs(cls, run_dir: Path) -> None:
        output_path = run_dir / "jobs_resume_pending.json"
        existing_payload = cls._load_resume_pending_payload(run_dir)
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
        summary = existing_payload.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        summary["mainStagePendingCount"] = 0
        payload = {
            **preserved_meta,
            "version": version,
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "jobs": [],
            "summary": summary,
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _refresh_resume_pending_jobs(cls, run_dir: Path) -> int:
        try:
            return cls._write_resume_pending_jobs(run_dir, include_found_fallback=True)
        except Exception:
            return 0

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
        source = str(settings.api_key_source or "").strip().lower() if settings is not None else ""
        use_env_only = source == "env"
        settings_key = settings.api_key.strip() if settings is not None else ""
        if settings is not None:
            if settings_key:
                env["OPENAI_API_KEY"] = settings_key
            elif use_env_only:
                env.pop("OPENAI_API_KEY", None)
            else:
                env.pop("OPENAI_API_KEY", None)
        elif not env.get("OPENAI_API_KEY", "").strip():
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

        from_path = shutil.which("node")
        if from_path:
            return from_path
        return ""

    def _resolve_npm_binary(self, node_bin: str = "") -> str:
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

        for candidate in ("npm.cmd", "npm", "npm.exe"):
            from_path = shutil.which(candidate)
            if from_path:
                return from_path
        return ""

    @staticmethod
    def _windows_subprocess_kwargs() -> dict[str, object]:
        if os.name != "nt":
            return {}

        kwargs: dict[str, object] = {}
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if creationflags:
            kwargs["creationflags"] = creationflags

        startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_factory is not None:
            startupinfo = startupinfo_factory()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = 0
            kwargs["startupinfo"] = startupinfo
        return kwargs

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
        query_rotation_seed: int = 0,
        semantic_profile: CandidateSemanticProfile | None = None,
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
        anchor_plan = self._build_discovery_anchor_plan(
            candidate=candidate,
            profiles=profiles,
            scope_profile=scope_profile,
            semantic_profile=semantic_profile,
            run_dir=run_dir,
        )
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
        if semantic_profile is not None and semantic_profile.is_usable():
            candidate_config["semanticProfilePath"] = str(
                self._candidate_semantic_profile_path(run_dir).resolve()
            )

        search_config["queries"] = []
        search_config["trackMix"] = self._build_search_track_mix(
            scope_profile=scope_profile,
            candidate=candidate,
            profiles=profiles,
            semantic_profile=semantic_profile,
        )
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
            anchor_plan=anchor_plan,
            semantic_profile=semantic_profile,
            run_dir=run_dir,
            companies_path=source_companies_path,
            rotation_seed=query_rotation_seed,
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

        # Main stage: company pool first, company sources as final job channel.
        adaptive_search_config = self._ensure_dict(config, "adaptiveSearch")
        fetch_config = self._ensure_dict(config, "fetch")

        sources_config["enableWebSearch"] = False
        sources_config["enableCompanySources"] = True
        sources_config["requireCompanyDiscovery"] = True
        sources_config["maxCompaniesPerRun"] = 4
        sources_config["maxJobsPerCompany"] = 5
        sources_config["maxJobLinksPerCompany"] = 12
        sources_config["enableCompanySearchFallback"] = True
        sources_config["maxCompanySearchFallbacksPerRun"] = 2

        company_discovery_config["enableAutoDiscovery"] = True
        company_discovery_config["queries"] = company_discovery_queries
        company_discovery_config["maxNewCompaniesPerRun"] = 3
        analysis_config["scoringUseWebSearch"] = False
        # Ask AI to post-verify link validity/expiry signals before final recommendation.
        analysis_config["postVerifyEnabled"] = True
        analysis_config["postVerifyUseWebSearch"] = True
        analysis_config["postVerifyRequireChecked"] = True
        analysis_config["maxJobsToAnalyzePerRun"] = 12
        analysis_config["jdFetchMaxJobsPerRun"] = 12
        analysis_config["postVerifyMaxJobsPerRun"] = 8
        fetch_config["timeoutMs"] = 12000
        adaptive_search_config.update(
            {
                "enabled": True,
                "targetNewJobs": 999999,
                "minNewJobsToContinue": 999999,
                "baseBudgetSeconds": 120,
                "baseRoundSeconds": 120,
                "extendBudgetSeconds": 120,
                "extendRoundSeconds": 120,
                "deepBudgetSeconds": 180,
                "deepRoundSeconds": 180,
                "baseExistingCompanies": 2,
                "baseExistingCompanyBatchSize": 2,
                "extendCompanyBatchSize": 1,
                "extendExistingCompanyBatchSize": 1,
                "maxExistingCompaniesPerRun": 4,
                "coldDiscoveryQueryBudget": 4,
                "coldStartQueryBudget": 4,
                "warmDiscoveryQueryBudget": 2,
                "deepSearchQueryBudget": 2,
                "coldDiscoveryCompanyBudget": 3,
                "coldStartMaxNewCompanies": 3,
                "warmDiscoveryCompanyBudget": 2,
                "deepSearchMaxNewCompanies": 2,
                "processDiscoveredCompaniesPerRound": 3,
                "coldStartImmediateProcessBatchSize": 2,
                "deepSearchImmediateProcessBatchSize": 2,
                "sessionEmptyRoundsBeforePause": 3,
                "sessionIdlePauseSeconds": 300,
                "sessionPassTimeoutSeconds": 600,
                "companyCooldownDaysNoJobs": 7,
                "companyCooldownDaysNoNewJobs": 7,
                "companyCooldownDaysSomeJobsNoNew": 7,
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
        resume_import_note = ""
        if raw_path:
            path = Path(raw_path)
            if path.exists() and path.is_file():
                suffix = path.suffix.lower()
                if suffix in {".md", ".txt"}:
                    return str(path.resolve())

                resume_result = load_resume_excerpt_result(str(path), max_chars=None)
                if resume_result.text:
                    normalized_resume = run_dir / "resume.source.normalized.md"
                    normalized_lines = [
                        f"# Resume Source: {path.name}",
                        "",
                        resume_result.text.strip(),
                        "",
                    ]
                    normalized_resume.write_text("\n".join(normalized_lines), encoding="utf-8")
                    return str(normalized_resume.resolve())
                if resume_result.error:
                    resume_import_note = resume_result.error

        generated_resume = run_dir / "resume.generated.md"
        lines = [
            f"# Candidate: {candidate.name}",
            "",
        ]
        if raw_path:
            lines.extend(
                [
                    f"- Resume Source Path: {raw_path}",
                    f"- Resume Import Status: {resume_import_note or 'Unavailable or unreadable; using structured candidate summary instead.'}",
                    "",
                ]
            )
        lines.extend(
            [
            f"- Base Location: {candidate.base_location or 'N/A'}",
            "- Preferred Locations:",
            candidate.preferred_locations.strip() or "N/A",
            "",
            "- Target Directions:",
            candidate.target_directions.strip() or "N/A",
            "",
            "- Professional Background Summary:",
            candidate.notes.strip() or "N/A",
            "",
            ]
        )
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

    @staticmethod
    def _candidate_semantic_profile_path(run_dir: Path) -> Path:
        return run_dir / "candidate.semantic_profile.generated.json"

    def _load_candidate_semantic_profile_for_run(
        self,
        *,
        candidate: CandidateRecord,
        settings: OpenAISettings | None,
        api_base_url: str,
        run_dir: Path,
    ) -> CandidateSemanticProfile | None:
        cache_path = self._candidate_semantic_profile_path(run_dir)
        service = OpenAIRoleRecommendationService()
        try:
            return service.extract_candidate_semantic_profile(
                candidate=candidate,
                settings=settings or OpenAISettings(),
                api_base_url=api_base_url,
                cache_path=cache_path,
            )
        except RoleRecommendationError:
            return None

    @staticmethod
    def _anchor_library(scope_profile: str) -> dict[str, list[tuple[str, str]]]:
        if scope_profile == "adjacent_mbse":
            return ADJACENT_SCOPE_BUSINESS_ANCHORS
        return MAINLINE_BUSINESS_ANCHORS

    @staticmethod
    def _default_anchor_buckets(scope_profile: str) -> dict[str, list[str]]:
        if scope_profile == "adjacent_mbse":
            return DEFAULT_BUSINESS_ANCHORS["adjacent_mbse"]
        return DEFAULT_BUSINESS_ANCHORS["hydrogen_mainline"]

    @staticmethod
    def _looks_like_role_phrase(text: str) -> bool:
        value = str(text or "").strip().casefold()
        if not value:
            return False
        return bool(
            re.search(
                r"\b(engineer|scientist|manager|specialist|analyst|developer|designer|intern|lead|director)\b|工程师|科学家|经理|专家|分析师|开发|总监",
                value,
            )
        )

    @staticmethod
    def _normalize_business_hint(text: str) -> str:
        value = re.sub(r"\s+", " ", str(text or "").strip())
        if not value:
            return ""
        value = value.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")
        value = re.sub(
            r"^(focus(?:ed)? on|speciali[sz](?:e|ed|ing) in|work(?:ed|ing)? on|experience in|background in|expertise in|interested in|related to|around)\s+",
            "",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"^(and|with|for|toward|towards|future|target|desired|adjacent|explor(?:e|ation))\s+",
            "",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"^(聚焦(?:于)?|专注(?:于)?|从事|擅长|熟悉|研究方向(?:为)?|研究|方向(?:为)?|相关(?:方向|领域)?|主要(?:方向|关注|做)?|涉及|偏向|偏重|以及|还有|并|和|未来(?:方向)?|目标(?:方向)?|邻近(?:方向)?|相邻(?:方向)?|探索(?:方向)?)\s*",
            "",
            value,
        )
        return value.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")

    @staticmethod
    def _looks_like_business_noise(text: str) -> bool:
        value = str(text or "").strip().casefold()
        if not value:
            return True
        if len(value) < 3:
            return True
        if len(value.split()) > 10:
            return True
        return bool(
            re.search(
                r"looking for|prefer|seeking|resume|curriculum vitae|\bcv\b|\bjob\b|博士背景|硕士背景|工作经历|专业背景|希望|想找|想做|保留|简历|候选人",
                value,
            )
        )

    def _collect_business_hint_terms(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        semantic_profile: CandidateSemanticProfile | None = None,
    ) -> list[str]:
        active_profiles = [profile for profile in profiles if profile.is_active]
        source = active_profiles if active_profiles else profiles
        hints: list[str] = []
        if semantic_profile is not None:
            hints.extend(list(semantic_profile.background_keywords))
            hints.extend(list(semantic_profile.target_direction_keywords))
            hints.extend(list(semantic_profile.strong_capabilities))
        for profile in source:
            hints.extend(self._split_multivalue_text(profile.company_focus))
            hints.extend(self._split_multivalue_text(profile.company_keyword_focus))
            hints.extend(self._split_multivalue_text(profile.keyword_focus))
            hints.extend(self._split_multivalue_text(profile.target_role))
        hints.extend(self._split_multivalue_text(candidate.target_directions))
        hints.extend(self._split_multivalue_text(candidate.notes))
        filtered: list[str] = []
        for hint in hints:
            text = self._normalize_business_hint(hint)
            if not text or len(text) > 72:
                continue
            if self._looks_like_role_phrase(text):
                continue
            if self._looks_like_business_noise(text):
                continue
            filtered.append(text)
        return self._dedup_text(filtered, limit=24)

    def _build_anchor_source_text(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        semantic_profile: CandidateSemanticProfile | None = None,
        run_dir: Path | None = None,
    ) -> str:
        active_profiles = [profile for profile in profiles if profile.is_active]
        source = active_profiles if active_profiles else profiles
        parts: list[str] = [
            candidate.target_directions.strip(),
            candidate.notes.strip(),
        ]
        if semantic_profile is not None:
            parts.append(semantic_profile.summary)
            parts.extend(semantic_profile.background_keywords)
            parts.extend(semantic_profile.target_direction_keywords)
            parts.extend(semantic_profile.core_business_areas)
            parts.extend(semantic_profile.adjacent_business_areas)
            parts.extend(semantic_profile.exploration_business_areas)
            parts.extend(semantic_profile.strong_capabilities)
        resume_result = load_resume_excerpt_result(candidate.active_resume_path, max_chars=12000)
        if resume_result.text:
            parts.append(resume_result.text)
        feedback = self._load_run_feedback(run_dir)
        parts.extend(feedback.get("keywords", [])[:16])
        for profile in source:
            parts.extend(role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name))
            parts.append(str(profile.target_role or "").strip())
            parts.extend(description_query_lines(profile.keyword_focus))
            parts.extend(self._split_multivalue_text(profile.company_focus))
            parts.extend(self._split_multivalue_text(profile.company_keyword_focus))
            parts.extend(self._split_multivalue_text("\n".join(profile.queries[:8])))
        return "\n".join(part for part in parts if str(part or "").strip())

    def _classify_business_hint(self, hint: str, scope_profile: str) -> str:
        text = str(hint or "").strip()
        if not text:
            return "core"
        for bucket in ("core", "adjacent", "explore"):
            for _, pattern in self._anchor_library(scope_profile).get(bucket, []):
                if re.search(pattern, text, flags=re.IGNORECASE):
                    return bucket
        return "core"

    def _build_discovery_anchor_plan(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        scope_profile: str,
        semantic_profile: CandidateSemanticProfile | None = None,
        run_dir: Path | None = None,
    ) -> DiscoveryAnchorPlan:
        defaults = self._default_anchor_buckets(scope_profile)
        if semantic_profile is not None and semantic_profile.is_usable():
            normalized = {
                "core": self._dedup_text(
                    [
                        self._normalize_business_hint(item)
                        for item in semantic_profile.core_business_areas
                    ],
                    limit=PHRASE_LIBRARY_LIMITS["core"],
                ),
                "adjacent": self._dedup_text(
                    [
                        self._normalize_business_hint(item)
                        for item in semantic_profile.adjacent_business_areas
                    ],
                    limit=PHRASE_LIBRARY_LIMITS["adjacent"],
                ),
                "explore": self._dedup_text(
                    [
                        self._normalize_business_hint(item)
                        for item in semantic_profile.exploration_business_areas
                    ],
                    limit=PHRASE_LIBRARY_LIMITS["explore"],
                ),
            }
            minimum_sizes = {"core": 6, "adjacent": 4, "explore": 3}
            for bucket in ("core", "adjacent", "explore"):
                values = normalized[bucket]
                for default_value in defaults.get(bucket, []):
                    if len(values) >= minimum_sizes[bucket]:
                        break
                    if default_value.casefold() not in {item.casefold() for item in values}:
                        values.append(default_value)
                normalized[bucket] = values[: PHRASE_LIBRARY_LIMITS[bucket]]
            return DiscoveryAnchorPlan(
                core=normalized["core"],
                adjacent=normalized["adjacent"],
                explore=normalized["explore"],
            )

        source_text = self._build_anchor_source_text(
            candidate,
            profiles,
            semantic_profile=semantic_profile,
            run_dir=run_dir,
        )
        library = self._anchor_library(scope_profile)
        buckets: dict[str, list[str]] = {"core": [], "adjacent": [], "explore": []}

        for bucket in ("core", "adjacent", "explore"):
            for label, pattern in library.get(bucket, []):
                if re.search(pattern, source_text, flags=re.IGNORECASE):
                    buckets[bucket].append(label)

        for hint in self._collect_business_hint_terms(
            candidate,
            profiles,
            semantic_profile=semantic_profile,
        ):
            buckets[self._classify_business_hint(hint, scope_profile)].append(hint)

        minimum_sizes = {"core": 4, "adjacent": 3, "explore": 2}
        normalized: dict[str, list[str]] = {}
        for bucket in ("core", "adjacent", "explore"):
            values = self._dedup_text(buckets[bucket], limit=PHRASE_LIBRARY_LIMITS[bucket])
            for default_value in defaults.get(bucket, []):
                if len(values) >= minimum_sizes[bucket]:
                    break
                if default_value.casefold() not in {item.casefold() for item in values}:
                    values.append(default_value)
            normalized[bucket] = values[: PHRASE_LIBRARY_LIMITS[bucket]]

        return DiscoveryAnchorPlan(
            core=normalized["core"],
            adjacent=normalized["adjacent"],
            explore=normalized["explore"],
        )

    @staticmethod
    def _sample_bucket_phrases(values: list[str], count: int, seed: int) -> list[str]:
        if count <= 0 or not values:
            return []
        rotated = LegacyJobflowRunner._rotate_list(values, seed)
        return rotated[: min(len(rotated), count)]

    def _select_round_phrase_plan(
        self,
        *,
        anchor_plan: DiscoveryAnchorPlan,
        seed: int,
    ) -> DiscoveryAnchorPlan:
        return DiscoveryAnchorPlan(
            core=self._sample_bucket_phrases(
                self._dedup_text(anchor_plan.core, limit=PHRASE_LIBRARY_LIMITS["core"]),
                ROUND_PHRASE_SAMPLE_COUNTS["core"],
                seed + 11,
            ),
            adjacent=self._sample_bucket_phrases(
                self._dedup_text(anchor_plan.adjacent, limit=PHRASE_LIBRARY_LIMITS["adjacent"]),
                ROUND_PHRASE_SAMPLE_COUNTS["adjacent"],
                seed + 23,
            ),
            explore=self._sample_bucket_phrases(
                self._dedup_text(anchor_plan.explore, limit=PHRASE_LIBRARY_LIMITS["explore"]),
                ROUND_PHRASE_SAMPLE_COUNTS["explore"],
                seed + 37,
            ),
        )

    @staticmethod
    def _rotate_list(values: list[str], seed: int) -> list[str]:
        if not values:
            return []
        offset = abs(int(seed)) % len(values)
        return list(values[offset:]) + list(values[:offset])

    @staticmethod
    def _allocate_weighted_counts(limit: int) -> dict[str, int]:
        total = max(0, int(limit or 0))
        if total <= 0:
            return {"core": 0, "adjacent": 0, "explore": 0}
        counts = {
            bucket: int(total * weight)
            for bucket, weight in BUSINESS_QUERY_WEIGHTS.items()
        }
        assigned = sum(counts.values())
        while assigned < total:
            for bucket in ("core", "adjacent", "explore"):
                if assigned >= total:
                    break
                counts[bucket] += 1
                assigned += 1
        return counts

    @staticmethod
    def _weighted_bucket_schedule(counts: dict[str, int]) -> list[str]:
        total = sum(max(0, int(value or 0)) for value in counts.values())
        if total <= 0:
            return []
        emitted = {bucket: 0 for bucket in ("core", "adjacent", "explore")}
        schedule: list[str] = []
        for position in range(total):
            best_bucket = ""
            best_score = float("-inf")
            for bucket in ("core", "adjacent", "explore"):
                target_count = max(0, int(counts.get(bucket, 0) or 0))
                if emitted[bucket] >= target_count:
                    continue
                desired = (position + 1) * (target_count / total)
                score = desired - emitted[bucket]
                if score > best_score:
                    best_score = score
                    best_bucket = bucket
            if not best_bucket:
                break
            emitted[best_bucket] += 1
            schedule.append(best_bucket)
        return schedule

    @staticmethod
    def _normalize_company_discovery_query(raw: str) -> str:
        text = re.sub(r"\s+", " ", str(raw or "").strip())
        if not text:
            return ""
        if len(text) > 200:
            text = text[:200].strip()
        return text

    def _generate_bucket_queries(
        self,
        *,
        anchors: list[str],
        templates: list[str],
        limit: int,
        seed: int,
        normalize: Callable[[str], str],
    ) -> list[str]:
        if limit <= 0 or not anchors or not templates:
            return []
        rotated_anchors = self._rotate_list(self._dedup_text(anchors, limit=32), seed)
        rotated_templates = self._rotate_list(list(templates), seed // 7 if seed else 0)
        queries: list[str] = []
        seen: set[str] = set()
        max_attempts = max(limit * 8, len(rotated_anchors) * len(rotated_templates))
        for attempt in range(max_attempts):
            anchor = rotated_anchors[attempt % len(rotated_anchors)]
            template = rotated_templates[(attempt // max(1, len(rotated_anchors))) % len(rotated_templates)]
            normalized = normalize(template.format(anchor=anchor))
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            queries.append(normalized)
            if len(queries) >= limit:
                break
        return queries

    def _generate_weighted_query_plan(
        self,
        *,
        anchor_plan: DiscoveryAnchorPlan,
        templates: dict[str, list[str]],
        limit: int,
        seed: int,
        normalize: Callable[[str], str],
    ) -> list[str]:
        counts = self._allocate_weighted_counts(limit)
        bucket_queries = {
            "core": self._generate_bucket_queries(
                anchors=anchor_plan.core,
                templates=templates.get("core", []),
                limit=max(counts["core"], 1),
                seed=seed + 11,
                normalize=normalize,
            ),
            "adjacent": self._generate_bucket_queries(
                anchors=anchor_plan.adjacent,
                templates=templates.get("adjacent", []),
                limit=max(counts["adjacent"], 1),
                seed=seed + 23,
                normalize=normalize,
            ),
            "explore": self._generate_bucket_queries(
                anchors=anchor_plan.explore,
                templates=templates.get("explore", []),
                limit=max(counts["explore"], 1),
                seed=seed + 37,
                normalize=normalize,
            ),
        }

        indices = {"core": 0, "adjacent": 0, "explore": 0}
        planned: list[str] = []
        seen: set[str] = set()
        for bucket in self._weighted_bucket_schedule(counts):
            bucket_list = bucket_queries.get(bucket, [])
            if indices[bucket] >= len(bucket_list):
                continue
            value = bucket_list[indices[bucket]]
            indices[bucket] += 1
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            planned.append(value)
            if len(planned) >= limit:
                break
        return planned

    @staticmethod
    def _search_track_patterns(scope_profile: str) -> dict[str, re.Pattern[str]]:
        if scope_profile == "adjacent_mbse":
            return ADJACENT_SCOPE_TRACK_PATTERNS
        return MAINLINE_SEARCH_TRACK_PATTERNS

    @staticmethod
    def _fallback_search_track_mix(scope_profile: str) -> dict[str, float]:
        fallback = SCOPE_TRACK_MIX_FALLBACKS.get(
            scope_profile,
            SCOPE_TRACK_MIX_FALLBACKS["hydrogen_mainline"],
        )
        return {
            key: float(fallback.get(key, 0.0) or 0.0)
            for key in SEARCH_TRACK_KEYS
        }

    @staticmethod
    def _normalize_search_track_mix(values: dict[str, float]) -> dict[str, float]:
        normalized = {
            key: max(0.0, float(values.get(key, 0.0) or 0.0))
            for key in SEARCH_TRACK_KEYS
        }
        total = sum(normalized.values())
        if total <= 0:
            equal_share = 1.0 / len(SEARCH_TRACK_KEYS)
            return {key: equal_share for key in SEARCH_TRACK_KEYS}
        normalized = {key: value / total for key, value in normalized.items()}
        minimum_share = 0.06
        if any(value < minimum_share for value in normalized.values()):
            normalized = {
                key: max(value, minimum_share)
                for key, value in normalized.items()
            }
            total = sum(normalized.values())
            normalized = {key: value / total for key, value in normalized.items()}
        return normalized

    def _build_search_track_mix_chunks(
        self,
        *,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        semantic_profile: CandidateSemanticProfile | None,
    ) -> list[tuple[str, float]]:
        active_profiles = [profile for profile in profiles if profile.is_active]
        source = active_profiles if active_profiles else profiles
        chunks: list[tuple[str, float]] = []

        def add(values: list[str] | tuple[str, ...], weight: float) -> None:
            for value in values:
                text = str(value or "").strip()
                if text:
                    chunks.append((text, weight))

        if semantic_profile is not None and semantic_profile.is_usable():
            add([semantic_profile.summary], 1.6)
            add(list(semantic_profile.core_business_areas), 4.0)
            add(list(semantic_profile.background_keywords), 3.2)
            add(list(semantic_profile.strong_capabilities), 2.8)
            add(list(semantic_profile.target_direction_keywords), 2.4)
            add(list(semantic_profile.adjacent_business_areas), 2.0)
            add(list(semantic_profile.exploration_business_areas), 1.2)
        else:
            resume_result = load_resume_excerpt_result(candidate.active_resume_path, max_chars=8000)
            if resume_result.text:
                add([resume_result.text], 1.4)

        add(self._split_multivalue_text(candidate.target_directions), 2.2)
        add(self._split_multivalue_text(candidate.notes), 1.6)

        for profile in source:
            add(role_name_query_lines(profile.role_name_i18n, fallback_name=profile.name), 1.8)
            add([profile.target_role], 1.8)
            add(self._split_multivalue_text(profile.keyword_focus), 1.8)
            add(self._split_multivalue_text(profile.company_focus), 1.4)
            add(self._split_multivalue_text(profile.company_keyword_focus), 1.4)

        return chunks

    def _score_search_tracks(
        self,
        *,
        scope_profile: str,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        semantic_profile: CandidateSemanticProfile | None,
    ) -> tuple[dict[str, float], int]:
        patterns = self._search_track_patterns(scope_profile)
        scores = {key: 0.0 for key in SEARCH_TRACK_KEYS}
        evidence_hits = 0

        for text, weight in self._build_search_track_mix_chunks(
            candidate=candidate,
            profiles=profiles,
            semantic_profile=semantic_profile,
        ):
            matched = False
            for key, pattern in patterns.items():
                if not pattern.search(text):
                    continue
                scores[key] += weight
                matched = True
            if matched:
                evidence_hits += 1

        if semantic_profile is not None and semantic_profile.is_usable():
            for text in semantic_profile.avoid_business_areas:
                for key, pattern in patterns.items():
                    if not pattern.search(text):
                        continue
                    scores[key] = max(0.0, scores[key] - 1.8)

        return scores, evidence_hits

    def _build_search_track_mix(
        self,
        *,
        scope_profile: str,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        semantic_profile: CandidateSemanticProfile | None = None,
    ) -> dict[str, float]:
        fallback = self._fallback_search_track_mix(scope_profile)
        scores, evidence_hits = self._score_search_tracks(
            scope_profile=scope_profile,
            candidate=candidate,
            profiles=profiles,
            semantic_profile=semantic_profile,
        )
        if evidence_hits <= 0 or sum(scores.values()) <= 0:
            return fallback

        evidence_mix = self._normalize_search_track_mix(scores)
        evidence_weight = 0.80 if semantic_profile is not None and semantic_profile.is_usable() else 0.65
        blended = {
            key: fallback[key] * (1.0 - evidence_weight) + evidence_mix[key] * evidence_weight
            for key in SEARCH_TRACK_KEYS
        }
        return self._normalize_search_track_mix(blended)

    def _build_company_discovery_queries(
        self,
        candidate: CandidateRecord,
        profiles: list[SearchProfileRecord],
        anchor_plan: DiscoveryAnchorPlan | None = None,
        semantic_profile: CandidateSemanticProfile | None = None,
        run_dir: Path | None = None,
        companies_path: Path | None = None,
        rotation_seed: int = 0,
    ) -> list[str]:
        scope_profile = self._resolve_scope_profile(profiles)
        resolved_anchor_plan = anchor_plan or self._build_discovery_anchor_plan(
            candidate=candidate,
            profiles=profiles,
            scope_profile=scope_profile,
            semantic_profile=semantic_profile,
            run_dir=run_dir,
        )
        round_phrase_plan = self._select_round_phrase_plan(
            anchor_plan=resolved_anchor_plan,
            seed=rotation_seed + 101,
        )
        generated = self._generate_weighted_query_plan(
            anchor_plan=round_phrase_plan,
            templates=BUSINESS_COMPANY_QUERY_TEMPLATES,
            limit=16,
            seed=rotation_seed + 101,
            normalize=self._normalize_company_discovery_query,
        )

        if not generated and semantic_profile is not None and semantic_profile.is_usable():
            for phrase in list(semantic_profile.company_discovery_phrase_library_en())[:8]:
                generated.append(self._normalize_company_discovery_query(f"{phrase} companies"))
                generated.append(
                    self._normalize_company_discovery_query(
                        f"{phrase} industrial technology companies"
                    )
                )

        dedup: list[str] = []
        seen: set[str] = set()
        for raw in generated:
            text = self._normalize_company_discovery_query(raw)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(text)
            if len(dedup) >= 16:
                break
        if not dedup:
            dedup = [
                "hydrogen systems companies",
                "systems engineering companies",
                "industrial technology companies",
            ]
        return dedup[:16]

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
                **self._windows_subprocess_kwargs(),
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
                    **LegacyJobflowRunner._windows_subprocess_kwargs(),
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
        tokens = re.split(r"[\n,;|，；、。]+", text)
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
                accepted = path.name == "jobs_recommended.json" or recommend or (
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

