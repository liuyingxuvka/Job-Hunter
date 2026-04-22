from __future__ import annotations

import io
import json
import re
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .sources_helpers import (
    collect_careers_page_job_candidates,
    extract_all_json_ld_job_postings,
    strip_html_to_text,
)


def fetch_supported_ats_jobs(
    ats_type: str,
    ats_id: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    normalized_type = str(ats_type or "").strip().lower()
    if normalized_type == "greenhouse":
        return fetch_greenhouse_jobs(ats_id, config=config, timeout_seconds=timeout_seconds)
    if normalized_type == "lever":
        return fetch_lever_jobs(ats_id, config=config, timeout_seconds=timeout_seconds)
    if normalized_type == "smartrecruiters":
        return fetch_smartrecruiters_jobs(ats_id, config=config, timeout_seconds=timeout_seconds)
    raise ValueError(f"Unsupported ATS type: {ats_type}")


def fetch_greenhouse_jobs(
    board: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    data = fetch_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
        config=config,
        timeout_seconds=timeout_seconds,
    )
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return []
    return [
        {
            "title": str(job.get("title") or "").strip(),
            "location": str(
                (
                    ((job.get("location") or {}) if isinstance(job.get("location"), Mapping) else {}).get("name")
                    or ""
                )
            ).strip(),
            "url": str(job.get("absolute_url") or "").strip(),
            "datePosted": to_iso_datetime(job.get("updated_at") or job.get("created_at") or ""),
            "summary": strip_html_to_text(job.get("content") or ""),
        }
        for job in jobs
        if isinstance(job, Mapping)
    ]


def fetch_lever_jobs(
    company: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    data = fetch_json(
        f"https://api.lever.co/v0/postings/{company}?mode=json",
        config=config,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(data, list):
        return []
    return [
        {
            "title": str(job.get("text") or "").strip(),
            "location": str(
                (
                    ((job.get("categories") or {}) if isinstance(job.get("categories"), Mapping) else {}).get("location")
                    or ""
                )
            ).strip(),
            "url": str(job.get("hostedUrl") or "").strip(),
            "datePosted": to_iso_datetime(job.get("createdAt") or ""),
            "summary": strip_html_to_text(job.get("description") or ""),
        }
        for job in data
        if isinstance(job, Mapping)
    ]


def fetch_smartrecruiters_jobs(
    company: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    data = fetch_json(
        f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100",
        config=config,
        timeout_seconds=timeout_seconds,
    )
    content = data.get("content")
    if not isinstance(content, list):
        return []
    jobs: list[dict[str, Any]] = []
    for job in content:
        if not isinstance(job, Mapping):
            continue
        location_payload = job.get("location")
        location = (
            ", ".join(
                str(part).strip()
                for part in (
                    (location_payload or {}).get("city"),
                    (location_payload or {}).get("region"),
                    (location_payload or {}).get("country"),
                )
                if str(part or "").strip()
            )
            if isinstance(location_payload, Mapping)
            else ""
        )
        jobs.append(
            {
                "title": str(job.get("name") or "").strip(),
                "location": location,
                "url": str(job.get("ref") or "").strip()
                or (
                    f"https://careers.smartrecruiters.com/{company}/{job.get('id')}"
                    if str(job.get("id") or "").strip()
                    else ""
                ),
                "datePosted": to_iso_datetime(job.get("releasedDate") or ""),
                "summary": smartrecruiters_summary(job.get("jobAd")),
            }
        )
    return jobs


def fetch_careers_page_jobs(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> list[dict[str, Any]]:
    html, final_url = fetch_text(url, config=config, timeout_seconds=timeout_seconds)
    return collect_careers_page_job_candidates(html, final_url or url)


def fetch_text(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> tuple[str, str]:
    payload, _, final_url = fetch_response(
        url,
        config=config,
        timeout_seconds=timeout_seconds,
    )
    return payload.decode("utf-8", errors="replace"), final_url


def fetch_response(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> tuple[bytes, str, str]:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 JobflowDesktop/1.0",
        },
    )
    request_timeout = request_timeout_seconds(config, timeout_seconds)
    with urlopen(request, timeout=request_timeout) as response:
        content = response.read()
        final_url = ""
        try:
            final_url = str(response.geturl() or "").strip()
        except Exception:
            final_url = url
        content_type = ""
        try:
            content_type = str(response.headers.get_content_type() or "").strip()
        except Exception:
            content_type = str(response.headers.get("Content-Type") or "").strip()
        return content, content_type, final_url or url


def fetch_json(
    url: str,
    *,
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 JobflowDesktop/1.0",
        },
    )
    request_timeout = request_timeout_seconds(config, timeout_seconds)
    try:
        with urlopen(request, timeout=request_timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    except URLError:
        raise


def extract_pdf_text_from_bytes(payload: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ModuleNotFoundError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(payload))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        return ""


def request_timeout_seconds(
    config: Mapping[str, Any] | None,
    timeout_seconds: int | None,
) -> int:
    fetch_config = dict(config.get("fetch") or {}) if isinstance(config, Mapping) else {}
    configured_seconds = max(1, int(to_number(fetch_config.get("timeoutMs"), 30000) / 1000))
    if timeout_seconds is None:
        return configured_seconds
    return max(1, min(configured_seconds, int(timeout_seconds)))


def parse_datetime(value: object) -> datetime | None:
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def to_iso_datetime(value: object) -> str:
    parsed = parse_datetime(value)
    return parsed.replace(microsecond=0).isoformat() if parsed is not None else str(value or "").strip()


def to_number(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        text = str(value).strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def remaining_seconds(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    return max(0, int(deadline - time.monotonic()))


def smartrecruiters_summary(job_ad: object) -> str:
    payload = dict(job_ad) if isinstance(job_ad, Mapping) else {}
    sections = payload.get("sections")
    if isinstance(sections, Mapping):
        parts = [
            strip_html_to_text(section.get("text") or "")
            for section in sections.values()
            if isinstance(section, Mapping)
        ]
        return " ".join(part for part in parts if part)
    if isinstance(sections, list):
        parts = [
            strip_html_to_text(section.get("text") or "")
            for section in sections
            if isinstance(section, Mapping)
        ]
        return " ".join(part for part in parts if part)
    return ""


__all__ = [
    "extract_pdf_text_from_bytes",
    "fetch_careers_page_jobs",
    "fetch_response",
    "fetch_greenhouse_jobs",
    "fetch_json",
    "fetch_lever_jobs",
    "fetch_smartrecruiters_jobs",
    "fetch_supported_ats_jobs",
    "fetch_text",
    "parse_datetime",
    "remaining_seconds",
    "request_timeout_seconds",
    "smartrecruiters_summary",
    "to_iso_datetime",
    "to_number",
]
