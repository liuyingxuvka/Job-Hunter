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
from .ranking_thresholds import JOB_PRERANK_MIN_SCORE
from ..output.final_output import (
    canonical_job_url,
    infer_region_tag,
    infer_source_quality,
    is_aggregator_host,
    is_likely_parking_host,
    normalize_job_url,
)

JOB_LINK_HARD_CAP_PER_COMPANY = 40
NON_ATS_LISTING_PAGES_PER_RUN = 3
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
_EXPLICIT_NON_JOB_PATHS = frozenset(
    {
        "/dashboard",
        "/jobs/alerts",
        "/jobs/recommendations",
        "/jobs/results",
        "/how-we-hire",
        "/how-we-work",
        "/privacy-policy",
        "/profile",
        "/saved-jobs",
        "/search-results",
        "/teams",
    }
)
_EXPLICIT_NON_JOB_PATH_SEGMENTS = frozenset(
    {
        "alerts",
        "dashboard",
        "how-we-hire",
        "how-we-work",
        "privacy-policy",
        "profile",
        "recommendations",
        "results",
        "saved-jobs",
        "search-results",
        "teams",
    }
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


def normalize_job_page_coverage_state(raw: object) -> dict[str, Any]:
    source = dict(raw) if isinstance(raw, Mapping) else {}
    pending_listing_urls = merge_unique_strings(
        [
            normalize_job_url(item)
            for item in (source.get("pendingListingUrls") if isinstance(source.get("pendingListingUrls"), list) else [])
            if normalize_job_url(item)
        ]
    )
    visited_listing_urls = merge_unique_strings(
        [
            normalize_job_url(item)
            for item in (source.get("visitedListingUrls") if isinstance(source.get("visitedListingUrls"), list) else [])
            if normalize_job_url(item)
        ]
    )
    raw_listing_page_cache = (
        dict(source.get("listingPageCache") or {})
        if isinstance(source.get("listingPageCache"), Mapping)
        else {}
    )
    listing_page_cache: dict[str, dict[str, Any]] = {}
    for raw_url, raw_entry in raw_listing_page_cache.items():
        normalized_url = normalize_job_url(raw_url)
        if not normalized_url or not isinstance(raw_entry, Mapping):
            continue
        page_fingerprint = str(raw_entry.get("pageFingerprint") or "").strip()
        jobs = dedupe_jobs_by_normalized_url(
            [dict(item) for item in raw_entry.get("jobs", []) if isinstance(item, Mapping)]
        )
        next_listing_urls = merge_unique_strings(
            [
                normalize_job_url(item)
                for item in (
                    raw_entry.get("nextListingUrls")
                    if isinstance(raw_entry.get("nextListingUrls"), list)
                    else []
                )
                if normalize_job_url(item)
            ]
        )
        if not page_fingerprint and not jobs and not next_listing_urls:
            continue
        listing_page_cache[normalized_url] = {
            "pageFingerprint": page_fingerprint,
            "jobs": jobs,
            "nextListingUrls": next_listing_urls,
        }
    raw_company_search_cache = (
        dict(source.get("companySearchCache") or {})
        if isinstance(source.get("companySearchCache"), Mapping)
        else {}
    )
    company_search_cache: dict[str, dict[str, Any]] = {}
    for raw_key, raw_entry in raw_company_search_cache.items():
        cache_key = str(raw_key or "").strip()
        if not cache_key or not isinstance(raw_entry, Mapping):
            continue
        jobs = dedupe_jobs_by_normalized_url(
            [dict(item) for item in raw_entry.get("jobs", []) if isinstance(item, Mapping)]
        )
        query = str(raw_entry.get("query") or "").strip()
        company_website = normalize_job_url(raw_entry.get("companyWebsite"))
        jobs_page_url = normalize_job_url(raw_entry.get("jobsPageUrl"))
        page_type = str(raw_entry.get("pageType") or "").strip().lower()
        sample_job_urls = merge_unique_strings(
            [
                normalize_job_url(item)
                for item in (
                    raw_entry.get("sampleJobUrls")
                    if isinstance(raw_entry.get("sampleJobUrls"), list)
                    else []
                )
                if normalize_job_url(item)
            ]
        )
        if not query and not jobs and not company_website and not jobs_page_url and not sample_job_urls:
            continue
        company_search_cache[cache_key] = {
            "query": query,
            "companyWebsite": company_website,
            "jobsPageUrl": jobs_page_url,
            "pageType": page_type,
            "sampleJobUrls": sample_job_urls,
            "jobs": jobs,
        }
    coverage_complete = bool(source.get("coverageComplete"))
    if (
        not pending_listing_urls
        and not visited_listing_urls
        and not coverage_complete
        and not listing_page_cache
        and not company_search_cache
    ):
        return {}
    normalized = {
        "pendingListingUrls": pending_listing_urls,
        "visitedListingUrls": visited_listing_urls,
        "coverageComplete": coverage_complete,
    }
    if listing_page_cache:
        normalized["listingPageCache"] = listing_page_cache
    if company_search_cache:
        normalized["companySearchCache"] = company_search_cache
    return normalized


def select_listing_urls_for_processing(
    *,
    entry_url: object,
    coverage_state: Mapping[str, Any] | None,
    limit: object,
    allow_entry_retry_when_coverage_complete: bool = False,
) -> list[str]:
    normalized_limit = max(1, int(_to_number(limit, 1)))
    normalized_entry_url = normalize_job_url(entry_url)
    normalized_state = normalize_job_page_coverage_state(coverage_state)
    if normalized_state.get("coverageComplete"):
        if normalized_entry_url and allow_entry_retry_when_coverage_complete:
            return [normalized_entry_url]
        return []
    pending_listing_urls = [
        normalize_job_url(item)
        for item in normalized_state.get("pendingListingUrls", [])
        if normalize_job_url(item)
    ]
    if pending_listing_urls:
        return pending_listing_urls[:normalized_limit]
    if normalized_entry_url:
        return [normalized_entry_url]
    return []


def update_job_page_coverage_state(
    *,
    entry_url: object,
    coverage_state: Mapping[str, Any] | None,
    processed_listing_urls: list[str] | None,
    discovered_listing_urls: list[str] | None,
    listing_page_cache_updates: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_entry_url = normalize_job_url(entry_url)
    current = normalize_job_page_coverage_state(coverage_state)
    pending_listing_urls = [
        normalize_job_url(item)
        for item in current.get("pendingListingUrls", [])
        if normalize_job_url(item)
    ]
    visited_listing_urls = [
        normalize_job_url(item)
        for item in current.get("visitedListingUrls", [])
        if normalize_job_url(item)
    ]
    processed = [
        normalize_job_url(item)
        for item in (processed_listing_urls or [])
        if normalize_job_url(item)
    ]
    discovered = [
        normalize_job_url(item)
        for item in (discovered_listing_urls or [])
        if normalize_job_url(item)
    ]
    listing_page_cache = dict(current.get("listingPageCache") or {})
    raw_cache_updates = (
        dict(listing_page_cache_updates or {})
        if isinstance(listing_page_cache_updates, Mapping)
        else {}
    )

    pending_set = set(pending_listing_urls)
    visited_set = set(visited_listing_urls)
    processed_set = set(processed)
    for url in processed:
        visited_set.add(url)
        pending_set.discard(url)

    newly_discovered_listing_urls: list[str] = []
    for url in merge_unique_strings(discovered):
        if url == normalized_entry_url:
            continue
        if url in visited_set or url in pending_set:
            continue
        pending_listing_urls.append(url)
        pending_set.add(url)
        newly_discovered_listing_urls.append(url)

    pending_listing_urls = [
        url for url in merge_unique_strings(pending_listing_urls)
        if url not in processed_set
    ]
    visited_listing_urls = merge_unique_strings(visited_listing_urls, processed)
    for raw_url, raw_entry in raw_cache_updates.items():
        normalized_url = normalize_job_url(raw_url)
        if not normalized_url or not isinstance(raw_entry, Mapping):
            continue
        page_fingerprint = str(raw_entry.get("pageFingerprint") or "").strip()
        jobs = dedupe_jobs_by_normalized_url(
            [dict(item) for item in raw_entry.get("jobs", []) if isinstance(item, Mapping)]
        )
        next_listing_urls = merge_unique_strings(
            [
                normalize_job_url(item)
                for item in (
                    raw_entry.get("nextListingUrls")
                    if isinstance(raw_entry.get("nextListingUrls"), list)
                    else []
                )
                if normalize_job_url(item)
            ]
        )
        if not page_fingerprint and not jobs and not next_listing_urls:
            continue
        listing_page_cache[normalized_url] = {
            "pageFingerprint": page_fingerprint,
            "jobs": jobs,
            "nextListingUrls": next_listing_urls,
        }

    coverage_complete = not pending_listing_urls

    company_search_cache = dict(current.get("companySearchCache") or {})

    if (
        not pending_listing_urls
        and not visited_listing_urls
        and not coverage_complete
        and not listing_page_cache
        and not company_search_cache
    ):
        return {}
    normalized = {
        "pendingListingUrls": pending_listing_urls,
        "visitedListingUrls": visited_listing_urls,
        "coverageComplete": coverage_complete,
    }
    if listing_page_cache:
        normalized["listingPageCache"] = listing_page_cache
    if company_search_cache:
        normalized["companySearchCache"] = company_search_cache
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
    completed_job_urls: set[str] | None = None,
) -> dict[str, Any]:
    existing_coverage = normalize_company_job_coverage_state(company.get("jobLinkCoverage"))
    unique_jobs = dedupe_jobs_by_normalized_url(jobs)
    normalized_limit = max(1, int(_to_number(limit, JOB_LINK_HARD_CAP_PER_COMPANY)))
    normalized_completed_job_urls = {
        normalize_job_url(item)
        for item in (completed_job_urls or set())
        if normalize_job_url(item)
    }
    not_completed_jobs = [
        job for job in unique_jobs if job["url"] not in normalized_completed_job_urls
    ]
    eligible_jobs = [job for job in not_completed_jobs if not _job_ai_prerank_excluded(job)]
    completed_jobs_excluded = len(unique_jobs) - len(not_completed_jobs)
    pending_prerank_jobs_excluded = sum(
        1 for job in not_completed_jobs if bool(job.get("aiPreRankPending"))
    )
    low_prerank_jobs_excluded = len(not_completed_jobs) - len(eligible_jobs) - pending_prerank_jobs_excluded
    if not unique_jobs:
        next_coverage: dict[str, Any] = {}
        return {
            "jobs": [],
            "jobLinkCoverage": next_coverage,
            "changed": existing_coverage != next_coverage,
            "poolSize": 0,
            "excludedCompletedCount": 0,
            "excludedLowPrerankCount": 0,
            "excludedPendingPrerankCount": 0,
            "pendingPoolSize": 0,
        }
    if not eligible_jobs:
        next_coverage = {}
        return {
            "jobs": [],
            "jobLinkCoverage": next_coverage,
            "changed": existing_coverage != next_coverage,
            "poolSize": 0,
            "excludedCompletedCount": completed_jobs_excluded,
            "excludedLowPrerankCount": low_prerank_jobs_excluded,
            "excludedPendingPrerankCount": pending_prerank_jobs_excluded,
            "pendingPoolSize": 0,
        }
    ranked_jobs: list[tuple[tuple[int, float, int], int, dict[str, Any]]] = []
    for index, job in enumerate(eligible_jobs):
        ranked_jobs.append((_job_schedule_sort_key(job), index, job))

    ranked_jobs.sort(key=lambda item: (-item[0][0], -item[0][1], -item[0][2], item[1]))
    selected_jobs = [
        item[2]
        for item in ranked_jobs[: min(normalized_limit, len(eligible_jobs))]
    ]
    next_coverage: dict[str, Any] = {}
    return {
        "jobs": selected_jobs,
        "jobLinkCoverage": next_coverage,
        "changed": existing_coverage != next_coverage,
        "poolSize": len(eligible_jobs),
        "excludedCompletedCount": completed_jobs_excluded,
        "excludedLowPrerankCount": low_prerank_jobs_excluded,
        "excludedPendingPrerankCount": pending_prerank_jobs_excluded,
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
        r"^saved\s+jobs?$",
        title,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"^(?:dashboard|job search|teams?|profiles?|your career|privacy policy|applicant (?:&|and) candidate privacy|candidate privacy|search results|recommended jobs|job alerts|how we hire|how we work|know your rights(?::.*)?)$",
        title,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"^(?:interns?|students?|graduates?)$",
        title,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"^(apply|apply now|view job|open job|job details?|learn more|details?|read more|continue|search|menu|navigation|careers?|join us)$",
        title,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"^(?:see|view|browse|explore)\s+(?:our\s+|all\s+|internal\s+|open\s+|current\s+|available\s+)*(?:engineering|product|sales|marketing|design|operations|team|teams|job|jobs|position|positions)\b",
        title,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\b(?:open|available|internal)\s+(?:job|jobs|position|positions)\b",
        title,
        flags=re.IGNORECASE,
    ) and not _JOB_WORD_RE.search(title):
        return True
    if re.search(
        r"^(?:internal|open|available|current)\s+(?:open\s+)?positions?$",
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
    path = str(urlsplit(normalized).path or "")
    normalized_path = path.rstrip("/") or "/"
    if normalized_path.casefold() in _EXPLICIT_NON_JOB_PATHS:
        return False
    path_segments = [segment for segment in path.split("/") if segment]
    if path_segments and path_segments[-1].casefold() in _EXPLICIT_NON_JOB_PATH_SEGMENTS:
        return False
    if len(path_segments) >= 3 and [segment.casefold() for segment in path_segments[-2:]] == ["jobs", "results"]:
        return False
    if len(path_segments) >= 2 and [segment.casefold() for segment in path_segments[-2:]] == ["jobs", "alerts"]:
        return False
    if len(path_segments) >= 2 and [segment.casefold() for segment in path_segments[-2:]] == ["jobs", "recommendations"]:
        return False
    if len(path_segments) >= 3 and [segment.casefold() for segment in path_segments[-3:-1]] == ["jobs", "results"]:
        tail = path_segments[-1]
        normalized_tail = tail.replace("-", " ").replace("_", " ")
        if not any(char.isdigit() for char in tail) and not _JOB_WORD_RE.search(normalized_tail):
            return False
    pdf_name = path_segments[-1] if path_segments else normalized
    if normalized.casefold().endswith(".pdf") and re.search(
        r"(job[-_ ]?spec|job[-_ ]?description|vacanc|position|opening|role|apply|recruit)",
        pdf_name,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(r"/jobs?/[^/]+|/job/[^/]+|/positions?/[^/]+|/vacancies/[^/]+|/openings/[^/]+|/opportunities/[^/]+|/posting/[^/]+|/role/[^/]+", path, flags=re.IGNORECASE):
        return True
    if re.search(r"[?&](gh_jid|jobid|job_id|jid|req|reqid|rid|lever-source)=", normalized, flags=re.IGNORECASE):
        return True
    if is_ats_host(normalized) and re.search(r"/(job|jobs|position|posting|vacanc|apply|role)s?(?:/|$)", path, flags=re.IGNORECASE):
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
    if not clean_title and not _JOB_WORD_RE.search(clean_summary):
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


def collect_careers_page_job_candidates(
    html: str,
    page_url: str,
    *,
    sample_job_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    del sample_job_urls
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
    return dedupe_jobs_by_normalized_url(link_jobs)


def collect_careers_page_link_snapshots(
    html: str,
    page_url: str,
    *,
    max_links: int = 120,
) -> list[dict[str, str]]:
    normalized_page_url = normalize_job_url(page_url)
    if not normalized_page_url or max_links <= 0:
        return []
    parser = _CareersPageParser()
    parser.feed(str(html or ""))
    snapshots: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for link in parser.links:
        absolute_url = normalize_job_url(urljoin(normalized_page_url, link.get("href") or ""))
        if not absolute_url or absolute_url == normalized_page_url or absolute_url in seen_urls:
            continue
        if is_aggregator_host(absolute_url) or is_likely_parking_host(absolute_url):
            continue
        if not has_job_signal(
            title=link.get("text") or "",
            url=absolute_url,
            summary="",
        ):
            continue
        seen_urls.add(absolute_url)
        snapshots.append(
            {
                "text": _WHITESPACE_RE.sub(" ", str(link.get("text") or "")).strip(),
                "url": absolute_url,
            }
        )
        if len(snapshots) >= max_links:
            break
    return snapshots


def filter_jobs_by_sample_job_urls(
    jobs: list[Mapping[str, Any]],
    sample_job_urls: list[str] | None,
) -> list[dict[str, Any]]:
    hints = _build_sample_job_url_hints(sample_job_urls)
    normalized_jobs = [dict(job) for job in jobs if isinstance(job, Mapping)]
    if not hints or not normalized_jobs:
        return dedupe_jobs_by_normalized_url(normalized_jobs)
    matched_jobs = [
        job for job in normalized_jobs if _matches_sample_job_url_hints(job.get("url") or "", hints)
    ]
    if matched_jobs:
        return dedupe_jobs_by_normalized_url(matched_jobs)
    return dedupe_jobs_by_normalized_url(normalized_jobs)


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
        "availabilityHint": str(job.get("availabilityHint") or "").strip(),
        "source": str(job.get("source") or "").strip() or f"company:{company_name}:{ats_type}",
        "sourceType": str(job.get("sourceType") or "").strip() or "company",
        "companyTags": list(company_tags),
    }
    normalized["sourceQuality"] = infer_source_quality(normalized, config)
    normalized["regionTag"] = infer_region_tag(normalized)
    return normalized


def _build_sample_job_url_hints(sample_job_urls: list[str] | None) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, bool]] = set()
    for raw_url in sample_job_urls or []:
        normalized_url = normalize_job_url(raw_url)
        if not normalized_url:
            continue
        host = _normalized_host(normalized_url)
        if not host:
            continue
        path_segments = [segment for segment in (urlsplit(normalized_url).path or "").split("/") if segment]
        if not path_segments:
            continue
        prefix_segments = _sample_prefix_segments(path_segments)
        prefix = "/" + "/".join(prefix_segments) + "/"
        requires_digit = any(any(char.isdigit() for char in segment) for segment in path_segments[len(prefix_segments) :])
        key = (host, prefix, len(path_segments), requires_digit)
        if key in seen:
            continue
        seen.add(key)
        hints.append(
            {
                "host": host,
                "prefix": prefix,
                "minSegments": len(path_segments),
                "requiresDigit": requires_digit,
            }
        )
    return hints


def _sample_prefix_segments(path_segments: list[str]) -> list[str]:
    prefix_segments: list[str] = []
    for segment in path_segments[:4]:
        if prefix_segments and _looks_dynamic_job_segment(segment):
            break
        prefix_segments.append(segment)
    return prefix_segments or path_segments[:1]


def _looks_dynamic_job_segment(segment: str) -> bool:
    text = str(segment or "").strip()
    if not text:
        return False
    if any(char.isdigit() for char in text):
        return True
    return len(text) >= 24 and text.count("-") >= 3


def _matches_sample_job_url_hints(raw_url: object, hints: list[dict[str, Any]]) -> bool:
    normalized_url = normalize_job_url(raw_url)
    if not normalized_url:
        return False
    host = _normalized_host(normalized_url)
    path_segments = [segment for segment in (urlsplit(normalized_url).path or "").split("/") if segment]
    path = "/" + "/".join(path_segments) + "/" if path_segments else "/"
    has_digit = any(any(char.isdigit() for char in segment) for segment in path_segments)
    for hint in hints:
        if host != hint.get("host"):
            continue
        prefix = str(hint.get("prefix") or "")
        if prefix and not path.startswith(prefix):
            continue
        if len(path_segments) < int(hint.get("minSegments") or 0):
            continue
        if bool(hint.get("requiresDigit")) and not has_digit:
            continue
        return True
    return False


def _normalized_host(raw_url: object) -> str:
    try:
        host = str(urlsplit(str(raw_url or "").strip()).hostname or "").strip().casefold()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _job_schedule_sort_key(
    job: Mapping[str, Any],
) -> tuple[int, float, int]:
    ai_score = _job_ai_prerank_score(job)
    return (
        1 if ai_score is not None else 0,
        ai_score if ai_score is not None else 0.0,
        _job_freshness_bonus(job),
    )


def _job_ai_prerank_score(job: Mapping[str, Any]) -> float | None:
    value = job.get("aiPreRankScore")
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:
        return None
    return max(0.0, min(100.0, score))


def overlay_cached_job_prerank_scores(
    jobs: list[dict[str, Any]],
    existing_jobs: list[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], int]:
    if not jobs or not isinstance(existing_jobs, list):
        return jobs, 0
    cached_by_url: dict[str, dict[str, Any]] = {}
    for raw_job in existing_jobs:
        if not isinstance(raw_job, Mapping):
            continue
        cached_score = _job_ai_prerank_score(raw_job)
        cached_reason = str(raw_job.get("aiPreRankReason") or "").strip()
        if cached_score is None and not cached_reason:
            continue
        for raw_url in (raw_job.get("url"), raw_job.get("canonicalUrl")):
            normalized_url = normalize_job_url(raw_url)
            if not normalized_url:
                continue
            cached_by_url[normalized_url] = {
                "aiPreRankScore": int(cached_score) if cached_score is not None else raw_job.get("aiPreRankScore"),
                "aiPreRankReason": cached_reason,
            }
    if not cached_by_url:
        return jobs, 0

    reused = 0
    for job in jobs:
        if _job_ai_prerank_score(job) is not None:
            continue
        cached = None
        for raw_url in (job.get("url"), job.get("canonicalUrl")):
            normalized_url = normalize_job_url(raw_url)
            if normalized_url and normalized_url in cached_by_url:
                cached = cached_by_url[normalized_url]
                break
        if cached is None:
            continue
        if cached.get("aiPreRankScore") is not None:
            job["aiPreRankScore"] = cached.get("aiPreRankScore")
        if str(cached.get("aiPreRankReason") or "").strip():
            job["aiPreRankReason"] = str(cached.get("aiPreRankReason") or "").strip()
        reused += 1
    return jobs, reused


def _job_ai_prerank_excluded(job: Mapping[str, Any]) -> bool:
    if bool(job.get("aiPreRankPending")):
        return True
    ai_score = _job_ai_prerank_score(job)
    if ai_score is None:
        return False
    return ai_score < float(JOB_PRERANK_MIN_SCORE)


def _job_freshness_bonus(job: Mapping[str, Any]) -> int:
    posted_at = _parse_datetime(job.get("datePosted") or "")
    if posted_at is None:
        return 0
    age_days = max(
        0.0,
        (datetime.now(timezone.utc) - posted_at.astimezone(timezone.utc)).total_seconds() / (24 * 60 * 60),
    )
    if age_days <= 7:
        return 40
    if age_days <= 30:
        return 20
    if age_days <= 90:
        return 5
    return 0


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
    "collect_careers_page_link_snapshots",
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
    "overlay_cached_job_prerank_scores",
    "sanitize_job_title_candidate",
    "select_company_jobs_for_coverage",
    "strip_html_to_text",
]
