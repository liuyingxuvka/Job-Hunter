from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from .discovery import merge_unique_strings
from ..output.final_output import (
    canonical_job_url,
    infer_region_tag,
    infer_source_quality,
    is_aggregator_host,
    is_likely_parking_host,
    normalize_job_url,
)

JOB_LINK_HARD_CAP_PER_COMPANY = 40
SUPPORTED_DIRECT_ATS_TYPES = frozenset({"greenhouse", "lever", "smartrecruiters"})
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_JOB_WORD_RE = re.compile(
    r"(engineer|scientist|researcher|specialist|manager|director|lead|technician|analyst|developer|architect|intern|co-?op|graduate|postdoc|工程师|研究员|经理|总监|实习)",
    flags=re.IGNORECASE,
)
_GENERIC_CAREERS_PATH_RE = re.compile(
    r"^/(careers?|jobs?|open-jobs|vacancies|opportunities)(?:\.html?)?$",
    flags=re.IGNORECASE,
)
_COMMON_CAREERS_PATHS = (
    "/careers",
    "/career",
    "/jobs",
    "/join-us",
    "/about/careers",
    "/about-us/careers",
    "/company/careers",
    "/work-with-us",
)
_ATS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greenhouse", re.compile(r"boards\.greenhouse\.io/([^/]+)", flags=re.IGNORECASE)),
    ("lever", re.compile(r"jobs\.lever\.co/([^/]+)", flags=re.IGNORECASE)),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([^/]+)", flags=re.IGNORECASE)),
    ("workable", re.compile(r"apply\.workable\.com/([^/]+)", flags=re.IGNORECASE)),
    ("workable", re.compile(r"([^/.]+)\.workable\.com", flags=re.IGNORECASE)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/]+)", flags=re.IGNORECASE)),
    ("workday", re.compile(r"myworkdayjobs\.com/([^/]+)", flags=re.IGNORECASE)),
)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_H1_TAG_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", flags=re.IGNORECASE | re.DOTALL)
_APPLY_TEXT_RE = re.compile(
    r"\b(apply|submit application|bewerben|candidature|postuler|応募|申請|申请)\b",
    flags=re.IGNORECASE,
)
_LOCATION_LABEL_RE = re.compile(
    r"(?:location|job location|地点|工作地点)\s*[:：]\s*([^\n|]{2,120})",
    flags=re.IGNORECASE,
)


class _CareersPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_ld_json = False
        self._current_script: list[str] = []
        self._current_href: str = ""
        self._current_link_text: list[str] = []
        self.json_ld_blocks: list[str] = []
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {str(key): str(value or "") for key, value in attrs}
        if tag.lower() == "script" and attr_map.get("type", "").lower() == "application/ld+json":
            self._in_ld_json = True
            self._current_script = []
            return
        if tag.lower() == "a" and attr_map.get("href"):
            self._current_href = attr_map["href"]
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._in_ld_json:
            self._current_script.append(data)
        if self._current_href:
            self._current_link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_ld_json:
            payload = "".join(self._current_script).strip()
            if payload:
                self.json_ld_blocks.append(payload)
            self._in_ld_json = False
            self._current_script = []
            return
        if tag.lower() == "a" and self._current_href:
            text = _WHITESPACE_RE.sub(" ", "".join(self._current_link_text)).strip()
            self.links.append({"href": self._current_href, "text": text})
            self._current_href = ""
            self._current_link_text = []


