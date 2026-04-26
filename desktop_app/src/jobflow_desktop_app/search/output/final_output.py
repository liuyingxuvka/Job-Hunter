from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .manual_tracking import has_manual_tracking
from .output_restore import merge_recommended_jobs_append_mode, sort_jobs_for_append_merge
from ..analysis.scoring_contract import overall_score, passes_unified_recommendation_threshold


Job = dict[str, Any]
OUTPUT_ELIGIBILITY_RULE_VERSION = 1

TRACK_CLUSTER_LABEL = {
    "direct_fit": "Direct-Fit",
    "adjacent_fit": "Adjacent-Domain",
    "transferable_fit": "Transferable-Skills",
    "exploratory_fit": "Exploratory",
}

TRACK_CN_LABEL = {
    "direct_fit": "目标岗位直配",
    "adjacent_fit": "相邻业务方向",
    "transferable_fit": "可迁移能力方向",
    "exploratory_fit": "扩展探索方向",
}

AGGREGATOR_HOST_TOKENS = (
    "indeed.",
    "glassdoor.",
    "ziprecruiter.",
    "simplyhired.",
    "monster.",
    "careerbuilder.",
    "jobrapido.",
    "talent.",
    "jobleads.",
    "join.com",
)

PARKING_HOST_TOKENS = (
    "sedoparking.com",
    "parkingcrew.net",
    "hugedomains.com",
    "dan.com",
    "afternic.com",
    "godaddy.com",
    "bodis.com",
)

JOB_SIGNAL_RE = re.compile(
    r"\b(job|jobs|career|careers|opening|openings|position|positions|requisition|req(?:uisition)?"
    r"|apply|application|engineer|scientist|developer|specialist|manager|director|technician"
    r"|intern|graduate|researcher|consultant)\b|职位|岗位|招聘|应聘|申请",
    flags=re.IGNORECASE,
)

SPECIFIC_JOB_PATH_RE = re.compile(
    r"/(job|jobs|job-details|jobdetail|position|positions|opening|openings|opportunities|vacancies|careers?/.+"
    r"|requisition|req|vacancy|apply|posting|postings|role|roles)/",
    flags=re.IGNORECASE,
)

GENERIC_CAREERS_PATH_RE = re.compile(
    r"^/(careers?|jobs?|join-us|work-with-us|opportunities?|vacancies?)/?$",
    flags=re.IGNORECASE,
)

GENERIC_TITLE_RE = re.compile(
    r"^(apply|apply now|view job|open job|job details?|learn more|details?|read more|see job|continue"
    r"|申请|立即申请|查看职位|职位详情|查看详情|更多信息)$",
    flags=re.IGNORECASE,
)

GENERIC_LOCATION_TITLE_RE = re.compile(
    r"^(remote|hybrid|onsite|on-site|global|europe|united states|usa|canada|germany|france|china|japan"
    r"|berlin|munich|shanghai|beijing|tokyo|london|paris|new york|san francisco|职位|岗位|远程|混合|现场)$",
    flags=re.IGNORECASE,
)

UNAVAILABLE_SIGNAL_RE = re.compile(
    r"\b(no longer accepting|no longer available|position filled|job closed|role closed|vacancy closed"
    r"|posting expired|position expired|application closed|applications closed|not accepting applications"
    r"|job is no longer available|this job is unavailable)\b|已下线|已关闭|职位已满|停止招聘|不再招聘|岗位失效",
    flags=re.IGNORECASE,
)

JD_BODY_SIGNAL_RE = re.compile(
    r"\b(responsibilities|requirements|qualifications|what you will do|what you'll do|about the role"
    r"|preferred qualifications|minimum qualifications|apply now|submit your application)\b|岗位职责|任职要求|岗位要求|申请方式",
    flags=re.IGNORECASE,
)

