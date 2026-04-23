from __future__ import annotations

from .client import DEFAULT_OPENAI_RESPONSES_API_URL
from .role_recommendations_models import (
    CandidateSemanticProfile,
    RoleRecommendationMixPlan,
    ResumeReadResult,
    RoleRecommendationError,
    TargetRoleSuggestion,
)
from .role_recommendations_parse import parse_refined_manual_role, parse_role_suggestions
from .role_recommendations_profile import (
    SEMANTIC_PROFILE_SCHEMA_VERSION,
    build_candidate_semantic_profile_source_signature,
    load_candidate_semantic_profile_cache,
    parse_candidate_semantic_profile,
    save_candidate_semantic_profile_cache,
)
from .role_recommendations_prompts import (
    CANDIDATE_SEMANTIC_PROFILE_PROMPT,
    JOB_DISPLAY_I18N_PROMPT,
    MANUAL_ROLE_ENRICH_PROMPT,
    ROLE_NAME_TRANSLATE_PROMPT,
    SYSTEM_PROMPT,
    TRANSLATE_PROMPT,
    build_candidate_semantic_profile_prompt,
    build_role_recommendation_prompt,
    compact_role_recommendation_semantic_profile_lines,
)
from .role_recommendations_resume import (
    build_missing_background_error,
    load_resume_excerpt,
    load_resume_excerpt_result,
    manual_background_summary,
)
from .role_recommendations_service import OpenAIRoleRecommendationService
from .role_recommendations_text import (
    ADJACENT_SCOPE,
    CORE_SCOPE,
    EXPLORATORY_SCOPE,
    decode_bilingual_description,
    decode_bilingual_role_name,
    description_for_prompt,
    description_query_lines,
    encode_bilingual_description,
    encode_bilingual_role_name,
    infer_scope_profile,
    is_generic_role_name,
    normalize_scope_profile,
    role_name_query_lines,
    select_bilingual_description,
    select_bilingual_role_name,
)


__all__ = [
    "CANDIDATE_SEMANTIC_PROFILE_PROMPT",
    "JOB_DISPLAY_I18N_PROMPT",
    "ADJACENT_SCOPE",
    "DEFAULT_OPENAI_RESPONSES_API_URL",
    "CandidateSemanticProfile",
    "CORE_SCOPE",
    "EXPLORATORY_SCOPE",
    "MANUAL_ROLE_ENRICH_PROMPT",
    "OpenAIRoleRecommendationService",
    "ROLE_NAME_TRANSLATE_PROMPT",
    "RoleRecommendationMixPlan",
    "ResumeReadResult",
    "RoleRecommendationError",
    "SEMANTIC_PROFILE_SCHEMA_VERSION",
    "SYSTEM_PROMPT",
    "TRANSLATE_PROMPT",
    "TargetRoleSuggestion",
    "build_candidate_semantic_profile_prompt",
    "build_candidate_semantic_profile_source_signature",
    "build_missing_background_error",
    "build_role_recommendation_prompt",
    "compact_role_recommendation_semantic_profile_lines",
    "decode_bilingual_description",
    "decode_bilingual_role_name",
    "description_for_prompt",
    "description_query_lines",
    "encode_bilingual_description",
    "encode_bilingual_role_name",
    "infer_scope_profile",
    "is_generic_role_name",
    "load_candidate_semantic_profile_cache",
    "load_resume_excerpt",
    "load_resume_excerpt_result",
    "manual_background_summary",
    "normalize_scope_profile",
    "parse_candidate_semantic_profile",
    "parse_refined_manual_role",
    "parse_role_suggestions",
    "role_name_query_lines",
    "save_candidate_semantic_profile_cache",
    "select_bilingual_description",
    "select_bilingual_role_name",
]
