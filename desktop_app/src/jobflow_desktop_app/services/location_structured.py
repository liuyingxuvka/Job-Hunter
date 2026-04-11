from __future__ import annotations

import json
import re
from typing import Any


LOCATION_TYPE_ORDER = ("city", "country", "region", "global", "remote")

REGION_SUGGESTIONS = (
    "Europe",
    "Asia",
    "North America",
    "South America",
    "Middle East",
    "Africa",
    "Oceania",
)

COUNTRY_ALIASES_BY_CODE: dict[str, tuple[str, ...]] = {
    "DE": ("Germany", "Deutschland", "DE"),
    "ES": ("Spain", "Espana", "ES"),
    "NL": ("Netherlands", "Holland", "NL"),
    "PT": ("Portugal", "PT"),
    "JP": ("Japan", "JP"),
    "US": ("United States", "USA", "US"),
    "GB": ("United Kingdom", "UK", "Great Britain", "GB"),
    "FR": ("France", "FR"),
    "IT": ("Italy", "IT"),
    "CH": ("Switzerland", "CH"),
    "AT": ("Austria", "AT"),
    "BE": ("Belgium", "BE"),
    "PL": ("Poland", "PL"),
    "SE": ("Sweden", "SE"),
    "NO": ("Norway", "NO"),
    "DK": ("Denmark", "DK"),
    "FI": ("Finland", "FI"),
    "CA": ("Canada", "CA"),
    "AU": ("Australia", "AU"),
    "SG": ("Singapore", "SG"),
    "IN": ("India", "IN"),
    "CN": ("China", "CN"),
    "IE": ("Ireland", "IE"),
    "KR": ("South Korea", "Korea", "KR"),
    "TW": ("Taiwan", "TW"),
    "AE": ("United Arab Emirates", "UAE", "AE"),
    "SA": ("Saudi Arabia", "SA"),
    "QA": ("Qatar", "QA"),
}

COUNTRY_LOOKUP: dict[str, tuple[str, str]] = {}
for country_code, aliases in COUNTRY_ALIASES_BY_CODE.items():
    canonical_country = aliases[0]
    for alias in aliases:
        COUNTRY_LOOKUP[alias.casefold()] = (canonical_country, country_code)

