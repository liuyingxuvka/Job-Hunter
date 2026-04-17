from __future__ import annotations

import json
import re

from .client import extract_json_object_text as shared_extract_json_object_text
from .role_recommendations_models import TargetRoleSuggestion
from .role_recommendations_text import infer_scope_profile, is_generic_role_name


def extract_json_object_text(raw_text: str) -> str:
    return shared_extract_json_object_text(raw_text)


def parse_role_suggestions(payload_text: str, max_items: int = 3) -> list[TargetRoleSuggestion]:
    text = payload_text.strip()
    if not text:
        return []

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

    data = json.loads(text)
    raw_roles = data.get("roles", [])
    suggestions: list[TargetRoleSuggestion] = []
    seen_names: set[str] = set()
    for raw_role in raw_roles:
        if not isinstance(raw_role, dict):
            continue
        name_en = str(raw_role.get("name_en", "")).strip()
        name_zh = str(raw_role.get("name_zh", "")).strip()
        name = name_en or str(raw_role.get("name", "")).strip() or name_zh
        fallback_description = str(raw_role.get("description", "")).strip()
        description_zh = str(raw_role.get("description_zh", "")).strip() or fallback_description
        description_en = str(raw_role.get("description_en", "")).strip() or fallback_description
        if not name:
            continue
        if is_generic_role_name(name):
            continue
        if name_en and is_generic_role_name(name_en):
            continue
        normalized = name.casefold()
        if normalized in seen_names:
            continue
        seen_names.add(normalized)
        summary_for_scope = f"{description_zh}\n{description_en}".strip()
        suggestions.append(
            TargetRoleSuggestion(
                name=name,
                description_zh=description_zh,
                description_en=description_en,
                scope_profile=infer_scope_profile(name, summary_for_scope),
                name_zh=name_zh,
                name_en=name_en or name,
            )
        )
        if len(suggestions) >= max_items:
            break
    return suggestions


def parse_refined_manual_role(
    payload_text: str,
    fallback_name: str,
    fallback_description: str,
) -> TargetRoleSuggestion | None:
    text = extract_json_object_text(payload_text)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    role_payload = payload.get("role")
    if isinstance(role_payload, dict):
        payload = role_payload

    name_en = str(payload.get("name_en") or "").strip()
    name_zh = str(payload.get("name_zh") or "").strip()
    name = name_en or str(payload.get("name") or "").strip() or name_zh or fallback_name.strip()
    fallback_description_text = str(payload.get("description") or "").strip()
    description_zh = str(payload.get("description_zh") or "").strip() or fallback_description_text
    description_en = str(payload.get("description_en") or "").strip() or fallback_description_text
    if not description_zh and fallback_description:
        description_zh = fallback_description.strip()
    if not description_en and fallback_description and re.search(r"[A-Za-z]", fallback_description or ""):
        description_en = fallback_description.strip()
    if not name:
        return None
    if is_generic_role_name(name):
        return None
    if name_en and is_generic_role_name(name_en):
        return None
    summary = f"{description_zh}\n{description_en}".strip()
    return TargetRoleSuggestion(
        name=name,
        description_zh=description_zh,
        description_en=description_en,
        scope_profile=infer_scope_profile(name, summary),
        name_zh=name_zh,
        name_en=name_en or name,
    )


__all__ = [
    "extract_json_object_text",
    "parse_refined_manual_role",
    "parse_role_suggestions",
]