def _to_number(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def detect_ats_from_url(url: object) -> dict[str, str] | None:
    text = str(url or "").strip()
    if not text:
        return None
    for ats_type, pattern in _ATS_PATTERNS:
        match = pattern.search(text)
        if match and match.group(1):
            return {"type": ats_type, "id": match.group(1)}
    return None


def normalize_company_job_coverage_state(raw: object) -> dict[str, Any]:
    source = dict(raw) if isinstance(raw, Mapping) else {}
    recent_seen_job_urls = merge_unique_strings(
        [
            normalize_job_url(item)
            for item in (source.get("recentSeenJobUrls") if isinstance(source.get("recentSeenJobUrls"), list) else [])
            if normalize_job_url(item)
        ]
    )
    cursor = max(0, int(_to_number(source.get("cursor"), 0)))
    last_pool_size = max(0, int(_to_number(source.get("lastPoolSize"), 0)))
    if not recent_seen_job_urls and cursor <= 0 and last_pool_size <= 0:
        return {}
    normalized = {
        "recentSeenJobUrls": recent_seen_job_urls,
        "cursor": cursor,
    }
    if last_pool_size > 0:
        normalized["lastPoolSize"] = last_pool_size
    return normalized


def get_normalized_company_job_url_list(company: Mapping[str, Any], field_name: str) -> list[str]:
    values = company.get(field_name)
    if not isinstance(values, list):
        return []
    normalized = [normalize_job_url(item) for item in values]
    return merge_unique_strings([value for value in normalized if value])


def dedupe_jobs_by_normalized_url(jobs: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for job in jobs:
        normalized_url = normalize_job_url(job.get("url") or "")
        if not normalized_url or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        normalized_job = dict(job)
        normalized_job["url"] = normalized_url
        deduped.append(normalized_job)
    return deduped


def select_company_jobs_for_coverage(
    *,
    company: Mapping[str, Any],
    jobs: list[Mapping[str, Any]],
    limit: object,
) -> dict[str, Any]:
    existing_coverage = normalize_company_job_coverage_state(company.get("jobLinkCoverage"))
    unique_jobs = dedupe_jobs_by_normalized_url(jobs)
    normalized_limit = max(1, int(_to_number(limit, JOB_LINK_HARD_CAP_PER_COMPANY)))
    if not unique_jobs:
        next_coverage: dict[str, Any] = {}
        return {
            "jobs": [],
            "jobLinkCoverage": next_coverage,
            "changed": existing_coverage != next_coverage,
            "poolSize": 0,
        }
    cursor = max(0, int(_to_number(existing_coverage.get("cursor"), 0))) % len(unique_jobs)
    rotated_jobs = unique_jobs[cursor:] + unique_jobs[:cursor]
    recent_seen = set(
        item
        for item in existing_coverage.get("recentSeenJobUrls", [])
        if isinstance(item, str) and item.strip()
    )
    unseen_jobs: list[dict[str, Any]] = []
    seen_jobs: list[dict[str, Any]] = []
    for job in rotated_jobs:
        if job["url"] in recent_seen:
            seen_jobs.append(job)
        else:
            unseen_jobs.append(job)
    selected_jobs = (unseen_jobs + seen_jobs)[: min(normalized_limit, len(unique_jobs))]
    recent_window_size = min(len(unique_jobs), JOB_LINK_HARD_CAP_PER_COMPANY)
    next_coverage = {
        "recentSeenJobUrls": merge_unique_strings(
            [job["url"] for job in selected_jobs],
            existing_coverage.get("recentSeenJobUrls"),
        )[:recent_window_size],
        "cursor": (cursor + len(selected_jobs)) % len(unique_jobs),
        "lastPoolSize": len(unique_jobs),
    }
    return {
        "jobs": selected_jobs,
        "jobLinkCoverage": next_coverage,
        "changed": existing_coverage != next_coverage,
        "poolSize": len(unique_jobs),
    }


def strip_html_to_text(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = unescape(cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def extract_fallback_job_title_from_html(html: str, page_url: str) -> str:
    for pattern in (_H1_TAG_RE, _TITLE_TAG_RE):
        match = pattern.search(str(html or ""))
        if not match:
            continue
        candidate = sanitize_job_title_candidate(strip_html_to_text(match.group(1)))
        if candidate:
            return candidate
    path_hint = str(urlsplit(page_url).path or "").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
    return sanitize_job_title_candidate(path_hint)


def extract_apply_url_from_html(html: str, page_url: str) -> str:
    parser = _CareersPageParser()
    parser.feed(str(html or ""))
    for link in parser.links:
        text = str(link.get("text") or "").strip()
        if not text or not _APPLY_TEXT_RE.search(text):
            continue
        normalized = normalize_job_url(urljoin(page_url, link.get("href") or ""))
        if normalized:
            return normalized
    return ""


def extract_location_from_text(text: object) -> str:
    normalized = str(text or "")
    if not normalized:
        return ""
    match = _LOCATION_LABEL_RE.search(normalized[:4000])
    if not match:
        return ""
    return _WHITESPACE_RE.sub(" ", match.group(1)).strip(" |,;")


def is_stale(date_posted: object, max_age_days: int) -> bool:
    if not date_posted or max_age_days <= 0:
        return False
    parsed = _parse_datetime(date_posted)
    if parsed is None:
        return False
    age_days = (datetime.now(timezone.utc) - parsed).total_seconds() / (60 * 60 * 24)
    return age_days > max_age_days


def sanitize_job_title_candidate(text: object) -> str:
    title = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if not title:
        return ""
    if is_likely_noise_title(title):
        return ""
    if not _JOB_WORD_RE.search(title):
        words = [part for part in title.split(" ") if part]
        if len(words) < 2 or len(title) < 8:
            return ""
    return title


def is_likely_noise_title(text: object) -> bool:
    title = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    if not title:
        return True
    if len(title) > 180:
        return True
    if re.search(r"</?[a-z][^>]*>", title, flags=re.IGNORECASE):
        return True
    if re.search(
        r"^(apply|apply now|view job|open job|job details?|learn more|details?|read more|continue|search|menu|navigation|careers?|join us)$",
        title,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def is_ats_host(raw_url: object) -> bool:
    text = str(raw_url or "").strip().lower()
    return bool(
        re.search(
            r"greenhouse\.io|lever\.co|smartrecruiters\.com|workable\.com|ashbyhq\.com|myworkdayjobs\.com|icims\.com|jobvite\.com|dayforcehcm\.com|successfactors\.com|bamboohr\.com|recruitee\.com|personio\.(?:de|com)|teamtailor\.com|workforcenow\.adp\.com|jobylon\.com",
            text,
        )
    )


def is_generic_careers_url(raw_url: object) -> bool:
    normalized = normalize_job_url(raw_url)
    if not normalized:
        return False
    try:
        path = urlsplit(normalized).path or "/"
    except Exception:
        path = "/"
    return bool(_GENERIC_CAREERS_PATH_RE.match(path or "/"))


def is_likely_job_url(raw_url: object) -> bool:
    normalized = normalize_job_url(raw_url)
    if not normalized or is_likely_parking_host(normalized) or is_generic_careers_url(normalized):
        return False
    if re.search(r"/jobs?/[^/]+|/job/[^/]+|/positions?/[^/]+|/vacancies/[^/]+|/openings/[^/]+|/opportunities/[^/]+|/posting/[^/]+|/role/[^/]+", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"[?&](gh_jid|jobid|job_id|jid|req|reqid|rid|lever-source)=", normalized, flags=re.IGNORECASE):
        return True
    if is_ats_host(normalized) and re.search(r"/(job|jobs|position|posting|vacanc|careers?)", normalized, flags=re.IGNORECASE):
        return True
    return False


def has_job_signal(*, title: object, url: object, summary: object) -> bool:
    clean_title = sanitize_job_title_candidate(title)
    clean_summary = _WHITESPACE_RE.sub(" ", str(summary or "")).strip()
    normalized_url = normalize_job_url(url)
    if not normalized_url or is_aggregator_host(normalized_url) or is_likely_parking_host(normalized_url):
        return False
    if is_generic_careers_url(normalized_url):
        return False
    if is_likely_job_url(normalized_url):
        return True
    if is_ats_host(normalized_url) and (_JOB_WORD_RE.search(clean_title) or _JOB_WORD_RE.search(clean_summary)):
        return True
    return bool(_JOB_WORD_RE.search(clean_title) and re.search(r"careers?|jobs?|openings?|vacancies?|requisition", normalized_url, flags=re.IGNORECASE))


def extract_all_json_ld_job_postings(html: str) -> list[dict[str, Any]]:
    parser = _CareersPageParser()
    parser.feed(html)
    candidates: list[Any] = []
    for block in parser.json_ld_blocks:
        try:
            parsed = json.loads(block)
        except Exception:
            continue
        if isinstance(parsed, list):
            candidates.extend(parsed)
        else:
            candidates.append(parsed)
    postings: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        raw_type = candidate.get("@type")
        is_job_posting = raw_type == "JobPosting" or (
            isinstance(raw_type, list) and "JobPosting" in raw_type
        )
        if is_job_posting:
            postings.append(dict(candidate))
    return postings


def job_posting_to_fields(job_posting: Mapping[str, Any]) -> dict[str, str]:
    description_html = str(job_posting.get("description") or "").strip()
    fields = {
        "company": "",
        "title": str(job_posting.get("title") or "").strip(),
        "description": strip_html_to_text(description_html),
        "datePosted": _to_iso_datetime(job_posting.get("datePosted") or ""),
        "location": "",
    }
    for org_key in ("hiringOrganization", "organization"):
        payload = job_posting.get(org_key)
        if isinstance(payload, Mapping) and str(payload.get("name") or "").strip():
            fields["company"] = str(payload.get("name") or "").strip()
            break

    def normalize_location_item(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, Mapping):
            return ""
        address = value.get("address")
        if isinstance(address, str):
            return address.strip()
        if isinstance(address, Mapping):
            parts = [
                str(address.get("addressLocality") or "").strip(),
                str(address.get("addressRegion") or "").strip(),
                str(address.get("addressCountry") or "").strip(),
            ]
            return ", ".join(part for part in parts if part)
        return ""

    job_location = job_posting.get("jobLocation")
    if isinstance(job_location, list):
        for item in job_location:
            normalized = normalize_location_item(item)
            if normalized:
                fields["location"] = normalized
                break
    else:
        fields["location"] = normalize_location_item(job_location)
    if not fields["location"]:
        requirements = job_posting.get("applicantLocationRequirements")
        if isinstance(requirements, Mapping):
            requirement_name = str(requirements.get("name") or "").strip()
            if requirement_name:
                fields["location"] = requirement_name
    if not fields["location"] and str(job_posting.get("jobLocationType") or "").strip():
        fields["location"] = str(job_posting.get("jobLocationType") or "").strip()
    return fields


def collect_careers_page_job_candidates(html: str, page_url: str) -> list[dict[str, Any]]:
    postings = extract_all_json_ld_job_postings(html)
    results: list[dict[str, Any]] = []
    for posting in postings:
        fields = job_posting_to_fields(posting)
        posting_url = normalize_job_url(posting.get("url") or "") or normalize_job_url(page_url)
        if not posting_url:
            continue
        results.append(
            {
                "title": str(fields.get("title") or "").strip(),
                "location": str(fields.get("location") or "").strip(),
                "url": posting_url,
                "datePosted": str(fields.get("datePosted") or "").strip(),
                "summary": str(fields.get("description") or "").strip(),
            }
        )
    if results:
        return dedupe_jobs_by_normalized_url(results)

    parser = _CareersPageParser()
    parser.feed(html)
    link_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link in parser.links:
        absolute_url = normalize_job_url(urljoin(page_url, link.get("href") or ""))
        if not absolute_url or absolute_url in seen_urls:
            continue
        title = sanitize_job_title_candidate(link.get("text") or "")
        if not has_job_signal(title=title, url=absolute_url, summary=""):
            continue
        seen_urls.add(absolute_url)
        link_jobs.append(
            {
                "title": title,
                "location": "",
                "url": absolute_url,
                "datePosted": "",
                "summary": "",
            }
        )
    return link_jobs


def normalize_company_job(
    raw_job: Mapping[str, Any],
    *,
    company_name: str,
    ats_type: str,
    company_tags: list[str],
    config: Mapping[str, Any] | None,
    discovered_at: str,
) -> dict[str, Any]:
    job = dict(raw_job)
    url = normalize_job_url(job.get("url") or "")
    normalized = {
        "title": str(job.get("title") or "").strip(),
        "company": company_name,
        "location": str(job.get("location") or "").strip(),
        "url": url,
        "canonicalUrl": normalize_job_url(job.get("canonicalUrl") or "") or canonical_job_url({"url": url}) or url,
        "datePosted": str(job.get("datePosted") or "").strip(),
        "dateFound": str(job.get("dateFound") or "").strip() or discovered_at,
        "summary": str(job.get("summary") or "").strip(),
        "source": str(job.get("source") or "").strip() or f"company:{company_name}:{ats_type}",
        "sourceType": str(job.get("sourceType") or "").strip() or "company",
        "companyTags": list(company_tags),
    }
    normalized["sourceQuality"] = infer_source_quality(normalized, config)
    normalized["regionTag"] = infer_region_tag(normalized)
    return normalized


def _to_iso_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("Z", "+00:00")
    if len(normalized) > 10 and normalized[10] == " ":
        normalized = normalized.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return text


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = [
    "JOB_LINK_HARD_CAP_PER_COMPANY",
    "_COMMON_CAREERS_PATHS",
    "SUPPORTED_DIRECT_ATS_TYPES",
    "collect_careers_page_job_candidates",
    "dedupe_jobs_by_normalized_url",
    "detect_ats_from_url",
    "extract_all_json_ld_job_postings",
    "extract_apply_url_from_html",
    "extract_fallback_job_title_from_html",
    "extract_location_from_text",
    "get_normalized_company_job_url_list",
    "has_job_signal",
    "is_ats_host",
    "is_generic_careers_url",
    "is_likely_job_url",
    "is_likely_noise_title",
    "is_stale",
    "job_posting_to_fields",
    "normalize_company_job",
    "normalize_company_job_coverage_state",
    "sanitize_job_title_candidate",
    "select_company_jobs_for_coverage",
    "strip_html_to_text",
]