LANDING_PAGE_SIGNAL_RE = re.compile(
    r"\b(search jobs|all jobs|join our talent community|talent network|explore careers|browse openings"
    r"|featured jobs|find your next opportunity)\b|搜索职位|全部职位|人才社区|浏览岗位",
    flags=re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_human_date(iso_text: str) -> str:
    text = str(iso_text or "").strip()
    return text[:10] if len(text) >= 10 else text


def _config_bool(config: Mapping[str, Any] | None, *path: str, default: bool = False) -> bool:
    current: Any = config
    for key in path:
        if not isinstance(current, Mapping):
            return default
        current = current.get(key)
    if current is None:
        return default
    return bool(current)


def _config_value(config: Mapping[str, Any] | None, *path: str, default: Any = None) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, Mapping):
            return default
        current = current.get(key)
    return default if current is None else current


def _config_int(config: Mapping[str, Any] | None, *path: str, default: int) -> int:
    value = _config_value(config, *path, default=default)
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_job_url(raw_url: object) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, flags=re.IGNORECASE):
        return ""
    try:
        parts = urlsplit(text)
    except Exception:
        return ""
    scheme = (parts.scheme or "https").lower()
    netloc = (parts.netloc or "").lower()
    if not netloc:
        return ""
    path = parts.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    path = re.sub(r"/{2,}", "/", path)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "fbclid", "gclid"))
    ]
    query = urlencode(query_items, doseq=True)
    return urlunsplit((scheme, netloc, path.rstrip("/") or "/", query, ""))


