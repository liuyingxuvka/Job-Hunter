from __future__ import annotations

from ..prompt_assets import load_prompt_asset
from ..db.repositories.candidates import CandidateRecord
from .role_recommendations_models import CandidateSemanticProfile, ResumeReadResult, RoleRecommendationMixPlan
from .role_recommendations_resume import (
    load_resume_excerpt_result,
    manual_background_summary,
)


SYSTEM_PROMPT = load_prompt_asset("ai", "system_prompt.txt")
TRANSLATE_PROMPT = load_prompt_asset("ai", "translate_prompt.txt")
ROLE_NAME_TRANSLATE_PROMPT = load_prompt_asset("ai", "role_name_translate_prompt.txt")
JOB_DISPLAY_I18N_PROMPT = load_prompt_asset("ai", "job_display_i18n_prompt.txt")
MANUAL_ROLE_ENRICH_PROMPT = load_prompt_asset("ai", "manual_role_enrich_prompt.txt")
CANDIDATE_SEMANTIC_PROFILE_PROMPT = load_prompt_asset("ai", "candidate_semantic_profile_prompt.txt")


def build_candidate_semantic_profile_prompt(
    candidate: CandidateRecord,
    *,
    resume_result: ResumeReadResult | None = None,
) -> str:
    resolved_resume = resume_result or load_resume_excerpt_result(candidate.active_resume_path, max_chars=12000)
    background_summary = manual_background_summary(candidate)
    parts = [
        f"Candidate name: {candidate.name}",
        f"Current location: {candidate.base_location or 'N/A'}",
        "Preferred locations:",
        candidate.preferred_locations.strip() or "N/A",
        "Future target directions (self-described):",
        candidate.target_directions.strip() or "N/A",
        "Professional background summary (manual):",
        background_summary or "N/A",
        "Important extraction intent:",
        "- Extract demonstrated business/technical experience from the resume and manual summary.",
        "- Separately extract future-oriented target directions from the self-described target directions field.",
        "- Keep the phrase library target-direction first, then background, then supporting capabilities, then adjacent exploration.",
        "- Prefer English business domains, product/platform areas, technical themes, and credible adjacent directions.",
        "- Do not collapse everything into generic job titles.",
        "- Build a reusable English phrase library for company discovery and job-search planning.",
    ]
    if candidate.active_resume_path.strip() and resolved_resume.text:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume excerpt:",
                resolved_resume.text,
            ]
        )
    elif candidate.active_resume_path.strip() and resolved_resume.error:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume read status:",
                resolved_resume.error,
                "If the resume text is unavailable, rely on the manual professional background summary and target directions.",
            ]
        )
    parts.append("Return strict JSON only.")
    return "\n".join(parts)


def semantic_profile_prompt_lines(profile: CandidateSemanticProfile | None) -> list[str]:
    if profile is None or not profile.is_usable():
        return []
    sections: list[tuple[str, tuple[str, ...] | str, int | None]] = [
        ("AI semantic summary:", profile.summary, None),
        ("AI extracted company-discovery primary anchors:", profile.company_discovery_primary_anchors, 10),
        ("AI extracted company-discovery secondary anchors:", profile.company_discovery_secondary_anchors, 8),
        ("AI extracted job-fit core terms:", profile.job_fit_core_terms, 16),
        ("AI extracted job-fit support terms:", profile.job_fit_support_terms, 12),
    ]
    lines: list[str] = []
    lines.extend(
        [
            "AI English business phrase library size:",
            f"- company-discovery phrases: {len(profile.company_discovery_phrase_library_en())}",
            f"- job-search phrases: {len(profile.job_search_phrase_library_en())}",
        ]
    )
    for label, value, limit in sections:
        if isinstance(value, tuple):
            if not value:
                continue
            lines.append(label)
            shown = value[: limit or len(value)]
            lines.extend(f"- {item}" for item in shown)
            if limit is not None and len(value) > limit:
                lines.append(f"- ... ({len(value) - limit} more)")
            continue
        if str(value or "").strip():
            lines.extend([label, str(value).strip()])
    if profile.avoid_business_areas:
        lines.append("AI extracted avoid / misleading directions:")
        shown_avoid = profile.avoid_business_areas[:8]
        lines.extend(f"- {item}" for item in shown_avoid)
        if len(profile.avoid_business_areas) > 8:
            lines.append(f"- ... ({len(profile.avoid_business_areas) - 8} more)")
    return lines


