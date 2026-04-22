from __future__ import annotations

DEFAULT_RUNTIME_CONFIG: dict[str, object] = {
    "candidate": {
        "resumePath": "./resume.md",
        "scopeProfiles": [],
        "locationPreference": "Primary and secondary target regions defined by the user. Remote or relocation depends on the candidate.",
    },
    "search": {
        "model": "gpt-4o-mini",
        "regionMode": "global_open",
        "allowPlatformListings": True,
        "platformListingDomains": ["linkedin.com"],
        "blockedDomains": [
            "linkedin.com",
            "indeed.com",
            "glassdoor.com",
            "monster.com",
            "ziprecruiter.com",
        ],
        "allowedDomains": [],
    },
    "sources": {
        "fallbackSearchRegions": [
            "region:EU",
            "region:US",
            "region:APAC",
        ],
        "priorityRegionWeights": {
            "region:EU": 60,
            "region:US": 50,
            "region:APAC": 40,
        },
    },
    "companyDiscovery": {
        "model": "gpt-4o-mini",
    },
    "adaptiveSearch": {
        "companyBatchSize": 4,
        "discoveryBreadth": 4,
        "cooldownBaseDays": 7,
    },
    "fetch": {
        "timeoutMs": 20000,
        "userAgent": "jobflow/1.0",
    },
    "filters": {
        "maxPostAgeDays": 180,
        "excludeUnavailableLinks": True,
        "outputLinkRecheckHours": 72,
        "excludeAggregatorLinks": True,
        "preferDirectEmployerSite": True,
        "extraBlockedDomains": [
            "echojobs.io",
            "jobsora.com",
            "jooble.org",
            "talent.com",
        ],
    },
    "analysis": {
        "model": "gpt-4o",
        "strictScoring": False,
        "preFilterEnabled": True,
        "preFilterScoreThreshold": 40,
        "lowTokenMode": True,
        "scoringUseWebSearch": False,
        "scoringJdMaxChars": 12000,
        "lowTokenJdMaxChars": 1800,
        "postVerifyEnabled": False,
        "postVerifyRequireChecked": False,
        "postVerifyModel": "gpt-4o-mini",
        "postVerifyUseWebSearch": False,
        "postVerifyJdMaxChars": 1000,
        "transferableFitEnabled": True,
        "recommendScoreThreshold": 60,
        "minTransferableScore": 55,
        "platformListingRecommendScoreThreshold": 68,
    },
    "translation": {
        "enable": False,
        "model": "gpt-4o-mini",
        "target": "zh-CN",
    },
    "output": {
        "trackerXlsxPath": "./jobs_recommended.xlsx",
        "xlsxPath": "./jobs.xlsx",
        "recommendedXlsxPath": "./jobs_recommended.xlsx",
        "recommendedMode": "append",
    },
}
