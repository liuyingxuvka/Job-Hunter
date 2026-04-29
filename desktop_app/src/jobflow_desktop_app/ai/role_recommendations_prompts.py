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
        ("Career and education history:", profile.career_and_education_history, None),
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
    if profile.career_and_education_history:
        lines.extend(["Career and education history:", profile.career_and_education_history])

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
        parts.extend(
            [
                "Before proposing roles, internally group the existing roles by broad job family and functional intent.",
                "Do not repeat existing roles, and do not return near-duplicate variants with the same functional intent.",
                "Do not treat a small wording change, seniority change, acronym swap, or narrower/wider rewrite as a new role.",
                "If existing roles are over-specific or AI-synthetic, do not imitate their naming style. Move to a different market hiring lane or return fewer roles.",
                "If an existing over-specific role is clearly a narrow version of a broader market title, treat that broader title as already covered.",
                "Do not propose a cleaner market-facing rewrite of an existing role as a new role; the new role must have different day-to-day hiring intent.",
                "A broader market title is acceptable only when it would retrieve a materially different set of real job postings than the existing roles.",
                "For every proposed role, the distinctness_check must name the different hiring lane it opens, not just say that wording is different.",
                "If you cannot explain the different hiring lane in one concrete sentence, discard that role.",
            ]
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
                "The role-mix target is subordinate to distinctness. Returning 0 or 1 role is better than filling a bucket with a repeat.",
            ]
        )
    else:
        parts.append("Please return up to 3 role directions.")

    parts.extend(
        [
            "Product context:",
            "These target roles are saved as search lenses for finding real, currently open jobs in the market.",
            "They are not a personalized label for the candidate's full research history or a compact abstract of every skill.",
            "The role title should be the market lane we can search on job boards and employer career pages; detailed technologies, acronyms, methods, and fit evidence belong in the descriptions.",
            "Think like a recruiter choosing job families to search, not like a researcher naming a thesis topic.",
            "Generation workflow:",
            "1. Read the candidate context and identify broad market role families that fit.",
            "2. Read existing roles and remove any family already covered, including close synonyms and nested variants.",
            "3. If a remaining idea is just a better market wording for an existing role, discard it instead of returning it.",
            "4. For each remaining idea, choose the simplest real-market title that still carries one clear domain or function qualifier.",
            "5. Keep only roles that open a distinct hiring lane from existing roles and from each other. Return fewer roles if necessary.",
            "The different hiring lane must be explainable as a different day-to-day job family or employer search cluster.",
            "Role-mix and count targets are subordinate to distinctness. Returning 0 or 1 role is better than filling a bucket with a repeat.",
            "Each role must include scope_profile as one of: core, adjacent, exploratory.",
            "Scope decision rubric:",
            "- core: the day-to-day work is a direct continuation of the candidate's demonstrated mainline work. The main job is still close to the candidate's strongest responsibilities, methods, and engineering problem type.",
            "- adjacent: the domain or product context is familiar, but the main job function changes. It is a credible transition into nearby product, application, validation, systems, customer-facing, or operational work.",
            "- exploratory: the role is a farther repositioning that still has a real anchor in the candidate's domain, customers, tools, or engineering workflow. It should require a larger career narrative shift than adjacent.",
            "Do not label a role core just because it contains the right domain words. If the daily function changes from modeling/analysis into application engineering, product ownership, sales support, program leadership, or broad systems coordination, it is usually adjacent or exploratory.",
            "Exploratory does not mean unrelated. Keep exploratory roles anchored in the candidate's demonstrated domain, technology stack, customer/problem context, or product-development workflow.",
            "Do not jump to battery-only, generic consulting, corporate strategy, or broad energy transition roles unless the candidate evidence explicitly supports that lane.",
            "role.name_en must be a concise, market-facing job-board title that could plausibly appear on LinkedIn, Indeed, or a company careers page.",
            "Keep role.name_en short: prefer 3-6 meaningful words, excluding seniority words such as Senior, Lead, or Principal.",
            "Do not pack research topics, methods, product details, and every keyword into the title. Put technical specificity in role.description_zh and role.description_en instead.",
            "Good role.name_en shape examples, not candidate-specific recommendations: <Domain> Performance Engineer; <Technology> Test Engineer; Reliability Engineer, <Product System>; Modeling & Simulation Engineer, <Domain Systems>; Application Engineer, <Product Systems>.",
            "Bad role.name_en examples: LT-PEM Fuel Cell Degradation Lifetime Multi-Physics Modeling Specialist; Advanced Hydrogen Energy System Dynamics and Durability Optimization Engineer; Data-Driven Prognostics and Health Monitoring Engineer for LT-PEM Fuel Cells.",
            "Treat examples as title-shape examples only. Do not copy an example if the existing roles already cover that hiring lane.",
            "Avoid generic role titles like Engineer/Manager/Specialist without domain qualifiers.",
            "Avoid broad titles like Systems Engineer / Software Engineer / Project Manager unless strongly specialized.",
            "Prefer titles with one clear domain or work context, not a chain of all possible contexts.",
            "Avoid Researcher, Scientist, or Architect titles unless that exact title family is common in job postings for this market lane.",
            "Across one recommendation round, cover different practical work settings such as modeling, test and validation, systems engineering, reliability, digital twin, or hydrogen systems instead of returning several long variants of the same idea.",
            "Do not return multiple roles that differ only by LT-PEM vs PEM, fuel cell vs electrolyzer, modeling vs degradation modeling, or engineer vs scientist unless the real hiring market treats them as separate job families.",
            "If existing roles already cover modeling, degradation/lifetime, reliability, health monitoring, digital twin, control strategy, test design, or validation as the main hiring intent, do not return a broad rewrite of that same lane.",
            "Do not propose a Systems Engineer, Test Engineer, Validation Engineer, Reliability Engineer, Modeling Engineer, or Simulation Engineer lane if existing roles already cover that same system, test, validation, reliability, modeling, or simulation intent under a narrower title.",
            "The role must be able to retrieve a different cluster of real job postings, not just the same postings under a more polished title.",
            "market_search_rationale should identify the job-board query lane this title enables.",
            "distinctness_check should explicitly contrast the proposed lane against the closest existing role or against another role in this batch.",
            "Prioritize the candidate's demonstrated domain continuity from resume, notes, and self-described directions.",
            "Do not over-index on isolated software/tool keywords if they are not central to the candidate's main work.",
            "Treat any inferred scope label as soft evidence only; do not force the candidate into a legacy default domain if the resume, notes, and self-described directions do not clearly support it.",
            "Provide both role.name_en and role.name_zh.",
            "role.description_zh must be Chinese and 2-3 sentences with concrete details.",
            "role.description_en must be English and 2-3 sentences with concrete details.",
            "Also include market_search_rationale as one short English sentence and distinctness_check as one short English sentence for each role. These audit fields are for quality review and should not contain long reasoning.",
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
        "Scope decision rubric:",
        "- core: direct continuation of the candidate's strongest demonstrated responsibilities, methods, and engineering problem type.",
        "- adjacent: familiar domain or product context, but a different main function such as application, validation, systems, customer-facing, product, or operational work.",
        "- exploratory: farther repositioning with a real anchor in the candidate's domain, customers, tools, or engineering workflow, requiring a larger career narrative shift.",
        "The title and descriptions should make the selected scope visible. Do not describe an exploratory role as if it were a direct core continuation, and do not make a core role sound like a broad career pivot.",
        "The saved role type is determined by the user's selection, so the content should support that selection rather than override it.",
        "Keep the refined role close to the candidate's demonstrated main domain instead of over-weighting isolated tool keywords.",
        "Keep role.name_en concise and searchable. If the user-provided title is already market-facing, preserve it or add only one short qualifier.",
        "Do not add extra methods, acronyms, products, or proof points to role.name_en just to demonstrate fit. Put that evidence in the descriptions.",
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