def compact_role_recommendation_semantic_profile_lines(
    profile: CandidateSemanticProfile | None,
) -> list[str]:
    if profile is None or not profile.is_usable():
        return []
    lines: list[str] = []
    if profile.summary:
        lines.extend(["AI semantic summary:", profile.summary])

    sections: list[tuple[str, tuple[str, ...], int]] = [
        ("AI extracted company-discovery primary anchors:", profile.company_discovery_primary_anchors, 8),
        ("AI extracted company-discovery secondary anchors:", profile.company_discovery_secondary_anchors, 6),
        ("AI extracted job-fit core terms:", profile.job_fit_core_terms, 10),
        ("AI extracted job-fit support terms:", profile.job_fit_support_terms, 8),
    ]
    for label, values, limit in sections:
        if not values:
            continue
        lines.append(label)
        lines.extend(f"- {item}" for item in values[:limit])
        if len(values) > limit:
            lines.append(f"- ... ({len(values) - limit} more)")

    lines.append(
        "Treat these phrases as soft evidence only. Do not let them override the resume, notes, or explicit target directions."
    )
    return lines


def build_role_recommendation_prompt(
    candidate: CandidateRecord,
    existing_roles: list[tuple[str, str]] | None = None,
    resume_result: ResumeReadResult | None = None,
    semantic_profile: CandidateSemanticProfile | None = None,
    mix_plan: RoleRecommendationMixPlan | None = None,
) -> str:
    resolved_resume = resume_result or load_resume_excerpt_result(candidate.active_resume_path)
    resume_excerpt = resolved_resume.text
    background_summary = manual_background_summary(candidate)
    normalized_existing: list[tuple[str, str]] = []
    for role in existing_roles or []:
        if not isinstance(role, tuple) or len(role) != 2:
            continue
        role_name = str(role[0] or "").strip()
        role_desc = str(role[1] or "").strip()
        if not role_name:
            continue
        normalized_existing.append((role_name, role_desc))

    parts = [
        f"Candidate name: {candidate.name}",
        f"Current location: {candidate.base_location or 'N/A'}",
        "Preferred locations:",
        candidate.preferred_locations.strip() or "N/A",
        "Current target directions (self-described):",
        candidate.target_directions.strip() or "N/A",
        "Professional background summary (manual):",
        background_summary or "N/A",
    ]
    parts.extend(compact_role_recommendation_semantic_profile_lines(semantic_profile))

    if candidate.active_resume_path.strip() and resume_excerpt:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume excerpt:",
                resume_excerpt,
            ]
        )
    elif candidate.active_resume_path.strip() and resolved_resume.error:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume read status:",
                "Resume text is unavailable. Use the manual professional background summary and the other candidate context as the primary source of truth.",
            ]
        )

    if normalized_existing:
        parts.append("existing roles (must not repeat):")
        for role_name, role_desc in normalized_existing:
            if role_desc:
                parts.append(f"- {role_name}: {role_desc}")
            else:
                parts.append(f"- {role_name}")
        parts.append(
            "Do not repeat existing roles, and do not return near-duplicate variants with the same functional intent."
        )
    else:
        parts.append("There are currently no saved target roles yet.")

    if mix_plan is not None:
        parts.extend(
            [
                "Current role-mix status:",
                f"- total saved roles: {mix_plan.current_total} / {mix_plan.total_cap}",
                f"- core roles: {mix_plan.current_core}",
                f"- adjacent roles: {mix_plan.current_adjacent}",
                f"- exploratory roles: {mix_plan.current_exploratory}",
                "This round recommendation target:",
                f"- return up to {mix_plan.request_total} NEW roles total",
                f"- core to add this round: {mix_plan.request_core}",
                f"- adjacent to add this round: {mix_plan.request_adjacent}",
                f"- exploratory to add this round: {mix_plan.request_exploratory}",
                "Keep the overall role list trending toward a 3:2:1 ratio of core : adjacent : exploratory roles.",
                "If the candidate already has enough of one type this round, do not return more of that type.",
                "If there are not enough good ideas for one bucket, return fewer roles instead of padding with weak or repetitive ideas.",
            ]
        )
    else:
        parts.append("Please return up to 3 role directions.")

    parts.extend(
        [
            "Each role must include scope_profile as one of: core, adjacent, exploratory.",
            "core means highly aligned with the candidate's demonstrated mainline experience and explicit target directions.",
            "adjacent means a credible transition role that is related, but not the most direct continuation.",
            "exploratory means broader or more experimental, but still realistically worth trying.",
            "role.name_en should be specific, not overly generic.",
            "Avoid generic role titles like Engineer/Manager/Specialist without domain qualifiers.",
            "Avoid broad titles like Systems Engineer / Software Engineer / Project Manager unless strongly specialized.",
            "Prefer titles that include concrete domain or method context.",
            "Prioritize the candidate's demonstrated domain continuity from resume, notes, and self-described directions.",
            "Do not over-index on isolated software/tool keywords if they are not central to the candidate's main work.",
            "Treat any inferred scope label as soft evidence only; do not force the candidate into a legacy default domain if the resume, notes, and self-described directions do not clearly support it.",
            "Provide both role.name_en and role.name_zh.",
            "role.description_zh must be Chinese and 2-3 sentences with concrete details.",
            "role.description_en must be English and 2-3 sentences with concrete details.",
            "Return strict JSON only.",
        ]
    )
    return "\n".join(parts)


