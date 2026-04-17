from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ...ai.client import build_json_schema_request, build_text_input_messages, parse_response_json
from ..analysis.service import ResponseRequestClient
from .selection import company_has_region_tag, company_matches_major_keyword
from .sources_fetchers import discover_careers_from_website, fetch_careers_page_jobs, to_number
from .sources_helpers import has_job_signal

_COMPANY_CAREERS_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "website": {"type": "string"},
        "careersUrl": {"type": "string"},
    },
    "required": ["website", "careersUrl"],
}
_JOB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "summary": {"type": "string"},
                    "datePosted": {"type": "string"},
                    "availabilityHint": {"type": "string"},
                },
                "required": [
                    "title",
                    "company",
                    "location",
                    "url",
                    "summary",
                    "datePosted",
                    "availabilityHint",
                ],
            },
        }
    },
    "required": ["jobs"],
}


def discover_company_careers(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any] | None,
    company_name: str,
) -> dict[str, str]:
    normalized_name = str(company_name or "").strip()
    if not normalized_name:
        return {"website": "", "careersUrl": ""}
    company_discovery = dict(config.get("companyDiscovery") or {}) if isinstance(config, Mapping) else {}
    model = str(company_discovery.get("model") or "").strip()
    if not model:
        return {"website": "", "careersUrl": ""}
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You identify official company websites and careers pages. Return only structured JSON.",
            (
                "Find the official website and the careers/jobs page for this company.\n"
                f"Company name: {normalized_name}\n\n"
                "Rules:\n"
                "- Use only the official company website, never aggregators or mirror pages.\n"
                "- If multiple official sites exist, prefer the global corporate site.\n"
                "- If the careers page is not found, return an empty string for careersUrl.\n"
                "Output only JSON matching the schema."
            ),
        ),
        schema_name="company_careers_discovery",
        schema=_COMPANY_CAREERS_DISCOVERY_SCHEMA,
        use_web_search=True,
    )
    response = client.create(request)
    payload = parse_response_json(response, f"Company careers discovery ({normalized_name})")
    return {
        "website": str(payload.get("website") or "").strip(),
        "careersUrl": str(payload.get("careersUrl") or "").strip(),
    }


def openai_search_jobs(
    client: ResponseRequestClient,
    *,
    config: Mapping[str, Any] | None,
    query: str,
) -> list[dict[str, str]]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []
    candidate = dict(config.get("candidate") or {}) if isinstance(config, Mapping) else {}
    search = dict(config.get("search") or {}) if isinstance(config, Mapping) else {}
    model = str(search.get("model") or "").strip()
    if not model:
        return []
    max_jobs_per_query = max(1, int(to_number(search.get("maxJobsPerQuery"), 20)))
    request = build_json_schema_request(
        model=model,
        input_payload=build_text_input_messages(
            "You find real job detail pages and return only structured JSON.",
            (
                "You are helping a candidate find relevant technical expert jobs.\n\n"
                "Candidate target role:\n"
                f"{str(candidate.get('targetRole') or '').strip()}\n\n"
                "Location preference:\n"
                f"{str(candidate.get('locationPreference') or '').strip()}\n\n"
                "Task:\n"
                "- Use web search and find real, currently accessible job postings.\n"
                "- Prefer official company career pages or ATS job detail pages.\n"
                "- Never invent companies, hostnames, or URLs.\n"
                "- Exclude generic search pages, careers homepages, list/filter pages, parked domains, and mirrors.\n"
                "- Professional job platforms are allowed only when the result is a concrete job detail page.\n"
                f"- Return up to {max_jobs_per_query} results.\n"
                "- Normalize posting dates to YYYY-MM-DD only when you are confident.\n"
                "- Put any active/closed/easy-apply hint into availabilityHint; otherwise use an empty string.\n\n"
                "Query:\n"
                f"{normalized_query}\n\n"
                "Output only JSON matching the schema."
            ),
        ),
        schema_name="job_search_results",
        schema=_JOB_SEARCH_SCHEMA,
        use_web_search=True,
    )
    response = client.create(request)
    payload = parse_response_json(response, f"Job search ({normalized_query})")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return []
    filtered: list[dict[str, str]] = []
    for raw_job in jobs:
        if not isinstance(raw_job, Mapping):
            continue
        if not has_job_signal(
            title=raw_job.get("title") or "",
            url=raw_job.get("url") or "",
            summary=raw_job.get("summary") or "",
        ):
            continue
        filtered.append(
            {
                "title": str(raw_job.get("title") or "").strip(),
                "company": str(raw_job.get("company") or "").strip(),
                "location": str(raw_job.get("location") or "").strip(),
                "url": str(raw_job.get("url") or "").strip(),
                "summary": str(raw_job.get("summary") or "").strip(),
                "datePosted": str(raw_job.get("datePosted") or "").strip(),
                "availabilityHint": str(raw_job.get("availabilityHint") or "").strip(),
            }
        )
    return filtered


