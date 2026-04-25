from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...ai.role_recommendations import (
    CandidateSemanticProfile,
    decode_bilingual_role_name,
    description_query_lines,
    load_resume_excerpt_result,
    role_name_query_lines,
)
from ...common.location_codec import candidate_location_preference_text
from ...db.repositories.candidates import CandidateRecord
from ...db.repositories.profiles import SearchProfileRecord
from ...db.repositories.settings import OpenAISettings
from ..runtime_defaults import DEFAULT_RUNTIME_CONFIG
from .. import runtime_strategy
from .candidate_search_signals import (
    CandidateSearchSignals,
    resolve_candidate_search_signals,
)
from . import company_discovery_queries as company_discovery_queries_module

HTTP_REQUEST_TIMEOUT_MS = 12000
DISCOVERY_COMPANIES_PER_QUERY_CAP = 12
JOB_LINK_HARD_CAP_PER_COMPANY = 40
COMPANY_FIT_TOOL_TERMS = set(company_discovery_queries_module.TOOL_ONLY_DISCOVERY_TERMS)


@dataclass(frozen=True)
class RuntimeCandidateInputPrep:
    resume_path: str
    scope_profiles: tuple[str, ...]
    target_roles: list[dict[str, object]]


@dataclass(frozen=True)
class RuntimeCandidateConfigContext:
    candidate_inputs: RuntimeCandidateInputPrep
    signals: CandidateSearchSignals
    discovery_query_input: dict[str, object] = field(default_factory=dict)

    @property
    def resume_path(self) -> str:
        return self.candidate_inputs.resume_path

    @property
    def scope_profiles(self) -> tuple[str, ...]:
        return self.candidate_inputs.scope_profiles

    @property
    def target_roles(self) -> list[dict[str, object]]:
        return self.candidate_inputs.target_roles


@dataclass(frozen=True)
class RuntimeConfigSections:
    candidate: dict
    search: dict
    sources: dict
    output: dict
    company_discovery: dict
    direct_job_discovery: dict
    analysis: dict
    translation: dict
    adaptive_search: dict
    fetch: dict


@dataclass(frozen=True)
class RuntimeModelOverrides:
    fast_model: str = ""
    quality_model: str = ""


def coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "on"}


def load_base_config() -> dict:
    return copy.deepcopy(DEFAULT_RUNTIME_CONFIG)


def build_runtime_env(settings: OpenAISettings | None, api_base_url: str) -> dict[str, str]:
    env = os.environ.copy()
    source = str(settings.api_key_source or "").strip().lower() if settings is not None else ""
    use_env_only = source == "env"
    settings_key = settings.api_key.strip() if settings is not None else ""
    env_var_name = str(settings.api_key_env_var or "").strip() if settings is not None else ""
    if settings is not None:
        if settings_key:
            env["OPENAI_API_KEY"] = settings_key
        elif use_env_only:
            env_lookup_name = env_var_name or "OPENAI_API_KEY"
            env_key = env.get(env_lookup_name, "").strip()
            if env_key:
                env["OPENAI_API_KEY"] = env_key
            else:
                env.pop("OPENAI_API_KEY", None)
        else:
            env.pop("OPENAI_API_KEY", None)
    elif not env.get("OPENAI_API_KEY", "").strip():
        azure_key = env.get("AZURE_OPENAI_API_KEY", "").strip()
        if azure_key:
            env["OPENAI_API_KEY"] = azure_key

    settings_fast_model = (
        str(getattr(settings, "fast_model", "") or getattr(settings, "model", "") or "").strip()
        if settings is not None
        else ""
    )
    settings_quality_model = (
        str(getattr(settings, "quality_model", "") or "").strip()
        if settings is not None
        else ""
    )
    if settings_fast_model:
        env["JOBFLOW_OPENAI_FAST_MODEL"] = settings_fast_model
        env["JOBFLOW_OPENAI_MODEL"] = settings_fast_model
    elif not env.get("JOBFLOW_OPENAI_MODEL", "").strip():
        azure_model = env.get("AZURE_OPENAI_MODEL", "").strip() or env.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
        if azure_model:
            env["JOBFLOW_OPENAI_FAST_MODEL"] = azure_model
            env["JOBFLOW_OPENAI_MODEL"] = azure_model

    if settings_quality_model:
        env["JOBFLOW_OPENAI_QUALITY_MODEL"] = settings_quality_model
    elif not env.get("JOBFLOW_OPENAI_QUALITY_MODEL", "").strip():
        env_quality_model = env.get("JOBFLOW_OPENAI_HIGH_QUALITY_MODEL", "").strip()
        if env_quality_model:
            env["JOBFLOW_OPENAI_QUALITY_MODEL"] = env_quality_model

    if not env.get("JOBFLOW_OPENAI_MODEL", "").strip():
        env["JOBFLOW_OPENAI_MODEL"] = "gpt-5"
    if not env.get("JOBFLOW_OPENAI_FAST_MODEL", "").strip():
        env["JOBFLOW_OPENAI_FAST_MODEL"] = env["JOBFLOW_OPENAI_MODEL"]

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
    if api_base_url.strip() and not (env.get("OPENAI_BASE_URL", "").strip() or env.get("OPENAI_API_BASE", "").strip()):
        env["OPENAI_BASE_URL"] = api_base_url.strip()
        env["OPENAI_API_BASE"] = api_base_url.strip()
    return env


