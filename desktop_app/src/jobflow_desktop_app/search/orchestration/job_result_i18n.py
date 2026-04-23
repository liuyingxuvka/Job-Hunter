from __future__ import annotations

from typing import Any

from ...ai.role_recommendations import OpenAIRoleRecommendationService, RoleRecommendationError

_LANGS = ("zh", "en")
_FIELDS = ("title", "location")
_MAX_TRANSLATION_BATCH = 12


def normalize_display_i18n(payload: object) -> dict[str, dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {
        language: {field: "" for field in _FIELDS}
        for language in _LANGS
    }
    if not isinstance(payload, dict):
        return normalized
    for language in _LANGS:
        section = payload.get(language)
        if not isinstance(section, dict):
            continue
        for field in _FIELDS:
            normalized[language][field] = str(section.get(field) or "").strip()
    return normalized


def merge_display_i18n(
    existing: object,
    incoming: dict[str, dict[str, str]] | None,
) -> dict[str, dict[str, str]]:
    merged = normalize_display_i18n(existing)
    if not isinstance(incoming, dict):
        return merged
    for language in _LANGS:
        section = incoming.get(language)
        if not isinstance(section, dict):
            continue
        for field in _FIELDS:
            text = str(section.get(field) or "").strip()
            if text:
                merged[language][field] = text
    return merged


def has_complete_display_i18n(
    payload: object,
    *,
    required_fields: tuple[str, ...] = _FIELDS,
) -> bool:
    normalized = normalize_display_i18n(payload)
    return all(
        normalized[language][field]
        for language in _LANGS
        for field in required_fields
    )


def select_display_text(
    *,
    ui_language: str,
    raw_text: str,
    zh_text: str,
    en_text: str,
) -> str:
    raw = str(raw_text or "").strip()
    zh = str(zh_text or "").strip()
    en = str(en_text or "").strip()
    if str(ui_language or "").strip().lower() == "en":
        return en or raw or zh
    return zh or raw or en


def _job_key_for_item(runner, item: dict[str, Any]) -> str:
    key_fn = getattr(runner, "_job_item_key", None)
    if callable(key_fn):
        try:
            key = str(key_fn(item) or "").strip()
        except Exception:
            key = ""
        if key:
            return key
    for field in ("jobKey", "job_key", "canonicalUrl", "url"):
        key = str(item.get(field) or "").strip()
        if key:
            return key.casefold()
    return ""


def _display_i18n_provider(runner):
    provider = getattr(runner, "_job_display_i18n_context_provider", None)
    if not callable(provider):
        return None, ""
    try:
        settings, api_base_url = provider()
    except Exception:
        return None, ""
    return settings, str(api_base_url or "").strip()


def _required_fields_for_item(item: dict[str, Any]) -> tuple[str, ...]:
    fields: list[str] = []
    for field, raw_field in (("title", "title"), ("location", "location")):
        if str(item.get(raw_field) or "").strip():
            fields.append(field)
    return tuple(fields)


def enrich_job_display_i18n(runner, candidate_id: int, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not jobs:
        return jobs
    settings, api_base_url = _display_i18n_provider(runner)
    if settings is None or not str(getattr(settings, "api_key", "") or "").strip():
        return jobs

    failed_keys = getattr(runner, "_job_display_i18n_failed_keys", None)
    if not isinstance(failed_keys, set):
        failed_keys = set()
        setattr(runner, "_job_display_i18n_failed_keys", failed_keys)

    requests: list[dict[str, str]] = []
    existing_by_key: dict[str, dict[str, dict[str, str]]] = {}
    items_by_key: dict[str, dict[str, Any]] = {}
    for item in jobs:
        if not isinstance(item, dict):
            continue
        job_key = _job_key_for_item(runner, item)
        if not job_key:
            continue
        items_by_key[job_key] = item
        existing = normalize_display_i18n(item.get("displayI18n"))
        existing_by_key[job_key] = existing
        required_fields = _required_fields_for_item(item)
        if not required_fields:
            item["displayI18n"] = existing
            continue
        if has_complete_display_i18n(existing, required_fields=required_fields) or job_key in failed_keys:
            item["displayI18n"] = existing
            continue
        requests.append(
            {
                "job_key": job_key,
                "title_raw": str(item.get("title") or "").strip(),
                "location_raw": str(item.get("location") or "").strip(),
            }
        )
    if not requests:
        return jobs

    requests = requests[:_MAX_TRANSLATION_BATCH]

    try:
        translated = OpenAIRoleRecommendationService().translate_job_display_bundle(
            requests,
            settings=settings,
            api_base_url=api_base_url,
        )
    except RoleRecommendationError:
        failed_keys.update(request["job_key"] for request in requests)
        return jobs

    persisted_updates: dict[str, dict[str, dict[str, str]]] = {}
    translated_keys: set[str] = set()
    for request in requests:
        job_key = request["job_key"]
        merged = merge_display_i18n(existing_by_key.get(job_key), translated.get(job_key))
        item = items_by_key.get(job_key)
        if item is not None:
            item["displayI18n"] = merged
        required_fields = _required_fields_for_item(item or {})
        if required_fields and has_complete_display_i18n(merged, required_fields=required_fields):
            persisted_updates[job_key] = merged
            translated_keys.add(job_key)

    failed_keys.difference_update(translated_keys)
    failed_keys.update(request["job_key"] for request in requests if request["job_key"] not in translated_keys)

    runtime_mirror = getattr(runner, "runtime_mirror", None)
    persist_fn = getattr(runtime_mirror, "persist_job_display_i18n", None)
    if persisted_updates and callable(persist_fn):
        try:
            persist_fn(candidate_id=int(candidate_id), updates=persisted_updates)
        except Exception:
            pass
    return jobs


__all__ = [
    "enrich_job_display_i18n",
    "has_complete_display_i18n",
    "merge_display_i18n",
    "normalize_display_i18n",
    "select_display_text",
]