def build_manual_role_enrich_prompt(
    candidate: CandidateRecord,
    *,
    role_name: str,
    rough_description: str,
    desired_scope_profile: str,
    resume_result: ResumeReadResult | None = None,
    semantic_profile: CandidateSemanticProfile | None = None,
) -> str:
    resolved_resume = resume_result or load_resume_excerpt_result(candidate.active_resume_path, max_chars=3500)
    resume_excerpt = resolved_resume.text
    background_summary = manual_background_summary(candidate)
    scope_label = {
        "core": "core",
        "adjacent": "adjacent",
        "exploratory": "exploratory",
    }.get(str(desired_scope_profile or "").strip().lower(), "core")
    user_prompt_parts = [
        f"Candidate name: {candidate.name}",
        f"Current location: {candidate.base_location or 'N/A'}",
        "Preferred locations:",
        candidate.preferred_locations.strip() or "N/A",
        "Current target directions:",
        candidate.target_directions.strip() or "N/A",
        "Professional background summary (manual):",
        background_summary or "N/A",
        "User provided role intent:",
        f"- Role name: {str(role_name or '').strip()}",
        f"- Rough description: {str(rough_description or '').strip() or 'N/A'}",
        f"- Required scope_profile: {scope_label}",
        "The returned role must stay inside that requested scope_profile. Do not silently switch it to another bucket.",
        "Use the requested scope to decide how conservative or exploratory the refinement should be.",
        "The saved role type is determined by the user's selection, so the content should support that selection rather than override it.",
        "Keep the refined role close to the candidate's demonstrated main domain instead of over-weighting isolated tool keywords.",
    ]
    user_prompt_parts.extend(compact_role_recommendation_semantic_profile_lines(semantic_profile))
    if candidate.active_resume_path.strip() and resume_excerpt:
        user_prompt_parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume excerpt:",
                resume_excerpt,
            ]
        )
    elif candidate.active_resume_path.strip() and resolved_resume.error:
        user_prompt_parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume read status:",
                "Resume text is unavailable. Use the manual professional background summary and the other candidate context as the primary source of truth.",
            ]
        )
    user_prompt_parts.append("Return strict JSON only.")
    return "\n".join(user_prompt_parts)


__all__ = [
    "CANDIDATE_SEMANTIC_PROFILE_PROMPT",
    "JOB_DISPLAY_I18N_PROMPT",
    "MANUAL_ROLE_ENRICH_PROMPT",
    "ROLE_NAME_TRANSLATE_PROMPT",
    "SYSTEM_PROMPT",
    "TRANSLATE_PROMPT",
    "build_candidate_semantic_profile_prompt",
    "build_manual_role_enrich_prompt",
    "build_role_recommendation_prompt",
    "compact_role_recommendation_semantic_profile_lines",
    "semantic_profile_prompt_lines",
]