def ensure_dict(container: dict, key: str) -> dict:
    value = container.get(key)
    if isinstance(value, dict):
        return value
    container[key] = {}
    return container[key]


def resolve_effective_max_companies(
    *,
    requested_max_companies: int | None,
    runtime_config: Mapping[str, Any],
) -> int:
    sources = runtime_config.get("sources")
    runtime_limit = 0
    if isinstance(sources, Mapping):
        try:
            runtime_limit = max(0, int(sources.get("maxCompaniesPerRun", 0) or 0))
        except (TypeError, ValueError):
            runtime_limit = 0
    if requested_max_companies is None:
        return runtime_limit
    requested_limit = max(1, int(requested_max_companies))
    if runtime_limit <= 0:
        return requested_limit
    return max(1, min(requested_limit, runtime_limit))


def update_section(section: dict, values: dict[str, object]) -> None:
    for key, value in values.items():
        section[key] = value


def runtime_config_sections(config: dict) -> RuntimeConfigSections:
    return RuntimeConfigSections(
        candidate=ensure_dict(config, "candidate"),
        search=ensure_dict(config, "search"),
        sources=ensure_dict(config, "sources"),
        output=ensure_dict(config, "output"),
        company_discovery=ensure_dict(config, "companyDiscovery"),
        direct_job_discovery=ensure_dict(config, "directJobDiscovery"),
        analysis=ensure_dict(config, "analysis"),
        translation=ensure_dict(config, "translation"),
        adaptive_search=ensure_dict(config, "adaptiveSearch"),
        fetch=ensure_dict(config, "fetch"),
    )


def resolve_resume_path(candidate: CandidateRecord, run_dir: Path) -> str:
    raw_path = str(candidate.active_resume_path or "").strip()
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
            str(candidate.preferred_locations or "").strip() or "N/A",
            "",
            "- Target Directions:",
            str(candidate.target_directions or "").strip() or "N/A",
            "",
            "- Professional Background Summary:",
            str(candidate.notes or "").strip() or "N/A",
            "",
        ]
    )
    generated_resume.write_text("\n".join(lines), encoding="utf-8")
    return str(generated_resume.resolve())


def resolve_scope_profiles(profiles: list[SearchProfileRecord]) -> tuple[str, ...]:
    active_profiles = [profile for profile in profiles if profile.is_active]
    source = active_profiles if active_profiles else profiles
    values: list[str] = []
    seen: set[str] = set()
    for profile in source:
        scope_profile = str(profile.scope_profile or "").strip()
        if not scope_profile:
            continue
        key = scope_profile.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(scope_profile)
    return tuple(values)