CITY_LOOKUP: dict[str, tuple[str, str, str]] = {
    "aachen": ("Aachen", "Germany", "DE"),
    "berlin": ("Berlin", "Germany", "DE"),
    "munich": ("Munich", "Germany", "DE"),
    "hamburg": ("Hamburg", "Germany", "DE"),
    "frankfurt": ("Frankfurt", "Germany", "DE"),
    "stuttgart": ("Stuttgart", "Germany", "DE"),
    "cologne": ("Cologne", "Germany", "DE"),
    "dusseldorf": ("Dusseldorf", "Germany", "DE"),
    "madrid": ("Madrid", "Spain", "ES"),
    "barcelona": ("Barcelona", "Spain", "ES"),
    "amsterdam": ("Amsterdam", "Netherlands", "NL"),
    "rotterdam": ("Rotterdam", "Netherlands", "NL"),
    "lisbon": ("Lisbon", "Portugal", "PT"),
    "porto": ("Porto", "Portugal", "PT"),
    "tokyo": ("Tokyo", "Japan", "JP"),
    "osaka": ("Osaka", "Japan", "JP"),
    "singapore": ("Singapore", "Singapore", "SG"),
    "london": ("London", "United Kingdom", "GB"),
    "manchester": ("Manchester", "United Kingdom", "GB"),
    "birmingham": ("Birmingham", "United Kingdom", "GB"),
    "edinburgh": ("Edinburgh", "United Kingdom", "GB"),
    "paris": ("Paris", "France", "FR"),
    "lyon": ("Lyon", "France", "FR"),
    "zurich": ("Zurich", "Switzerland", "CH"),
    "geneva": ("Geneva", "Switzerland", "CH"),
    "vienna": ("Vienna", "Austria", "AT"),
    "brussels": ("Brussels", "Belgium", "BE"),
    "antwerp": ("Antwerp", "Belgium", "BE"),
    "warsaw": ("Warsaw", "Poland", "PL"),
    "krakow": ("Krakow", "Poland", "PL"),
    "stockholm": ("Stockholm", "Sweden", "SE"),
    "gothenburg": ("Gothenburg", "Sweden", "SE"),
    "oslo": ("Oslo", "Norway", "NO"),
    "copenhagen": ("Copenhagen", "Denmark", "DK"),
    "helsinki": ("Helsinki", "Finland", "FI"),
    "dublin": ("Dublin", "Ireland", "IE"),
    "new york": ("New York", "United States", "US"),
    "boston": ("Boston", "United States", "US"),
    "seattle": ("Seattle", "United States", "US"),
    "san francisco": ("San Francisco", "United States", "US"),
    "los angeles": ("Los Angeles", "United States", "US"),
    "austin": ("Austin", "United States", "US"),
    "chicago": ("Chicago", "United States", "US"),
    "toronto": ("Toronto", "Canada", "CA"),
    "vancouver": ("Vancouver", "Canada", "CA"),
    "montreal": ("Montreal", "Canada", "CA"),
    "sydney": ("Sydney", "Australia", "AU"),
    "melbourne": ("Melbourne", "Australia", "AU"),
    "brisbane": ("Brisbane", "Australia", "AU"),
    "perth": ("Perth", "Australia", "AU"),
    "beijing": ("Beijing", "China", "CN"),
    "shanghai": ("Shanghai", "China", "CN"),
    "shenzhen": ("Shenzhen", "China", "CN"),
    "hong kong": ("Hong Kong", "China", "CN"),
    "delhi": ("Delhi", "India", "IN"),
    "mumbai": ("Mumbai", "India", "IN"),
    "bangalore": ("Bengaluru", "India", "IN"),
    "pune": ("Pune", "India", "IN"),
    "seoul": ("Seoul", "South Korea", "KR"),
    "busan": ("Busan", "South Korea", "KR"),
    "taipei": ("Taipei", "Taiwan", "TW"),
    "dubai": ("Dubai", "United Arab Emirates", "AE"),
    "abu dhabi": ("Abu Dhabi", "United Arab Emirates", "AE"),
    "riyadh": ("Riyadh", "Saudi Arabia", "SA"),
    "doha": ("Doha", "Qatar", "QA"),
}

GLOBAL_KEYWORDS = {"global", "worldwide", "international"}
REMOTE_KEYWORDS = {"remote", "work from home", "wfh", "home office", "hybrid"}


def _normalize_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def location_type_suggestions(location_type: str) -> list[str]:
    normalized = str(location_type or "").strip().lower()
    if normalized == "global":
        return ["Global"]
    if normalized == "remote":
        return ["Remote", "Hybrid"]
    if normalized == "region":
        return list(REGION_SUGGESTIONS)
    if normalized == "country":
        countries = sorted({aliases[0] for aliases in COUNTRY_ALIASES_BY_CODE.values()})
        return countries
    if normalized == "city":
        pairs = sorted(
            {(item[0], item[1]) for item in CITY_LOOKUP.values()},
            key=lambda pair: (pair[1].casefold(), pair[0].casefold()),
        )
        return [f"{city}, {country}" for city, country in pairs]
    return []


def _resolve_country(raw_text: str) -> tuple[str, str]:
    text = _normalize_text(raw_text)
    if not text:
        return "", ""
    if text.casefold() in COUNTRY_LOOKUP:
        return COUNTRY_LOOKUP[text.casefold()]
    return "", ""


def infer_location_type(raw_text: str) -> str:
    text = _normalize_text(raw_text)
    if not text:
        return "country"
    lowered = text.casefold()
    if lowered in GLOBAL_KEYWORDS:
        return "global"
    if lowered in REMOTE_KEYWORDS:
        return "remote"
    if lowered in {region.casefold() for region in REGION_SUGGESTIONS}:
        return "region"
    country, _ = _resolve_country(text)
    if country:
        return "country"
    if "," in text:
        return "city"
    if lowered in CITY_LOOKUP:
        return "city"
    return "country"


def _sanitize_location_type(raw_type: str) -> str:
    normalized = str(raw_type or "").strip().lower()
    if normalized in LOCATION_TYPE_ORDER:
        return normalized
    return "country"


