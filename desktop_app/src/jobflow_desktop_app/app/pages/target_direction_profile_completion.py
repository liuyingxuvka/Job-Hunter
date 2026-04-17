from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...db.repositories.settings import OpenAISettings


@dataclass(frozen=True)
class CompletedProfileText:
    role_name_zh: str
    role_name_en: str
    description_zh: str
    description_en: str


def complete_profile_text(
    *,
    role_name_zh: str,
    role_name_en: str,
    description_zh: str,
    description_en: str,
    fallback_name: str,
    settings: OpenAISettings,
    api_base_url: str,
    use_ai: bool,
    complete_role_name_pair: Callable[[str, str, OpenAISettings, str, bool], tuple[str, str]],
    complete_description_pair: Callable[[str, str, str, OpenAISettings, str, bool], tuple[str, str]],
) -> CompletedProfileText:
    completed_role_name_zh, completed_role_name_en = complete_role_name_pair(
        role_name_zh,
        role_name_en,
        settings,
        api_base_url,
        use_ai,
    )
    canonical_name_for_translate = completed_role_name_en or completed_role_name_zh or fallback_name
    completed_description_zh, completed_description_en = complete_description_pair(
        canonical_name_for_translate,
        description_zh,
        description_en,
        settings,
        api_base_url,
        use_ai,
    )
    return CompletedProfileText(
        role_name_zh=completed_role_name_zh,
        role_name_en=completed_role_name_en,
        description_zh=completed_description_zh,
        description_en=completed_description_en,
    )


__all__ = [
    "CompletedProfileText",
    "complete_profile_text",
]