def build_target_roles_payload(candidate: CandidateRecord, profiles: list[SearchProfileRecord]) -> list[dict[str, object]]:
    active_profiles = [profile for profile in profiles if profile.is_active]
    source = active_profiles if active_profiles else profiles
    payload: list[dict[str, object]] = []
    seen: set[str] = set()
    for profile in source:
        target_role_text = str(profile.target_role or "").strip()
        fallback_role_name = target_role_text or str(profile.name or "").strip()
        zh_name, en_name = decode_bilingual_role_name(
            profile.role_name_i18n,
            fallback_name=fallback_role_name,
        )
        display_name = en_name or zh_name or fallback_role_name
        if not display_name and not target_role_text:
            continue
        role_id = (
            f"profile:{int(profile.profile_id)}"
            if profile.profile_id is not None
            else re.sub(r"[^a-z0-9]+", "-", display_name.casefold()).strip("-")
        )
        if not role_id:
            role_id = f"role:{len(payload) + 1}"
        if role_id in seen:
            continue
        seen.add(role_id)
        description_lines = description_query_lines(profile.keyword_focus)
        summary = " ".join(description_lines[:2]).strip()
        payload.append(
            {
                "roleId": role_id,
                "profileId": int(profile.profile_id) if profile.profile_id is not None else None,
                "nameZh": zh_name,
                "nameEn": en_name,
                "displayName": display_name,
                "targetRoleText": target_role_text,
                "summary": summary,
                "scopeProfile": str(profile.scope_profile or "").strip(),
            }
        )
    if payload:
        return payload
    fallback_text = str(candidate.target_directions or "").strip()
    if fallback_text:
        return [
            {
                "roleId": "candidate-default",
                "profileId": None,
                "nameZh": "",
                "nameEn": fallback_text,
                "displayName": fallback_text,
                "targetRoleText": fallback_text,
                "summary": "",
                "scopeProfile": "",
            }
        ]
    return []


def resolve_model_override(settings: OpenAISettings | None) -> str:
    env_model = os.getenv("JOBFLOW_OPENAI_MODEL", "").strip()
    if env_model:
        return env_model
    azure_model = os.getenv("AZURE_OPENAI_MODEL", "").strip() or os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    if azure_model:
        return azure_model
    if settings is None:
        return ""
    return str(getattr(settings, "fast_model", "") or getattr(settings, "model", "") or "").strip()


def resolve_model_overrides(settings: OpenAISettings | None) -> RuntimeModelOverrides:
    fast_model = os.getenv("JOBFLOW_OPENAI_FAST_MODEL", "").strip() or resolve_model_override(settings)
    quality_model = (
        os.getenv("JOBFLOW_OPENAI_QUALITY_MODEL", "").strip()
        or os.getenv("JOBFLOW_OPENAI_HIGH_QUALITY_MODEL", "").strip()
    )
    if not quality_model and settings is not None:
        quality_model = str(getattr(settings, "quality_model", "") or "").strip()
    return RuntimeModelOverrides(
        fast_model=fast_model,
        quality_model=quality_model,
    )


def prepare_runtime_candidate_inputs(
    *,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    run_dir: Path,
) -> RuntimeCandidateInputPrep:
    resume_path = resolve_resume_path(candidate, run_dir)
    scope_profiles = resolve_scope_profiles(profiles)
    target_roles = build_target_roles_payload(candidate, profiles)
    return RuntimeCandidateInputPrep(
        resume_path=resume_path,
        scope_profiles=scope_profiles,
        target_roles=target_roles,
    )


def build_runtime_candidate_context_from_inputs(
    *,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    semantic_profile: CandidateSemanticProfile | None,
    candidate_inputs: RuntimeCandidateInputPrep,
    signals: CandidateSearchSignals | None = None,
) -> RuntimeCandidateConfigContext:
    resolved_signals = resolve_candidate_search_signals(
        profiles=profiles,
        semantic_profile=semantic_profile,
        signals=signals,
    )
    return RuntimeCandidateConfigContext(
        candidate_inputs=candidate_inputs,
        signals=resolved_signals,
        discovery_query_input=build_candidate_context_company_discovery_query_input(
            candidate=candidate,
            profiles=profiles,
            semantic_profile=semantic_profile,
            candidate_inputs=candidate_inputs,
        ),
    )


def build_runtime_candidate_context(
    *,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    run_dir: Path,
    semantic_profile: CandidateSemanticProfile | None,
    signals: CandidateSearchSignals | None = None,
) -> RuntimeCandidateConfigContext:
    candidate_inputs = prepare_runtime_candidate_inputs(
        candidate=candidate,
        profiles=profiles,
        run_dir=run_dir,
    )
    return build_runtime_candidate_context_from_inputs(
        candidate=candidate,
        profiles=profiles,
        semantic_profile=semantic_profile,
        candidate_inputs=candidate_inputs,
        signals=signals,
    )


