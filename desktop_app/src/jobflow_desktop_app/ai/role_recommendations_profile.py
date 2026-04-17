from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..db.repositories.candidates import CandidateRecord
from .role_recommendations_models import CandidateSemanticProfile, ResumeReadResult
from .role_recommendations_resume import manual_background_summary


SEMANTIC_PROFILE_SCHEMA_VERSION = 2


def _normalize_semantic_list(
    raw_value: Any,
    *,
    max_items: int,
    max_length: int = 72,
) -> tuple[str, ...]:
    if not isinstance(raw_value, list):
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        text = text.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")
        if not text:
            continue
        if len(text) > max_length:
            text = text[:max_length].rstrip(" ,;|，；、。.!?：:()[]{}<>\"'`")
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
        if len(ordered) >= max_items:
            break
    return tuple(ordered)


def build_candidate_semantic_profile_source_signature(
    candidate: CandidateRecord,
    resume_result: ResumeReadResult,
) -> str:
    payload = {
        "semantic_profile_schema_version": SEMANTIC_PROFILE_SCHEMA_VERSION,
        "candidate": {
            "name": candidate.name.strip(),
            "base_location": candidate.base_location.strip(),
            "preferred_locations": candidate.preferred_locations.strip(),
            "target_directions": candidate.target_directions.strip(),
            "notes": manual_background_summary(candidate),
            "active_resume_path": candidate.active_resume_path.strip(),
        },
        "resume": {
            "text": resume_result.text,
            "error": resume_result.error,
            "source_type": resume_result.source_type,
        },
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parse_candidate_semantic_profile(
    payload_text: str,
    *,
    source_signature: str = "",
    extract_json_object_text,
) -> CandidateSemanticProfile | None:
    text = extract_json_object_text(payload_text)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    summary = re.sub(r"\s+", " ", str(payload.get("summary") or "").strip())
    if len(summary) > 320:
        summary = summary[:320].rstrip() + "..."

    profile = CandidateSemanticProfile(
        source_signature=str(payload.get("source_signature") or source_signature or "").strip(),
        summary=summary,
        background_keywords=_normalize_semantic_list(payload.get("background_keywords"), max_items=20),
        target_direction_keywords=_normalize_semantic_list(payload.get("target_direction_keywords"), max_items=20),
        core_business_areas=_normalize_semantic_list(payload.get("core_business_areas"), max_items=45),
        adjacent_business_areas=_normalize_semantic_list(payload.get("adjacent_business_areas"), max_items=30),
        exploration_business_areas=_normalize_semantic_list(payload.get("exploration_business_areas"), max_items=15),
        avoid_business_areas=_normalize_semantic_list(payload.get("avoid_business_areas"), max_items=10),
        strong_capabilities=_normalize_semantic_list(payload.get("strong_capabilities"), max_items=10),
        seniority_signals=_normalize_semantic_list(payload.get("seniority_signals"), max_items=8),
    )
    if not profile.is_usable():
        return None
    if not profile.source_signature and source_signature:
        profile = CandidateSemanticProfile(
            source_signature=source_signature,
            summary=profile.summary,
            background_keywords=profile.background_keywords,
            target_direction_keywords=profile.target_direction_keywords,
            core_business_areas=profile.core_business_areas,
            adjacent_business_areas=profile.adjacent_business_areas,
            exploration_business_areas=profile.exploration_business_areas,
            avoid_business_areas=profile.avoid_business_areas,
            strong_capabilities=profile.strong_capabilities,
            seniority_signals=profile.seniority_signals,
        )
    return profile


def load_candidate_semantic_profile_cache(
    cache_path: Path | None,
    *,
    source_signature: str,
    extract_json_object_text,
) -> CandidateSemanticProfile | None:
    if cache_path is None or not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    profile = parse_candidate_semantic_profile(
        json.dumps(payload, ensure_ascii=False),
        source_signature=source_signature,
        extract_json_object_text=extract_json_object_text,
    )
    if profile is None or not profile.is_usable():
        return None
    if profile.source_signature and profile.source_signature != source_signature:
        return None
    return profile


def save_candidate_semantic_profile_cache(
    cache_path: Path | None,
    profile: CandidateSemanticProfile,
) -> None:
    if cache_path is None:
        return
    cache_path.write_text(
        json.dumps(profile.to_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "SEMANTIC_PROFILE_SCHEMA_VERSION",
    "build_candidate_semantic_profile_source_signature",
    "load_candidate_semantic_profile_cache",
    "parse_candidate_semantic_profile",
    "save_candidate_semantic_profile_cache",
]
