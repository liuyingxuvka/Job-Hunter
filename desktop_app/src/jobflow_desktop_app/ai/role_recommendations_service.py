from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..db.repositories.candidates import CandidateRecord
from ..db.repositories.settings import OpenAISettings
from .client import (
    DEFAULT_OPENAI_RESPONSES_API_URL,
    extract_output_text as shared_extract_output_text,
    resolve_openai_responses_url,
)
from .role_recommendations_models import (
    CandidateSemanticProfile,
    RoleRecommendationMixPlan,
    RoleRecommendationError,
    TargetRoleSuggestion,
)
from .role_recommendations_parse import (
    extract_json_object_text,
    parse_refined_manual_role,
    parse_role_suggestions,
)
from .role_recommendations_profile import (
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
    build_manual_role_enrich_prompt,
    build_role_recommendation_prompt,
)
from .role_recommendations_resume import (
    build_missing_background_error,
    load_resume_excerpt_result,
)


def _extract_output_text(response_payload: dict[str, Any]) -> str:
    return shared_extract_output_text(response_payload)


def _post_responses_request(
    *,
    api_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RoleRecommendationError(f"OpenAI API request failed: HTTP {exc.code}. {detail}") from exc
    except urllib.error.URLError as exc:
        raise RoleRecommendationError(f"Unable to connect OpenAI API: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RoleRecommendationError("OpenAI API request timed out.") from exc


def _parse_response_object(
    output_text: str,
    *,
    parse_error_message: str,
    invalid_format_message: str,
) -> dict[str, Any]:
    json_text = extract_json_object_text(output_text)
    try:
        payload_json = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RoleRecommendationError(parse_error_message) from exc
    if not isinstance(payload_json, dict):
        raise RoleRecommendationError(invalid_format_message)
    return payload_json


class OpenAIRoleRecommendationService:
    default_api_url = DEFAULT_OPENAI_RESPONSES_API_URL

    @staticmethod
    def resolve_api_url(api_base_url: str = "") -> str:
        return resolve_openai_responses_url(api_base_url)

    def extract_candidate_semantic_profile(
        self,
        candidate: CandidateRecord,
        settings: OpenAISettings,
        api_base_url: str = "",
        cache_path: Path | None = None,
    ) -> CandidateSemanticProfile:
        resume_result = load_resume_excerpt_result(candidate.active_resume_path, max_chars=12000)
        source_signature = build_candidate_semantic_profile_source_signature(candidate, resume_result)
        cached_profile = load_candidate_semantic_profile_cache(
            cache_path,
            source_signature=source_signature,
            extract_json_object_text=extract_json_object_text,
        )
        if cached_profile is not None:
            return cached_profile

        background_error = build_missing_background_error(
            action_name="AI semantic profile extraction",
            candidate=candidate,
            resume_result=resume_result,
        )
        if background_error:
            raise RoleRecommendationError(background_error)
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")

        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": CANDIDATE_SEMANTIC_PROFILE_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_candidate_semantic_profile_prompt(
                                candidate,
                                resume_result=resume_result,
                            ),
                        }
                    ],
                },
            ],
        }
        response_payload = _post_responses_request(
            api_url=self.resolve_api_url(api_base_url),
            api_key=settings.api_key,
            payload=payload,
            timeout=90,
        )
        output_text = _extract_output_text(response_payload)
        profile = parse_candidate_semantic_profile(
            output_text,
            source_signature=source_signature,
            extract_json_object_text=extract_json_object_text,
        )
        if profile is None or not profile.is_usable():
            raise RoleRecommendationError("AI did not return a usable candidate semantic profile.")
        save_candidate_semantic_profile_cache(cache_path, profile)
        return profile

    def recommend_roles(
        self,
        candidate: CandidateRecord,
        settings: OpenAISettings,
        api_base_url: str = "",
        max_items: int = 3,
        existing_roles: list[tuple[str, str]] | None = None,
        mix_plan: RoleRecommendationMixPlan | None = None,
    ) -> list[TargetRoleSuggestion]:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")

        resume_result = load_resume_excerpt_result(candidate.active_resume_path)
        background_error = build_missing_background_error(
            action_name="AI role recommendations",
            candidate=candidate,
            resume_result=resume_result,
        )
        if background_error:
            raise RoleRecommendationError(background_error)

        existing_names = {
            str(role[0] or "").strip().casefold()
            for role in (existing_roles or [])
            if isinstance(role, tuple) and len(role) == 2 and str(role[0] or "").strip()
        }
        semantic_profile: CandidateSemanticProfile | None = None
        try:
            semantic_profile = self.extract_candidate_semantic_profile(
                candidate=candidate,
                settings=settings,
                api_base_url=api_base_url,
            )
        except RoleRecommendationError:
            semantic_profile = None

        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": SYSTEM_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_role_recommendation_prompt(
                                candidate,
                                existing_roles=existing_roles,
                                resume_result=resume_result,
                                semantic_profile=semantic_profile,
                                mix_plan=mix_plan,
                            ),
                        }
                    ],
                },
            ],
        }
        response_payload = _post_responses_request(
            api_url=self.resolve_api_url(api_base_url),
            api_key=settings.api_key,
            payload=payload,
            timeout=90,
        )
        output_text = _extract_output_text(response_payload)
        try:
            suggestions = parse_role_suggestions(output_text, max_items=max_items)
        except json.JSONDecodeError as exc:
            raise RoleRecommendationError("AI response is not parseable JSON.") from exc

        if existing_names:
            suggestions = [
                suggestion
                for suggestion in suggestions
                if suggestion.name.strip().casefold() not in existing_names
            ]

        if not suggestions:
            raise RoleRecommendationError("AI did not return usable role suggestions.")
        return suggestions

    def enrich_manual_role(
        self,
        candidate: CandidateRecord,
        settings: OpenAISettings,
        role_name: str,
        rough_description: str = "",
        desired_scope_profile: str = "",
        api_base_url: str = "",
    ) -> TargetRoleSuggestion:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")
        intent_name = str(role_name or "").strip()
        if not intent_name:
            raise RoleRecommendationError("Role name is required.")

        resume_result = load_resume_excerpt_result(candidate.active_resume_path, max_chars=3500)
        background_error = build_missing_background_error(
            action_name="AI role refinement",
            candidate=candidate,
            resume_result=resume_result,
        )
        if background_error:
            raise RoleRecommendationError(background_error)
        semantic_profile: CandidateSemanticProfile | None = None
        try:
            semantic_profile = self.extract_candidate_semantic_profile(
                candidate=candidate,
                settings=settings,
                api_base_url=api_base_url,
            )
        except RoleRecommendationError:
            semantic_profile = None
        user_prompt = build_manual_role_enrich_prompt(
            candidate,
            role_name=intent_name,
            rough_description=str(rough_description or ""),
            desired_scope_profile=desired_scope_profile,
            resume_result=resume_result,
            semantic_profile=semantic_profile,
        )

        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": MANUAL_ROLE_ENRICH_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        response_payload = _post_responses_request(
            api_url=self.resolve_api_url(api_base_url),
            api_key=settings.api_key,
            payload=payload,
            timeout=90,
        )
        output_text = _extract_output_text(response_payload)
        suggestion = parse_refined_manual_role(
            output_text,
            fallback_name=intent_name,
            fallback_description=str(rough_description or ""),
        )
        if suggestion is None:
            raise RoleRecommendationError("AI did not return a usable refined role.")
        return suggestion

    def translate_role_name(
        self,
        role_name: str,
        target_language: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")
        source_name = str(role_name or "").strip()
        if not source_name:
            return ""

        language = "zh" if str(target_language or "").strip().lower().startswith("zh") else "en"
        user_prompt = "\n".join(
            [
                f"Source role title: {source_name}",
                f"Target language: {language}",
                "Return strict JSON only.",
            ]
        )
        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": ROLE_NAME_TRANSLATE_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        response_payload = _post_responses_request(
            api_url=self.resolve_api_url(api_base_url),
            api_key=settings.api_key,
            payload=payload,
            timeout=45,
        )
        output_text = _extract_output_text(response_payload)
        payload_json = _parse_response_object(
            output_text,
            parse_error_message="AI role-name translation response is not parseable JSON.",
            invalid_format_message="AI role-name translation response has invalid format.",
        )
        translated = str(payload_json.get("name_translated") or "").strip()
        if not translated:
            raise RoleRecommendationError("AI role-name translation response did not include name_translated.")
        return translated

    def translate_description(
        self,
        role_name: str,
        source_description: str,
        target_language: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")
        source_text = str(source_description or "").strip()
        if not source_text:
            return ""

        language = "zh" if str(target_language or "").strip().lower().startswith("zh") else "en"
        user_prompt = "\n".join(
            [
                f"Role name: {role_name.strip() or 'N/A'}",
                "Source description:",
                source_text,
                f"Target language: {language}",
                "Return strict JSON only.",
            ]
        )
        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": TRANSLATE_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        response_payload = _post_responses_request(
            api_url=self.resolve_api_url(api_base_url),
            api_key=settings.api_key,
            payload=payload,
            timeout=60,
        )
        output_text = _extract_output_text(response_payload)
        payload_json = _parse_response_object(
            output_text,
            parse_error_message="AI translation response is not parseable JSON.",
            invalid_format_message="AI translation response has invalid format.",
        )
        translated = str(
            payload_json.get("description_translated")
            or payload_json.get("description_en")
            or payload_json.get("description_zh")
            or ""
        ).strip()
        if not translated:
            raise RoleRecommendationError("AI translation response did not include translated text.")
        return translated

    def translate_description_to_english(
        self,
        role_name: str,
        description_zh: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        return self.translate_description(
            role_name=role_name,
            source_description=description_zh,
            target_language="en",
            settings=settings,
            api_base_url=api_base_url,
        )

    def translate_description_to_chinese(
        self,
        role_name: str,
        description_en: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        return self.translate_description(
            role_name=role_name,
            source_description=description_en,
            target_language="zh",
            settings=settings,
            api_base_url=api_base_url,
        )

    def translate_job_display_bundle(
        self,
        jobs: list[dict[str, str]],
        *,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> dict[str, dict[str, dict[str, str]]]:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")

        normalized_jobs: list[dict[str, str]] = []
        seen_keys: set[str] = set()
        for item in jobs:
            if not isinstance(item, dict):
                continue
            job_key = str(item.get("job_key") or "").strip()
            if not job_key or job_key in seen_keys:
                continue
            seen_keys.add(job_key)
            normalized_jobs.append(
                {
                    "job_key": job_key,
                    "title_raw": str(item.get("title_raw") or "").strip(),
                    "location_raw": str(item.get("location_raw") or "").strip(),
                }
            )
        if not normalized_jobs:
            return {}

        user_prompt = "\n".join(
            [
                "Translate these job display fields for a bilingual UI.",
                "Input JSON:",
                json.dumps({"jobs": normalized_jobs}, ensure_ascii=False),
            ]
        )
        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": JOB_DISPLAY_I18N_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        response_payload = _post_responses_request(
            api_url=self.resolve_api_url(api_base_url),
            api_key=settings.api_key,
            payload=payload,
            timeout=60,
        )
        output_text = _extract_output_text(response_payload)
        payload_json = _parse_response_object(
            output_text,
            parse_error_message="AI job display translation response is not parseable JSON.",
            invalid_format_message="AI job display translation response has invalid format.",
        )
        translations_raw = payload_json.get("translations")
        if not isinstance(translations_raw, list):
            raise RoleRecommendationError(
                "AI job display translation response did not include a translations list."
            )

        results: dict[str, dict[str, dict[str, str]]] = {}
        for row in translations_raw:
            if not isinstance(row, dict):
                continue
            job_key = str(row.get("job_key") or "").strip()
            if not job_key:
                continue
            results[job_key] = {
                "zh": {
                    "title": str(row.get("title_zh") or "").strip(),
                    "location": str(row.get("location_zh") or "").strip(),
                },
                "en": {
                    "title": str(row.get("title_en") or "").strip(),
                    "location": str(row.get("location_en") or "").strip(),
                },
            }
        if not results:
            raise RoleRecommendationError(
                "AI job display translation response did not include usable translations."
            )
        return results


__all__ = ["OpenAIRoleRecommendationService"]
