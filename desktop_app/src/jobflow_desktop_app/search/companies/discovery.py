from __future__ import annotations

import json
import random
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from ..analysis.service import ResponseRequestClient
from ...ai.client import build_json_schema_request, build_text_input_messages, parse_response_json

DISCOVERY_COMPANIES_PER_QUERY_CAP = 12

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
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "region": {"type": "string"},
                },
                "required": ["name", "website", "tags", "region"],
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
    input_text = str(text or "").casefold()
    if not input_text:
        return []
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("hydrogen", ("hydrogen", "h2", "氢")),
        ("fuel_cell", ("fuel cell", "fuel-cell", "燃料电池")),
        ("electrolyzer", ("electrolyzer", "electrolyser", "electrolysis", "电解槽", "制氢")),
        ("battery", ("battery", "bms", "储能", "电池")),
        ("digital_twin", ("digital twin", "digital-twin", "数字孪生")),
        ("phm", ("phm", "prognostics", "health management", "健康管理")),
        ("condition_monitoring", ("condition monitoring", "asset health", "状态监测")),
        ("mbse", ("mbse", "sysml", "systems engineering", "系统工程")),
        ("validation", ("validation", "verification", "v&v", "验证", "确认")),
        ("reliability", ("reliability", "durability", "aging", "可靠性", "耐久", "老化")),
        ("industrial_automation", ("automation", "controls", "plc", "scada", "工业自动化", "控制")),
        ("automotive", ("automotive", "vehicle", "powertrain", "drivetrain", "ev", "汽车", "动力总成")),
        ("testing", ("test", "testing", "diagnostic", "诊断", "测试")),
    ]
    tags: list[str] = []
    for tag, keywords in rules:
        if any(keyword in input_text for keyword in keywords):
            tags.append(tag)
    return tags


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
    item["tags"] = merge_unique_strings(
        item.get("tags") if isinstance(item.get("tags"), list) else [],
        derive_discovery_tags_from_text(
            " ".join(
                part
                for part in (
                    item.get("name"),
                    item.get("website"),
                    item.get("careersUrl"),
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


def normalize_company_discovery_query_stats(raw: object) -> dict[str, int]:
    source = dict(raw) if isinstance(raw, Mapping) else {}
    normalized: dict[str, int] = {}
    for raw_key, raw_value in source.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if isinstance(raw_value, Mapping):
            value = raw_value.get("noNewCompanyCount")
        else:
            value = raw_value
        count = max(0, int(_coerce_number(value) or 0))
        if count > 0:
            normalized[key] = count
    return normalized


def get_discovery_query_bad_score(query_stats: Mapping[str, int], query: object) -> int:
    key = str(query or "").strip()
    if not key:
        return 0
    return max(0, int(_coerce_number(query_stats.get(key)) or 0))


def set_discovery_query_bad_score(query_stats: dict[str, int], query: object, value: object) -> bool:
    key = str(query or "").strip()
    if not key:
        return False
    next_value = max(0, int(_coerce_number(value) or 0))
    current = get_discovery_query_bad_score(query_stats, key)
    if next_value <= 0:
        query_stats.pop(key, None)
    else:
        query_stats[key] = next_value
    return current != next_value


def sample_weighted_discovery_queries(
    raw_queries: object,
    query_stats: Mapping[str, int],
    limit: int,
    *,
    rng: random.Random | None = None,
    used_queries: set[str] | None = None,
) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_query in raw_queries if isinstance(raw_queries, list) else []:
        query = str(raw_query or "").strip()
        if not query:
            continue
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    if not deduped:
        return []
    pool = [query for query in deduped if not (used_queries and query in used_queries)]
    if not pool:
        pool = list(deduped)
    target = max(0, min(len(pool), int(limit)))
    generator = rng or random.Random()
    selected: list[str] = []
    while pool and len(selected) < target:
        weights = [1.0 / (1.0 + get_discovery_query_bad_score(query_stats, query)) for query in pool]
        picked = generator.choices(pool, weights=weights, k=1)[0]
        pool.remove(picked)
        selected.append(picked)
        if used_queries is not None:
            used_queries.add(picked)
    return selected


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


def discover_companies_from_query(
    client: ResponseRequestClient,
    *,
    model: str,
    query: str,
    excluded_companies: list[str] | None = None,
    adjacent_scope: bool = False,
) -> list[dict[str, Any]]:
    excluded_list = [
        str(item or "").strip()
        for item in (excluded_companies or [])
        if str(item or "").strip()
    ][:60]
    exclusion_section = ""
    if excluded_list:
        exclusion_section = (
            "Already-covered companies to avoid returning again:\n"
            + "\n".join(f"- {item}" for item in excluded_list)
            + "\n\nRules for this exclusion list:\n"
            + "- Do not return the same company again.\n"
            + "- Do not return the same legal entity under a slightly different name.\n"
            + "- Do not return the same official domain again.\n"
            + "- Prefer competitors, adjacent suppliers, regional alternatives, or similar companies outside this list.\n\n"
        )
    if adjacent_scope:
        user_text = (
            "Find real companies with official websites operating in adjacent technical business domains around "
            "MBSE, systems engineering, requirements/traceability, verification & validation, integration, "
            "reliability/durability, diagnostics, digital twin/PHM, technical interface, and owner engineering.\n"
            "Prefer companies whose products, platforms, or industrial programs sit in automotive & complex equipment, "
            "industrial equipment & automation, aerospace & high-end manufacturing, plus energy/infrastructure systems "
            "with strong systems-engineering needs.\n"
            "Return only real companies with official websites (no aggregators).\n"
            "Region should be one of: Global, EU, US, CN, JP, KR, CA, AU, UK, CH, IL, IN, ME, AE, SA, ES, PT, SE, NO, DK, NL, FR, DE.\n"
            "Tags should be short lowercase keywords, e.g. mbse, systems, requirements, traceability, verification, validation, "
            "integration, reliability, durability, digital_twin, phm, technical_interface, owner_engineering, automotive, "
            "complex_equipment, industrial_automation, aerospace, high_end_manufacturing, energy, infrastructure.\n\n"
            f"{exclusion_section}Query:\n{query}\n\n"
            f"Return up to {DISCOVERY_COMPANIES_PER_QUERY_CAP} companies.\n"
            "Output only JSON matching the schema."
        )
    else:
        user_text = (
            "Find real companies with official websites operating in the business or technical area suggested by the query.\n"
            "Return only real companies with official websites (no aggregators).\n"
            "Prefer companies with meaningful products, platforms, industrial programs, or R&D activity in that area.\n"
            "Region should be one of: Global, EU, US, CN, JP, KR, CA, AU, UK, CH, IL, IN, ME, AE, SA, ES, PT, SE, NO, DK, NL, FR, DE.\n"
            "Tags should be short lowercase keywords that describe the company business area, product area, or industry segment.\n\n"
            f"{exclusion_section}Query:\n{query}\n\n"
            f"Return up to {DISCOVERY_COMPANIES_PER_QUERY_CAP} companies.\n"
            "Output only JSON matching the schema."
        )
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You identify real companies and return only structured JSON.",
            user_text,
        ),
        schema_name="company_list",
        schema=_COMPANY_DISCOVERY_SCHEMA,
        use_web_search=True,
    )
    response = client.create(request)
    data = parse_response_json(response, f"Company list discovery ({query})")
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
                        "query": str(query or "").strip(),
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


def auto_discover_companies_in_pool(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any],
    companies: object,
    query_stats: object = None,
    query_budget: int | None = None,
    max_new_companies: int | None = None,
    rng: random.Random | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    company_discovery = config.get("companyDiscovery")
    source_companies = companies if isinstance(companies, list) else []
    if not isinstance(company_discovery, Mapping):
        return {
            "added": 0,
            "total": 0,
            "newCompanies": [],
            "queryCount": 0,
            "companies": [],
            "queryStats": {},
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
            "queryCount": 0,
            "companies": normalized_companies,
            "queryStats": normalize_company_discovery_query_stats(query_stats),
            "changed": False,
        }
    model = str(company_discovery.get("model") or "").strip()
    if not model:
        raise ValueError("companyDiscovery.model is required for Python company discovery.")

    normalized_query_stats = normalize_company_discovery_query_stats(query_stats)
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

    all_queries = [
        str(item or "").strip()
        for item in company_discovery.get("queries", [])
        if str(item or "").strip()
    ]
    if not all_queries:
        return {
            "added": 0,
            "total": len(normalized_companies),
            "newCompanies": [],
            "queryCount": 0,
            "companies": normalized_companies,
            "queryStats": dict(normalized_query_stats),
            "changed": changed,
        }
    limit = max(0, int(query_budget if query_budget is not None else len(all_queries)))
    used_queries: set[str] = set()
    selected_queries = sample_weighted_discovery_queries(
        all_queries,
        normalized_query_stats,
        limit,
        rng=rng,
        used_queries=used_queries,
    )
    new_company_cap = max(
        0,
        int(
            max_new_companies
            if max_new_companies is not None
            else company_discovery.get("maxNewCompaniesPerRun") or 0
        ),
    )
    if new_company_cap <= 0:
        return {
            "added": 0,
            "total": len(normalized_companies),
            "newCompanies": [],
            "queryCount": 0,
            "companies": normalized_companies,
            "queryStats": dict(normalized_query_stats),
            "changed": changed,
        }

    excluded_companies = build_repeated_company_avoid_list(normalized_companies, limit=60)
    new_companies: list[dict[str, Any]] = []
    adjacent_scope = str(
        (config.get("candidate") or {}).get("scopeProfile") if isinstance(config.get("candidate"), Mapping) else ""
    ).strip() == "adjacent_mbse"

    for query in selected_queries:
        if progress_callback is not None:
            progress_callback(f"Python company discovery query: {query}")
        discovered = discover_companies_from_query(
            client,
            model=model,
            query=query,
            excluded_companies=excluded_companies,
            adjacent_scope=adjacent_scope,
        )
        added_for_query = 0
        for item in discovered:
            merged = add_or_merge_company_candidate(normalized_companies, key_to_index, item)
            if merged["changed"]:
                changed = True
            if merged["isNew"] and merged["company"]:
                new_companies.append(merged["company"])
                excluded_companies.append(str(merged["company"].get("name") or "").strip())
                added_for_query += 1
            if len(new_companies) >= new_company_cap:
                break
        next_bad_score = 0 if added_for_query > 0 else get_discovery_query_bad_score(normalized_query_stats, query) + 1
        if set_discovery_query_bad_score(normalized_query_stats, query, next_bad_score):
            changed = True
        if len(new_companies) >= new_company_cap:
            break

    return {
        "added": len(new_companies),
        "total": len(normalized_companies),
        "newCompanies": new_companies,
        "queryCount": len(selected_queries),
        "companies": normalized_companies,
        "queryStats": dict(normalized_query_stats),
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
    "add_or_merge_company_candidate",
    "auto_discover_companies_in_pool",
    "build_company_identity_keys",
    "build_repeated_company_avoid_list",
    "company_domain",
    "decay_company_repeat_counts",
    "derive_discovery_tags_from_text",
    "discover_companies_from_query",
    "get_discovery_query_bad_score",
    "merge_company_candidates",
    "normalize_company_candidate",
    "normalize_company_discovery_query_stats",
    "normalize_company_name",
    "sample_weighted_discovery_queries",
    "set_discovery_query_bad_score",
]
