from __future__ import annotations

from pathlib import Path

from ..repositories.candidates import CandidateRecord
from ..repositories.profiles import SearchProfileRecord
from ...app.context import AppContext
from ...ai.role_recommendations import (
    encode_bilingual_description,
    encode_bilingual_role_name,
)


DEMO_SEED_MARKER_KEY = "bootstrap_demo_seed_state"
DEMO_SEED_VERSION = "v1"


def ensure_demo_candidate_seeded(context: AppContext) -> bool:
    if context.candidates.count() > 0:
        return False

    resume_path = _ensure_demo_resume_file(context.paths.data_dir / "demo_candidate_resume.md")
    preferred_locations = "Munich, Germany\nBerlin, Germany\nRemote"
    candidate_id = context.candidates.save(
        CandidateRecord(
            candidate_id=None,
            name="Demo Candidate",
            email="demo.candidate@example.com",
            base_location="Munich, Germany",
            preferred_locations=preferred_locations,
            target_directions=(
                "Hydrogen Systems Integration Engineer\n"
                "MBSE & Requirements Verification Engineer\n"
                "Digital Twin & PHM Engineer (Energy Systems)"
            ),
            notes=(
                "Demo account for onboarding and feature testing. "
                "Open to EU relocation and remote-friendly roles."
            ),
            active_resume_path=str(resume_path.resolve()),
            created_at="",
            updated_at="",
        )
    )

    _save_demo_profile(
        context=context,
        candidate_id=candidate_id,
        location_preference=preferred_locations,
        scope_profile="hydrogen_mainline",
        role_name_en="Hydrogen Systems Integration Engineer (Electrolyzer/Fuel Cell)",
        role_name_zh="氢能系统集成工程师（电解槽/燃料电池）",
        description_en=(
            "Drive system-level integration across electrolyzer and fuel-cell subsystems, "
            "from requirements to validation planning. "
            "Coordinate interfaces with controls, testing, and reliability teams to improve launch readiness."
        ),
        description_zh=(
            "负责电解槽与燃料电池子系统的系统级集成，从需求到验证计划闭环。"
            "协同控制、测试与可靠性团队推进接口对齐，提高项目落地效率。"
        ),
        company_focus="Electrolyzer OEMs\nFuel cell system integrators\nIndustrial gas companies",
        company_keyword_focus="electrolyzer careers\nfuel cell systems jobs\nhydrogen integration engineer",
        queries=[
            "hydrogen systems integration engineer job germany",
            "electrolyzer system engineer careers europe",
            "fuel cell system integration engineer job",
        ],
    )
    _save_demo_profile(
        context=context,
        candidate_id=candidate_id,
        location_preference=preferred_locations,
        scope_profile="adjacent_mbse",
        role_name_en="MBSE & Requirements Verification Engineer (Energy Systems)",
        role_name_zh="MBSE与需求验证工程师（能源系统）",
        description_en=(
            "Own requirements engineering, traceability, and verification strategy for complex energy systems. "
            "Apply SysML/MBSE methods to connect architecture decisions with integration and test evidence."
        ),
        description_zh=(
            "负责复杂能源系统的需求工程、可追踪性与验证策略。"
            "使用SysML/MBSE方法将架构决策与集成测试证据打通。"
        ),
        company_focus="Energy equipment manufacturers\nAerospace & mobility systems\nIndustrial automation",
        company_keyword_focus="MBSE engineer\nrequirements verification engineer\nSysML systems engineer",
        queries=[
            "MBSE requirements verification engineer job germany",
            "SysML systems engineer energy systems careers",
            "systems requirements traceability engineer europe",
        ],
    )
    _save_demo_profile(
        context=context,
        candidate_id=candidate_id,
        location_preference=preferred_locations,
        scope_profile="adjacent_mbse",
        role_name_en="Digital Twin & PHM Engineer (Energy Equipment)",
        role_name_zh="数字孪生与PHM工程师（能源装备）",
        description_en=(
            "Build digital-twin and condition-monitoring workflows for energy assets to support prognosis and maintenance planning. "
            "Translate operational data into reliability insights and actionable engineering improvements."
        ),
        description_zh=(
            "构建能源装备的数字孪生与状态监测流程，支持寿命预测与维护决策。"
            "将运行数据转化为可靠性洞察，并落地到工程改进动作。"
        ),
        company_focus="Grid technology companies\nPower electronics & storage\nIndustrial digital teams",
        company_keyword_focus="digital twin engineer\nPHM engineer\ncondition monitoring engineer",
        queries=[
            "digital twin PHM engineer energy job germany",
            "condition monitoring engineer power systems careers",
            "asset health prognostics engineer europe",
        ],
    )

    context.settings.set_value(DEMO_SEED_MARKER_KEY, DEMO_SEED_VERSION)
    return True


def _save_demo_profile(
    *,
    context: AppContext,
    candidate_id: int,
    location_preference: str,
    scope_profile: str,
    role_name_en: str,
    role_name_zh: str,
    description_en: str,
    description_zh: str,
    company_focus: str,
    company_keyword_focus: str,
    queries: list[str],
) -> None:
    role_name_i18n = encode_bilingual_role_name(role_name_zh, role_name_en)
    description_i18n = encode_bilingual_description(description_zh, description_en)
    context.profiles.save(
        SearchProfileRecord(
            profile_id=None,
            candidate_id=candidate_id,
            name=role_name_en,
            scope_profile=scope_profile,
            target_role=role_name_en,
            location_preference=location_preference,
            company_focus=company_focus,
            company_keyword_focus=company_keyword_focus,
            role_name_i18n=role_name_i18n,
            keyword_focus=description_i18n,
            is_active=True,
            queries=queries,
        )
    )


def _ensure_demo_resume_file(path: Path) -> Path:
    if path.exists() and path.is_file():
        return path
    content = (
        "# Demo Candidate Resume\n\n"
        "## Summary\n"
        "Systems-oriented engineer focused on hydrogen and energy equipment domains. "
        "Experience includes system integration, requirements verification, and reliability workflows.\n\n"
        "## Core Skills\n"
        "- Systems integration and interface management\n"
        "- Requirements engineering and traceability\n"
        "- Verification & validation planning\n"
        "- Digital twin, PHM, and condition monitoring\n\n"
        "## Preferred Domains\n"
        "- Electrolyzer and fuel-cell systems\n"
        "- Energy equipment reliability\n"
        "- Model-based systems engineering\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