def normalize_location_entry(location_type: str, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    normalized_type = _sanitize_location_type(location_type)
    raw_label = _normalize_text(label)

    if normalized_type in {"global", "remote"}:
        canonical_label = "Global" if normalized_type == "global" else "Remote"
        return (
            {
                "type": normalized_type,
                "label": canonical_label,
                "country_code": "",
                "country": "",
                "city": "",
                "region": "",
                "lat": "",
                "lon": "",
                "normalized": True,
            },
            warnings,
        )

    if not raw_label:
        warnings.append("empty_label")
        return None, warnings

    if normalized_type == "region":
        region_match = next(
            (region for region in REGION_SUGGESTIONS if region.casefold() == raw_label.casefold()),
            "",
        )
        if not region_match:
            warnings.append("unknown_region")
        region_value = region_match or raw_label
        return (
            {
                "type": "region",
                "label": region_value,
                "country_code": "",
                "country": "",
                "city": "",
                "region": region_value,
                "lat": "",
                "lon": "",
                "normalized": bool(region_match),
            },
            warnings,
        )

    if normalized_type == "country":
        country, country_code = _resolve_country(raw_label)
        if not country:
            warnings.append("unknown_country")
            country = raw_label
        return (
            {
                "type": "country",
                "label": country,
                "country_code": country_code,
                "country": country,
                "city": "",
                "region": "",
                "lat": "",
                "lon": "",
                "normalized": bool(country_code),
            },
            warnings,
        )

    city_part = raw_label
    country_part = ""
    if "," in raw_label:
        left, right = raw_label.split(",", 1)
        city_part = _normalize_text(left)
        country_part = _normalize_text(right)

    city_key = city_part.casefold()
    country = ""
    country_code = ""
    city = city_part
    if city_key in CITY_LOOKUP:
        city, country, country_code = CITY_LOOKUP[city_key]
    if country_part:
        resolved_country, resolved_code = _resolve_country(country_part)
        if resolved_country:
            country = resolved_country
            country_code = resolved_code
        else:
            country = country_part
            warnings.append("unknown_country")

    if not country:
        warnings.append("city_without_country")

    normalized = bool(city_key in CITY_LOOKUP and country_code)
    if city_key not in CITY_LOOKUP:
        warnings.append("unknown_city")

    label_value = f"{city}, {country}" if country else city
    return (
        {
            "type": "city",
            "label": label_value,
            "country_code": country_code,
            "country": country,
            "city": city,
            "region": "",
            "lat": "",
            "lon": "",
            "normalized": normalized,
        },
        warnings,
    )


def sanitize_location_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized_type = _sanitize_location_type(str(entry.get("type", "")))
    label = _normalize_text(str(entry.get("label", "")))
    country_code = _normalize_text(str(entry.get("country_code", ""))).upper()
    country = _normalize_text(str(entry.get("country", "")))
    city = _normalize_text(str(entry.get("city", "")))
    region = _normalize_text(str(entry.get("region", "")))
    lat = _normalize_text(str(entry.get("lat", "")))
    lon = _normalize_text(str(entry.get("lon", "")))
    normalized = bool(entry.get("normalized", False))
    return {
        "type": normalized_type,
        "label": label,
        "country_code": country_code,
        "country": country,
        "city": city,
        "region": region,
        "lat": lat,
        "lon": lon,
        "normalized": normalized,
    }


def _entry_identity(entry: dict[str, Any]) -> str:
    normalized = sanitize_location_entry(entry)
    type_part = normalized["type"]
    label_part = normalized["label"].casefold()
    code_part = normalized["country_code"].casefold()
    return f"{type_part}|{label_part}|{code_part}"


def dedup_location_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = sanitize_location_entry(raw_entry)
        if not entry["label"]:
            continue
        key = _entry_identity(entry)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def location_entry_display(entry: dict[str, Any]) -> str:
    normalized = sanitize_location_entry(entry)
    return normalized["label"]


def preferred_locations_plain_text(entries: list[dict[str, Any]]) -> str:
    lines = [location_entry_display(item) for item in dedup_location_entries(entries)]
    return "\n".join(line for line in lines if line)


def _country_alias_terms(country_code: str, country: str) -> list[str]:
    terms: list[str] = []
    code = str(country_code or "").strip().upper()
    if code and code in COUNTRY_ALIASES_BY_CODE:
        terms.extend(COUNTRY_ALIASES_BY_CODE[code])
    elif country:
        terms.append(country)
    return terms


def location_entry_query_terms(entry: dict[str, Any]) -> list[str]:
    normalized = sanitize_location_entry(entry)
    location_type = normalized["type"]
    if location_type == "global":
        return ["Global", "Worldwide", "International"]
    if location_type == "remote":
        return ["Remote", "Work from home", "Hybrid"]
    if location_type == "region":
        return [normalized["region"] or normalized["label"]]
    if location_type == "country":
        return _country_alias_terms(normalized["country_code"], normalized["country"])
    if location_type == "city":
        terms: list[str] = []
        city = normalized["city"] or normalized["label"]
        country = normalized["country"]
        terms.append(city)
        if country:
            terms.append(f"{city} {country}")
            terms.extend(_country_alias_terms(normalized["country_code"], country))
        return terms
    return [normalized["label"]]


def encode_base_location_struct(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    payload = {"v": 1, "entry": sanitize_location_entry(entry)}
    return json.dumps(payload, ensure_ascii=False)


def decode_base_location_struct(raw_struct: str, fallback_text: str = "") -> dict[str, Any] | None:
    text = _normalize_text(raw_struct)
    if text:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                if isinstance(payload.get("entry"), dict):
                    entry = sanitize_location_entry(payload.get("entry"))
                    if entry["label"]:
                        return entry
                if isinstance(payload.get("type"), str) and isinstance(payload.get("label"), str):
                    entry = sanitize_location_entry(payload)
                    if entry["label"]:
                        return entry
        except Exception:
            pass

    fallback = _normalize_text(fallback_text)
    if not fallback:
        return None
    inferred_type = infer_location_type(fallback)
    entry, _ = normalize_location_entry(inferred_type, fallback)
    return entry


def encode_preferred_locations_struct(entries: list[dict[str, Any]]) -> str:
    normalized_items = dedup_location_entries(entries)
    if not normalized_items:
        return ""
    payload = {"v": 1, "items": normalized_items}
    return json.dumps(payload, ensure_ascii=False)


def decode_preferred_locations_struct(raw_struct: str, fallback_text: str = "") -> list[dict[str, Any]]:
    text = _normalize_text(raw_struct)
    if text:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                raw_items = payload.get("items")
                if isinstance(raw_items, list):
                    return dedup_location_entries([item for item in raw_items if isinstance(item, dict)])
            if isinstance(payload, list):
                return dedup_location_entries([item for item in payload if isinstance(item, dict)])
        except Exception:
            pass

    parsed: list[dict[str, Any]] = []
    for raw_line in str(fallback_text or "").splitlines():
        text_line = _normalize_text(raw_line)
        if not text_line:
            continue
        inferred_type = infer_location_type(text_line)
        entry, _ = normalize_location_entry(inferred_type, text_line)
        if entry is not None:
            parsed.append(entry)
    return dedup_location_entries(parsed)


def candidate_location_query_terms(
    base_location_struct: str,
    preferred_locations_struct: str,
    base_location_text: str,
    preferred_locations_text: str,
) -> list[str]:
    entries = decode_preferred_locations_struct(preferred_locations_struct, preferred_locations_text)
    base_entry = decode_base_location_struct(base_location_struct, base_location_text)
    if base_entry is not None:
        entries.append(base_entry)

    ordered: list[str] = []
    seen: set[str] = set()
    for entry in dedup_location_entries(entries):
        for term in location_entry_query_terms(entry):
            text = _normalize_text(term)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(text)
    return ordered


def candidate_location_preference_text(
    base_location_struct: str,
    preferred_locations_struct: str,
    base_location_text: str,
    preferred_locations_text: str,
) -> str:
    preferred_entries = decode_preferred_locations_struct(preferred_locations_struct, preferred_locations_text)
    preferred_text = preferred_locations_plain_text(preferred_entries).strip()
    if preferred_text:
        return preferred_text

    base_entry = decode_base_location_struct(base_location_struct, base_location_text)
    if base_entry is not None:
        return location_entry_display(base_entry)

    fallback = _normalize_text(preferred_locations_text or base_location_text)
    return fallback or "Global"
