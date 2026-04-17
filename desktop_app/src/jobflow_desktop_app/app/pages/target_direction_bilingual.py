from __future__ import annotations

from typing import Any

from ...db.repositories.settings import OpenAISettings
from ...ai.role_recommendations import (
    RoleRecommendationError,
    decode_bilingual_role_name,
)


def canonical_role_name(role_name_i18n: str, fallback_name: str = "") -> str:
    name_zh, name_en = decode_bilingual_role_name(role_name_i18n, fallback_name=fallback_name)
    return name_en or name_zh or str(fallback_name or "").strip()


def display_role_name(profile: Any, ui_language: str, select_bilingual_role_name_fn: Any) -> str:
    return select_bilingual_role_name_fn(
        getattr(profile, "role_name_i18n", ""),
        ui_language,
        fallback_name=getattr(profile, "name", ""),
    )


def complete_role_name_pair(
    recommender: Any,
    *,
    name_zh: str,
    name_en: str,
    settings: OpenAISettings,
    api_base_url: str,
    use_ai: bool,
) -> tuple[str, str]:
    completed_zh = str(name_zh or "").strip()
    completed_en = str(name_en or "").strip()
    if completed_zh and completed_en:
        return completed_zh, completed_en

    if use_ai:
        try:
            if not completed_zh and completed_en:
                completed_zh = recommender.translate_role_name(
                    role_name=completed_en,
                    target_language="zh",
                    settings=settings,
                    api_base_url=api_base_url,
                ).strip()
            elif not completed_en and completed_zh:
                completed_en = recommender.translate_role_name(
                    role_name=completed_zh,
                    target_language="en",
                    settings=settings,
                    api_base_url=api_base_url,
                ).strip()
        except RoleRecommendationError:
            pass

    if not completed_zh and completed_en:
        completed_zh = completed_en
    if not completed_en and completed_zh:
        completed_en = completed_zh
    return completed_zh, completed_en


def complete_description_pair(
    recommender: Any,
    *,
    role_name: str,
    description_zh: str,
    description_en: str,
    settings: OpenAISettings,
    api_base_url: str,
    use_ai: bool,
) -> tuple[str, str]:
    completed_zh = str(description_zh or "").strip()
    completed_en = str(description_en or "").strip()
    if not completed_zh and not completed_en:
        return "", ""
    if completed_zh and completed_en:
        return completed_zh, completed_en

    if use_ai:
        try:
            if not completed_en and completed_zh:
                completed_en = recommender.translate_description_to_english(
                    role_name=role_name,
                    description_zh=completed_zh,
                    settings=settings,
                    api_base_url=api_base_url,
                ).strip()
            elif not completed_zh and completed_en:
                completed_zh = recommender.translate_description_to_chinese(
                    role_name=role_name,
                    description_en=completed_en,
                    settings=settings,
                    api_base_url=api_base_url,
                ).strip()
        except RoleRecommendationError:
            pass

    if not completed_zh and completed_en:
        completed_zh = completed_en
    if not completed_en and completed_zh:
        completed_en = completed_zh
    return completed_zh, completed_en


__all__ = [
    "canonical_role_name",
    "complete_description_pair",
    "complete_role_name_pair",
    "display_role_name",
]
