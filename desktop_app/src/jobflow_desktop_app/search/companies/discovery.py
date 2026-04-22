from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from ..analysis.service import ResponseRequestClient
from ...ai.client import (
    OpenAIResponsesError,
    build_json_schema_request,
    build_text_input_messages,
    parse_response_json,
)
from ...prompt_assets import load_prompt_asset

DISCOVERY_COMPANIES_PER_QUERY_CAP = 12
DIRECT_COMPANY_DISCOVERY_DEFAULT_COUNT = 5
DIRECT_COMPANY_DISCOVERY_PROMPT = load_prompt_asset(
    "search_discovery",
    "company_direct_discovery_prompt.txt",
)
DIRECT_COMPANY_DISCOVERY_RETRY_LIMIT = 1

_COMPANY_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "website": {"type": "string"},
                    "businessSummary": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "region": {"type": "string"},
                },
                "required": ["name", "website", "businessSummary", "tags", "region"],
            },
        }
    },
    "required": ["companies"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_url(raw_url: object) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return text
    if not parsed.scheme or not parsed.netloc:
        return text
    return parsed._replace(fragment="").geturl()


def company_domain(website: object) -> str:
    text = normalize_url(website)
    if not text:
        return ""
    try:
        host = urlparse(text).netloc
    except Exception:
        return ""
    return str(host or "").replace("www.", "").strip().casefold()


def normalize_company_name(name: object) -> str:
    text = str(name or "").strip().casefold()
    if not text:
        return ""
    normalized_chars: list[str] = []
    previous_space = False
    for char in text:
        keep = char.isalnum() or ("\u4e00" <= char <= "\u9fff")
        if keep:
            normalized_chars.append(char)
            previous_space = False
            continue
        if not previous_space:
            normalized_chars.append(" ")
            previous_space = True
    return "".join(normalized_chars).strip()


def merge_unique_strings(*groups: object) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
    return merged


def merge_source_evidence(existing: object, incoming: object) -> dict[str, Any]:
    left = dict(existing) if isinstance(existing, Mapping) else {}
    right = dict(incoming) if isinstance(incoming, Mapping) else {}
    merged = dict(left)
    for key, value in right.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = {**dict(current), **dict(value)}
        else:
            merged[key] = value
    return merged


def derive_discovery_tags_from_text(text: object) -> list[str]:
    del text
    return []


def build_company_identity_keys(company: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for raw_url in (
        str(company.get("website") or "").strip(),
        str(company.get("careersUrl") or "").strip(),
    ):
        domain = company_domain(raw_url)
        if domain:
            keys.append(f"domain:{domain}")
    jurisdiction = str(
        company.get("jurisdictionCode") or company.get("jurisdiction_code") or ""
    ).strip().casefold()
    company_number = str(
        company.get("companyNumber") or company.get("company_number") or ""
    ).strip().casefold()
    if jurisdiction and company_number:
        keys.append(f"registry:{jurisdiction}:{company_number}")
    name = normalize_company_name(company.get("name") or "")
    if name:
        keys.append(f"name:{name}")
    return merge_unique_strings(keys)


def normalize_company_candidate(raw: object) -> dict[str, Any]:
    item = dict(raw) if isinstance(raw, Mapping) else {}
    item["name"] = str(item.get("name") or "").strip()
    item["website"] = normalize_url(item.get("website"))
    item["careersUrl"] = normalize_url(item.get("careersUrl"))
    item["businessSummary"] = str(item.get("businessSummary") or "").strip()
    item["tags"] = merge_unique_strings(
        item.get("tags") if isinstance(item.get("tags"), list) else [],
        derive_discovery_tags_from_text(
            " ".join(
                part
                for part in (
                    item.get("name"),
                    item.get("website"),
                    item.get("careersUrl"),
                    item.get("businessSummary"),
                    " ".join(item.get("tags") or []) if isinstance(item.get("tags"), list) else "",
                )
                if str(part or "").strip()
            )
        ),
    )
    item["discoverySources"] = merge_unique_strings(
        item.get("discoverySources") if isinstance(item.get("discoverySources"), list) else [],
        [item.get("source")] if str(item.get("source") or "").strip() else [],
    )
    item["sourceEvidence"] = merge_source_evidence(item.get("sourceEvidence"), {})
    for key in (
        "jurisdictionCode",
        "companyNumber",
        "companyType",
        "currentStatus",
        "registryUrl",
        "branchStatus",
    ):
        item[key] = str(item.get(key) or item.get(key.replace("Code", "_code")) or "").strip()
    if not isinstance(item.get("inactive"), bool):
        item["inactive"] = str(item.get("inactive") or "").strip().casefold() == "true"
    priority = _coerce_number(item.get("priority"))
    if priority is not None:
        item["priority"] = priority
    else:
        item.pop("priority", None)
    signal_count = _coerce_positive_int(item.get("signalCount"))
    if signal_count is not None:
        item["signalCount"] = signal_count
    else:
        item.pop("signalCount", None)
    repeat_count = _coerce_positive_int(item.get("repeatCount"))
    if repeat_count is not None:
        item["repeatCount"] = repeat_count
    else:
        item.pop("repeatCount", None)
    if "web_search" in item["discoverySources"]:
        item["tags"] = merge_unique_strings(item["tags"], ["source:web"])
    return item


def merge_company_candidates(existing: object, incoming: object) -> dict[str, Any]:
    left = normalize_company_candidate(existing)
    right = normalize_company_candidate(incoming)
    out = dict(left)

    def choose_text(primary: object, fallback: object) -> str:
        preferred = str(primary or "").strip()
        if preferred:
            return preferred
        return str(fallback or "").strip()

    out["name"] = choose_text(left.get("name"), right.get("name"))
    out["website"] = choose_text(left.get("website"), right.get("website"))
    out["careersUrl"] = choose_text(left.get("careersUrl"), right.get("careersUrl"))
    out["businessSummary"] = choose_text(left.get("businessSummary"), right.get("businessSummary"))
    out["source"] = choose_text(left.get("source"), right.get("source"))
    out["tags"] = merge_unique_strings(left.get("tags"), right.get("tags"))
    out["discoverySources"] = merge_unique_strings(
        left.get("discoverySources"), right.get("discoverySources")
    )
    out["sourceEvidence"] = merge_source_evidence(
        left.get("sourceEvidence"), right.get("sourceEvidence")
    )
    for key in (
        "jurisdictionCode",
        "companyNumber",
        "companyType",
        "currentStatus",
        "registryUrl",
        "branchStatus",
    ):
        out[key] = choose_text(left.get(key), right.get(key))
    out["inactive"] = bool(left.get("inactive")) or bool(right.get("inactive"))
    out["priority"] = max(_coerce_number(left.get("priority")) or 0, _coerce_number(right.get("priority")) or 0)
    signal_count = max(
        0,
        (_coerce_positive_int(left.get("signalCount")) or 0)
        + (_coerce_positive_int(right.get("signalCount")) or 0),
    )
    if signal_count > 0:
        out["signalCount"] = signal_count
    else:
        out.pop("signalCount", None)
    repeat_count = max(
        _coerce_positive_int(left.get("repeatCount")) or 0,
        _coerce_positive_int(right.get("repeatCount")) or 0,
    )
    if repeat_count > 0:
        out["repeatCount"] = repeat_count
    else:
        out.pop("repeatCount", None)
    return out


def add_or_merge_company_candidate(
    companies: list[dict[str, Any]],
    key_to_index: dict[str, int],
    raw_candidate: object,
) -> dict[str, Any]:
    candidate = normalize_company_candidate(raw_candidate)
    identity_keys = build_company_identity_keys(candidate)
    if not identity_keys:
        return {"company": None, "changed": False, "isNew": False, "isRepeat": False}

    index = next((key_to_index[key] for key in identity_keys if key in key_to_index), None)
    if index is None:
        companies.append(candidate)
        new_index = len(companies) - 1
        for key in identity_keys:
            key_to_index[key] = new_index
        return {"company": companies[new_index], "changed": True, "isNew": True, "isRepeat": False}

    before = json.dumps(companies[index], ensure_ascii=False, sort_keys=True)
    merged = merge_company_candidates(companies[index], candidate)
    companies[index] = merged
    after = json.dumps(merged, ensure_ascii=False, sort_keys=True)
    for key in build_company_identity_keys(merged):
        key_to_index[key] = index
    return {"company": merged, "changed": before != after, "isNew": False, "isRepeat": True}


def decay_company_repeat_counts(companies: list[dict[str, Any]]) -> bool:
    if not companies:
        return False
    changed = False
    for company in companies:
        current = _coerce_positive_int(company.get("repeatCount")) or 0
        if current <= 0:
            continue
        next_value = max(0, current - 1)
        if next_value > 0:
            company["repeatCount"] = next_value
        else:
            company.pop("repeatCount", None)
        if next_value != current:
            changed = True
    return changed


def _normalize_string_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    return values


def _truncate_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def build_company_discovery_existing_company_names(
    companies: object,
    *,
    limit: int = 24,
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw_company in companies if isinstance(companies, list) else []:
        if not isinstance(raw_company, Mapping):
            continue
        name = str(raw_company.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(name)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _normalize_company_discovery_input(raw_input: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(raw_input) if isinstance(raw_input, Mapping) else {}
    return {
        "summary": _truncate_text(payload.get("summary"), 240),
        "targetRoles": _normalize_string_list(payload.get("targetRoles"))[:6],
        "desiredWorkDirections": _normalize_string_list(payload.get("desiredWorkDirections"))[:18],
        "avoidBusinessAreas": _normalize_string_list(payload.get("avoidBusinessAreas"))[:10],
        "locationPreference": _truncate_text(payload.get("locationPreference"), 160),
    }


def _company_discovery_input(config: Mapping[str, Any]) -> dict[str, Any]:
    company_discovery = (
        dict(config.get("companyDiscovery"))
        if isinstance(config.get("companyDiscovery"), Mapping)
        else {}
    )
    raw_input = company_discovery.get("companyDiscoveryInput")
    return _normalize_company_discovery_input(
        raw_input if isinstance(raw_input, Mapping) else {}
    )


def _build_direct_company_discovery_user_text(
    *,
    candidate_context: Mapping[str, Any],
    company_count: int,
    existing_companies: list[str] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "candidateContext": dict(candidate_context),
        "desiredCompanyCount": max(1, int(company_count)),
    }
    normalized_existing_companies = _normalize_string_list(existing_companies)[:24]
    if normalized_existing_companies:
        payload["existingCompanies"] = normalized_existing_companies
    return (
        "Candidate discovery input:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + f"\n\nReturn up to {max(1, int(company_count))} companies."
        + "\nDo not repeat any company listed in existingCompanies."
        + "\nIf existingCompanies is present, broaden to different employers outside that set."
        + "\nOutput only JSON matching the schema."
    )


def _is_timeout_error(exc: Exception) -> bool:
    message = str(exc or "").strip().casefold()
    return isinstance(exc, OpenAIResponsesError) and "timed out" in message


def discover_companies_for_candidate(
    client: ResponseRequestClient,
    *,
    model: str,
    candidate_context: Mapping[str, Any] | None,
    company_count: int,
    existing_companies: list[str] | None = None,
    retry_limit: int = DIRECT_COMPANY_DISCOVERY_RETRY_LIMIT,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    normalized_context = _normalize_company_discovery_input(candidate_context)
    normalized_existing_companies = _normalize_string_list(existing_companies)[:24]
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            DIRECT_COMPANY_DISCOVERY_PROMPT,
            _build_direct_company_discovery_user_text(
                candidate_context=normalized_context,
                company_count=company_count,
                existing_companies=normalized_existing_companies,
            ),
        ),
        schema_name="direct_company_list",
        schema=_COMPANY_DISCOVERY_SCHEMA,
        use_web_search=True,
    )
    last_exc: Exception | None = None
    max_attempts = max(1, int(retry_limit) + 1)
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.create(request)
            data = parse_response_json(response, "Direct company discovery")
            companies = data.get("companies", [])
            if not isinstance(companies, list):
                return []
            discovered_at = now_iso()
            discovered: list[dict[str, Any]] = []
            for raw_company in companies:
                if not isinstance(raw_company, Mapping):
                    continue
                region = str(raw_company.get("region") or "").strip()
                region_tag = f"region:{region.upper()}" if region else ""
                item = normalize_company_candidate(
                    {
                        **dict(raw_company),
                        "source": "web_search",
                        "discoverySources": ["web_search"],
                        "signalCount": int(_coerce_number(raw_company.get("signalCount")) or 1),
                        "lastSeen": discovered_at,
                        "sourceEvidence": {
                            "webSearch": {
                                "mode": "candidate_direct",
                                "discoveredAt": discovered_at,
                            }
                        },
                    }
                )
                if region_tag:
                    item["tags"] = merge_unique_strings(item.get("tags"), [region_tag])
                if item.get("name"):
                    discovered.append(item)
            return discovered
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_timeout_error(exc):
                raise
            if progress_callback is not None:
                progress_callback(
                    "Python direct company discovery timed out; retrying once."
                )
    if last_exc is not None:
        raise last_exc
    return []


def build_repeated_company_avoid_list(companies: object, limit: int = 60) -> list[str]:
    ranked: list[tuple[int, str, str]] = []
    for raw_company in companies if isinstance(companies, list) else []:
        if not isinstance(raw_company, Mapping):
            continue
        repeat_count = _coerce_positive_int(raw_company.get("repeatCount")) or 0
        name = str(raw_company.get("name") or "").strip()
        domain = company_domain(raw_company.get("website") or raw_company.get("careersUrl") or "")
        if repeat_count <= 0 or not name:
            continue
        ranked.append((repeat_count, name, domain))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    output: list[str] = []
    for _, name, domain in ranked[: max(0, int(limit))]:
        output.append(f"{name} ({domain})" if domain else name)
    return output


def auto_discover_companies_in_pool(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any],
    companies: object,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    company_discovery = config.get("companyDiscovery")
    source_companies = companies if isinstance(companies, list) else []
    if not isinstance(company_discovery, Mapping):
        return {
            "added": 0,
            "total": 0,
            "newCompanies": [],
            "companies": [],
            "changed": False,
        }
    if company_discovery.get("enableAutoDiscovery") is False:
        normalized_companies = [
            normalize_company_candidate(item)
            for item in source_companies
            if isinstance(item, Mapping)
        ]
        return {
            "added": 0,
            "total": len(normalized_companies),
            "newCompanies": [],
            "companies": normalized_companies,
            "changed": False,
        }
    model = str(company_discovery.get("model") or "").strip()
    if not model:
        raise ValueError("companyDiscovery.model is required for Python company discovery.")
    normalized_companies = [
        normalize_company_candidate(item)
        for item in source_companies
        if isinstance(item, Mapping)
    ]

    changed = decay_company_repeat_counts(normalized_companies)
    key_to_index: dict[str, int] = {}
    for index, company in enumerate(normalized_companies):
        for key in build_company_identity_keys(company):
            key_to_index[key] = index

    existing_companies = build_company_discovery_existing_company_names(
        normalized_companies,
        limit=60,
    )
    candidate_context = _company_discovery_input(config)
    has_candidate_context = any(
        bool(value) if isinstance(value, list) else bool(str(value or "").strip())
        for value in candidate_context.values()
    )
    if not has_candidate_context:
        return {
            "added": 0,
            "total": len(normalized_companies),
            "newCompanies": [],
            "companies": normalized_companies,
            "changed": changed,
        }
    company_count = max(
        1,
        int(
            company_discovery.get("maxCompaniesPerCall")
            or DIRECT_COMPANY_DISCOVERY_DEFAULT_COUNT
        ),
    )
    if progress_callback is not None:
        progress_callback(
            f"Python direct company discovery request: up to {company_count} companies."
        )
    discovered = discover_companies_for_candidate(
        client,
        model=model,
        candidate_context=candidate_context,
        company_count=company_count,
        existing_companies=existing_companies,
        progress_callback=progress_callback,
    )
    changed = changed or bool(discovered)
    new_companies: list[dict[str, Any]] = []
    for item in discovered:
        merged = add_or_merge_company_candidate(normalized_companies, key_to_index, item)
        if merged["changed"]:
            changed = True
        if merged["isNew"] and merged["company"]:
            new_companies.append(merged["company"])

    return {
        "added": len(new_companies),
        "total": len(normalized_companies),
        "newCompanies": new_companies,
        "companies": normalized_companies,
        "changed": changed,
    }


def _coerce_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value or "").strip()
        if not text:
            return None
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def _coerce_positive_int(value: object) -> int | None:
    number = _coerce_number(value)
    if number is None:
        return None
    return max(0, int(number))


__all__ = [
    "DISCOVERY_COMPANIES_PER_QUERY_CAP",
    "DIRECT_COMPANY_DISCOVERY_DEFAULT_COUNT",
    "add_or_merge_company_candidate",
    "auto_discover_companies_in_pool",
    "build_company_discovery_existing_company_names",
    "build_company_identity_keys",
    "build_repeated_company_avoid_list",
    "company_domain",
    "decay_company_repeat_counts",
    "derive_discovery_tags_from_text",
    "discover_companies_for_candidate",
    "merge_company_candidates",
    "normalize_company_candidate",
    "normalize_company_name",
]