def refresh_runtime_candidate_context(
    *,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    semantic_profile: CandidateSemanticProfile | None,
    candidate_context: RuntimeCandidateConfigContext,
    signals: CandidateSearchSignals | None = None,
) -> RuntimeCandidateConfigContext:
    return build_runtime_candidate_context_from_inputs(
        candidate=candidate,
        profiles=profiles,
        semantic_profile=semantic_profile,
        candidate_inputs=candidate_context.candidate_inputs,
        signals=signals or candidate_context.signals,
    )


def _normalize_discovery_query_input_terms(
    values: list[object],
    *,
    limit: int,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = company_discovery_queries_module.normalize_business_hint(str(raw or "").strip())
        if not text:
            continue
        if text.casefold() in COMPANY_FIT_TOOL_TERMS:
            continue
        if company_discovery_queries_module.looks_like_role_phrase(text):
            continue
        if company_discovery_queries_module.looks_like_business_noise(text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def build_candidate_context_company_discovery_query_input(
    *,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    semantic_profile: CandidateSemanticProfile | None,
    candidate_inputs: RuntimeCandidateInputPrep,
) -> dict[str, object]:
    active_profiles = [profile for profile in profiles if profile.is_active]
    source_profiles = active_profiles if active_profiles else profiles
    desired_work_directions: list[object] = []
    if semantic_profile is not None and semantic_profile.is_usable():
        desired_work_directions.extend(semantic_profile.company_discovery_primary_anchors or [])
        desired_work_directions.extend(semantic_profile.company_discovery_secondary_anchors or [])
        desired_work_directions.extend(semantic_profile.job_fit_core_terms or [])
        desired_work_directions.extend(semantic_profile.job_fit_support_terms or [])
    for profile in source_profiles:
        desired_work_directions.extend(description_query_lines(profile.keyword_focus))
    desired_work_directions.extend(_non_empty_lines(candidate.target_directions, limit=8))
    location_preference = candidate_location_preference_text(
        base_location_struct=candidate.base_location_struct,
        preferred_locations_struct=candidate.preferred_locations_struct,
        base_location_text=candidate.base_location,
        preferred_locations_text=candidate.preferred_locations,
    )
    summary = ""
    avoid_business_areas: list[str] = []
    if semantic_profile is not None and semantic_profile.is_usable():
        summary = _truncate_text(semantic_profile.summary, 260)
        avoid_business_areas = _normalize_discovery_query_input_terms(
            list(semantic_profile.avoid_business_areas or []),
            limit=8,
        )
    if not summary:
        summary = _truncate_text(candidate.notes, 260)
    target_role_names: list[str] = []
    seen_target_roles: set[str] = set()
    for target_role in candidate_inputs.target_roles:
        display_name = str(
            target_role.get("displayName")
            or target_role.get("targetRoleText")
            or target_role.get("nameEn")
            or target_role.get("nameZh")
            or ""
        ).strip()
        if not display_name:
            continue
        key = display_name.casefold()
        if key in seen_target_roles:
            continue
        seen_target_roles.add(key)
        target_role_names.append(display_name)
        if len(target_role_names) >= 6:
            break
    return {
        "summary": summary,
        "targetRoles": target_role_names,
        "desiredWorkDirections": _normalize_discovery_query_input_terms(
            desired_work_directions,
            limit=18,
        ),
        "avoidBusinessAreas": avoid_business_areas,
        "locationPreference": _truncate_text(location_preference, 160),
    }


def _normalize_company_fit_terms(
    values: list[str],
    *,
    limit: int,
    allow_tools_only: bool,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = company_discovery_queries_module.normalize_business_hint(str(raw or "").strip())
        if not text:
            continue
        key = text.casefold()
        is_tool_only = key in COMPANY_FIT_TOOL_TERMS
        if is_tool_only and not allow_tools_only:
            continue
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _truncate_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _non_empty_lines(value: object, *, limit: int) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in str(value or "").splitlines():
        line = _truncate_text(raw_line, 96)
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def build_candidate_context_company_fit_terms(
    candidate_context: RuntimeCandidateConfigContext,
) -> dict[str, list[str]]:
    signals = getattr(candidate_context, "signals", None)
    profile = getattr(signals, "profile", None)
    semantic = getattr(signals, "semantic", None)
    if profile is None or semantic is None:
        return {"core": [], "support": []}
    return {
        "core": _normalize_company_fit_terms(
            [
                *getattr(profile, "target_roles", []),
                *getattr(profile, "keyword_focus_terms", []),
                *getattr(semantic, "job_fit_core_terms", []),
            ],
            limit=18,
            allow_tools_only=False,
        ),
        "support": _normalize_company_fit_terms(
            [
                *getattr(semantic, "job_fit_support_terms", []),
                *getattr(profile, "role_names", []),
            ],
            limit=14,
            allow_tools_only=False,
        ),
    }


def apply_runtime_candidate_context(
    *,
    candidate_config: dict,
    search_config: dict,
    sources_config: dict | None = None,
    candidate: CandidateRecord,
    candidate_context: RuntimeCandidateConfigContext,
    semantic_profile: CandidateSemanticProfile | None,
) -> None:
    update_section(
        candidate_config,
        {
            "resumePath": candidate_context.resume_path,
            "scopeProfiles": list(candidate_context.scope_profiles),
            "targetRoles": candidate_context.target_roles,
            "locationPreference": candidate_location_preference_text(
                base_location_struct=candidate.base_location_struct,
                preferred_locations_struct=candidate.preferred_locations_struct,
                base_location_text=candidate.base_location,
                preferred_locations_text=candidate.preferred_locations,
            ),
        },
    )
    if semantic_profile is not None and semantic_profile.is_usable():
        update_section(
            candidate_config,
            {
                "semanticProfile": semantic_profile.to_payload(),
            },
        )
    update_section(
        search_config,
        {
            "queries": [],
            "maxJobsPerQuery": min(50, max(10, int(search_config.get("maxJobsPerQuery", 30)))),
        },
    )
    if isinstance(sources_config, dict):
        update_section(
            sources_config,
            {
                "companyFitTerms": build_candidate_context_company_fit_terms(candidate_context),
            },
        )


def apply_runtime_model_override(
    *,
    search_config: dict,
    company_discovery_config: dict,
    analysis_config: dict,
    translation_config: dict,
    model_override: str,
    quality_model_override: str = "",
) -> None:
    fast_model = str(model_override or "").strip()
    quality_model = str(quality_model_override or "").strip() or fast_model
    if not fast_model and not quality_model:
        return
    if fast_model:
        update_section(search_config, {"model": fast_model})
        update_section(translation_config, {"model": fast_model})
    update_section(
        company_discovery_config,
        {
            "model": fast_model or quality_model,
            "fitModel": quality_model,
        },
    )
    update_section(
        analysis_config,
        {
            "model": quality_model,
            "postVerifyModel": quality_model,
            "preRankModel": fast_model or quality_model,
        },
    )


def apply_runtime_analysis_defaults(*, analysis_config: dict) -> None:
    analysis_config.setdefault("preFilterEnabled", False)
    analysis_config.setdefault("recommendScoreThreshold", 50)
    analysis_config.setdefault("targetRoleBindingMinScore", 50)
    analysis_config.setdefault("minTransferableScore", 50)
    analysis_work_cap = min(
        80,
        max(20, int(analysis_config.get("maxJobsToAnalyzePerRun", 60))),
    )
    update_section(
        analysis_config,
        {
            "maxJobsToAnalyzePerRun": analysis_work_cap,
            "jdFetchMaxJobsPerRun": analysis_work_cap,
            "postVerifyMaxJobsPerRun": analysis_work_cap,
        },
    )


def populate_runtime_config_common(
    runtime_mirror: Any,
    *,
    candidate_config: dict,
    search_config: dict,
    sources_config: dict,
    company_discovery_config: dict,
    analysis_config: dict,
    translation_config: dict,
    fetch_config: dict,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    run_dir: Path,
    query_rotation_seed: int,
    semantic_profile: CandidateSemanticProfile | None,
    model_override: str,
    quality_model_override: str = "",
    signals: CandidateSearchSignals | None = None,
    candidate_context: RuntimeCandidateConfigContext | None = None,
) -> RuntimeCandidateConfigContext:
    resolved_candidate_context = candidate_context or build_runtime_candidate_context(
        candidate=candidate,
        profiles=profiles,
        run_dir=run_dir,
        semantic_profile=semantic_profile,
        signals=signals,
    )
    apply_runtime_candidate_context(
        candidate_config=candidate_config,
        search_config=search_config,
        sources_config=sources_config,
        candidate=candidate,
        candidate_context=resolved_candidate_context,
        semantic_profile=semantic_profile,
    )
    apply_runtime_model_override(
        search_config=search_config,
        company_discovery_config=company_discovery_config,
        analysis_config=analysis_config,
        translation_config=translation_config,
        model_override=model_override,
        quality_model_override=quality_model_override,
    )
    apply_runtime_analysis_defaults(analysis_config=analysis_config)
    update_section(fetch_config, {"timeoutMs": HTTP_REQUEST_TIMEOUT_MS})
    update_section(
        company_discovery_config,
        {
            "companyDiscoveryInput": resolved_candidate_context.discovery_query_input,
        },
    )
    return resolved_candidate_context


def apply_runtime_config_resume_pending_stage(*, sources_config: dict, company_discovery_config: dict, direct_job_discovery_config: dict, analysis_config: dict, output_config: dict) -> None:
    update_section(
        sources_config,
        {
            "enableWebSearch": False,
            "enableCompanySearchFallback": False,
        },
    )
    update_section(
        company_discovery_config,
        {
            "enableAutoDiscovery": False,
        },
    )
    update_section(
        direct_job_discovery_config,
        {
            "enabled": False,
        },
    )
    update_section(
        analysis_config,
        {
            "scoringUseWebSearch": False,
            # Resume/finalize should focus on clearing queued jobs, not spend
            # extra budget on the optional post-verify pass.
            "postVerifyEnabled": False,
            "postVerifyUseWebSearch": False,
            "postVerifyRequireChecked": False,
        },
    )
def apply_runtime_config_main_stage(*, search_config: dict, sources_config: dict, company_discovery_config: dict, direct_job_discovery_config: dict, analysis_config: dict, translation_config: dict, adaptive_search_config: dict, fetch_config: dict, output_config: dict) -> None:
    adaptive_strategy = runtime_strategy.derive_adaptive_runtime_strategy(adaptive_search_config)
    update_section(
        sources_config,
        {
            "enableWebSearch": False,
            "maxCompaniesPerRun": adaptive_strategy["max_companies_per_run"],
            "maxJobsPerCompany": adaptive_strategy["max_jobs_per_company"],
            "maxJobLinksPerCompany": JOB_LINK_HARD_CAP_PER_COMPANY,
            "enableCompanySearchFallback": True,
            "companyRotationIntervalDays": adaptive_strategy["company_rotation_interval_days"],
        },
    )
    update_section(
        company_discovery_config,
        {
            "enableAutoDiscovery": True,
            "maxCompaniesPerCall": 5,
        },
    )
    update_section(
        direct_job_discovery_config,
        {
            "enabled": True,
            "maxJobsPerRound": runtime_strategy.positive_int(
                direct_job_discovery_config.get("maxJobsPerRound"),
                10,
            ),
            "timeoutSeconds": runtime_strategy.positive_int(
                direct_job_discovery_config.get("timeoutSeconds"),
                600,
            ),
            "companyUpsertMinScore": runtime_strategy.positive_int(
                direct_job_discovery_config.get("companyUpsertMinScore"),
                60,
                minimum=0,
            ),
        },
    )
    update_section(search_config, {"maxJobsPerQuery": adaptive_strategy["max_jobs_per_query"]})
    update_section(
        analysis_config,
        {
            "scoringUseWebSearch": False,
            # Post-verify remains opt-in in the main stage; default runs should
            # not spend extra web-search budget on a second verification pass.
            "postVerifyEnabled": False,
            "postVerifyUseWebSearch": False,
            "postVerifyRequireChecked": False,
            "maxJobsToAnalyzePerRun": adaptive_strategy["analysis_work_cap"],
            "jdFetchMaxJobsPerRun": adaptive_strategy["analysis_work_cap"],
            "postVerifyMaxJobsPerRun": adaptive_strategy["analysis_work_cap"],
        },
    )
    update_section(translation_config, {"enable": False})
    update_section(
        output_config,
        {
            "trackerXlsxPath": str(output_config.get("trackerXlsxPath") or "./jobs_recommended.xlsx"),
            "xlsxPath": str(output_config.get("xlsxPath") or "./jobs.xlsx"),
            "recommendedXlsxPath": str(output_config.get("recommendedXlsxPath") or "./jobs_recommended.xlsx"),
        },
    )


def build_company_sources_only_runtime_config(runtime_config: dict) -> dict:
    config = copy.deepcopy(runtime_config)
    ensure_dict(config, "companyDiscovery")["enableAutoDiscovery"] = False
    return config


def build_runtime_config(
    runtime_mirror: Any,
    base_config: dict,
    candidate: CandidateRecord,
    profiles: list[SearchProfileRecord],
    run_dir: Path,
    query_rotation_seed: int = 0,
    semantic_profile: CandidateSemanticProfile | None = None,
    model_override: str = "",
    quality_model_override: str = "",
    pipeline_stage: str = "main",
    signals: CandidateSearchSignals | None = None,
    candidate_context: RuntimeCandidateConfigContext | None = None,
) -> dict:
    config = copy.deepcopy(base_config)
    sections = runtime_config_sections(config)
    runtime_strategy.compact_adaptive_search_config(sections.adaptive_search)
    populate_runtime_config_common(
        runtime_mirror,
        candidate_config=sections.candidate,
        search_config=sections.search,
        sources_config=sections.sources,
        company_discovery_config=sections.company_discovery,
        analysis_config=sections.analysis,
        translation_config=sections.translation,
        fetch_config=sections.fetch,
        candidate=candidate,
        profiles=profiles,
        run_dir=run_dir,
        query_rotation_seed=query_rotation_seed,
        semantic_profile=semantic_profile,
        model_override=model_override,
        quality_model_override=quality_model_override,
        signals=signals,
        candidate_context=candidate_context,
    )
    if pipeline_stage == "resume_pending":
        apply_runtime_config_resume_pending_stage(
            sources_config=sections.sources,
            company_discovery_config=sections.company_discovery,
            direct_job_discovery_config=sections.direct_job_discovery,
            analysis_config=sections.analysis,
            output_config=sections.output,
        )
        runtime_strategy.compact_adaptive_search_config(sections.adaptive_search)
        return config
    apply_runtime_config_main_stage(
        search_config=sections.search,
        sources_config=sections.sources,
        company_discovery_config=sections.company_discovery,
        direct_job_discovery_config=sections.direct_job_discovery,
        analysis_config=sections.analysis,
        translation_config=sections.translation,
        adaptive_search_config=sections.adaptive_search,
        fetch_config=sections.fetch,
        output_config=sections.output,
    )
    runtime_strategy.compact_adaptive_search_config(sections.adaptive_search)
    return config


__all__ = [
    "DISCOVERY_COMPANIES_PER_QUERY_CAP",
    "HTTP_REQUEST_TIMEOUT_MS",
    "JOB_LINK_HARD_CAP_PER_COMPANY",
    "RuntimeCandidateInputPrep",
    "RuntimeCandidateConfigContext",
    "RuntimeConfigSections",
    "RuntimeModelOverrides",
    "apply_runtime_analysis_defaults",
    "apply_runtime_candidate_context",
    "apply_runtime_config_main_stage",
    "apply_runtime_config_resume_pending_stage",
    "apply_runtime_model_override",
    "build_candidate_context_company_discovery_query_input",
    "build_candidate_context_company_fit_terms",
    "build_company_sources_only_runtime_config",
    "build_runtime_candidate_context",
    "build_runtime_candidate_context_from_inputs",
    "build_runtime_config",
    "build_runtime_env",
    "build_target_roles_payload",
    "coerce_bool",
    "ensure_dict",
    "load_base_config",
    "prepare_runtime_candidate_inputs",
    "refresh_runtime_candidate_context",
    "resolve_model_override",
    "resolve_model_overrides",
    "resolve_resume_path",
    "resolve_scope_profiles",
    "runtime_config_sections",
    "update_section",
]
