from __future__ import annotations

ADAPTIVE_SEARCH_HIGH_LEVEL_DEFAULTS = {
    "companyBatchSize": 4,
    "discoveryBreadth": 4,
    "cooldownBaseDays": 7,
}

JOB_LINK_HARD_CAP_PER_COMPANY = 40


def positive_int(value: object, fallback: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return max(minimum, parsed)


def _resolve_company_batch_size(config: dict) -> int:
    return positive_int(
        config.get("companyBatchSize"),
        ADAPTIVE_SEARCH_HIGH_LEVEL_DEFAULTS["companyBatchSize"],
    )


def normalize_adaptive_search_config(adaptive_search_config: dict | None) -> dict[str, int]:
    config = adaptive_search_config if isinstance(adaptive_search_config, dict) else {}
    return {
        "companyBatchSize": _resolve_company_batch_size(config),
        "discoveryBreadth": positive_int(
            config.get("discoveryBreadth"),
            ADAPTIVE_SEARCH_HIGH_LEVEL_DEFAULTS["discoveryBreadth"],
        ),
        "cooldownBaseDays": positive_int(
            config.get("cooldownBaseDays"),
            ADAPTIVE_SEARCH_HIGH_LEVEL_DEFAULTS["cooldownBaseDays"],
        ),
    }


def compact_adaptive_search_config(adaptive_search_config: dict) -> None:
    if not isinstance(adaptive_search_config, dict):
        return
    normalized = normalize_adaptive_search_config(adaptive_search_config)
    adaptive_search_config.clear()
    adaptive_search_config.update(normalized)


def derive_adaptive_runtime_strategy(adaptive_search_config: dict) -> dict[str, int]:
    normalized_config = normalize_adaptive_search_config(adaptive_search_config)
    company_batch_size = int(normalized_config["companyBatchSize"])
    discovery_breadth = int(normalized_config["discoveryBreadth"])
    cooldown_base_days = int(normalized_config["cooldownBaseDays"])

    max_companies_per_run = max(1, company_batch_size)
    jobs_per_company_bias = max(1, company_batch_size // 2)
    max_jobs_per_company = min(
        JOB_LINK_HARD_CAP_PER_COMPANY,
        max(1, discovery_breadth + jobs_per_company_bias),
    )
    analysis_work_cap = max(1, max_companies_per_run * max_jobs_per_company)
    return {
        "max_companies_per_run": max_companies_per_run,
        "max_jobs_per_company": max_jobs_per_company,
        "analysis_work_cap": analysis_work_cap,
        "company_rotation_interval_days": max(1, cooldown_base_days // 3),
        "max_jobs_per_query": min(50, max(10, discovery_breadth + company_batch_size)),
    }