def company_search_fallback_enabled(company: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    sources = dict(config.get("sources") or {}) if isinstance(config, Mapping) else {}
    if sources.get("enableCompanySearchFallback") is False:
        return False
    if isinstance(config, Mapping) and company_matches_major_keyword(company, config):
        return True
    fallback_regions = [
        str(item or "").strip()
        for item in sources.get("fallbackSearchRegions", ["region:JP"])
        if str(item or "").strip()
    ]
    return any(company_has_region_tag(company, region_tag) for region_tag in fallback_regions)


def build_company_search_fallback_query(
    company: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> str:
    name = str(company.get("name") or "").strip()
    if not name:
        return ""
    candidate = dict(config.get("candidate") or {}) if isinstance(config, Mapping) else {}
    scope_profile = str(candidate.get("scopeProfile") or "").strip()
    tags = [
        str(item or "").strip().lower()
        for item in company.get("tags", [])
        if str(item or "").strip()
    ]
    joined_tags = " ".join(tags)

    if scope_profile == "adjacent_mbse":
        is_china = company_has_region_tag(company, "region:CN")
        focus = "MBSE systems engineering verification validation integration engineer"
        if re.search(r"digital_twin|phm|condition_monitoring|asset_health", joined_tags):
            focus = "digital twin PHM condition monitoring engineer"
        elif re.search(
            r"reliability|durability|diagnostics|failure|validation|verification|integration",
            joined_tags,
        ):
            focus = "reliability validation integration engineer"
        elif re.search(r"technical_interface|owner_engineering", joined_tags):
            focus = "technical interface owner engineer"

        sector = "complex systems"
        if re.search(r"automotive|complex_equipment|battery|powertrain", joined_tags):
            sector = "automotive complex equipment"
        elif re.search(r"industrial_automation|industrial_equipment|automation|robotics", joined_tags):
            sector = "industrial automation equipment"
        elif re.search(r"aerospace|high_end_manufacturing|defense", joined_tags):
            sector = "aerospace high end manufacturing"
        elif re.search(r"energy|infrastructure|grid|utility", joined_tags):
            sector = "energy infrastructure"

        if is_china:
            return f"{name} 招聘 系统工程 MBSE 验证 集成 可靠性 数字孪生 工程师"
        return f"{name} careers {focus} {sector}"

    has_hydrogen_signal = any(
        re.search(r"fuel_cell|electrolyzer|hydrogen|stack|system|controls|balance_of_plant", tag)
        for tag in tags
    )
    has_materials_signal = any(re.search(r"materials|membrane|mea|catalyst", tag) for tag in tags)
    has_testing_signal = any(re.search(r"testing|diagnostics|validation|verification", tag) for tag in tags)
    has_battery_signal = any(re.search(r"battery|energy_storage|ess", tag) for tag in tags)
    is_japan = company_has_region_tag(company, "region:JP")
    is_china = company_has_region_tag(company, "region:CN")
    is_spain_portugal = company_has_region_tag(company, "region:ES") or company_has_region_tag(
        company,
        "region:PT",
    )
    is_middle_east = any(
        company_has_region_tag(company, region_tag) for region_tag in ("region:ME", "region:AE", "region:SA")
    )
    is_germany_nordics = any(
        company_has_region_tag(company, region_tag)
        for region_tag in ("region:DE", "region:SE", "region:NO", "region:DK")
    )

    focus = "systems engineering engineer"
    if has_materials_signal:
        focus = "materials membrane catalyst engineer"
    elif has_testing_signal:
        focus = "test validation diagnostics engineer"
    elif has_battery_signal and not has_hydrogen_signal:
        focus = "battery energy storage engineer"

    if is_japan:
        if has_materials_signal:
            return f"{name} 採用 膜 電極 触媒 電気化学 材料 エンジニア"
        if has_testing_signal:
            return f"{name} 採用 試験 検証 診断 エンジニア"
        if has_battery_signal and not has_hydrogen_signal:
            return f"{name} 採用 電池 蓄電池 エンジニア"
        return f"{name} 採用 システム エンジニア"

    if is_china:
        if has_materials_signal:
            return f"{name} 招聘 电化学 材料 膜电极 催化剂 工程师"
        if has_testing_signal:
            return f"{name} 招聘 测试 验证 诊断 工程师"
        if has_battery_signal and not has_hydrogen_signal:
            return f"{name} 招聘 电池 储能 工程师"
        return f"{name} 招聘 系统工程 工程师"

    if is_spain_portugal:
        if has_materials_signal:
            return f"{name} careers materials membrane catalyst engineer Spain Portugal"
        if has_testing_signal:
            return f"{name} careers testing validation diagnostics engineer Spain Portugal"
        if has_battery_signal and not has_hydrogen_signal:
            return f"{name} careers battery energy storage engineer Spain Portugal"
        return f"{name} careers systems engineer Spain Portugal"

    if is_middle_east:
        if has_materials_signal:
            return f"{name} careers materials membrane catalyst engineer Middle East"
        if has_testing_signal:
            return f"{name} careers testing validation diagnostics engineer Middle East"
        if has_battery_signal and not has_hydrogen_signal:
            return f"{name} careers battery energy storage engineer Middle East"
        return f"{name} careers systems engineer Middle East"

    if is_germany_nordics:
        if has_materials_signal:
            return f"{name} careers materials membrane catalyst engineer Germany Nordics"
        if has_testing_signal:
            return f"{name} careers testing validation diagnostics engineer Germany Nordics"
        if has_battery_signal and not has_hydrogen_signal:
            return f"{name} careers battery energy storage engineer Germany Nordics"
        return f"{name} careers systems engineer Germany Nordics"

    if has_hydrogen_signal:
        return f"{name} careers {focus} hydrogen"
    if has_materials_signal:
        return f"{name} careers {focus} materials"
    if has_testing_signal:
        return f"{name} careers {focus} testing"
    if has_battery_signal:
        return f"{name} careers {focus} battery"
    return f"{name} careers {focus}"


__all__ = [
    "build_company_search_fallback_query",
    "company_search_fallback_enabled",
    "discover_careers_from_website",
    "discover_company_careers",
    "fetch_careers_page_jobs",
    "openai_search_jobs",
]