def domain_of(raw_url: object) -> str:
    normalized = normalize_job_url(raw_url)
    if not normalized:
        return ""
    hostname = (urlsplit(normalized).hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def is_likely_parking_host(raw_url: object) -> bool:
    hostname = domain_of(raw_url)
    if not hostname:
        return False
    return any(token in hostname for token in PARKING_HOST_TOKENS)


def is_aggregator_host(raw_url: object) -> bool:
    hostname = domain_of(raw_url)
    if not hostname:
        return False
    return any(token in hostname for token in AGGREGATOR_HOST_TOKENS)


def normalize_company_name(text: object) -> str:
    value = re.sub(r"[\s\-_.,;:(){}\[\]<>]+", " ", str(text or "").strip()).casefold()
    return value.strip()


def normalize_title_for_key(text: object) -> str:
    value = re.sub(r"[\s\-_.,;:(){}\[\]<>]+", " ", str(text or "").strip()).casefold()
    return value.strip()


def normalize_location_for_key(text: object) -> str:
    value = re.sub(r"[\s\-_.,;:(){}\[\]<>]+", " ", str(text or "").strip()).casefold()
    return value.strip()


def canonical_job_url(job: Mapping[str, Any]) -> str:
    final_url = (
        _config_value(job, "analysis", "postVerify", "finalUrl", default="")
        or _config_value(job, "jd", "finalUrl", default="")
        or _config_value(job, "postVerify", "finalUrl", default="")
        or job.get("canonicalUrl")
        or ""
    )
    return normalize_job_url(final_url)


def build_job_composite_key(job: Mapping[str, Any]) -> str:
    company = normalize_company_name(job.get("company"))
    title = normalize_title_for_key(job.get("title"))
    location = normalize_location_for_key(job.get("location"))
    if not company and not title:
        return ""
    return f"{company}|{title}|{location}"


def build_job_dedupe_key(job: Mapping[str, Any]) -> str:
    composite = build_job_composite_key(job)
    source_type = str(job.get("sourceType") or "").lower()
    if "platform_listing" in source_type and composite:
        return composite
    canonical = canonical_job_url(job)
    if canonical:
        return canonical
    return composite


def platform_listing_label_for_url(url: object, config: Mapping[str, Any] | None) -> str:
    if not _config_bool(config, "search", "allowPlatformListings", default=False):
        return ""
    domains = _config_value(config, "search", "platformListingDomains", default=[])
    if not isinstance(domains, list):
        return ""
    normalized_domains = [str(item or "").strip().lower() for item in domains if str(item or "").strip()]
    if not normalized_domains:
        return ""
    hostname = domain_of(url)
    if not hostname:
        return ""
    matched = next(
        (item for item in normalized_domains if hostname == item or hostname.endswith(f".{item}")),
        "",
    )
    if matched == "linkedin.com":
        return "LinkedIn"
    return matched


def is_allowed_platform_listing_url(url: object, config: Mapping[str, Any] | None) -> bool:
    return bool(platform_listing_label_for_url(url, config))


def is_limited_platform_listing_job(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    return is_allowed_platform_listing_url(canonical_job_url(job) or job.get("url") or "", config)


def has_job_signal(*, title: object, url: object, summary: object) -> bool:
    haystack = "\n".join(
        [
            str(title or "").strip(),
            normalize_job_url(url),
            str(summary or "").strip(),
        ]
    )
    return bool(JOB_SIGNAL_RE.search(haystack))


def is_specific_job_detail_url(raw_url: object) -> bool:
    normalized = normalize_job_url(raw_url)
    if not normalized:
        return False
    parts = urlsplit(normalized)
    path = parts.path or "/"
    if GENERIC_CAREERS_PATH_RE.match(path):
        return False
    if SPECIFIC_JOB_PATH_RE.search(path):
        return True
    if re.search(r"/[^/]*(job|career|position|opening|vacancy|requisition)[^/]*/[^/]+", path, re.IGNORECASE):
        return True
    if re.search(r"/\d{4,}", path):
        return True
    return False


def is_generic_careers_url(raw_url: object) -> bool:
    normalized = normalize_job_url(raw_url)
    if not normalized:
        return False
    path = urlsplit(normalized).path or "/"
    return bool(GENERIC_CAREERS_PATH_RE.match(path))


def is_generic_location_or_category_title(text: object) -> bool:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if not value:
        return False
    return bool(GENERIC_LOCATION_TITLE_RE.match(value))


def is_likely_noise_title(text: object) -> bool:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if not value:
        return True
    if len(value) <= 2:
        return True
    if re.fullmatch(r"[\W_]+", value):
        return True
    return False


def has_unavailable_signal(text: object) -> bool:
    return bool(UNAVAILABLE_SIGNAL_RE.search(str(text or "")))


def has_jd_body_signal(text: object) -> bool:
    return bool(JD_BODY_SIGNAL_RE.search(str(text or "")))


def is_likely_landing_page_text(text: object) -> bool:
    return bool(LANDING_PAGE_SIGNAL_RE.search(str(text or "")))


def job_availability_text(job: Mapping[str, Any]) -> str:
    pieces = [
        job.get("title") or "",
        job.get("summary") or "",
        _config_value(job, "jd", "text", default=""),
        _config_value(job, "jd", "rawText", default=""),
        _config_value(job, "jd", "finalUrl", default=""),
        _config_value(job, "jd", "applyUrl", default=""),
        _config_value(job, "analysis", "jobPostingEvidenceCn", default=""),
        _config_value(job, "analysis", "recommendReasonCn", default=""),
    ]
    return "\n".join(str(piece or "") for piece in pieces)


def has_explicit_unavailable_job_signal(job: Mapping[str, Any]) -> bool:
    return has_unavailable_signal(job_availability_text(job))


def output_eligibility_policy_key(config: Mapping[str, Any] | None) -> str:
    policy = {
        "analysis": {
            "postVerifyEnabled": _config_bool(config, "analysis", "postVerifyEnabled", default=False),
            "postVerifyRequireChecked": _config_bool(config, "analysis", "postVerifyRequireChecked", default=True),
            "recommendScoreThreshold": _config_int(config, "analysis", "recommendScoreThreshold", default=50),
        },
        "filters": {
            "excludeUnavailableLinks": _config_bool(config, "filters", "excludeUnavailableLinks", default=True),
            "excludeAggregatorLinks": _config_bool(config, "filters", "excludeAggregatorLinks", default=True),
        },
        "search": {
            "allowPlatformListings": _config_bool(config, "search", "allowPlatformListings", default=False),
            "platformListingDomains": sorted(
                str(item).strip().casefold()
                for item in (_config_value(config, "search", "platformListingDomains", default=[]) or [])
                if str(item).strip()
            ),
        },
    }
    return json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_output_link_evidence(job: Mapping[str, Any]) -> Job:
    normalized = copy.deepcopy(dict(job))
    jd = normalized.get("jd")
    normalized_jd = dict(jd) if isinstance(jd, Mapping) else {}
    top_level_apply_url = normalize_job_url(normalized.get("applyUrl") or "")
    top_level_final_url = normalize_job_url(normalized.get("finalUrl") or "")
    if top_level_apply_url and not normalize_job_url(normalized_jd.get("applyUrl") or ""):
        normalized_jd["applyUrl"] = top_level_apply_url
    if top_level_final_url and not normalize_job_url(normalized_jd.get("finalUrl") or ""):
        normalized_jd["finalUrl"] = top_level_final_url
    if normalized_jd:
        normalized["jd"] = normalized_jd
    return normalized


def choose_output_job_url(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> str:
    job = normalize_output_link_evidence(job)
    post_verify_enabled = _config_bool(config, "analysis", "postVerifyEnabled", default=False)
    verified = _config_value(job, "analysis", "postVerify", default={})
    verified_final_url = ""
    if post_verify_enabled and isinstance(verified, Mapping) and _config_value(verified, "isValidJobPage", default=False) is True:
        verified_final_url = _config_value(verified, "finalUrl", default="")
    candidates = [
        verified_final_url,
        _config_value(job, "jd", "applyUrl", default=""),
        _config_value(job, "jd", "finalUrl", default=""),
        job.get("canonicalUrl") or "",
    ]
    for candidate in candidates:
        normalized = normalize_job_url(candidate)
        if not normalized:
            continue
        if is_allowed_platform_listing_url(normalized, config):
            continue
        if not is_specific_job_detail_url(normalized):
            continue
        return normalized
    return ""


def is_applyable_job_page(job: Mapping[str, Any]) -> bool:
    job = normalize_output_link_evidence(job)
    if _config_value(job, "analysis", "postVerify", "isValidJobPage", default=False):
        return True
    apply_url = normalize_job_url(_config_value(job, "jd", "applyUrl", default=""))
    if apply_url:
        return True
    final_url = normalize_job_url(_config_value(job, "jd", "finalUrl", default=""))
    if final_url and is_specific_job_detail_url(final_url):
        status = int(_config_value(job, "jd", "status", default=0) or 0)
        return status == 0 or 0 < status < 500
    return False


def has_reliable_output_link(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    job = normalize_output_link_evidence(job)
    output_url = choose_output_job_url(job, config)
    if not output_url:
        return False
    post_verify_enabled = _config_bool(config, "analysis", "postVerifyEnabled", default=False)
    post_verify_url = normalize_job_url(_config_value(job, "analysis", "postVerify", "finalUrl", default=""))
    apply_url = normalize_job_url(_config_value(job, "jd", "applyUrl", default=""))
    final_url = normalize_job_url(_config_value(job, "jd", "finalUrl", default=""))
    original_url = normalize_job_url(job.get("url") or "")
    if post_verify_enabled and post_verify_url and output_url == post_verify_url:
        return _config_value(job, "analysis", "postVerify", "isValidJobPage", default=False) is True
    if apply_url and output_url == apply_url:
        return True
    if output_url in {final_url, original_url} and is_applyable_job_page(job):
        return True
    return False


def has_meaningful_output_title(job: Mapping[str, Any]) -> bool:
    title = str(job.get("title") or "").strip()
    if not title:
        return False
    if is_likely_noise_title(title):
        return False
    if is_generic_location_or_category_title(title):
        return False
    if GENERIC_TITLE_RE.match(title):
        return False
    if not has_job_signal(title=title, url=job.get("url") or "", summary=job.get("summary") or "") and len(title) < 8:
        return False
    return True


def is_unavailable_job(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    if not _config_bool(config, "filters", "excludeUnavailableLinks", default=True):
        return False
    url = job.get("url") or ""
    if (
        _config_bool(config, "filters", "excludeAggregatorLinks", default=True)
        and is_aggregator_host(url)
        and not is_allowed_platform_listing_url(url, config)
    ):
        return True
    if is_likely_parking_host(url):
        return True
    if is_generic_location_or_category_title(job.get("title") or ""):
        return True
    try:
        status = int(_config_value(job, "jd", "status", default=0) or 0)
    except (TypeError, ValueError):
        status = 0
    if status in {404, 410, 451}:
        return True
    final_url = str(_config_value(job, "jd", "finalUrl", default="") or "").strip()
    if final_url and (is_likely_parking_host(final_url) or is_generic_careers_url(final_url)):
        return True
    if _config_value(job, "jd", "redirected", default=False) and final_url:
        redirected_to_job_like = has_job_signal(title="", url=final_url, summary="")
        if not redirected_to_job_like:
            return True
    text = job_availability_text(job)
    if (
        _config_value(job, "jd", "ok", default=False)
        and not has_jd_body_signal(_config_value(job, "jd", "rawText", default=""))
        and is_likely_landing_page_text(_config_value(job, "jd", "rawText", default=""))
    ):
        return True
    return has_unavailable_signal(text)


def passes_final_output_check(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    output_url = choose_output_job_url(job, config)
    if not output_url:
        return False
    if not has_meaningful_output_title(job):
        return False
    if is_unavailable_job(job, config):
        return False
    if is_limited_platform_listing_job(job, config):
        platform_url = normalize_job_url(job.get("url") or "")
        if not platform_url or not is_allowed_platform_listing_url(platform_url, config):
            return False
        return has_job_signal(
            title=job.get("title") or "",
            url=platform_url,
            summary=job.get("summary") or "",
        ) and bool(re.search(r"/jobs/view/[^/?#]+", platform_url, re.IGNORECASE))

    if is_likely_parking_host(output_url) or is_generic_careers_url(output_url) or is_aggregator_host(output_url):
        return False
    if not is_specific_job_detail_url(output_url):
        return False
    return has_reliable_output_link(job, config)


def evaluate_output_eligibility(
    job: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    if _config_value(job, "analysis", "recommend", default=False) is not True:
        return False, "not_recommended"
    if not passes_unified_recommendation_threshold(job, threshold=config):
        return False, "below_threshold"
    if not pass_post_verify(job, config, require_recommend=True):
        return False, "post_verify_failed"
    if not passes_final_output_check(job, config):
        return False, "final_output_check_failed"
    return True, "eligible"


def is_output_eligible(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    eligible, _reason = evaluate_output_eligibility(job, config)
    return eligible


def _materialized_output_rule_version(job: Mapping[str, Any]) -> int | None:
    analysis = job.get("analysis")
    if not isinstance(analysis, Mapping):
        return None
    value = analysis.get("outputEligibilityRuleVersion")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def has_current_output_eligibility(
    job: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> bool:
    analysis = job.get("analysis")
    return (
        isinstance(analysis, Mapping)
        and "eligibleForOutput" in analysis
        and _materialized_output_rule_version(job) == OUTPUT_ELIGIBILITY_RULE_VERSION
        and str(analysis.get("outputEligibilityPolicyKey") or "") == output_eligibility_policy_key(config)
    )


def materialize_output_eligibility(
    job: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> Job:
    normalized = normalize_output_link_evidence(job)
    analysis = normalized.get("analysis")
    normalized_analysis = dict(analysis) if isinstance(analysis, Mapping) else {}
    normalized["analysis"] = normalized_analysis
    eligible, reason = evaluate_output_eligibility(normalized, config)
    normalized_analysis["eligibleForOutput"] = eligible
    normalized_analysis["outputEligibilityReason"] = reason
    normalized_analysis["outputEligibilityRuleVersion"] = OUTPUT_ELIGIBILITY_RULE_VERSION
    normalized_analysis["outputEligibilityPolicyKey"] = output_eligibility_policy_key(config)
    output_url = choose_output_job_url(normalized, config)
    if output_url:
        normalized["outputUrl"] = output_url
    else:
        normalized.pop("outputUrl", None)
    canonical_url = canonical_job_url(normalized)
    if canonical_url:
        normalized["canonicalUrl"] = canonical_url
    return normalized


def should_restore_historical_recommended_job(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    output_url = choose_output_job_url(job, config)
    if not output_url:
        return False
    if not str(job.get("dateFound") or "").strip():
        return False
    if is_likely_parking_host(output_url):
        return False
    if is_likely_parking_host(_config_value(job, "jd", "finalUrl", default="")):
        return False
    if has_explicit_unavailable_job_signal(job):
        return False
    return is_output_eligible(job, config)


def infer_source_quality(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> str:
    target_url = canonical_job_url(job) or str(job.get("url") or "")
    if not target_url:
        return "Uncertain"
    if is_allowed_platform_listing_url(target_url, config):
        return "Platform Listing"
    if is_aggregator_host(target_url):
        return "Aggregator"
    source_type = str(job.get("sourceType") or "").lower()
    hostname = domain_of(target_url)
    if (
        source_type == "company"
        or "company" in source_type
        or ("jobs." not in hostname and "careers." not in hostname and not is_aggregator_host(target_url))
    ):
        return "Direct Employer"
    return "Uncertain"


def source_quality_rank(value: object) -> int:
    text = str(value or "")
    if text == "Direct Employer":
        return 4
    if text == "Platform Listing":
        return 2
    if text == "Uncertain":
        return 1
    return 0


def compare_jobs_by_preference(
    current: Mapping[str, Any],
    candidate: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> int:
    current_quality = infer_source_quality(current, config)
    candidate_quality = infer_source_quality(candidate, config)
    if _config_bool(config, "filters", "preferDirectEmployerSite", default=False):
        rank_diff = source_quality_rank(candidate_quality) - source_quality_rank(current_quality)
        if rank_diff != 0:
            return rank_diff
    current_apply = 1 if is_applyable_job_page(current) else 0
    candidate_apply = 1 if is_applyable_job_page(candidate) else 0
    if candidate_apply != current_apply:
        return candidate_apply - current_apply
    current_score = _analysis_preference_score(current)
    candidate_score = _analysis_preference_score(candidate)
    if candidate_score != current_score:
        return candidate_score - current_score
    current_len = len(str(current.get("summary") or _config_value(current, "jd", "text", default="") or ""))
    candidate_len = len(str(candidate.get("summary") or _config_value(candidate, "jd", "text", default="") or ""))
    if candidate_len != current_len:
        return candidate_len - current_len
    candidate_date = str(candidate.get("dateFound") or "")
    current_date = str(current.get("dateFound") or "")
    return (candidate_date > current_date) - (candidate_date < current_date)


def _analysis_preference_score(job: Mapping[str, Any]) -> int:
    analysis = job.get("analysis")
    if not isinstance(analysis, Mapping):
        return -1
    score = overall_score(analysis)
    return score if score > 0 else -1


def prefers_candidate_over_existing(
    existing: Mapping[str, Any],
    candidate: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> bool:
    return compare_jobs_by_preference(existing, candidate, config) > 0


def platform_listing_tag(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> str:
    label = platform_listing_label_for_url(canonical_job_url(job) or job.get("url") or "", config)
    return f"{label}线索" if label else ""


def infer_region_tag(job: Mapping[str, Any]) -> str:
    text = " ".join(
        [
            str(job.get("location") or ""),
            str(job.get("title") or ""),
            str(job.get("summary") or ""),
            str(job.get("url") or ""),
            str(_config_value(job, "jd", "rawText", default="") or ""),
        ]
    ).lower()
    if not text.strip():
        return ""
    if re.search(r"\b(global|worldwide|anywhere|remote worldwide|multiple countries)\b", text):
        return "Global"
    if re.search(r"\b(canada|ontario|quebec|vancouver|toronto|montreal)\b", text):
        return "CA"
    if re.search(r"\b(united states|usa|\bus\b|california|texas|new york|massachusetts|washington|seattle)\b", text):
        return "US"
    if re.search(r"\b(germany|france|netherlands|belgium|spain|italy|sweden|norway|denmark|finland|austria|switzerland|poland|ireland|portugal|uk|united kingdom|europe|eu)\b", text):
        return "EU"
    if re.search(r"\b(japan|tokyo|osaka|yokohama|nagoya)\b", text):
        return "JP"
    if re.search(r"\b(australia|sydney|melbourne|brisbane|perth|adelaide)\b", text):
        return "AU"
    if re.search(r"\b(china|shanghai|beijing|shenzhen|guangzhou|suzhou)\b", text):
        return "CN"
    if re.search(r"\b(india|bengaluru|bangalore|chennai|pune|hyderabad)\b", text):
        return "IN"
    return ""


def build_final_output_dedupe_key(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> str:
    explicit_output_url = normalize_job_url(job.get("outputUrl") or "")
    if explicit_output_url:
        return explicit_output_url
    output_url = choose_output_job_url(job, config)
    if output_url:
        return output_url
    canonical = canonical_job_url(job)
    if canonical:
        return canonical
    return build_job_dedupe_key(job)


def _analysis_with_derived_defaults(job: Mapping[str, Any]) -> dict[str, Any]:
    analysis = job.get("analysis")
    normalized = dict(analysis) if isinstance(analysis, Mapping) else {}
    fit_track = str(normalized.get("fitTrack") or "").strip() or "direct_fit"
    normalized.setdefault("fitTrack", fit_track)
    normalized.setdefault("jobCluster", TRACK_CLUSTER_LABEL.get(fit_track, TRACK_CLUSTER_LABEL["direct_fit"]))
    normalized.setdefault("industryTrackCn", TRACK_CN_LABEL.get(fit_track, TRACK_CN_LABEL["direct_fit"]))
    return normalized


def enrich_recommended_job(job: Mapping[str, Any], config: Mapping[str, Any] | None) -> Job:
    normalized = normalize_output_link_evidence(job)
    normalized["analysis"] = _analysis_with_derived_defaults(normalized)
    normalized["outputUrl"] = choose_output_job_url(normalized, config)
    normalized["canonicalUrl"] = canonical_job_url(normalized)
    normalized["sourceQuality"] = str(normalized.get("sourceQuality") or infer_source_quality(normalized, config))
    normalized["regionTag"] = str(normalized.get("regionTag") or infer_region_tag(normalized))
    existing_tags = normalized.get("listTags")
    tags: list[str] = []
    if isinstance(existing_tags, list):
        tags.extend(str(item).strip() for item in existing_tags if str(item).strip())
    elif isinstance(existing_tags, str) and existing_tags.strip():
        tags.extend(part.strip() for part in existing_tags.split("|") if part.strip())
    for tag in ("推荐", platform_listing_tag(normalized, config)):
        if tag and tag not in tags:
            tags.append(tag)
    normalized["listTags"] = tags
    return normalized


def pass_post_verify(job: Mapping[str, Any], config: Mapping[str, Any] | None, *, require_recommend: bool) -> bool:
    if is_limited_platform_listing_job(job, config):
        return True
    if _config_value(job, "analysis", "postVerifySkipped", default=False) is True:
        return True
    if not _config_bool(config, "analysis", "postVerifyEnabled", default=False):
        return True
    verified = _config_value(job, "analysis", "postVerify", default={})
    require_checked = _config_bool(config, "analysis", "postVerifyRequireChecked", default=True)
    if not isinstance(verified, Mapping) or not verified:
        return not require_checked
    if verified.get("isValidJobPage") is not True:
        return False
    if require_recommend:
        return verified.get("recommend") is True
    return True


@dataclass(frozen=True)
class RecommendedOutputRebuildResult:
    payload: dict[str, Any]
    pruned_recent_invalid_rows: int = 0


def _materialize_final_recommended_jobs(
    jobs: list[Mapping[str, Any]],
    config: Mapping[str, Any] | None,
) -> list[Job]:
    materialized_jobs: list[Job] = []
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        stamped = materialize_output_eligibility(enrich_recommended_job(job, config), config)
        analysis = stamped.get("analysis")
        if isinstance(analysis, Mapping) and analysis.get("eligibleForOutput") is True:
            materialized_jobs.append(stamped)
    return materialized_jobs


def rebuild_recommended_output_payload(
    *,
    all_jobs: list[Mapping[str, Any]],
    existing_recommended_jobs: list[Mapping[str, Any]],
    config: Mapping[str, Any] | None,
    generated_at: str | None = None,
) -> RecommendedOutputRebuildResult:
    timestamp = generated_at or now_iso()
    prepared_all_jobs = [
        materialize_output_eligibility(enrich_recommended_job(job, config), config)
        for job in all_jobs
        if isinstance(job, Mapping)
    ]

    recommended_only_jobs = [
        job
        for job in prepared_all_jobs
        if isinstance(job.get("analysis"), Mapping)
        and job["analysis"].get("eligibleForOutput") is True
    ]

    combined_map: dict[str, Job] = {}
    for job in recommended_only_jobs:
        key = build_final_output_dedupe_key(job, config)
        if not key:
            continue
        current = combined_map.get(key)
        if current is None:
            combined_map[key] = job
            continue
        merged_tags = list(dict.fromkeys([*current.get("listTags", []), *job.get("listTags", [])]))
        if prefers_candidate_over_existing(current, job, config):
            combined_map[key] = {**job, "listTags": merged_tags}
        else:
            combined_map[key] = {**current, "listTags": merged_tags}

    unified_recommended_jobs = list(combined_map.values())
    recommended_mode = str(_config_value(config, "output", "recommendedMode", default="replace") or "replace")
    pruned_recent_invalid_rows = 0

    if recommended_mode == "append":
        tracker_today = to_human_date(timestamp)
        all_jobs_by_key = {
            build_final_output_dedupe_key(job, config): job
            for job in prepared_all_jobs
            if build_final_output_dedupe_key(job, config)
        }
        historical_jobs = [
            job
            for job in prepared_all_jobs
            if should_restore_historical_recommended_job(job, config)
        ]
        existing_rows = [
            {
                "job": enrich_recommended_job(job, config),
                "dateFound": str(job.get("dateFound") or "").strip(),
                "interest": str(job.get("interest") or "").strip(),
                "appliedDate": str(job.get("appliedDate") or "").strip(),
                "appliedCn": str(job.get("appliedCn") or "").strip(),
                "responseStatus": str(job.get("responseStatus") or "").strip(),
                "notInterested": str(job.get("notInterested") or "").strip(),
                "notesCn": str(job.get("notesCn") or "").strip(),
            }
            for job in existing_recommended_jobs
            if isinstance(job, Mapping)
        ]
        merge_result = merge_recommended_jobs_append_mode(
            existing_rows=existing_rows,
            all_jobs_by_key=all_jobs_by_key,
            historical_jobs=historical_jobs,
            new_jobs=unified_recommended_jobs,
            tracker_now=timestamp,
            tracker_today=tracker_today,
            row_to_job=lambda row: enrich_recommended_job(row.get("job") or {}, config)
            if isinstance(row.get("job"), Mapping)
            else None,
            key_for_job=lambda job: build_final_output_dedupe_key(job, config),
            passes_unified_threshold=lambda job: is_output_eligible(job, config),
            passes_final_output_check=lambda job: is_output_eligible(job, config),
            has_manual_tracking=has_manual_tracking,
            prefers_candidate_over_existing=lambda existing, candidate: prefers_candidate_over_existing(
                existing,
                candidate,
                config,
            ),
        )
        unified_recommended_jobs = merge_result.jobs
        pruned_recent_invalid_rows = merge_result.pruned_recent_invalid_rows
    else:
        unified_recommended_jobs = sort_jobs_for_append_merge(unified_recommended_jobs)

    unified_recommended_jobs = _materialize_final_recommended_jobs(unified_recommended_jobs, config)

    return RecommendedOutputRebuildResult(
        payload={
            "generatedAt": timestamp,
            "jobs": unified_recommended_jobs,
        },
        pruned_recent_invalid_rows=pruned_recent_invalid_rows,
    )
