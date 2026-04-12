import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import * as cheerio from "cheerio";
import ExcelJS from "exceljs";
import OpenAI from "openai";
import pLimit from "p-limit";

function nowIso() {
  return new Date().toISOString();
}

function toHumanDate(isoString) {
  if (!isoString) return "";
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return String(isoString);
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function normalizeUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    url.hash = "";
    const dropKeys = [
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "utm_term",
      "utm_content",
      "utm_id",
      "utm_name",
      "gclid",
      "fbclid",
      "mc_cid",
      "mc_eid"
    ];
    for (const key of dropKeys) url.searchParams.delete(key);
    const keep = Array.from(url.searchParams.entries());
    url.search = keep.length ? `?${new URLSearchParams(keep).toString()}` : "";
    return url.toString();
  } catch {
    return rawUrl;
  }
}

function resolveUrl(rawUrl, baseUrl) {
  try {
    return new URL(String(rawUrl || "").trim(), baseUrl).toString();
  } catch {
    return "";
  }
}

function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\\./, "");
  } catch {
    return "";
  }
}

function isLikelyParkingHost(rawUrl) {
  try {
    const host = new URL(rawUrl).hostname || "";
    return /sedo\.com|dan\.com|afternic\.com|parkingcrew\.(?:com|net)|hugedomains\.com|buydomains\.com|bodis\.com|undeveloped\.com|domainmarket\.com/i.test(
      host
    );
  } catch {
    return false;
  }
}

function normalizeCompanyName(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function companyDomain(website) {
  return domainOf(website || "");
}

function toFiniteNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function clampNumber(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function normalizeTitleForKey(title) {
  return String(title || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeLocationForKey(location) {
  return String(location || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s,/-]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeJobUrl(rawUrl) {
  const normalized = normalizeUrl(String(rawUrl || "").trim());
  return normalized || "";
}

function extractAnalysisMatchScore(analysis) {
  if (!analysis || typeof analysis !== "object") return null;
  const rawScore = analysis.matchScore;
  if (typeof rawScore === "number" && Number.isFinite(rawScore)) {
    return Math.trunc(rawScore);
  }
  if (typeof rawScore === "string") {
    const text = rawScore.trim();
    if (!text) return null;
    const parsed = Number(text);
    if (Number.isFinite(parsed)) return Math.trunc(parsed);
  }
  return null;
}

function hasCompletedAnalysis(job) {
  if (!job || typeof job !== "object") return false;
  const analysis = job.analysis;
  if (!analysis || typeof analysis !== "object") return false;
  return extractAnalysisMatchScore(analysis) !== null || analysis.prefilterRejected === true;
}

function needsAnalysis(job) {
  return !hasCompletedAnalysis(job);
}

function jobSourceDescriptor(job) {
  const source = String(job?.source || "").toLowerCase();
  const sourceType = String(job?.sourceType || "").toLowerCase();
  return `${source} | ${sourceType}`.trim();
}

function isSignalOnlyJob(job) {
  const descriptor = jobSourceDescriptor(job);
  if (!descriptor) return false;
  if (!descriptor.includes("web_search")) return false;
  return !descriptor.includes("company");
}

function isPromotableSignalJob(job) {
  if (!job || typeof job !== "object") return false;
  if (!isSignalOnlyJob(job)) return false;
  if (hasCompletedAnalysis(job)) return false;

  const targetUrl = canonicalJobUrl(job) || job?.url || "";
  if (!targetUrl) return false;
  if (isLikelyParkingHost(targetUrl) || isGenericCareersUrl(targetUrl) || isAggregatorHost(targetUrl))
    return false;
  if (!isSpecificJobDetailUrl(targetUrl)) return false;
  if (isGenericLocationOrCategoryTitle(job?.title || "")) return false;

  return hasJobSignal({
    title: job?.title || "",
    url: targetUrl,
    summary: job?.summary || ""
  });
}

function sortResumePendingJobs(jobs) {
  return Array.isArray(jobs)
    ? [...jobs].sort((a, b) => {
        const aDate = String(a?.dateFound || "");
        const bDate = String(b?.dateFound || "");
        if (aDate !== bDate) return aDate.localeCompare(bDate);
        const aCompany = String(a?.company || "").toLowerCase();
        const bCompany = String(b?.company || "").toLowerCase();
        if (aCompany !== bCompany) return aCompany.localeCompare(bCompany);
        const aTitle = String(a?.title || "").toLowerCase();
        const bTitle = String(b?.title || "").toLowerCase();
        if (aTitle !== bTitle) return aTitle.localeCompare(bTitle);
        return String(a?.url || "").localeCompare(String(b?.url || ""));
      })
    : [];
}

function canonicalJobUrl(job) {
  const finalUrl =
    job?.analysis?.postVerify?.finalUrl ||
    job?.jd?.finalUrl ||
    job?.postVerify?.finalUrl ||
    job?.url ||
    "";
  return normalizeJobUrl(finalUrl);
}

function buildJobCompositeKey(job) {
  const company = normalizeCompanyName(job?.company || "");
  const title = normalizeTitleForKey(job?.title || "");
  const location = normalizeLocationForKey(job?.location || "");
  if (!company && !title) return "";
  return `${company}|${title}|${location}`;
}

function buildJobDedupeKey(job) {
  const composite = buildJobCompositeKey(job);
  const sourceType = String(job?.sourceType || "").toLowerCase();
  if (sourceType.includes("platform_listing") && composite) return composite;
  const canonical = canonicalJobUrl(job);
  if (canonical) return canonical;
  return composite;
}

function trackConfigDefaults() {
  return {
    hydrogen_core: 0.4,
    energy_digitalization: 0.35,
    battery_ess_powertrain: 0.15,
    test_validation_reliability: 0.1
  };
}

const TRACK_KEYS = [
  "hydrogen_core",
  "energy_digitalization",
  "battery_ess_powertrain",
  "test_validation_reliability"
];

const TRACK_CLUSTER_LABEL = {
  hydrogen_core: "Hydrogen",
  energy_digitalization: "Energy-Digitalization",
  battery_ess_powertrain: "Battery-ESS",
  test_validation_reliability: "Test-Validation"
};

const TRACK_CN_LABEL = {
  hydrogen_core: "氢能主线",
  energy_digitalization: "能源系统数字化",
  battery_ess_powertrain: "电池/储能/电驱",
  test_validation_reliability: "试验验证与可靠性"
};

const SCOPE_PROFILE = {
  HYDROGEN_MAINLINE: "hydrogen_mainline",
  ADJACENT_MBSE: "adjacent_mbse"
};

const ADJACENT_DIRECTION_DEFS = [
  {
    key: "mbse_systems",
    labelCn: "MBSE/系统工程",
    fitTrack: "hydrogen_core",
    jobCluster: "Adjacent-MBSE",
    patterns: [
      /\b(mbse|model based systems engineering|systems engineering|systems engineer|system engineer|sysml|requirements engineer|requirements management|traceability|system architecture|architecture engineer)\b|模型驱动|系统工程|系统架构|需求工程|需求管理|可追溯/i
    ],
    evidenceCn: "MBSE/系统工程/需求可追溯关键词"
  },
  {
    key: "vv_integration",
    labelCn: "V&V/集成验证",
    fitTrack: "test_validation_reliability",
    jobCluster: "Adjacent-V&V",
    patterns: [
      /\b(verification|validation|v&v|integration engineer|integration test|qualification|commissioning|system test|test engineer|verification engineer|validation engineer)\b|验证|确认|集成|联调|测试工程师|试验验证|鉴定/i
    ],
    evidenceCn: "验证/集成/鉴定关键词"
  },
  {
    key: "reliability_diagnostics",
    labelCn: "可靠性/寿命/诊断",
    fitTrack: "test_validation_reliability",
    jobCluster: "Adjacent-Reliability",
    patterns: [
      /\b(reliability|durability|lifetime|aging|diagnostic|diagnostics|failure analysis|root cause|fault tree|fmea|dfmea|rams|rca)\b|可靠性|耐久|寿命|老化|诊断|故障分析|根因分析|故障树/i
    ],
    evidenceCn: "可靠性/寿命/故障诊断关键词"
  },
  {
    key: "digital_twin_phm",
    labelCn: "数字孪生/PHM",
    fitTrack: "energy_digitalization",
    jobCluster: "Adjacent-DigitalTwin",
    patterns: [
      /\b(digital twin|digital-twin|phm|condition monitoring|asset health|prognostics|remaining useful life|rul|state monitoring|predictive maintenance)\b|数字孪生|状态监测|资产健康|寿命预测|预测性维护|健康管理/i
    ],
    evidenceCn: "数字孪生/PHM/状态监测关键词"
  },
  {
    key: "technical_interface",
    labelCn: "技术接口/Owner Engineering",
    fitTrack: "hydrogen_core",
    jobCluster: "Adjacent-Interface",
    patterns: [
      /\b(owner'?s engineer|owner engineering|technical interface|interface engineer|technical project engineer|cross-functional technical lead|system architect|technical lead|owner representative)\b|技术接口|业主工程|跨部门技术牵头|技术负责人/i
    ],
    evidenceCn: "技术接口/业主工程关键词"
  },
  {
    key: "automotive_powertrain",
    labelCn: "汽车/电驱复杂装备",
    fitTrack: "battery_ess_powertrain",
    jobCluster: "Adjacent-Automotive",
    patterns: [
      /\b(automotive|vehicle|truck|bus|powertrain|battery|bms|drivetrain|cell|module|pack|ev|e-mobility)\b|汽车|整车|卡车|客车|动力总成|电驱|电池|储能/i
    ],
    evidenceCn: "汽车/电驱/电池复杂装备关键词"
  }
];

const ADJACENT_CLUSTER_DEFS = [
  {
    labelCn: "汽车与复杂装备",
    patterns: [
      /\b(automotive|vehicle|truck|bus|powertrain|drivetrain|battery|bms|cell|module|pack|e-mobility|rail|rolling stock)\b|汽车|整车|动力总成|电驱|轨交/i,
      /\bautomotive\b|\bvehicle\b|\bpowertrain\b|\bbattery\b|\bcomplex_equipment\b/i
    ]
  },
  {
    labelCn: "工业设备与自动化",
    patterns: [
      /\b(industrial automation|automation|robotics|machinery|compressor|turbine|pump|factory automation|plc|scada|process control|manufacturing equipment)\b|工业自动化|机器人|机械设备|压缩机|涡轮|泵|过程控制/i,
      /\bindustrial_automation\b|\bindustrial_equipment\b|\bautomation\b/i
    ]
  },
  {
    labelCn: "航空航天与高端制造",
    patterns: [
      /\b(aerospace|aviation|aircraft|avionics|satellite|space|propulsion|defense|semiconductor equipment|lithography)\b|航空航天|飞机|航电|卫星|航天|推进|国防|半导体设备/i,
      /\baerospace\b|\bhigh_end_manufacturing\b|\bdefense\b/i
    ]
  },
  {
    labelCn: "能源与基础设施",
    patterns: [
      /\b(energy|grid|power systems|utility|substation|transmission|renewable|hydrogen|fuel cell|electrolyzer|industrial gas|infrastructure)\b|能源|电网|电力系统|公用事业|氢能|燃料电池|电解槽|基础设施/i,
      /\benergy\b|\binfrastructure\b|\butility\b/i
    ]
  }
];

const TRACK_PATTERNS = {
  hydrogen_core:
    /\b(fuel cell|fuel-cell|electrolyzer|electrolysis|hydrogen|h2|electrochemical|pem|lt-?pem|ht-?pem|mea|membrane electrode|catalyst|anode|cathode)\b|燃料电池|电解槽|氢能|电化学|膜电极|催化剂/i,
  energy_digitalization:
    /\b(digital twin|digital-twin|phm|prognostics|health management|condition monitoring|state monitoring|asset health|predictive maintenance|remaining useful life|rul|model[- ]based|mbse|systems engineering)\b|数字孪生|状态监测|健康管理|寿命预测|模型驱动|系统工程/i,
  battery_ess_powertrain:
    /\b(battery|bms|state of health|soh|state of charge|soc|energy storage|ess|pack|cell|module|powertrain|e-?mobility|ev|thermal runaway|inverter|motor control)\b|电池|储能|电驱|热失控|BMS/i,
  test_validation_reliability:
    /\b(test data|test bench|validation|verification|v&v|ast|accelerated stress test|durability|reliability|lifetime|parameter identification|parameter estimation|system identification|calibration|doe)\b|测试数据|试验台架|验证|方法学|加速应力测试|可靠性|寿命|参数辨识|参数识别|标定/i
};

const TRANSFERABLE_PATTERNS = [
  TRACK_PATTERNS.energy_digitalization,
  TRACK_PATTERNS.battery_ess_powertrain,
  TRACK_PATTERNS.test_validation_reliability,
  /\b(model|modeling|simulation|simulations|diagnostic|diagnostics|calibration|system identification|parameter identification|validation|verification)\b|建模|仿真|诊断|参数辨识|验证/i
];

function extractJobText(job) {
  return [
    job?.title || "",
    job?.company || "",
    job?.location || "",
    job?.summary || "",
    job?.jd?.text || "",
    job?.jd?.rawText || "",
    Array.isArray(job?.companyTags) ? job.companyTags.join(" ") : "",
    job?.source || "",
    job?.url || ""
  ]
    .map((v) => String(v || ""))
    .join("\n");
}

function getScopeProfile(config) {
  const scope = String(config?.candidate?.scopeProfile || "").trim().toLowerCase();
  if (scope === SCOPE_PROFILE.ADJACENT_MBSE) return SCOPE_PROFILE.ADJACENT_MBSE;
  return SCOPE_PROFILE.HYDROGEN_MAINLINE;
}

function isAdjacentScope(config) {
  return getScopeProfile(config) === SCOPE_PROFILE.ADJACENT_MBSE;
}

function inferAdjacentIndustryCluster(job) {
  const text = extractJobText(job);
  const tagText = Array.isArray(job?.companyTags) ? job.companyTags.join(" ") : "";
  const combined = `${text}\n${tagText}`;
  for (const cluster of ADJACENT_CLUSTER_DEFS) {
    if (cluster.patterns.some((pattern) => pattern.test(combined))) return cluster.labelCn;
  }
  return "通用复杂系统";
}

function deriveTrackAndSignalsMainline(job) {
  const text = extractJobText(job);
  const lower = text.toLowerCase();
  const trackScores = {
    hydrogen_core: 0,
    energy_digitalization: 0,
    battery_ess_powertrain: 0,
    test_validation_reliability: 0
  };

  const matchedLabels = [];
  for (const track of TRACK_KEYS) {
    const hit = TRACK_PATTERNS[track].test(text);
    if (!hit) continue;
    if (track === "hydrogen_core") trackScores[track] += 72;
    if (track === "energy_digitalization") trackScores[track] += 68;
    if (track === "battery_ess_powertrain") trackScores[track] += 62;
    if (track === "test_validation_reliability") trackScores[track] += 64;
    matchedLabels.push(track);
  }

  const hasModelingSignal =
    /\b(model|models|modeling|simulation|simulations|cfd|calibration|parameter|parameters|diagnostic|diagnostics)\b|建模|仿真|参数|诊断/i.test(
      text
    );
  const hasReliabilitySignal =
    /\b(degradation|durability|reliability|lifetime|aging|soh|rul)\b|退化|耐久|可靠性|寿命/i.test(
      text
    );
  const hasTransferableSignal = TRANSFERABLE_PATTERNS.some((pattern) => pattern.test(text));
  if (hasModelingSignal) {
    trackScores.energy_digitalization += 12;
    trackScores.battery_ess_powertrain += 8;
    trackScores.test_validation_reliability += 10;
    trackScores.hydrogen_core += 8;
  }
  if (hasReliabilitySignal) {
    trackScores.hydrogen_core += 10;
    trackScores.battery_ess_powertrain += 8;
    trackScores.test_validation_reliability += 12;
  }

  let fitTrack = "hydrogen_core";
  let best = -1;
  for (const track of TRACK_KEYS) {
    if (trackScores[track] > best) {
      best = trackScores[track];
      fitTrack = track;
    }
  }

  const domainScore = clampNumber(trackScores.hydrogen_core, 0, 100);
  const transferableBase = Math.max(
    trackScores.energy_digitalization,
    trackScores.battery_ess_powertrain,
    trackScores.test_validation_reliability
  );
  const transferableScore = clampNumber(
    transferableBase + (hasTransferableSignal ? 10 : 0) + (hasModelingSignal ? 6 : 0),
    0,
    100
  );

  const evidence = [];
  if (TRACK_PATTERNS.hydrogen_core.test(text)) evidence.push("氢能/燃料电池/电解槽关键词");
  if (TRACK_PATTERNS.energy_digitalization.test(text))
    evidence.push("数字孪生/PHM/状态监测关键词");
  if (TRACK_PATTERNS.battery_ess_powertrain.test(text))
    evidence.push("电池/储能/电驱关键词");
  if (TRACK_PATTERNS.test_validation_reliability.test(text))
    evidence.push("测试验证/可靠性/参数辨识关键词");
  if (hasModelingSignal) evidence.push("建模/仿真/诊断关键词");
  if (hasReliabilitySignal) evidence.push("退化/寿命/可靠性关键词");

  const primaryEvidenceCn =
    evidence.length > 0
      ? evidence.slice(0, 2).join(" + ")
      : "岗位文本中可迁移技术证据较弱，建议人工复核。";

  return {
    fitTrack,
    jobCluster: TRACK_CLUSTER_LABEL[fitTrack] || "Hydrogen",
    industryTrackCn: TRACK_CN_LABEL[fitTrack] || "氢能主线",
    domainScore,
    transferableScore,
    hasTransferableSignal,
    hasDomainSignal: domainScore >= 40,
    primaryEvidenceCn,
    matchedTracks: matchedLabels,
    text: lower
  };
}

function deriveTrackAndSignalsAdjacent(job) {
  const text = extractJobText(job);
  const lower = text.toLowerCase();
  const matchedDirections = [];
  const directionScores = new Map();
  for (const direction of ADJACENT_DIRECTION_DEFS) {
    if (direction.patterns.some((pattern) => pattern.test(text))) {
      matchedDirections.push(direction.key);
      directionScores.set(
        direction.key,
        (directionScores.get(direction.key) || 0) +
          (direction.key === "mbse_systems" ? 74 : direction.key === "vv_integration" ? 72 : 68)
      );
    }
  }

  const hasModelingSignal =
    /\b(model|modeling|simulation|simulations|requirements|architecture|traceability|digital twin|system model|diagnostic)\b|建模|仿真|需求|架构|可追溯|数字孪生|诊断/i.test(
      text
    );
  const hasReliabilitySignal =
    /\b(reliability|durability|lifetime|aging|failure analysis|diagnostic|diagnostics|phm)\b|可靠性|耐久|寿命|老化|故障分析|诊断|健康管理/i.test(
      text
    );
  const hasTransferableSignal =
    /\b(mbse|systems engineering|sysml|verification|validation|v&v|integration|qualification|digital twin|phm|condition monitoring|reliability|durability|failure analysis|technical interface|owner engineering)\b|系统工程|验证|集成|数字孪生|状态监测|可靠性|故障分析|技术接口|业主工程/i.test(
      text
    );

  if (hasModelingSignal) {
    directionScores.set("mbse_systems", (directionScores.get("mbse_systems") || 0) + 10);
    directionScores.set("digital_twin_phm", (directionScores.get("digital_twin_phm") || 0) + 12);
  }
  if (hasReliabilitySignal) {
    directionScores.set(
      "reliability_diagnostics",
      (directionScores.get("reliability_diagnostics") || 0) + 12
    );
    directionScores.set("vv_integration", (directionScores.get("vv_integration") || 0) + 6);
  }

  let bestDirection = ADJACENT_DIRECTION_DEFS[0];
  let bestScore = -1;
  for (const direction of ADJACENT_DIRECTION_DEFS) {
    const score = directionScores.get(direction.key) || 0;
    if (score > bestScore) {
      bestScore = score;
      bestDirection = direction;
    }
  }

  const domainScore = clampNumber(bestScore > 0 ? bestScore : hasTransferableSignal ? 52 : 18, 0, 100);
  const transferableScore = clampNumber(
    domainScore + (hasTransferableSignal ? 12 : 0) + (hasModelingSignal ? 6 : 0),
    0,
    100
  );

  const evidence = [];
  for (const direction of ADJACENT_DIRECTION_DEFS) {
    if ((directionScores.get(direction.key) || 0) > 0) evidence.push(direction.evidenceCn);
  }
  if (hasModelingSignal) evidence.push("建模/架构/需求关键词");
  if (hasReliabilitySignal) evidence.push("可靠性/寿命/诊断关键词");

  return {
    fitTrack: bestDirection.fitTrack,
    jobCluster: bestDirection.jobCluster,
    industryTrackCn: "副线：MBSE/系统验证/技术接口",
    domainScore,
    transferableScore,
    hasTransferableSignal,
    hasDomainSignal: domainScore >= 45,
    primaryEvidenceCn:
      evidence.length > 0
        ? evidence.slice(0, 2).join(" + ")
        : "岗位文本中出现复杂系统工程/验证相关信号，建议人工复核。",
    matchedTracks: matchedDirections,
    text: lower,
    adjacentDirectionCn: bestDirection.labelCn,
    industryClusterCn: inferAdjacentIndustryCluster(job)
  };
}

function deriveTrackAndSignals(job, config) {
  if (isAdjacentScope(config)) return deriveTrackAndSignalsAdjacent(job);
  return deriveTrackAndSignalsMainline(job);
}

function inferRegionTag(job) {
  const text = [
    job?.location || "",
    job?.title || "",
    job?.summary || "",
    job?.url || "",
    job?.jd?.rawText || ""
  ]
    .map((v) => String(v || ""))
    .join(" ")
    .toLowerCase();
  if (!text.trim()) return "";
  if (/\b(global|worldwide|anywhere|remote worldwide|multiple countries)\b/.test(text)) return "Global";
  if (/\b(remote)\b/.test(text) && !/\b(us|usa|canada|europe|japan|australia|china|india)\b/.test(text))
    return "Global";
  if (/\b(canada|ontario|quebec|british columbia|alberta|vancouver|toronto|montreal)\b/.test(text))
    return "CA";
  if (/\b(united states|usa|\bus\b|california|texas|new york|massachusetts|washington|seattle|ann arbor)\b/.test(text))
    return "US";
  if (
    /\b(germany|france|netherlands|belgium|spain|italy|sweden|norway|denmark|finland|austria|switzerland|poland|czech|hungary|ireland|portugal|uk|united kingdom|europe|eu)\b/.test(
      text
    )
  )
    return "EU";
  if (/\b(japan|tokyo|osaka|yokohama|nagoya)\b/.test(text)) return "JP";
  if (/\b(australia|sydney|melbourne|brisbane|perth|adelaide)\b/.test(text)) return "AU";
  if (/\b(china|shanghai|beijing|shenzhen|guangzhou|suzhou)\b/.test(text)) return "CN";
  if (/\b(india|bengaluru|bangalore|chennai|pune|hyderabad)\b/.test(text)) return "IN";
  return "";
}

function isPreferredFiveRegion(regionTag) {
  return ["CA", "US", "EU", "JP", "AU", "Global"].includes(String(regionTag || ""));
}

function passesRegionMode(job, config) {
  const mode = String(config?.search?.regionMode || "global_open").trim().toLowerCase();
  if (!mode || mode === "global_open") return true;
  const region = inferRegionTag(job);
  if (mode === "strict_five_regions") return isPreferredFiveRegion(region);
  if (mode === "five_regions_preferred") return true;
  return true;
}

function isDirectEmployerLikeUrl(url, sourceType = "") {
  const normalizedSource = String(sourceType || "").toLowerCase();
  if (normalizedSource === "company") return true;
  if (!url) return false;
  if (isAtsHost(url)) return true;
  if (isAggregatorHost(url)) return false;
  try {
    const u = new URL(url);
    const path = String(u.pathname || "").toLowerCase();
    if (/\/(careers?|jobs?|jobslisting|job|position|positions|vacancies|opportunities)\b/.test(path))
      return true;
  } catch {
    return false;
  }
  return false;
}

function platformListingLabelForUrl(url, config) {
  if (!config?.search?.allowPlatformListings || !url) return "";
  const domains = Array.isArray(config?.search?.platformListingDomains)
    ? config.search.platformListingDomains
        .map((d) => String(d || "").trim().toLowerCase())
        .filter(Boolean)
    : [];
  if (domains.length === 0) return "";
  const domain = String(domainOf(url) || "").toLowerCase();
  if (!domain) return "";
  const matched = domains.find((d) => domain === d || domain.endsWith(`.${d}`));
  if (!matched) return "";
  if (matched === "linkedin.com") return "LinkedIn";
  return matched;
}

function isAllowedPlatformListingUrl(url, config) {
  return Boolean(platformListingLabelForUrl(url, config));
}

function isLimitedPlatformListingJob(job, config) {
  const targetUrl = canonicalJobUrl(job) || job?.url || "";
  return isAllowedPlatformListingUrl(targetUrl, config);
}

function platformListingTag(job, config) {
  const label = platformListingLabelForUrl(canonicalJobUrl(job) || job?.url || "", config);
  return label ? `${label}线索` : "";
}

function inferSourceQuality(job, config) {
  const targetUrl = canonicalJobUrl(job) || job?.url || "";
  if (!targetUrl) return "Uncertain";
  if (isAllowedPlatformListingUrl(targetUrl, config)) return "Platform Listing";
  if (isAggregatorHost(targetUrl)) return "Aggregator";
  if (isDirectEmployerLikeUrl(targetUrl, job?.sourceType || "")) return "Direct Employer";
  if (config?.filters?.preferDirectEmployerSite && job?.sourceType === "company")
    return "Direct Employer";
  return "Uncertain";
}

function sourceQualityRank(value) {
  if (value === "Direct Employer") return 4;
  if (value === "Platform Listing") return 2;
  if (value === "Uncertain") return 1;
  if (value === "Aggregator") return 0;
  return 0;
}

function chunk(text, maxChars) {
  if (!text) return "";
  if (text.length <= maxChars) return text;
  return text.slice(0, maxChars);
}

function isQuotaOrRateLimitError(err) {
  const message = String(err?.message || err || "");
  const code = String(err?.code || "");
  const type = String(err?.type || "");
  const status = Number(err?.status || 0);
  return (
    status === 429 ||
    /insufficient_quota|rate.?limit/i.test(message) ||
    /insufficient_quota|rate.?limit/i.test(code) ||
    /insufficient_quota|rate.?limit/i.test(type)
  );
}

function formatOpenAIError(err) {
  const status = Number(err?.status || 0) || "";
  const code = String(err?.code || "");
  const type = String(err?.type || "");
  const message = String(err?.message || err || "");
  const compact = [status, code, type].filter(Boolean).join("/");
  return compact ? `${compact} ${message}`.trim() : message;
}

async function readJsonIfExists(filePath) {
  try {
    const raw = await fs.readFile(filePath, "utf8");
    const normalized = raw.replace(/^\uFEFF/, "");
    return JSON.parse(normalized);
  } catch (err) {
    if (err && err.code === "ENOENT") return null;
    throw err;
  }
}

function extractJobsList(payload) {
  if (Array.isArray(payload?.jobs)) {
    return payload.jobs.filter((item) => item && typeof item === "object");
  }
  if (Array.isArray(payload)) {
    return payload.filter((item) => item && typeof item === "object");
  }
  return [];
}

function normalizeQueuedJob(job, config) {
  const url = normalizeJobUrl(job?.url || job?.canonicalUrl || "");
  if (!url) return null;
  const normalized = {
    ...job,
    url
  };
  normalized.canonicalUrl =
    normalizeJobUrl(normalized.canonicalUrl || "") || canonicalJobUrl(normalized) || url;
  normalized.sourceQuality = normalized.sourceQuality || inferSourceQuality(normalized, config);
  normalized.regionTag = normalized.regionTag || inferRegionTag(normalized);
  return normalized;
}

function uniqueQueuedJobs(jobs, config) {
  const seen = new Set();
  const ordered = [];
  for (const job of Array.isArray(jobs) ? jobs : []) {
    const normalized = normalizeQueuedJob(job, config);
    if (!normalized || !normalized.url || seen.has(normalized.url)) continue;
    seen.add(normalized.url);
    ordered.push(normalized);
  }
  return ordered;
}

async function writeJsonAtomic(filePath, data) {
  const dir = path.dirname(filePath);
  await fs.mkdir(dir, { recursive: true });
  const tempPath = `${filePath}.${Date.now()}.tmp`;
  await fs.writeFile(tempPath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  await fs.rename(tempPath, filePath);
}

function extractFirstJsonBlock(text) {
  const input = String(text || "");
  const startBrace = input.indexOf("{");
  const startBracket = input.indexOf("[");
  let start = -1;
  if (startBrace >= 0 && startBracket >= 0) start = Math.min(startBrace, startBracket);
  else start = Math.max(startBrace, startBracket);
  if (start < 0) return "";

  const stack = [];
  let inString = false;
  let escaped = false;
  for (let i = start; i < input.length; i += 1) {
    const ch = input[i];
    if (inString) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (ch === "\"") {
        inString = false;
      }
      continue;
    }
    if (ch === "\"") {
      inString = true;
      continue;
    }
    if (ch === "{" || ch === "[") {
      stack.push(ch);
      continue;
    }
    if (ch === "}" || ch === "]") {
      const top = stack[stack.length - 1];
      const okPair = (top === "{" && ch === "}") || (top === "[" && ch === "]");
      if (!okPair) return "";
      stack.pop();
      if (stack.length === 0) {
        return input.slice(start, i + 1);
      }
    }
  }
  return "";
}

function parseJsonLenient(rawText) {
  const raw = String(rawText || "").trim();
  if (!raw) throw new Error("Empty JSON text.");
  const candidates = [raw];
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fenced && fenced[1]) candidates.push(String(fenced[1]).trim());

  for (const candidate of candidates) {
    try {
      return JSON.parse(candidate);
    } catch {
      // continue
    }
    const block = extractFirstJsonBlock(candidate);
    if (!block) continue;
    try {
      return JSON.parse(block);
    } catch {
      // continue
    }
  }
  throw new Error("Unable to parse JSON from response text.");
}

function parseStructuredResponseJson(response, label) {
  if (response?.output_parsed && typeof response.output_parsed === "object") {
    return response.output_parsed;
  }

  const textCandidates = [];
  if (typeof response?.output_text === "string" && response.output_text.trim()) {
    textCandidates.push(response.output_text);
  }
  const outputItems = Array.isArray(response?.output) ? response.output : [];
  for (const item of outputItems) {
    const content = Array.isArray(item?.content) ? item.content : [];
    for (const part of content) {
      if (part?.json && typeof part.json === "object") return part.json;
      if (typeof part?.text === "string" && part.text.trim()) textCandidates.push(part.text);
    }
  }

  for (const candidate of textCandidates) {
    try {
      return parseJsonLenient(candidate);
    } catch {
      // try next candidate
    }
  }
  throw new Error(`${label} returned non-JSON response.`);
}

async function callStructuredJsonWithRetry({ label, maxAttempts = 2, fn }) {
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const response = await fn();
    try {
      return parseStructuredResponseJson(response, label);
    } catch (err) {
      lastError = err;
      if (attempt < maxAttempts) {
        console.log(
          `[${nowIso()}] ${label} JSON parse failed, retrying (${attempt}/${maxAttempts - 1})...`
        );
      }
    }
  }
  throw lastError || new Error(`${label} failed without details.`);
}

function withDefaults(config) {
  return {
    ...config,
    candidate: {
      ...(config?.candidate || {}),
      scopeProfile:
        String(config?.candidate?.scopeProfile || "").trim() || SCOPE_PROFILE.HYDROGEN_MAINLINE
    },
    search: {
      ...(config?.search || {}),
      trackMix:
        config?.search?.trackMix && typeof config.search.trackMix === "object"
          ? config.search.trackMix
          : {
              hydrogen_core: 0.4,
              energy_digitalization: 0.35,
              battery_ess_powertrain: 0.15,
              test_validation_reliability: 0.1
            },
      regionMode: config?.search?.regionMode ?? "global_open",
      feedbackWeightEnabled: config?.search?.feedbackWeightEnabled ?? true,
      webSearchConcurrency: Math.max(
        1,
        Math.floor(toFiniteNumber(config?.search?.webSearchConcurrency, 3))
      ),
      allowPlatformListings: config?.search?.allowPlatformListings ?? false,
      platformListingDomains: Array.isArray(config?.search?.platformListingDomains)
        ? config.search.platformListingDomains
        : ["linkedin.com"]
    },
    analysis: {
      model: config?.analysis?.model ?? "gpt-5.2",
      strictScoring: config?.analysis?.strictScoring ?? false,
      maxJobsToAnalyzePerRun: config?.analysis?.maxJobsToAnalyzePerRun ?? 200,
      jdFetchMaxJobsPerRun: Math.max(
        0,
        Math.floor(toFiniteNumber(config?.analysis?.jdFetchMaxJobsPerRun, 10))
      ),
      preFilterEnabled: config?.analysis?.preFilterEnabled ?? true,
      preFilterScoreThreshold: config?.analysis?.preFilterScoreThreshold ?? 40,
      lowTokenMode: config?.analysis?.lowTokenMode ?? false,
      scoringUseWebSearch: config?.analysis?.scoringUseWebSearch ?? true,
      scoringJdMaxChars: config?.analysis?.scoringJdMaxChars ?? 12000,
      lowTokenJdMaxChars: config?.analysis?.lowTokenJdMaxChars ?? 1800,
      postVerifyEnabled: config?.analysis?.postVerifyEnabled ?? false,
      postVerifyRequireChecked: config?.analysis?.postVerifyRequireChecked ?? true,
      postVerifyModel: config?.analysis?.postVerifyModel ?? "gpt-4o-mini",
      postVerifyUseWebSearch: config?.analysis?.postVerifyUseWebSearch ?? true,
      postVerifyMaxJobsPerRun: config?.analysis?.postVerifyMaxJobsPerRun ?? 40,
      postVerifyJdMaxChars: config?.analysis?.postVerifyJdMaxChars ?? 1200,
      transferableFitEnabled: config?.analysis?.transferableFitEnabled ?? true,
      recommendScoreThreshold: config?.analysis?.recommendScoreThreshold ?? 60,
      minTransferableScore: config?.analysis?.minTransferableScore ?? 55,
      platformListingRecommendScoreThreshold:
        config?.analysis?.platformListingRecommendScoreThreshold ?? 68
    },
    sources: {
      enableWebSearch: config?.sources?.enableWebSearch ?? true,
      enableCompanySources: config?.sources?.enableCompanySources ?? true,
      requireCompanyDiscovery: config?.sources?.requireCompanyDiscovery ?? true,
      companiesPath: config?.sources?.companiesPath ?? "./companies.json",
      maxCompaniesPerRun: config?.sources?.maxCompaniesPerRun ?? 50,
      maxJobsPerCompany: config?.sources?.maxJobsPerCompany ?? 60,
      maxJobLinksPerCompany: config?.sources?.maxJobLinksPerCompany ?? 40,
      companyConcurrency: Math.max(
        1,
        Math.floor(toFiniteNumber(config?.sources?.companyConcurrency, 4))
      ),
      preferMajorCompanies: config?.sources?.preferMajorCompanies ?? true,
      rotateCompanyWindow: config?.sources?.rotateCompanyWindow ?? true,
      majorCompanyPinnedCount: Math.max(
        0,
        Math.floor(toFiniteNumber(config?.sources?.majorCompanyPinnedCount, 140))
      ),
      companyRotationIntervalDays: Math.max(
        1,
        Math.floor(toFiniteNumber(config?.sources?.companyRotationIntervalDays, 1))
      ),
      enableCompanySearchFallback: config?.sources?.enableCompanySearchFallback ?? true,
      maxCompanySearchFallbacksPerRun: Math.max(
        0,
        Math.floor(toFiniteNumber(config?.sources?.maxCompanySearchFallbacksPerRun, 18))
      ),
      fallbackSearchRegions: Array.isArray(config?.sources?.fallbackSearchRegions)
        ? config.sources.fallbackSearchRegions
        : ["region:JP"],
      priorityRegionWeights:
        config?.sources?.priorityRegionWeights &&
        typeof config.sources.priorityRegionWeights === "object"
          ? config.sources.priorityRegionWeights
          : {},
      majorCompanyKeywords: Array.isArray(config?.sources?.majorCompanyKeywords)
        ? config.sources.majorCompanyKeywords
        : [
            "bosch",
            "bmw",
            "mercedes",
            "volkswagen",
            "toyota",
            "honda",
            "hyundai",
            "stellantis",
            "ford",
            "cummins",
            "siemens energy",
            "air liquide",
            "linde",
            "shell",
            "ballard",
            "plug power",
            "nel hydrogen",
            "john cockerill"
          ],
      cnHydrogenCompanyKeywords: Array.isArray(config?.sources?.cnHydrogenCompanyKeywords)
        ? config.sources.cnHydrogenCompanyKeywords
        : DEFAULT_CN_HYDROGEN_COMPANY_KEYWORDS
    },
    filters: {
      maxPostAgeDays: config?.filters?.maxPostAgeDays ?? 180,
      excludeUnavailableLinks: config?.filters?.excludeUnavailableLinks ?? true,
      outputLinkRecheckHours: config?.filters?.outputLinkRecheckHours ?? 72,
      excludeAggregatorLinks: config?.filters?.excludeAggregatorLinks ?? true,
      preferDirectEmployerSite: config?.filters?.preferDirectEmployerSite ?? true,
      extraBlockedDomains: Array.isArray(config?.filters?.extraBlockedDomains)
        ? config.filters.extraBlockedDomains
        : []
    },
    companyDiscovery: {
      model: config?.companyDiscovery?.model ?? config?.search?.model ?? "gpt-4o-mini",
      enableAutoDiscovery: config?.companyDiscovery?.enableAutoDiscovery ?? true,
      maxNewCompaniesPerRun: config?.companyDiscovery?.maxNewCompaniesPerRun ?? 40,
      maxCompaniesPerQuery: config?.companyDiscovery?.maxCompaniesPerQuery ?? 12,
      queryConcurrency: Math.max(
        1,
        Math.floor(toFiniteNumber(config?.companyDiscovery?.queryConcurrency, 3))
      ),
      queries: Array.isArray(config?.companyDiscovery?.queries)
        ? config.companyDiscovery.queries
        : []
    },
    adaptiveSearch: {
      enabled: config?.adaptiveSearch?.enabled ?? true,
      minNewJobsToContinue: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.minNewJobsToContinue,
            toFiniteNumber(config?.adaptiveSearch?.targetNewJobs, 3)
          )
        )
      ),
      baseRoundSeconds: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.baseRoundSeconds,
            toFiniteNumber(config?.adaptiveSearch?.baseBudgetSeconds, 300)
          )
        )
      ),
      extendRoundSeconds: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.extendRoundSeconds,
            toFiniteNumber(config?.adaptiveSearch?.extendBudgetSeconds, 480)
          )
        )
      ),
      deepRoundSeconds: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.deepRoundSeconds,
            toFiniteNumber(config?.adaptiveSearch?.deepBudgetSeconds, 720)
          )
        )
      ),
      baseExistingCompanyBatchSize: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.baseExistingCompanyBatchSize,
            toFiniteNumber(config?.adaptiveSearch?.baseExistingCompanies, 8)
          )
        )
      ),
      extendExistingCompanyBatchSize: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.extendExistingCompanyBatchSize,
            toFiniteNumber(config?.adaptiveSearch?.extendCompanyBatchSize, 4)
          )
        )
      ),
      coldStartQueryBudget: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.coldStartQueryBudget,
            toFiniteNumber(config?.adaptiveSearch?.coldDiscoveryQueryBudget, 6)
          )
        )
      ),
      coldStartMaxNewCompanies: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.coldStartMaxNewCompanies,
            toFiniteNumber(config?.adaptiveSearch?.coldDiscoveryCompanyBudget, 6)
          )
        )
      ),
      coldStartImmediateProcessBatchSize: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.coldStartImmediateProcessBatchSize,
            toFiniteNumber(config?.adaptiveSearch?.processDiscoveredCompaniesPerRound, 4)
          )
        )
      ),
      deepSearchQueryBudget: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.deepSearchQueryBudget,
            toFiniteNumber(config?.adaptiveSearch?.warmDiscoveryQueryBudget, 4)
          )
        )
      ),
      deepSearchMaxNewCompanies: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.deepSearchMaxNewCompanies,
            toFiniteNumber(config?.adaptiveSearch?.warmDiscoveryCompanyBudget, 4)
          )
        )
      ),
      deepSearchImmediateProcessBatchSize: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.deepSearchImmediateProcessBatchSize,
            toFiniteNumber(config?.adaptiveSearch?.processDiscoveredCompaniesPerRound, 4)
          )
        )
      ),
      companyCooldownDaysNoJobs: Math.max(
        1,
        Math.floor(toFiniteNumber(config?.adaptiveSearch?.companyCooldownDaysNoJobs, 7))
      ),
      companyCooldownDaysSomeJobsNoNew: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.companyCooldownDaysSomeJobsNoNew,
            toFiniteNumber(config?.adaptiveSearch?.companyCooldownDaysNoNewJobs, 3)
          )
        )
      ),
      companyCooldownDaysWithNew: Math.max(
        1,
        Math.floor(
          toFiniteNumber(
            config?.adaptiveSearch?.companyCooldownDaysWithNew,
            toFiniteNumber(config?.adaptiveSearch?.companyCooldownDaysWithNewJobs, 2)
          )
        )
      )
    },
    translation: {
      enable: config?.translation?.enable ?? false,
      model: config?.translation?.model ?? config?.analysis?.model ?? "gpt-4o-mini",
      target: config?.translation?.target ?? "zh-CN"
    },
    output: {
      ...config?.output,
      trackerXlsxPath:
        config?.output?.trackerXlsxPath ??
        config?.output?.recommendedXlsxPath ??
        "./jobs_recommended.xlsx",
      recommendedMode: config?.output?.recommendedMode ?? "append"
    }
  };
}

async function loadCompanies(companiesPath) {
  try {
    const raw = await fs.readFile(companiesPath, "utf8");
    const normalized = raw.replace(/^\uFEFF/, "");
    const parsed = JSON.parse(normalized);
    return parsed;
  } catch (err) {
    if (err && err.code === "ENOENT") {
      const empty = { companies: [] };
      await fs.writeFile(companiesPath, JSON.stringify(empty, null, 2), "utf8");
      return empty;
    }
    throw err;
  }
}

async function writeCompanies(companiesPath, data) {
  await writeJsonAtomic(companiesPath, data);
}

function detectAtsFromUrl(url) {
  if (!url) return null;
  const patterns = [
    { type: "greenhouse", regex: /boards\.greenhouse\.io\/([^/]+)/i },
    { type: "lever", regex: /jobs\.lever\.co\/([^/]+)/i },
    { type: "smartrecruiters", regex: /careers\.smartrecruiters\.com\/([^/]+)/i },
    { type: "workable", regex: /apply\.workable\.com\/([^/]+)/i },
    { type: "workable", regex: /([^/.]+)\.workable\.com/i },
    { type: "ashby", regex: /jobs\.ashbyhq\.com\/([^/]+)/i },
    { type: "workday", regex: /myworkdayjobs\.com\/([^/]+)/i }
  ];
  for (const p of patterns) {
    const match = url.match(p.regex);
    if (match && match[1]) return { type: p.type, id: match[1] };
  }
  return null;
}

async function discoverCareersFromWebsite({ website, config }) {
  if (!website) return "";
  let baseUrl;
  try {
    baseUrl = new URL(website);
  } catch {
    return "";
  }
  const candidates = [
    "/careers",
    "/career",
    "/jobs",
    "/join-us",
    "/about/careers",
    "/about-us/careers",
    "/company/careers",
    "/work-with-us"
  ];

  for (const pathCandidate of candidates) {
    let target;
    try {
      target = new URL(pathCandidate, baseUrl).toString();
    } catch {
      continue;
    }
    try {
      const res = await fetchWithConfigTimeout(target, config);
      if (!res.ok) continue;
      const html = await res.text();
      const plain = stripHtmlToText(html).slice(0, 2500).toLowerCase();
      const hasSignal =
        extractAllJsonLdJobPostings(html).length > 0 ||
        /careers?|jobs?|openings?|vacancies?|join us|hiring|apply/i.test(plain);
      if (hasSignal) return target;
    } catch {
      // ignore probe failures
    }
  }
  return "";
}

async function fetchWithConfigTimeout(url, config, options = {}) {
  const controller = new AbortController();
  const timeoutMs = Math.max(1000, Math.floor(toFiniteNumber(config?.fetch?.timeoutMs, 20000)));
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const mergedHeaders = {
      "user-agent": config?.fetch?.userAgent || "jobflow/1.0",
      ...(options.headers || {})
    };
    return await fetch(url, {
      ...options,
      headers: mergedHeaders,
      signal: controller.signal
    });
  } finally {
    clearTimeout(timeout);
  }
}

async function discoverCompanyCareers({ client, config, companyName }) {
  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      website: { type: "string" },
      careersUrl: { type: "string" }
    },
    required: ["website", "careersUrl"]
  };

  const input = `Find the official website and the careers/jobs page for this company.
Company name: ${companyName}

Rules:
- Use only official company website (not aggregators).
- If multiple sites exist, prefer the global corporate site.
- If careers page is not found, return an empty string for careersUrl.

Output ONLY JSON matching the schema.`;

  const data = await callStructuredJsonWithRetry({
    label: `Company careers discovery (${companyName})`,
    maxAttempts: 2,
    fn: () =>
      client.responses.create({
        model: config.companyDiscovery.model,
        tools: [buildWebSearchTool(config)],
        input,
        text: {
          format: {
            type: "json_schema",
            name: "company_discovery",
            strict: true,
            schema
          }
        }
      })
  });
  return data;
}

async function discoverCompaniesFromQuery({ client, config, query }) {
  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      companies: {
        type: "array",
        items: {
          type: "object",
          additionalProperties: false,
          properties: {
            name: { type: "string" },
            website: { type: "string" },
            tags: { type: "array", items: { type: "string" } },
            region: { type: "string" }
          },
          required: ["name", "website", "tags", "region"]
        }
      }
    },
    required: ["companies"]
  };

  const input = isAdjacentScope(config)
    ? `Find real companies with official websites operating in adjacent technical business domains around MBSE, systems engineering, requirements/traceability, verification & validation, integration, reliability/durability, diagnostics, digital twin/PHM, technical interface, and owner engineering.
Prefer companies whose products, platforms, or industrial programs sit in automotive & complex equipment, industrial equipment & automation, aerospace & high-end manufacturing, plus energy/infrastructure systems with strong systems-engineering needs.
Return only real companies with official websites (no aggregators).
Region should be one of: Global, EU, US, CN, JP, KR, CA, AU, UK, CH, IL, IN, ME, AE, SA, ES, PT, SE, NO, DK, NL, FR, DE.
Tags should be short lowercase keywords, e.g. mbse, systems, requirements, traceability, verification, validation, integration, reliability, durability, digital_twin, phm, technical_interface, owner_engineering, automotive, complex_equipment, industrial_automation, aerospace, high_end_manufacturing, energy, infrastructure.

Query:
${query}

Return up to ${config.companyDiscovery.maxCompaniesPerQuery} companies.
Output ONLY JSON matching the schema.`
    : `Find real companies with official websites operating in business areas related to hydrogen systems, electrolyzers, fuel cells, MEA/membranes/catalysts, diagnostic testing, controls, durability, and electrochemical energy systems.
Return only real companies with official websites (no aggregators). Prefer companies with meaningful products, platforms, industrial programs, or R&D activity in these areas.
Region should be one of: Global, EU, US, CN, JP, KR, CA, AU, UK, CH, IL, IN, ME, AE, SA, ES, PT, SE, NO, DK, NL, FR, DE.
Tags should be short lowercase keywords, e.g. electrolyzer, fuel_cell, materials, catalyst, membrane, MEA, diagnostics, testing, systems, controls, stack, balance_of_plant, OEM, energy_storage, research.

Query:
${query}

Return up to ${config.companyDiscovery.maxCompaniesPerQuery} companies.
Output ONLY JSON matching the schema.`;

  const data = await callStructuredJsonWithRetry({
    label: `Company list discovery (${query})`,
    maxAttempts: 2,
    fn: () =>
      client.responses.create({
        model: config.companyDiscovery.model,
        tools: [buildWebSearchTool(config)],
        input,
        text: {
          format: {
            type: "json_schema",
            name: "company_list",
            strict: true,
            schema
          }
        }
      })
  });
  if (!data || !Array.isArray(data.companies)) return [];
  return data.companies;
}

async function autoDiscoverCompanies({
  client,
  config,
  companiesPath,
  baseDir,
  disabled,
  companiesData = null,
  queryStartIndex = 0,
  queryBudget = Infinity,
  maxNewCompanies = null
}) {
  if (disabled || !config.companyDiscovery.enableAutoDiscovery) {
    return { added: 0, total: 0, nextQueryIndex: queryStartIndex, newCompanies: [] };
  }
  const data = companiesData || (await loadCompanies(companiesPath));
  const companies = Array.isArray(data.companies) ? data.companies : [];

  const nameSet = new Set(companies.map((c) => normalizeCompanyName(c.name)));
  const domainSet = new Set(companies.map((c) => companyDomain(c.website)));
  const newCompanies = [];
  const allQueries =
    Array.isArray(config.companyDiscovery.queries) && config.companyDiscovery.queries.length > 0
      ? config.companyDiscovery.queries
      : [
          "global PEM electrolyzer companies",
          "hydrogen fuel cell stack manufacturers",
          "MEA membrane electrode assembly companies",
          "fuel cell catalyst companies",
          "hydrogen system controls and balance of plant companies",
          "electrochemical diagnostics and testing companies"
        ];
  const startIndex = Math.max(0, Math.floor(toFiniteNumber(queryStartIndex, 0)));
  const limit = Number.isFinite(queryBudget)
    ? Math.max(0, Math.floor(toFiniteNumber(queryBudget, 0)))
    : allQueries.length;
  const endIndex = Math.min(allQueries.length, startIndex + limit);
  const queries = allQueries.slice(startIndex, endIndex);
  const newCompanyCap =
    Number.isFinite(maxNewCompanies) && maxNewCompanies !== null
      ? Math.max(0, Math.floor(toFiniteNumber(maxNewCompanies, 0)))
      : config.companyDiscovery.maxNewCompaniesPerRun;

  const queryConcurrency = Math.max(
    1,
    Math.floor(toFiniteNumber(config?.companyDiscovery?.queryConcurrency, 3))
  );

  for (let start = 0; start < queries.length; start += queryConcurrency) {
    const batch = queries.slice(start, start + queryConcurrency);
    const batchResults = await Promise.all(
      batch.map(async (query) => {
        try {
          return await discoverCompaniesFromQuery({ client, config, query });
        } catch (err) {
          console.log(
            `[${nowIso()}] Company query failed, skip: ${query} | ${String(err?.message || err)}`
          );
          return [];
        }
      })
    );
    for (const found of batchResults) {
      for (const item of found) {
        const name = String(item.name || "").trim();
        if (!name) continue;
        const normName = normalizeCompanyName(name);
        const website = String(item.website || "").trim();
        const domain = companyDomain(website);
        if (nameSet.has(normName)) continue;
        if (domain && domainSet.has(domain)) continue;

        const tags = Array.isArray(item.tags) ? item.tags.filter(Boolean) : [];
        const region = String(item.region || "").trim();
        const regionTag = region ? `region:${region.toUpperCase()}` : "";
        if (regionTag && !tags.includes(regionTag)) tags.push(regionTag);

        newCompanies.push({
          name,
          website,
          careersUrl: "",
          tags
        });
        nameSet.add(normName);
        if (domain) domainSet.add(domain);
        if (newCompanies.length >= newCompanyCap) break;
      }
      if (newCompanies.length >= newCompanyCap) break;
    }
    if (newCompanies.length >= newCompanyCap) break;
  }

  if (newCompanies.length > 0) {
    companies.push(...newCompanies);
    data.companies = companies;
    await writeCompanies(companiesPath, data);
  }

  return {
    added: newCompanies.length,
    total: companies.length,
    nextQueryIndex: endIndex,
    newCompanies
  };
}

async function processCompanySource({
  company,
  companyIndex,
  toProcessLength,
  client,
  config,
  args,
  forceDiscover,
  allowCompanySearchFallback,
  maxFallbackSearches,
  state,
  seenJobUrls,
  adaptiveSearch
}) {
  const name = String(company.name || "").trim();
  if (!name) return { jobs: [], changed: false };
  if (companyIndex > 0 && companyIndex % 25 === 0) {
    console.log(
      `[${nowIso()}] Company sources progress: ${companyIndex}/${toProcessLength} processed.`
    );
  }

  let changed = false;
  if ((forceDiscover || args.discoverCompanies) && !args.offline) {
    if (!company.website || !company.careersUrl) {
      if (company.website && !company.careersUrl) {
        try {
          const guessedCareersUrl = await discoverCareersFromWebsite({
            website: company.website,
            config
          });
          if (guessedCareersUrl) {
            company.careersUrl = guessedCareersUrl;
            changed = true;
          }
        } catch {
          // ignore guess failures
        }
      }
      try {
        if (!company.website || !company.careersUrl) {
          const found = await discoverCompanyCareers({
            client,
            config,
            companyName: name
          });
          if (found.website && !company.website) {
            company.website = found.website;
            changed = true;
          }
          if (found.careersUrl && !company.careersUrl) {
            company.careersUrl = found.careersUrl;
            changed = true;
          }
        }
      } catch {
        // ignore discovery failures per company
      }
    }
  }

  let atsType = company.atsType || "";
  let atsId = company.atsId || "";
  if (!atsType || !atsId) {
    const detected = detectAtsFromUrl(company.careersUrl || company.website || "");
    if (detected && !atsType) {
      atsType = detected.type;
      atsId = detected.id;
      if (args.discoverCompanies) {
        company.atsType = atsType;
        company.atsId = atsId;
        changed = true;
      }
    }
  }

  let jobs = [];
  try {
    if (atsType === "greenhouse" && atsId) {
      jobs = await fetchGreenhouseJobs(atsId, config);
    } else if (atsType === "lever" && atsId) {
      jobs = await fetchLeverJobs(atsId, config);
    } else if (atsType === "smartrecruiters" && atsId) {
      jobs = await fetchSmartRecruitersJobs(atsId, config);
    } else if (company.careersUrl) {
      jobs = await fetchCareersPageJobs({
        url: company.careersUrl,
        maxLinks: config.sources.maxJobLinksPerCompany,
        config
      });
    }
  } catch {
    jobs = [];
  }

  const sourceLabel = `company:${name}${atsType ? `:${atsType}` : ""}`;
  const tags = Array.isArray(company.tags) ? company.tags.filter(Boolean) : [];
  const filtered = jobs
    .filter((job) => job && job.url)
    .filter((job) => !isStale(job.datePosted, config.filters.maxPostAgeDays))
    .slice(0, config.sources.maxJobsPerCompany)
    .map((job) => ({
      title: job.title || "",
      company: name,
      location: job.location || "",
      url: job.url,
      datePosted: job.datePosted || "",
      summary: job.summary || "",
      source: sourceLabel,
      sourceType: "company",
      companyTags: tags
    }));

  const results = [...filtered];
  if (
    filtered.length === 0 &&
    allowCompanySearchFallback &&
    state.fallbackSearchesUsed < maxFallbackSearches &&
    companySearchFallbackEnabled(company, config)
  ) {
    const fallbackQuery = buildCompanySearchFallbackQuery(company, config);
    if (fallbackQuery) {
      state.fallbackSearchesUsed += 1;
      try {
        console.log(`[${nowIso()}] Company search fallback: ${name} | ${fallbackQuery}`);
        const fallbackJobs = await openaiSearchJobs({
          client,
          config,
          query: fallbackQuery
        });
        const fallbackSource = `company_search:${name}`;
        const normalizedFallbackJobs = fallbackJobs
          .filter((job) => job && job.url)
          .filter((job) => !isStale(job.datePosted, config.filters.maxPostAgeDays))
          .slice(0, config.sources.maxJobsPerCompany)
          .map((job) => ({
            title: job.title || "",
            company: job.company || name,
            location: job.location || "",
            url: job.url,
            datePosted: job.datePosted || "",
            summary: job.summary || "",
            source: fallbackSource,
            sourceType: "company_search",
            companyTags: tags
          }));
        results.push(...normalizedFallbackJobs);
      } catch (err) {
        console.log(
          `[${nowIso()}] Company search fallback failed for ${name}: ${String(err?.message || err)}`
        );
      }
    }
  }

  const uniqueJobs = [];
  const jobUrlSet = new Set();
  for (const job of results) {
    const normalizedUrl = normalizeJobUrl(job?.url || "");
    if (!normalizedUrl || jobUrlSet.has(normalizedUrl)) continue;
    jobUrlSet.add(normalizedUrl);
    uniqueJobs.push({ ...job, url: normalizedUrl });
  }

  const jobsFoundCount = uniqueJobs.length;
  let newJobsCount = 0;
  const knownUrls = seenJobUrls instanceof Set ? seenJobUrls : null;
  if (knownUrls) {
    for (const job of uniqueJobs) {
      if (!knownUrls.has(job.url)) newJobsCount += 1;
      knownUrls.add(job.url);
    }
  } else {
    newJobsCount = jobsFoundCount;
  }

  const now = nowIso();
  company.lastSearchedAt = now;
  company.lastJobsFoundCount = jobsFoundCount;
  company.lastNewJobsCount = newJobsCount;
  company.cooldownUntil = getCompanyCooldownUntil(
    adaptiveSearch,
    jobsFoundCount,
    newJobsCount,
    Date.now()
  );
  changed = true;

  return {
    jobs: uniqueJobs,
    changed,
    stats: {
      jobsFoundCount,
      newJobsCount,
      cooldownUntil: company.cooldownUntil,
      searchedAt: now
    }
  };
}

async function collectCompanyJobs({
  client,
  config,
  args,
  baseDir,
  forceDiscover = false,
  disableCompanyDiscovery = false,
  runStartedAt = Date.now(),
  seenJobUrls = new Set()
}) {
  if (!config.sources.enableCompanySources || args.offline) return [];

  const companiesPath = path.resolve(
    baseDir,
    args.companiesPath || config.sources.companiesPath
  );
  const companiesData = await loadCompanies(companiesPath);
  if (!Array.isArray(companiesData.companies)) companiesData.companies = [];
  const companies = companiesData.companies;

  const adaptiveSearch = config.adaptiveSearch || {};
  const adaptiveEnabled = adaptiveSearch.enabled !== false;
  const allowDiscovery =
    !disableCompanyDiscovery &&
    (forceDiscover || adaptiveEnabled) &&
    config.companyDiscovery.enableAutoDiscovery !== false;

  const maxCompanies =
    typeof args.maxCompanies === "number" && Number.isFinite(args.maxCompanies)
      ? Math.max(0, args.maxCompanies)
      : config.sources.maxCompaniesPerRun;

  const baseRoundMs = Math.max(0, Math.floor(toFiniteNumber(adaptiveSearch.baseRoundSeconds, 300))) * 1000;
  const extendRoundMs =
    Math.max(0, Math.floor(toFiniteNumber(adaptiveSearch.extendRoundSeconds, 480))) * 1000;
  const deepRoundMs = Math.max(0, Math.floor(toFiniteNumber(adaptiveSearch.deepRoundSeconds, 720))) * 1000;
  const minNewJobsToContinue = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.minNewJobsToContinue, 3))
  );
  const baseExistingBatchSize = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.baseExistingCompanyBatchSize, 8))
  );
  const extendExistingBatchSize = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.extendExistingCompanyBatchSize, 4))
  );
  const coldStartQueryBudget = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.coldStartQueryBudget, 6))
  );
  const coldStartMaxNewCompanies = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.coldStartMaxNewCompanies, 6))
  );
  const coldStartImmediateProcessBatchSize = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.coldStartImmediateProcessBatchSize, 4))
  );
  const deepSearchQueryBudget = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.deepSearchQueryBudget, 4))
  );
  const deepSearchMaxNewCompanies = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.deepSearchMaxNewCompanies, 4))
  );
  const deepSearchImmediateProcessBatchSize = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch.deepSearchImmediateProcessBatchSize, 4))
  );

  const prioritizedCompanies = prioritizeCompaniesForRun(companies, config);
  const initialSelection = buildCompanyRunSelection(prioritizedCompanies, maxCompanies, config);
  let orderedCompanies = initialSelection.companies;
  if (prioritizedCompanies.length && maxCompanies > 0 && config.sources.preferMajorCompanies) {
    const sample = orderedCompanies
      .slice(0, Math.min(12, orderedCompanies.length))
      .map((c) => String(c?.name || "").trim())
      .filter(Boolean)
      .join(" | ");
    console.log(
      `[${nowIso()}] Company priority enabled. Processing ${orderedCompanies.length}/${prioritizedCompanies.length} companies (${initialSelection.pinnedCount} pinned${
        initialSelection.rotated ? ` + rotated tail offset ${initialSelection.rotationOffset}` : ""
      }). Sample: ${sample}`
    );
  }

  const knownJobUrls = seenJobUrls instanceof Set ? seenJobUrls : new Set();
  const processedCompanyKeys = new Set();
  const state = { fallbackSearchesUsed: 0 };
  const allowCompanySearchFallback = Boolean(
    client &&
      config?.sources?.enableWebSearch !== false &&
      config?.sources?.enableCompanySearchFallback !== false &&
      !args.disableWebSearch &&
      !args.offline
  );
  const maxFallbackSearches = Math.max(
    0,
    Math.floor(toFiniteNumber(config?.sources?.maxCompanySearchFallbacksPerRun, 18))
  );
  const companyLimit = pLimit(
    Math.max(1, Math.floor(toFiniteNumber(config?.sources?.companyConcurrency, 4)))
  );
  const results = [];
  let nextDiscoveryQueryIndex = 0;
  let totalNewJobs = 0;
  let totalJobsFound = 0;
  let totalDiscoveredCompanies = 0;

  const runElapsedMs = () => Date.now() - runStartedAt;
  const canStartRound = (budgetMs) => runElapsedMs() < budgetMs;

  function refreshOrderedCompanies(preferredKeys = new Set()) {
    const prioritized = prioritizeCompaniesForRun(companies, config);
    const selection = buildCompanyRunSelection(prioritized, maxCompanies, config);
    const base = selection.companies;
    if (!preferredKeys || preferredKeys.size === 0) return base;
    const preferred = [];
    const rest = [];
    for (const company of base) {
      const key = companyRecordKey(company);
      if (key && preferredKeys.has(key)) preferred.push(company);
      else rest.push(company);
    }
    return preferred.concat(rest);
  }

  function pickEligibleCompanies(limit, preferredKeys = new Set()) {
    orderedCompanies = refreshOrderedCompanies(preferredKeys);
    const batch = [];
    const batchKeys = new Set();
    for (const company of orderedCompanies) {
      if (batch.length >= limit) break;
      const key = companyRecordKey(company);
      if (!key || processedCompanyKeys.has(key) || batchKeys.has(key)) continue;
      if (isCompanyInCooldown(company, Date.now())) continue;
      batch.push(company);
      batchKeys.add(key);
    }
    return batch;
  }

  async function processCompanyBatch(label, batch, discoveryAdded = 0) {
    if (!Array.isArray(batch) || batch.length === 0) {
      console.log(
        `[${nowIso()}] Adaptive round ${label}: processed 0 existing companies, discovered ${discoveryAdded} companies, found 0 jobs, new 0 jobs.`
      );
      return { jobs: [], jobsFoundCount: 0, newJobsCount: 0, processedCount: 0 };
    }

    const batchJobs = [];
    const batchResults = await Promise.all(
      batch.map((company, companyIndex) =>
        companyLimit(async () => {
          try {
            return await processCompanySource({
              company,
              companyIndex,
              toProcessLength: batch.length,
              client,
              config,
              args,
              forceDiscover,
              allowCompanySearchFallback,
              maxFallbackSearches,
              state,
              seenJobUrls: knownJobUrls,
              adaptiveSearch
            });
          } catch (err) {
            console.log(
              `[${nowIso()}] Adaptive round ${label}: company failed, skip ${String(
                company?.name || ""
              )} | ${String(err?.message || err)}`
            );
            return { jobs: [], changed: false, stats: { jobsFoundCount: 0, newJobsCount: 0 } };
          }
        })
      )
    );

    let changed = false;
    let jobsFoundCount = 0;
    let newJobsCount = 0;
    for (let index = 0; index < batch.length; index += 1) {
      const company = batch[index];
      const key = companyRecordKey(company);
      if (key) processedCompanyKeys.add(key);
      const item = batchResults[index];
      if (!item) continue;
      if (item.changed) changed = true;
      jobsFoundCount += Math.max(0, Math.floor(toFiniteNumber(item.stats?.jobsFoundCount, 0)));
      newJobsCount += Math.max(0, Math.floor(toFiniteNumber(item.stats?.newJobsCount, 0)));
      if (Array.isArray(item.jobs) && item.jobs.length > 0) {
        results.push(...item.jobs);
        batchJobs.push(...item.jobs);
      }
    }
    totalJobsFound += jobsFoundCount;
    totalNewJobs += newJobsCount;
    if (changed) {
      await writeCompanies(companiesPath, companiesData);
    }
    console.log(
      `[${nowIso()}] Adaptive round ${label}: processed ${batch.length} existing companies, discovered ${discoveryAdded} companies, found ${jobsFoundCount} jobs, new ${newJobsCount} jobs.`
    );
    return { jobs: batchJobs, jobsFoundCount, newJobsCount, processedCount: batch.length };
  }

  async function runDiscoveryRound(label, queryBudget, maxNewCompanies, immediateBatchSize) {
    if (!allowDiscovery) {
      console.log(
        `[${nowIso()}] Adaptive round ${label}: company discovery disabled, skip discovery.`
      );
      return {
        added: 0,
        total: companies.length,
        nextQueryIndex: nextDiscoveryQueryIndex,
        newCompanies: [],
        jobsFoundCount: 0,
        newJobsCount: 0,
        processedCount: 0
      };
    }

    const discovery = await autoDiscoverCompanies({
      client,
      config,
      companiesPath,
      baseDir,
      disabled: !allowDiscovery,
      companiesData,
      queryStartIndex: nextDiscoveryQueryIndex,
      queryBudget,
      maxNewCompanies
    });
    nextDiscoveryQueryIndex =
      typeof discovery.nextQueryIndex === "number" ? discovery.nextQueryIndex : nextDiscoveryQueryIndex;
    totalDiscoveredCompanies += Math.max(
      0,
      Math.floor(toFiniteNumber(discovery.added, 0))
    );
    const preferredKeys = new Set(
      Array.isArray(discovery.newCompanies)
        ? discovery.newCompanies.map((company) => companyRecordKey(company)).filter(Boolean)
        : []
    );
    orderedCompanies = refreshOrderedCompanies(preferredKeys);
    const batch = pickEligibleCompanies(immediateBatchSize, preferredKeys);
    const batchResult = await processCompanyBatch(label, batch, discovery.added || 0);
    return {
      ...discovery,
      ...batchResult
    };
  }

  async function runAdaptiveRounds() {
    const companyPoolEmpty = companies.length === 0;
    if (!canStartRound(baseRoundMs)) {
      return;
    }

    if (companyPoolEmpty) {
      await runDiscoveryRound(
        "base",
        coldStartQueryBudget,
        coldStartMaxNewCompanies,
        coldStartImmediateProcessBatchSize
      );
      if (totalNewJobs >= minNewJobsToContinue) return;
    } else {
      const baseBatch = pickEligibleCompanies(baseExistingBatchSize);
      await processCompanyBatch("base", baseBatch, 0);
      if (totalNewJobs >= minNewJobsToContinue) return;
    }

    if (!canStartRound(extendRoundMs)) {
      return;
    }

    const extendBatch = pickEligibleCompanies(extendExistingBatchSize);
    await processCompanyBatch("extend", extendBatch, 0);
    if (totalNewJobs >= minNewJobsToContinue) return;

    if (!canStartRound(deepRoundMs)) {
      return;
    }

    await runDiscoveryRound(
      "deep",
      deepSearchQueryBudget,
      deepSearchMaxNewCompanies,
      deepSearchImmediateProcessBatchSize
    );
    if (totalNewJobs >= minNewJobsToContinue) return;
  }

  await runAdaptiveRounds();

  if (totalDiscoveredCompanies > 0) {
    console.log(
      `[${nowIso()}] Adaptive search summary: discovered ${totalDiscoveredCompanies} new companies, found ${totalJobsFound} jobs, new ${totalNewJobs} jobs.`
    );
  } else {
    console.log(
      `[${nowIso()}] Adaptive search summary: found ${totalJobsFound} jobs, new ${totalNewJobs} jobs.`
    );
  }

  return results;
}

async function fetchGreenhouseJobs(board, config) {
  const url = `https://boards-api.greenhouse.io/v1/boards/${board}/jobs?content=true`;
  const res = await fetchWithConfigTimeout(url, config);
  if (!res.ok) return [];
  const data = await res.json();
  if (!data || !Array.isArray(data.jobs)) return [];
  return data.jobs.map((job) => ({
    title: job.title || "",
    location: job.location?.name || "",
    url: job.absolute_url || "",
    datePosted: job.updated_at || job.created_at || "",
    summary: job.content ? stripHtmlToText(job.content) : ""
  }));
}

async function fetchLeverJobs(company, config) {
  const url = `https://api.lever.co/v0/postings/${company}?mode=json`;
  const res = await fetchWithConfigTimeout(url, config);
  if (!res.ok) return [];
  const data = await res.json();
  if (!Array.isArray(data)) return [];
  return data.map((job) => ({
    title: job.text || "",
    location: job.categories?.location || "",
    url: job.hostedUrl || "",
    datePosted: job.createdAt ? new Date(job.createdAt).toISOString() : "",
    summary: job.description ? stripHtmlToText(job.description) : ""
  }));
}

async function fetchSmartRecruitersJobs(company, config) {
  const url = `https://api.smartrecruiters.com/v1/companies/${company}/postings?limit=100`;
  const res = await fetchWithConfigTimeout(url, config);
  if (!res.ok) return [];
  const data = await res.json();
  const list = Array.isArray(data?.content) ? data.content : [];
  return list.map((job) => ({
    title: job.name || "",
    location: [job.location?.city, job.location?.region, job.location?.country]
      .filter(Boolean)
      .join(", "),
    url: job.ref || (job.id ? `https://careers.smartrecruiters.com/${company}/${job.id}` : ""),
    datePosted: job.releasedDate || "",
    summary: job.jobAd?.sections
      ? Object.values(job.jobAd.sections)
          .map((s) => (s && s.text ? stripHtmlToText(s.text) : ""))
          .join(" ")
      : ""
  }));
}

function extractAllJsonLdJobPostings(html) {
  const $ = cheerio.load(html);
  const scripts = $('script[type="application/ld+json"]');
  const candidates = [];
  scripts.each((_, el) => {
    const text = $(el).contents().text();
    if (!text) return;
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) candidates.push(...parsed);
      else candidates.push(parsed);
    } catch {
      // ignore invalid JSON
    }
  });
  const postings = [];
  for (const obj of candidates) {
    if (!obj || typeof obj !== "object") continue;
    const type = obj["@type"];
    const isJobPosting = type === "JobPosting" || (Array.isArray(type) && type.includes("JobPosting"));
    if (isJobPosting) postings.push(obj);
  }
  return postings;
}

function isAtsHost(rawUrl) {
  try {
    const host = new URL(rawUrl).hostname || "";
    return /greenhouse\.io|lever\.co|smartrecruiters\.com|workable\.com|ashbyhq\.com|myworkdayjobs\.com|icims\.com|jobvite\.com|dayforcehcm\.com|successfactors\.com|bamboohr\.com|recruitee\.com|personio\.de|personio\.com|teamtailor\.com|workforcenow\.adp\.com|jobylon\.com/i.test(
      host
    );
  } catch {
    return false;
  }
}

function isAggregatorHost(rawUrl) {
  try {
    const host = new URL(rawUrl).hostname || "";
    return /rejobs\.org|jobboardly\.com|zerohero(?:-net|\.net)|indeed\.com|glassdoor\.com|linkedin\.com|monster\.com|ziprecruiter\.com|jooble\.org|talent\.com|jobsora\.com|simplyhired\.com|careerjet\.|adzuna\.|climatetechlist\.com|workingreen\.jobs|climatedraft\.org|theladders\.com|huntr\.co|careercentral\.pitt\.edu|jobs\.toyota\.ventures|builtin[a-z]*\.com|echojobs\.io|biomedjobs\.com|weekday\.works|greenjobsearch\.org|jobsin[A-Za-z]+\.com|nextgenenergyjobs\.com|diversityjobs\.com|motorsportjobs\.com|euroengineerjobs\.com|saurenergy\.com|climatechangecareers\.com|aurawoo\.com/i.test(
      host
    );
  } catch {
    return false;
  }
}

function isLikelyJobText(text) {
  if (!text) return false;
  const t = String(text).replace(/\s+/g, " ").trim();
  if (!t) return false;
  const roleWords =
    /(engineer|scientist|researcher|specialist|manager|director|lead|technician|analyst|developer|architect|intern|co-?op|graduate|postdoc|phd|博士后|工程师|研究员|经理|总监|专家|实习)/i;
  return roleWords.test(t);
}

function isGenericLocationOrCategoryTitle(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  if (isLikelyJobText(t)) return false;
  return /^(remote|global|worldwide|multiple(?: locations)?|all locations|europe|european union|germany|france|spain|italy|japan|australia|canada|united states|usa|india|china|korea|singapore|netherlands|belgium|sweden|norway|denmark|finland|switzerland|austria|poland|hungary|ireland|portugal|uk|united kingdom)$/i.test(
    t
  );
}

function isGenericCareersUrl(rawUrl) {
  try {
    const u = new URL(rawUrl);
    const path = String(u.pathname || "")
      .toLowerCase()
      .replace(/\/+$/, "");
    const queryKeys = new Set(
      Array.from(u.searchParams.keys()).map((key) => String(key || "").toLowerCase())
    );
    const hasSpecificId = ["gh_jid", "jobid", "job_id", "jid", "req", "reqid", "rid"].some(
      (key) => queryKeys.has(key)
    );
    if (/^\/(careers?|jobs?|open-jobs|vacancies|opportunities)(?:\.html?)?$/.test(path))
      return true;
    if (/^\/[a-z]{2}(?:-[a-z]{2})?\/(?:careers?|jobs?)(?:\.html?)?$/.test(path)) return true;
    if (/^\/hcmui\/candidateexperience\/.+\/jobs$/.test(path)) return true;
    if (/\/jobs?\/(location|locations|team|category|department|search|all)\b/.test(path))
      return true;
    if (/\/search$/.test(path) && /job|career/i.test(String(rawUrl || ""))) return true;
    if (
      (queryKeys.has("location") ||
        queryKeys.has("locationid") ||
        queryKeys.has("locationlevel") ||
        queryKeys.has("keyword") ||
        queryKeys.has("keywords")) &&
      !hasSpecificId
    )
      return true;
    if (String(u.searchParams.get("mode") || "").toLowerCase() === "location") return true;
    return false;
  } catch {
    return false;
  }
}

function isLikelyNoiseTitle(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  if (t.length > 180) return true;
  if (/<\/?[a-z][^>]*>/i.test(t)) return true;
  if (/^\$\{[^}]+\}$/.test(t)) return true;
  if (isGenericLocationOrCategoryTitle(t)) return true;
  if (
    /^(skip to (?:content|main content|main navigation)|skip navigation|language|languages|menu|navigation|content|search|locale|country|region|zum hauptinhalt|zum inhalt|zum seiteninhalt|direkt zum inhalt|springe zum inhalt|aller au contenu|aller au contenu principal|aller au contenu principal|ir al contenido|saltar al contenido|vai al contenuto|accedi al contenuto|ga naar inhoud|naar inhoud|gå til indhold|hoppa till innehåll)$/i.test(
      t
    )
  )
    return true;
  if (/^(apply|apply now|view job|open job|job details?|learn more|details?|read more|see job|continue)$/i.test(t))
    return true;
  if (/^(申请|立即申请|查看职位|职位详情|查看详情|更多信息)$/i.test(t)) return true;
  if (
    /^(explore all open jobs|career opportunities|careers?|join us|join our team|co-?op or internship opportunities)$/i.test(
      t
    )
  )
    return true;
  if (/(career|careers|open jobs|opportunities|vacancies|招聘职位)/i.test(t) && !isLikelyJobText(t))
    return true;
  if (
    /\b(home|about|contact|news|events|blog|products?|solutions?|technology|applications?|industries|media|investors?)\b/i.test(
      t
    ) &&
    !isLikelyJobText(t)
  )
    return true;
  return false;
}

function sanitizeJobTitleCandidate(text) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  if (!t) return "";
  if (isLikelyNoiseTitle(t) || isGenericLocationOrCategoryTitle(t)) return "";
  if (!isLikelyJobText(t)) {
    const words = t.split(/\s+/).filter(Boolean);
    if (words.length < 2 || t.length < 8) return "";
  }
  return t;
}

function preferJobTitle(currentTitle, nextTitle) {
  const currentClean = sanitizeJobTitleCandidate(currentTitle);
  const nextClean = sanitizeJobTitleCandidate(nextTitle);
  if (nextClean && !currentClean) return nextClean;
  if (currentClean) return currentClean;
  if (nextClean) return nextClean;
  return String(currentTitle || nextTitle || "").replace(/\s+/g, " ").trim();
}

function normalizeDocumentTitleCandidate(text) {
  const raw = String(text || "").replace(/\s+/g, " ").trim();
  if (!raw) return "";

  const candidates = [];
  const pushCandidate = (value) => {
    const clean = String(value || "")
      .replace(/\s+/g, " ")
      .replace(
        /\s*(?:[\-|:\u2013\u2014]\s*)?(?:job details?|career details?|vacancy details?|opening details?|posting details?|stellendetails?|stellenangebot(?:details)?|职位详情|岗位详情)$/i,
        ""
      )
      .trim();
    if (!clean) return;
    candidates.push(clean);
  };

  pushCandidate(raw);
  raw
    .split(/\s*[|\u2013\u2014]\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .forEach(pushCandidate);

  for (const candidate of candidates) {
    const accepted = sanitizeJobTitleCandidate(candidate);
    if (accepted) return accepted;
  }
  return "";
}

function extractFallbackJobTitleFromDocument($, finalUrl) {
  const metaSelectors = [
    'meta[property="og:title"]',
    'meta[name="twitter:title"]',
    'meta[name="title"]'
  ];
  for (const selector of metaSelectors) {
    const content = normalizeDocumentTitleCandidate($(selector).attr("content") || "");
    if (content) return content;
  }

  const headingTitle = normalizeDocumentTitleCandidate($("h1").first().text());
  if (headingTitle) return headingTitle;

  const titleTag = normalizeDocumentTitleCandidate($("title").first().text());
  if (titleTag) return titleTag;

  try {
    const parts = String(new URL(finalUrl).pathname || "")
      .split("/")
      .filter(Boolean)
      .map((part) => decodeURIComponent(part));
    const slug = parts.find((part) => /[A-Za-z].*-[A-Za-z]/.test(part)) || "";
    const slugTitle = normalizeDocumentTitleCandidate(slug.replace(/[-_]+/g, " "));
    if (slugTitle) return slugTitle;
  } catch {
    // ignore malformed URL
  }

  return "";
}

function hasJobSignal({ title, url, summary }) {
  const cleanTitle = String(title || "").trim();
  const cleanSummary = String(summary || "").trim();
  if (isLikelyNoiseTitle(cleanTitle)) return false;
  if (isLikelyParkingHost(url) || isGenericCareersUrl(url)) return false;
  const urlSignal = isLikelyJobUrl(url);
  const atsSignal = isAtsHost(url);
  const titleSignal = isLikelyJobText(cleanTitle);
  const summarySignal = isLikelyJobText(cleanSummary.slice(0, 500));
  const urlTextSignal = /careers?|jobs?|openings?|vacancies?|requisition|req-?\d+/i.test(
    String(url || "")
  );
  if (urlSignal) return true;
  if (atsSignal && (titleSignal || summarySignal)) return true;
  if (titleSignal && urlTextSignal) return true;
  return false;
}

function isLikelyJobUrl(rawUrl) {
  try {
    const u = new URL(rawUrl);
    const host = u.hostname || "";
    const path = u.pathname || "";
    if (isLikelyParkingHost(rawUrl) || isGenericCareersUrl(rawUrl)) return false;
    if (/^\/(careers?|jobs?)(?:\.html?)?\/?$/i.test(path)) return false;
    if (/\/jobs?\/(location|locations|team|category|department|search|all)\b/i.test(path))
      return false;
    const jobPathRegexes = [
      /^\/jobs?\/[^/]+/i,
      /^\/job\/[^/]+/i,
      /^\/careers?\/jobs?\/[^/]+/i,
      /^\/careers?\/[^/]+\/jobs?\/[^/]+/i,
      /^\/position\/[^/]+/i,
      /^\/positions\/[^/]+/i,
      /^\/vacancies\/[^/]+/i,
      /^\/openings\/[^/]+/i,
      /^\/opportunities\/[^/]+/i,
      /^\/careers?\/[^/]*\/?jobs?\/[^/]+/i,
      /^\/jobposting\/[^/]+/i,
      /^\/job-posting\/[^/]+/i,
      /^\/jobdetail\/[^/]+/i,
      /^\/job-detail\/[^/]+/i,
      /^\/job\/[^/]+\/[^/]+/i,
      /^\/job\/[A-Za-z0-9_.-]+$/i
    ];
    if (jobPathRegexes.some((r) => r.test(path))) return true;
    const queryKeys = new Set(
      Array.from(u.searchParams.keys()).map((key) => String(key || "").toLowerCase())
    );
    if (
      ["gh_jid", "jobid", "job_id", "jid", "req", "reqid", "rid", "lever-source"].some((key) =>
        queryKeys.has(key)
      )
    )
      return true;
    if (isAtsHost(rawUrl) && /\/job|\/jobs|\/position|\/posting|\/vacanc|\/careers?/i.test(path))
      return true;
    if (isAtsHost(rawUrl) && /\/job$/i.test(path)) return true;
    if (
      /instagram\.com|youtube\.com|facebook\.com|x\.com|twitter\.com|theorg\.com|tealhq\.com|simplify\.jobs|monster\.com/i.test(
        host
      )
    )
      return false;
    return false;
  } catch {
    return false;
  }
}

function isSpecificJobDetailUrl(rawUrl) {
  if (!rawUrl) return false;
  if (isLikelyParkingHost(rawUrl) || isGenericCareersUrl(rawUrl) || isAggregatorHost(rawUrl))
    return false;
  try {
    const path = String(new URL(rawUrl).pathname || "").toLowerCase();
    if (/\.(pdf|doc|docx)$/i.test(path)) return false;
  } catch {
    return false;
  }
  return isLikelyJobUrl(rawUrl);
}

async function fetchCareersPageJobs({ url, maxLinks, config }) {
  const res = await fetchWithConfigTimeout(url, config);
  if (!res.ok) return [];
  const html = await res.text();

  const postings = extractAllJsonLdJobPostings(html);
  const results = [];
  for (const posting of postings) {
    const fields = jobPostingToFields(posting) || {};
    const postingUrl = posting.url || url;
    results.push({
      title: fields.title || "",
      location: fields.location || "",
      url: postingUrl,
      datePosted: fields.datePosted || "",
      summary: fields.description || ""
    });
  }

  if (results.length > 0) return results;

  const $ = cheerio.load(html);
  const seen = new Set();
  const links = [];
  $("a[href]").each((_, el) => {
    const href = $(el).attr("href");
    if (!href) return;
    const abs = new URL(href, url).toString();
    if (seen.has(abs)) return;
    const text = $(el).text().replace(/\s+/g, " ").trim();
    if (!hasJobSignal({ title: text, url: abs, summary: "" })) return;
    links.push({ url: abs, title: text });
    seen.add(abs);
  });

  return links.slice(0, maxLinks).map((l) => ({
    title: l.title || "",
    location: "",
    url: l.url,
    datePosted: "",
    summary: ""
  }));
}

function isStale(datePosted, maxAgeDays) {
  if (!datePosted || !maxAgeDays) return false;
  const dt = new Date(datePosted);
  if (Number.isNaN(dt.getTime())) return false;
  const diffMs = Date.now() - dt.getTime();
  const days = diffMs / (1000 * 60 * 60 * 24);
  return days > maxAgeDays;
}

function hasUnavailableSignal(text) {
  const t = String(text || "").replace(/\s+/g, " ").toLowerCase();
  if (!t) return false;
  return /(no longer available|position has been filled|this opportunity has been filled|job is closed|application closed|applications closed|not accepting applications|no longer accepting applications|no longer being accepted|applications for this job are no longer being accepted|this job is no longer accepting applications|we are no longer accepting applications|vacancy closed|vacancy has expired|job has expired|position closed|position expired|requisition canceled|job does not exist|error 404|status 404|status 410|page not found|access denied|just a moment|verify you are human|captcha|request blocked|security check|cloudflare|enable javascript|domain (?:is|may be) for sale|buy this domain|make an offer|inquire about this domain|parkingcrew|sedo|afternic|dan\.com|huge ?domains|buydomains|bodis|undeveloped|岗位已关闭|职位已关闭|岗位已结束|职位已结束|岗位不存在|职位不存在|停止招聘|已招满|已过期|已停止接受申请|不再接受申请)/i.test(
    t
  );
}

function hasJdBodySignal(text) {
  const t = String(text || "");
  if (!t) return false;
  const strongPatterns = [
    /job description/i,
    /responsibilit(?:y|ies)/i,
    /minimum qualifications?/i,
    /preferred qualifications?/i,
    /qualifications?/i,
    /requirements?/i,
    /what you('|’)ll do/i,
    /about (this|the) role/i,
    /essential functions?/i,
    /duties/i,
    /position summary/i,
    /职位描述|岗位职责|任职要求|任职资格|工作内容/
  ];
  let hit = 0;
  for (const pattern of strongPatterns) {
    if (pattern.test(t)) hit += 1;
    if (hit >= 2) return true;
  }
  return false;
}

function isLikelyLandingPageText(text) {
  const t = String(text || "");
  if (!t) return false;
  return /(search jobs|search results|browse jobs|all jobs|all openings|career home|join our talent community|join our team|career offerings|job alerts|keyword search|filter results|choose location|sort by|load more jobs|how we hire|working at|manage preferences|accept all|privacy|cookie|our brands|watch the video|jobs by country|jobs by industry|certificate verification|internships)/i.test(
    t
  );
}

function hasTitleEvidenceInText(title, text) {
  const cleanTitle = String(title || "").toLowerCase();
  const body = String(text || "").toLowerCase();
  if (!cleanTitle || !body) return false;
  const stopWords = new Set([
    "senior",
    "staff",
    "lead",
    "principal",
    "engineer",
    "engineering",
    "specialist",
    "manager",
    "system",
    "systems",
    "control",
    "controls",
    "fuel",
    "cell",
    "cells",
    "electrolyzer",
    "electrolysis"
  ]);
  const tokens = cleanTitle
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .map((w) => w.trim())
    .filter((w) => w.length >= 4 && !stopWords.has(w));
  if (tokens.length === 0) return false;
  let matched = 0;
  for (const token of tokens) {
    if (body.includes(token)) matched += 1;
    if (matched >= 1) return true;
  }
  return false;
}

function isApplyableJobPage(job) {
  if (!job?.jd) return false;
  const status = Number(job.jd.status || 0);
  if (status === 0) return false;
  if (job.jd.ok === false && status !== 429) return false;
  if (status >= 400 && status !== 429) return false;
  const rawText = String(job.jd.rawText || "");
  const shortText = rawText.slice(0, 20000);
  if (hasUnavailableSignal(shortText)) return false;
  const hasStructuredDescription = String(job.jd.text || "").length >= 240;
  const hasBodyByText = hasJdBodySignal(shortText);
  const titleEvidence = hasTitleEvidenceInText(job.title || "", shortText);
  const hasBody = hasStructuredDescription || (hasBodyByText && titleEvidence);
  if (!hasBody) return false;
  if (isLikelyLandingPageText(shortText) && !hasJdBodySignal(shortText)) return false;
  const finalUrl = String(job.jd.finalUrl || job.url || "");
  if (isLikelyParkingHost(finalUrl) || isLikelyParkingHost(job.url || "")) return false;
  if (isAggregatorHost(finalUrl)) return false;
  if (!isSpecificJobDetailUrl(finalUrl)) return false;
  if (
    job.jd.redirected &&
    finalUrl &&
    !hasJobSignal({
      title: "",
      url: finalUrl,
      summary: ""
    })
  ) {
    return false;
  }
  return true;
}

function isOutputablePlatformListingJob(job, config) {
  if (!isLimitedPlatformListingJob(job, config)) return false;
  if (job?.analysis?.recommend !== true) return false;
  if (!hasMeaningfulOutputTitle(job)) return false;
  if (
    job?.analysis?.isJobPosting !== true &&
    !hasJobSignal({
      title: job?.title || "",
      url: job?.url || "",
      summary: job?.summary || ""
    })
  ) {
    return false;
  }
  return Boolean(String(job?.title || "").trim() && String(job?.company || "").trim());
}

function jobAvailabilityText(job) {
  return [
    job?.title || "",
    job?.summary || "",
    job?.availabilityHint || "",
    job?.jd?.applyUrl || "",
    job?.jd?.rawText || "",
    job?.analysis?.jobPostingEvidenceCn || "",
    job?.analysis?.recommendReasonCn || ""
  ].join("\n");
}

function hasExplicitUnavailableJobSignal(job) {
  return hasUnavailableSignal(jobAvailabilityText(job));
}

function hasLikelyActiveJobSignal(job, config) {
  if (!job?.url) return false;
  if (hasExplicitUnavailableJobSignal(job)) return false;
  if (isStale(job?.datePosted, config?.filters?.maxPostAgeDays)) return false;
  if (isGenericLocationOrCategoryTitle(job?.title || "")) return false;

  const targetUrl = canonicalJobUrl(job) || job.url || "";
  const applyUrl = normalizeJobUrl(job?.jd?.applyUrl || "");
  const jobLikeUrl = hasJobSignal({
    title: job?.title || "",
    url: targetUrl,
    summary: job?.summary || ""
  });
  if (!jobLikeUrl) return false;

  if (isLimitedPlatformListingJob(job, config)) {
    return Boolean(String(job?.title || "").trim() && String(job?.company || "").trim());
  }
  if (!isSpecificJobDetailUrl(targetUrl) && !(applyUrl && isSpecificJobDetailUrl(applyUrl))) return false;

  const status = Number(job?.jd?.status || 0);
  const redirectedTarget = String(job?.jd?.finalUrl || targetUrl || "").trim();
  if (job?.jd?.redirected && redirectedTarget) {
    const redirectedToJobLike = hasJobSignal({
      title: job?.title || "",
      url: redirectedTarget,
      summary: job?.summary || ""
    });
    if (!redirectedToJobLike) return false;
  }

  if ([404, 410, 451].includes(status)) return false;
  if (job?.jd?.ok === true && status > 0 && status < 400) return true;
  if (applyUrl && isSpecificJobDetailUrl(applyUrl)) return true;
  if ([0, 401, 403, 429].includes(status)) return true;
  if (status >= 500) return true;
  if (job?.sourceType === "company" || String(job?.sourceType || "").includes("company")) return true;
  return String(job?.summary || "").trim().length >= 80;
}

function isUnavailableJob(job, config) {
  if (!config?.filters?.excludeUnavailableLinks) return false;
  if (
    config?.filters?.excludeAggregatorLinks &&
    isAggregatorHost(job?.url || "") &&
    !isAllowedPlatformListingUrl(job?.url || "", config)
  )
    return true;
  if (isLikelyParkingHost(job?.url || "")) return true;
  if (isGenericLocationOrCategoryTitle(job?.title || "")) return true;
  const status = Number(job?.jd?.status || 0);
  if ([404, 410, 451].includes(status)) return true;
  const finalUrl = String(job?.jd?.finalUrl || "").trim();
  if (finalUrl && (isLikelyParkingHost(finalUrl) || isGenericCareersUrl(finalUrl))) return true;
  if (job?.jd?.redirected && finalUrl) {
    const redirectedToJobLike = hasJobSignal({
      title: "",
      url: finalUrl,
      summary: ""
    });
    if (!redirectedToJobLike) return true;
  }
  const text = jobAvailabilityText(job);
  if (
    job?.jd?.ok &&
    !hasJdBodySignal(job?.jd?.rawText || "") &&
    isLikelyLandingPageText(job?.jd?.rawText || "")
  ) {
    return true;
  }
  return hasUnavailableSignal(text);
}

function chooseOutputJobUrl(job, config) {
  const candidates = [
    job?.analysis?.postVerify?.finalUrl || "",
    job?.jd?.applyUrl || "",
    job?.jd?.finalUrl || "",
    job?.canonicalUrl || "",
    job?.url || ""
  ];
  for (const candidate of candidates) {
    const normalized = normalizeJobUrl(candidate);
    if (!normalized) continue;
    if (isAllowedPlatformListingUrl(normalized, config)) continue;
    if (!isSpecificJobDetailUrl(normalized)) continue;
    return normalized;
  }
  return "";
}

function hasReliableOutputLink(job, config) {
  const outputUrl = chooseOutputJobUrl(job, config);
  if (!outputUrl) return false;
  const postVerifyUrl = normalizeJobUrl(job?.analysis?.postVerify?.finalUrl || "");
  const applyUrl = normalizeJobUrl(job?.jd?.applyUrl || "");
  const finalUrl = normalizeJobUrl(job?.jd?.finalUrl || "");
  const originalUrl = normalizeJobUrl(job?.url || "");
  if (postVerifyUrl && outputUrl === postVerifyUrl && job?.analysis?.postVerify?.isValidJobPage === true)
    return true;
  if (applyUrl && outputUrl === applyUrl) return true;
  if ((outputUrl === finalUrl || outputUrl === originalUrl) && isApplyableJobPage(job)) return true;
  return false;
}

function hasMeaningfulOutputTitle(job) {
  const title = String(job?.title || "").trim();
  if (!title) return false;
  if (isLikelyNoiseTitle(title)) return false;
  if (isGenericLocationOrCategoryTitle(title)) return false;
  if (/^(apply|apply now|view job|open job|job details?|learn more|details?|read more|see job|continue)$/i.test(title))
    return false;
  if (/^(申请|立即申请|查看职位|职位详情|查看详情|更多信息)$/i.test(title)) return false;
  if (!isLikelyJobText(title) && title.length < 8) return false;
  return true;
}

function passesFinalOutputCheck(job, config) {
  if (!job?.url) return false;
  if (!hasMeaningfulOutputTitle(job)) return false;
  if (isUnavailableJob(job, config)) return false;

  if (isLimitedPlatformListingJob(job, config)) {
    const platformUrl = normalizeJobUrl(job?.url || "");
    if (!platformUrl || !isAllowedPlatformListingUrl(platformUrl, config)) return false;
    if (
      !hasJobSignal({
        title: job?.title || "",
        url: platformUrl,
        summary: job?.summary || ""
      })
    )
      return false;
    return /\/jobs\/view\/[^/?#]+/i.test(platformUrl);
  }

  const outputUrl = chooseOutputJobUrl(job, config);
  if (!outputUrl) return false;
  if (isLikelyParkingHost(outputUrl) || isGenericCareersUrl(outputUrl) || isAggregatorHost(outputUrl))
    return false;
  if (!isSpecificJobDetailUrl(outputUrl)) return false;

  const verified = job?.analysis?.postVerify;
  if (verified) {
    if (verified.isValidJobPage !== true) return false;
    const verifiedUrl = normalizeJobUrl(verified.finalUrl || "");
    if (
      verifiedUrl &&
      (isLikelyParkingHost(verifiedUrl) ||
        isGenericCareersUrl(verifiedUrl) ||
        isAggregatorHost(verifiedUrl) ||
        !isSpecificJobDetailUrl(verifiedUrl))
    ) {
      return false;
    }
  }

  if (hasReliableOutputLink(job, config)) return true;
  if (verified?.isValidJobPage === true) return true;
  return false;
}

function shouldRestoreHistoricalRecommendedJob(job, config) {
  if (!job?.url) return false;
  if (job?.analysis?.recommend !== true) return false;
  if (!job?.dateFound) return false;
  if (isLikelyParkingHost(job?.url || "")) return false;
  if (isLikelyParkingHost(job?.jd?.finalUrl || "")) return false;
  if (hasExplicitUnavailableJobSignal(job)) return false;
  return true;
}

function shouldRecheckJobLink(job, config) {
  if (!job || !job.url) return false;
  if (!job.jd || !job.jd.fetchedAt) return true;
  const hours = Number(config?.filters?.outputLinkRecheckHours ?? 72);
  if (!Number.isFinite(hours) || hours <= 0) return true;
  const t = new Date(job.jd.fetchedAt).getTime();
  if (!Number.isFinite(t)) return true;
  const ageHours = (Date.now() - t) / (1000 * 60 * 60);
  return ageHours >= hours;
}

async function refreshJobLinkStatusInPlace(job, config) {
  if (!job || !job.url) return;
  const details = await fetchJobDetails({ url: job.url, config });
  const extracted = details.extracted || {};
  job.jd = {
    fetchedAt: details.fetchedAt,
    ok: details.ok,
    status: details.status,
    finalUrl: details.finalUrl || job.url,
    redirected: Boolean(details.redirected),
    text: extracted.description || "",
    rawText: details.rawText,
    applyUrl: details.applyUrl || ""
  };
  if (extracted.title) job.title = preferJobTitle(job.title, extracted.title);
  if (extracted.company) job.company = extracted.company;
  if (extracted.location) job.location = extracted.location;
  if (extracted.datePosted) job.datePosted = extracted.datePosted;
  if (!job.location && details.locationHint) job.location = details.locationHint;
  if (extracted.description) job.summary = chunk(extracted.description, 400);
}

async function recheckCandidateLinkHealth({ jobs, config }) {
  const unique = new Map();
  for (const job of Array.isArray(jobs) ? jobs : []) {
    if (!job?.url) continue;
    unique.set(job.url, job);
  }
  const targets = Array.from(unique.values()).filter((job) =>
    shouldRecheckJobLink(job, config)
  );
  if (targets.length === 0) return;
  const limit = pLimit(6);
  await Promise.all(
    targets.map((job) =>
      limit(async () => {
        try {
          await refreshJobLinkStatusInPlace(job, config);
        } catch {
          // keep existing snapshot if refresh fails
        }
      })
    )
  );
}

const DEFAULT_CN_HYDROGEN_COMPANY_KEYWORDS = [
  "sungrow",
  "sungrow hydrogen",
  "longi",
  "longi hydrogen",
  "peric",
  "sinohytec",
  "sino-synergy",
  "horizon",
  "horizon fuel cell",
  "refire",
  "envision",
  "cockerill jingli",
  "shanghai electric",
  "yihuatong",
  "亿华通",
  "beijing sinohytec"
];

const EUROPE_TEXT_PATTERNS = [
  /\beurope\b/i,
  /\bgermany\b/i,
  /\bfrance\b/i,
  /\bnetherlands\b/i,
  /\bbelgium\b/i,
  /\bspain\b/i,
  /\bitaly\b/i,
  /\bsweden\b/i,
  /\bnorway\b/i,
  /\bdenmark\b/i,
  /\bfinland\b/i,
  /\baustria\b/i,
  /\bswitzerland\b/i,
  /\bpoland\b/i,
  /\bczech\b/i,
  /\bhungary\b/i,
  /\bireland\b/i,
  /\bportugal\b/i,
  /\bromania\b/i,
  /\bslovakia\b/i,
  /\bslovenia\b/i,
  /\blithuania\b/i,
  /\blatvia\b/i,
  /\bestonia\b/i,
  /\bluxembourg\b/i,
  /\buk\b/i,
  /\bunited kingdom\b/i,
  /\bengland\b/i,
  /\bscotland\b/i,
  /\bwales\b/i,
  /\bmunich\b/i,
  /\bberlin\b/i,
  /\bhamburg\b/i,
  /\baachen\b/i,
  /\bparis\b/i,
  /\blyon\b/i,
  /\bamsterdam\b/i,
  /\brotterdam\b/i,
  /\beindhoven\b/i,
  /\bbrussels\b/i,
  /\bstockholm\b/i,
  /\boslo\b/i,
  /\bcopenhagen\b/i,
  /\bhelsinki\b/i,
  /\bvienna\b/i,
  /\bzurich\b/i,
  /\bwarsaw\b/i,
  /\bprague\b/i,
  /\bbudapest\b/i,
  /\bdublin\b/i,
  /\bmilan\b/i,
  /\bturin\b/i,
  /\bmadrid\b/i,
  /\bbarcelona\b/i
];

function toLowerArray(items) {
  if (!Array.isArray(items)) return [];
  return items.map((x) => String(x || "").trim().toLowerCase()).filter(Boolean);
}

function isChinaHydrogenCompanyJob(job, config) {
  const tags = toLowerArray(job?.companyTags);
  const companyName = String(job?.company || "").toLowerCase();
  const cnTag = tags.includes("region:cn") || tags.includes("region:china");
  const hydrogenTag = tags.some((tag) =>
    /fuel_cell|electrolyzer|hydrogen|pem|mea|membrane|catalyst|stack/.test(tag)
  );
  const keywords = toLowerArray(config?.sources?.cnHydrogenCompanyKeywords);
  const activeKeywords = keywords.length ? keywords : DEFAULT_CN_HYDROGEN_COMPANY_KEYWORDS;
  const keywordHit = activeKeywords.some((keyword) => companyName.includes(keyword));
  return (cnTag && hydrogenTag) || keywordHit;
}

function isEuropeRelatedJob(job) {
  const text = [
    job?.location || "",
    job?.title || "",
    job?.summary || "",
    job?.url || "",
    job?.source || ""
  ]
    .map((x) => String(x))
    .join(" ");
  if (EUROPE_TEXT_PATTERNS.some((pattern) => pattern.test(text))) return true;
  try {
    const host = new URL(String(job?.url || "")).hostname.toLowerCase();
    if (/\.(de|fr|nl|be|es|it|se|no|dk|fi|at|ch|pl|cz|hu|ie|pt|ro|sk|si|lt|lv|ee|lu|eu|co\.uk)$/.test(host))
      return true;
  } catch {
    // ignore URL parse failures
  }
  return false;
}

function isChinaHydrogenEuropeJob(job, config) {
  return isChinaHydrogenCompanyJob(job, config) && isEuropeRelatedJob(job);
}

function prioritizeCompaniesForRun(companies, config) {
  if (!Array.isArray(companies) || companies.length === 0) return [];
  if (!config?.sources?.preferMajorCompanies) return companies;
  const keywords = Array.isArray(config?.sources?.majorCompanyKeywords)
    ? config.sources.majorCompanyKeywords
        .map((x) => String(x || "").trim().toLowerCase())
        .filter(Boolean)
    : [];
  const regionWeightEntries =
    config?.sources?.priorityRegionWeights &&
    typeof config.sources.priorityRegionWeights === "object"
      ? Object.entries(config.sources.priorityRegionWeights)
          .map(([key, value]) => [String(key || "").trim().toLowerCase(), toFiniteNumber(value, 0)])
          .filter(([key, value]) => key && Number.isFinite(value) && value !== 0)
      : [];
  const regionWeights = new Map(regionWeightEntries);

  const scored = companies.map((company, index) => {
    const name = String(company?.name || "").toLowerCase();
    const tags = Array.isArray(company?.tags)
      ? company.tags.map((t) => String(t || "").toLowerCase())
      : [];
    let score = 0;
    for (const keyword of keywords) {
      if (name.includes(keyword)) score += 100;
    }
    if (tags.some((t) => /fuel_cell|electrolyzer|hydrogen|stack|system|controls|materials|testing/.test(t)))
      score += 10;
    if (tags.some((t) => /nev_oem|oem|industrial_gas/.test(t))) score += 15;
    if (regionWeights.size > 0) {
      let regionBoost = 0;
      for (const tag of tags) {
        const weight = toFiniteNumber(regionWeights.get(tag), 0);
        if (weight > regionBoost) regionBoost = weight;
      }
      score += regionBoost;
    }
    if (company?.careersUrl) score += 3;
    if (company?.website) score += 2;
    const customPriority = Number(company?.priority || 0);
    if (Number.isFinite(customPriority)) score += customPriority;
    return { company, index, score };
  });

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.index - b.index;
  });
  return scored.map((x) => x.company);
}

function companyMatchesMajorKeyword(company, config) {
  const keywords = Array.isArray(config?.sources?.majorCompanyKeywords)
    ? config.sources.majorCompanyKeywords
        .map((x) => String(x || "").trim().toLowerCase())
        .filter(Boolean)
    : [];
  const name = String(company?.name || "").trim().toLowerCase();
  if (!name || keywords.length === 0) return false;
  return keywords.some((keyword) => name.includes(keyword));
}

function companyHasRegionTag(company, regionTag) {
  const tags = Array.isArray(company?.tags)
    ? company.tags.map((t) => String(t || "").trim().toLowerCase())
    : [];
  const target = String(regionTag || "").trim().toLowerCase();
  if (!target) return false;
  return tags.includes(target);
}

function buildCompanyRunSelection(companies, maxCompanies, config) {
  const limit = Math.max(0, Math.floor(toFiniteNumber(maxCompanies, 0)));
  if (!Array.isArray(companies) || companies.length === 0 || limit <= 0) {
    return {
      companies: [],
      pinnedCount: 0,
      rotationOffset: 0,
      rotated: false,
      tailSize: 0
    };
  }
  if (companies.length <= limit) {
    return {
      companies: companies.slice(0, limit),
      pinnedCount: Math.min(companies.length, limit),
      rotationOffset: 0,
      rotated: false,
      tailSize: 0
    };
  }

  const rotationEnabled = config?.sources?.rotateCompanyWindow !== false;
  if (!rotationEnabled) {
    return {
      companies: companies.slice(0, limit),
      pinnedCount: Math.min(companies.length, limit),
      rotationOffset: 0,
      rotated: false,
      tailSize: Math.max(0, companies.length - limit)
    };
  }

  const requestedPinned = Math.floor(
    toFiniteNumber(config?.sources?.majorCompanyPinnedCount, Math.min(140, limit))
  );
  const pinnedCount = clampNumber(requestedPinned, 0, limit);
  const pinned = companies.slice(0, pinnedCount);
  const tail = companies.slice(pinnedCount);
  const remainingSlots = limit - pinned.length;
  if (remainingSlots <= 0 || tail.length <= remainingSlots) {
    return {
      companies: companies.slice(0, limit),
      pinnedCount: pinned.length,
      rotationOffset: 0,
      rotated: false,
      tailSize: tail.length
    };
  }

  const intervalDays = Math.max(
    1,
    Math.floor(toFiniteNumber(config?.sources?.companyRotationIntervalDays, 1))
  );
  const utcDay = Math.floor(Date.now() / (1000 * 60 * 60 * 24));
  const rotationIndex = Math.floor(utcDay / intervalDays);
  const rotationOffset = (rotationIndex * remainingSlots) % tail.length;
  const rotatedTail = tail.slice(rotationOffset).concat(tail.slice(0, rotationOffset));
  return {
    companies: pinned.concat(rotatedTail.slice(0, remainingSlots)),
    pinnedCount: pinned.length,
    rotationOffset,
    rotated: true,
    tailSize: tail.length
  };
}

function companyRecordKey(company) {
  const name = normalizeCompanyName(company?.name || "");
  const website = String(company?.website || "").trim();
  const domain = companyDomain(website);
  if (name) return name;
  return domain || String(company?.name || "").trim().toLowerCase();
}

function isCompanyInCooldown(company, nowMs = Date.now()) {
  const cooldownUntil = new Date(String(company?.cooldownUntil || "")).getTime();
  return Number.isFinite(cooldownUntil) && cooldownUntil > nowMs;
}

function getCompanyCooldownUntil(adaptiveSearch, jobsFoundCount, newJobsCount, nowMs = Date.now()) {
  const dayMs = 24 * 60 * 60 * 1000;
  const noJobsDays = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch?.companyCooldownDaysNoJobs, 7))
  );
  const someJobsNoNewDays = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch?.companyCooldownDaysSomeJobsNoNew, 3))
  );
  const withNewDays = Math.max(
    1,
    Math.floor(toFiniteNumber(adaptiveSearch?.companyCooldownDaysWithNew, 2))
  );
  const days =
    newJobsCount > 0
      ? withNewDays
      : jobsFoundCount > 0
        ? someJobsNoNewDays
        : noJobsDays;
  return new Date(nowMs + days * dayMs).toISOString();
}

function companySearchFallbackEnabled(company, config) {
  if (config?.sources?.enableCompanySearchFallback === false) return false;
  if (companyMatchesMajorKeyword(company, config)) return true;
  const fallbackRegions = Array.isArray(config?.sources?.fallbackSearchRegions)
    ? config.sources.fallbackSearchRegions
    : ["region:JP"];
  return fallbackRegions.some((tag) => companyHasRegionTag(company, tag));
}

function buildCompanySearchFallbackQuery(company, config) {
  const name = String(company?.name || "").trim();
  if (!name) return "";
  if (isAdjacentScope(config)) {
    const tags = Array.isArray(company?.tags)
      ? company.tags.map((t) => String(t || "").trim().toLowerCase())
      : [];
    const joinedTags = tags.join(" ");
    const isChina = companyHasRegionTag(company, "region:CN");
    let focus = "MBSE systems engineering verification validation integration engineer";
    if (/digital_twin|phm|condition_monitoring|asset_health/.test(joinedTags)) {
      focus = "digital twin PHM condition monitoring engineer";
    } else if (/reliability|durability|diagnostics|failure|validation|verification|integration/.test(joinedTags)) {
      focus = "reliability validation integration engineer";
    } else if (/technical_interface|owner_engineering/.test(joinedTags)) {
      focus = "technical interface owner engineer";
    }

    let sector = "complex systems";
    if (/automotive|complex_equipment|battery|powertrain/.test(joinedTags)) {
      sector = "automotive complex equipment";
    } else if (/industrial_automation|industrial_equipment|automation|robotics/.test(joinedTags)) {
      sector = "industrial automation equipment";
    } else if (/aerospace|high_end_manufacturing|defense/.test(joinedTags)) {
      sector = "aerospace high end manufacturing";
    } else if (/energy|infrastructure|grid|utility/.test(joinedTags)) {
      sector = "energy infrastructure";
    }

    if (isChina) {
      return `${name} 招聘 系统工程 MBSE 验证 集成 可靠性 数字孪生 工程师`;
    }
    return `${name} careers ${focus} ${sector}`;
  }
  const tags = Array.isArray(company?.tags)
    ? company.tags.map((t) => String(t || "").trim().toLowerCase())
    : [];
  const hasHydrogenSignal = tags.some((t) =>
    /fuel_cell|electrolyzer|hydrogen|stack|system|controls|balance_of_plant/.test(t)
  );
  const hasMaterialsSignal = tags.some((t) => /materials|membrane|mea|catalyst/.test(t));
  const hasTestingSignal = tags.some((t) => /testing|diagnostics|validation|verification/.test(t));
  const hasBatterySignal = tags.some((t) => /battery|energy_storage|ess/.test(t));
  const isJapan = companyHasRegionTag(company, "region:JP");
  const isChina = companyHasRegionTag(company, "region:CN");
  const isSpainPortugal =
    companyHasRegionTag(company, "region:ES") || companyHasRegionTag(company, "region:PT");
  const isMiddleEast =
    companyHasRegionTag(company, "region:ME") ||
    companyHasRegionTag(company, "region:AE") ||
    companyHasRegionTag(company, "region:SA");
  const isGermanyNordics =
    companyHasRegionTag(company, "region:DE") ||
    companyHasRegionTag(company, "region:SE") ||
    companyHasRegionTag(company, "region:NO") ||
    companyHasRegionTag(company, "region:DK");

  let focus = "hydrogen fuel cell electrolyzer engineer";
  if (hasMaterialsSignal) focus = "electrochemical materials membrane catalyst engineer";
  else if (hasTestingSignal) focus = "test validation diagnostics engineer";
  else if (hasBatterySignal && !hasHydrogenSignal) focus = "battery energy storage engineer";

  if (isJapan) {
    if (hasMaterialsSignal) return `${name} 採用 膜 電極 触媒 電気化学 材料 エンジニア`;
    if (hasTestingSignal) return `${name} 採用 試験 検証 診断 エンジニア`;
    if (hasBatterySignal && !hasHydrogenSignal) return `${name} 採用 電池 蓄電池 エンジニア`;
    return `${name} 採用 水素 燃料電池 電解槽 エンジニア`;
  }

  if (isChina) {
    if (hasMaterialsSignal) return `${name} 招聘 电化学 材料 膜电极 催化剂 工程师`;
    if (hasTestingSignal) return `${name} 招聘 测试 验证 诊断 工程师`;
    if (hasBatterySignal && !hasHydrogenSignal) return `${name} 招聘 电池 储能 工程师`;
    return `${name} 招聘 氢能 燃料电池 电解槽 工程师`;
  }

  if (isSpainPortugal) {
    if (hasMaterialsSignal) return `${name} careers electrochemical materials membrane catalyst engineer Spain Portugal`;
    if (hasTestingSignal) return `${name} careers hydrogen test validation diagnostics engineer Spain Portugal`;
    if (hasBatterySignal && !hasHydrogenSignal)
      return `${name} careers battery energy storage engineer Spain Portugal`;
    return `${name} careers hydrogen electrolyzer system engineer Spain Portugal`;
  }

  if (isMiddleEast) {
    if (hasMaterialsSignal) return `${name} careers electrochemical materials engineer Saudi UAE Middle East`;
    if (hasTestingSignal) return `${name} careers hydrogen validation diagnostics engineer Saudi UAE Middle East`;
    if (hasBatterySignal && !hasHydrogenSignal)
      return `${name} careers battery energy storage engineer Saudi UAE Middle East`;
    return `${name} careers hydrogen project systems engineer Saudi UAE Middle East`;
  }

  if (isGermanyNordics) {
    if (hasMaterialsSignal) return `${name} careers electrochemical materials engineer Germany Nordics`;
    if (hasTestingSignal) return `${name} careers hydrogen validation diagnostics engineer Germany Nordics`;
    if (hasBatterySignal && !hasHydrogenSignal)
      return `${name} careers battery energy storage engineer Germany Nordics`;
    return `${name} careers hydrogen systems engineer Germany Nordics`;
  }

  return `${name} careers ${focus}`;
}

function parseDotEnv(raw) {
  const out = new Map();
  const lines = String(raw || "").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx <= 0) continue;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    if (!key) continue;
    if (
      (value.startsWith("\"") && value.endsWith("\"")) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out.set(key, value);
  }
  return out;
}

async function loadDotEnvIfPresent(dirPath) {
  const dotEnvPath = path.join(dirPath, ".env");
  try {
    const raw = await fs.readFile(dotEnvPath, "utf8");
    const parsed = parseDotEnv(raw);
    const loaded = [];
    for (const [k, v] of parsed.entries()) {
      if (!process.env[k] && v) {
        process.env[k] = v;
        loaded.push(k);
      }
    }
    if (loaded.length) {
      console.log(`[${nowIso()}] Loaded env from .env: ${loaded.join(", ")}`);
    }
  } catch (err) {
    if (err && err.code === "ENOENT") return;
    throw err;
  }
}

function parseArgs(argv) {
  const args = {
    configPath: "./config.json",
    dryRun: false,
    reanalyze: false,
    retranslate: false,
    reset: false,
    maxNewJobs: null,
    maxQueries: null,
    query: null,
    offline: false,
    disableWebSearch: false,
    disableCompanyDiscovery: false,
    strictScoring: false,
    lowTokenMode: false,
    companiesOnly: false,
    discoverCompanies: false,
    maxCompanies: null,
    companiesPath: null
  };
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--config") {
      args.configPath = argv[i + 1];
      i += 1;
      continue;
    }
    if (a === "--query") {
      args.query = argv[i + 1];
      i += 1;
      continue;
    }
    if (a === "--max-queries") {
      args.maxQueries = Number(argv[i + 1]);
      i += 1;
      continue;
    }
    if (a === "--max-companies") {
      args.maxCompanies = Number(argv[i + 1]);
      i += 1;
      continue;
    }
    if (a === "--companies-path") {
      args.companiesPath = argv[i + 1];
      i += 1;
      continue;
    }
    if (a === "--dry-run") {
      args.dryRun = true;
      continue;
    }
    if (a === "--offline") {
      args.offline = true;
      continue;
    }
    if (a === "--reset") {
      args.reset = true;
      continue;
    }
    if (a === "--retranslate") {
      args.retranslate = true;
      continue;
    }
    if (a === "--no-web-search") {
      args.disableWebSearch = true;
      continue;
    }
    if (a === "--no-company-discovery") {
      args.disableCompanyDiscovery = true;
      continue;
    }
    if (a === "--strict-scoring") {
      args.strictScoring = true;
      continue;
    }
    if (a === "--low-token") {
      args.lowTokenMode = true;
      continue;
    }
    if (a === "--companies-only") {
      args.companiesOnly = true;
      args.disableWebSearch = true;
      continue;
    }
    if (a === "--discover-companies") {
      args.discoverCompanies = true;
      continue;
    }
    if (a === "--reanalyze") {
      args.reanalyze = true;
      continue;
    }
    if (a === "--max-new") {
      args.maxNewJobs = Number(argv[i + 1]);
      i += 1;
      continue;
    }
    if (a === "--help" || a === "-h") {
      console.log(`Usage:
  node jobflow.mjs [--config ./config.json] [--query \"...\"] [--max-queries 2] [--max-companies 20] [--companies-only]
                 [--discover-companies] [--no-company-discovery] [--no-web-search] [--dry-run] [--offline]
                 [--reanalyze] [--retranslate] [--reset] [--max-new 20] [--strict-scoring] [--low-token]
`);
      process.exit(0);
    }
  }
  return args;
}

async function loadConfig(configPath) {
  const resolved = path.resolve(configPath);
  const raw = await fs.readFile(resolved, "utf8");
  const config = JSON.parse(raw);
  return { config, configPath: resolved };
}

async function ensureConfigExists(configPath) {
  try {
    await fs.access(configPath);
  } catch (err) {
    if (err && err.code === "ENOENT") {
      const examplePath = path.join(path.dirname(configPath), "config.example.json");
      const exampleRaw = await fs.readFile(examplePath, "utf8");
      await fs.writeFile(configPath, exampleRaw, "utf8");
      throw new Error(`Missing config. Created ${configPath} from config.example.json. Please review it and run again.`);
    }
    throw err;
  }
}

function buildWebSearchTool(config) {
  const tool = { type: "web_search" };
  const allowedDomains = Array.isArray(config.search.allowedDomains)
    ? config.search.allowedDomains.filter(Boolean)
    : [];
  if (allowedDomains.length > 0) {
    tool.filters = { allowed_domains: allowedDomains };
  }
  return tool;
}

function blockedDomainsFromConfig(config) {
  const fromSearch = Array.isArray(config?.search?.blockedDomains)
    ? config.search.blockedDomains
    : [];
  const fromFilters = Array.isArray(config?.filters?.extraBlockedDomains)
    ? config.filters.extraBlockedDomains
    : [];
  return Array.from(
    new Set(
      [...fromSearch, ...fromFilters]
        .map((d) => String(d || "").trim().toLowerCase())
        .filter(Boolean)
    )
  );
}

function shouldBlockUrl(url, config) {
  const blocked = blockedDomainsFromConfig(config);
  const allowed = Array.isArray(config.search.allowedDomains)
    ? config.search.allowedDomains.map((d) => String(d).toLowerCase())
    : [];
  const domain = String(domainOf(url) || "").toLowerCase();
  if (!domain) return true;
  if (isLikelyParkingHost(url) || isGenericCareersUrl(url)) return true;
  if (
    allowed.length > 0 &&
    !allowed.some((d) => domain === d || domain.endsWith(`.${d}`))
  )
    return true;
  if (isAllowedPlatformListingUrl(url, config)) return false;
  if (config?.filters?.excludeAggregatorLinks !== false && isAggregatorHost(url)) return true;
  if (blocked.some((d) => domain === d || domain.endsWith(`.${d}`))) return true;
  return false;
}

async function openaiSearchJobs({ client, config, query }) {
  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      jobs: {
        type: "array",
        items: {
          type: "object",
          additionalProperties: false,
          properties: {
            title: { type: "string" },
            company: { type: "string" },
            location: { type: "string" },
            url: { type: "string" },
            summary: { type: "string" },
            datePosted: { type: "string" },
            availabilityHint: { type: "string" }
          },
          required: [
            "title",
            "company",
            "location",
            "url",
            "summary",
            "datePosted",
            "availabilityHint"
          ]
        }
      }
    },
    required: ["jobs"]
  };

  const input = `You are helping a candidate find relevant technical expert jobs.

Candidate target role:
${config.candidate.targetRole}

Location preference:
${config.candidate.locationPreference}

Task:
Use web search. Find real, currently accessible job postings.
Prefer company career pages or ATS pages.
Never invent companies, hostnames, or URLs.
Exclude domain-sale pages, generic careers homepages, search/list/filter pages, country/location landing pages, and mirror/aggregator pages unless they clearly resolve to a real employer or ATS job detail page.
If a role is only discoverable as a platform listing from an allowed professional jobs site such as LinkedIn, you may include it, but only if it is clearly a real job listing rather than a generic search page.
Return up to ${config.search.maxJobsPerQuery} results. If unsure about a posting, do not include it.
For each result, provide a short summary based on the search snippet or visible listing text.
If the search result shows a posting date or relative freshness, normalize it to YYYY-MM-DD when you are confident; otherwise return an empty string.
If the search result suggests the job is active, closed, expired, or easy apply only, put that in availabilityHint briefly; otherwise return an empty string.

Query:
${query}

Output ONLY valid JSON matching the schema.`;

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const response = await client.responses.create({
      model: config.search.model,
      tools: [buildWebSearchTool(config)],
      input,
      text: {
        format: {
          type: "json_schema",
          name: "job_search_results",
          strict: true,
          schema
        }
      }
    });

    try {
      const data = JSON.parse(response.output_text);
      if (!data || !Array.isArray(data.jobs)) return [];
      return data.jobs.filter((j) =>
        hasJobSignal({
          title: j.title || "",
          url: j.url || "",
          summary: ""
        })
      );
    } catch (err) {
      if (attempt === 0) {
        console.log(
          `[${nowIso()}] Web search JSON parse failed, retrying once for query: ${query}`
        );
        continue;
      }
      throw err;
    }
  }

  return [];
}

function stripHtmlToText(html) {
  if (!html) return "";
  const $ = cheerio.load(html);
  return $("body").text().replace(/\\s+/g, " ").trim();
}

function extractLocationFromText(text) {
  if (!text) return "";
  const patterns = [
    /(?:^|\\n|\\r)(?:Location|Job Location|Work Location|Office Location|Location\\(s\\))\\s*[:：]\\s*([^\\n\\r]+)/i,
    /(?:^|\\n|\\r)(?:勤務地|工作地点|地点|工作地址)\\s*[:：]\\s*([^\\n\\r]+)/i
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1]) {
      return match[1].trim().replace(/\\s{2,}/g, " ");
    }
  }
  return "";
}

function extractApplyUrlFromHtml(html, pageUrl) {
  if (!html || !pageUrl) return "";
  const $ = cheerio.load(html);
  const seen = new Set();
  const candidates = [];
  const pushCandidate = (rawHref, label) => {
    const href = String(rawHref || "").trim();
    if (!href) return;
    if (/^(javascript:|mailto:|tel:|#)/i.test(href)) return;
    const absolute = normalizeJobUrl(resolveUrl(href, pageUrl));
    if (!absolute || absolute === normalizeJobUrl(pageUrl) || seen.has(absolute)) return;
    seen.add(absolute);
    let score = 0;
    const text = String(label || "")
      .replace(/\s+/g, " ")
      .trim();
    if (/apply|application|candidate|submit|立即申请|申请职位|投递|应聘/i.test(text)) score += 5;
    if (/view job|job details|职位详情|see details/i.test(text)) score += 2;
    if (isAtsHost(absolute)) score += 4;
    if (isSpecificJobDetailUrl(absolute)) score += 3;
    if (
      /apply|career|job|jobs|lever|greenhouse|workday|icims|jobvite|ashby|smartrecruiters|successfactors|dayforce/i.test(
        absolute
      )
    )
      score += 2;
    if (isAggregatorHost(absolute) || isGenericCareersUrl(absolute) || isLikelyParkingHost(absolute))
      score -= 6;
    candidates.push({ url: absolute, score });
  };

  $("a[href], [data-apply-url], [data-job-url], button[data-href], button[onclick]").each((_, el) => {
    const node = $(el);
    const label =
      node.text() ||
      node.attr("aria-label") ||
      node.attr("title") ||
      node.attr("data-label") ||
      "";
    pushCandidate(node.attr("href"), label);
    pushCandidate(node.attr("data-apply-url"), label);
    pushCandidate(node.attr("data-job-url"), label);
    pushCandidate(node.attr("data-href"), label);
    const onclick = String(node.attr("onclick") || "");
    const match = onclick.match(
      /(?:location(?:\\.href)?|window\\.open)\s*=?\s*['"]([^'"]+)['"]/i
    );
    if (match && match[1]) pushCandidate(match[1], label);
  });

  candidates.sort((a, b) => b.score - a.score);
  const best = candidates.find((candidate) => candidate.score >= 4);
  return best?.url || "";
}

function extractJsonLdJobPosting(html) {
  const $ = cheerio.load(html);
  const scripts = $('script[type="application/ld+json"]');
  const candidates = [];
  scripts.each((_, el) => {
    const text = $(el).contents().text();
    if (!text) return;
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) candidates.push(...parsed);
      else candidates.push(parsed);
    } catch {
      // ignore invalid JSON
    }
  });

  for (const obj of candidates) {
    if (!obj || typeof obj !== "object") continue;
    const type = obj["@type"];
    if (type === "JobPosting") return obj;
    if (Array.isArray(type) && type.includes("JobPosting")) return obj;
  }
  return null;
}

function jobPostingToFields(jobPosting) {
  if (!jobPosting || typeof jobPosting !== "object") return null;
  const company =
    (jobPosting.hiringOrganization && jobPosting.hiringOrganization.name) ||
    (jobPosting.organization && jobPosting.organization.name) ||
    "";
  const title = jobPosting.title || "";
  const descriptionHtml = jobPosting.description || "";
  const description = stripHtmlToText(descriptionHtml);
  const datePosted = jobPosting.datePosted || "";

  let location = "";
  const loc = jobPosting.jobLocation;
  const normalizeLoc = (l) => {
    if (!l) return "";
    if (typeof l === "string") return l;
    const addr = l.address || l.address?.addressLocality;
    if (typeof addr === "string") return addr;
    if (addr && typeof addr === "object") {
      const parts = [
        addr.addressLocality,
        addr.addressRegion,
        addr.addressCountry
      ].filter(Boolean);
      if (parts.length) return parts.join(", ");
    }
    return "";
  };
  if (Array.isArray(loc)) {
    location = loc.map(normalizeLoc).filter(Boolean)[0] || "";
  } else {
    location = normalizeLoc(loc);
  }

  if (!location && jobPosting.applicantLocationRequirements) {
    const req = jobPosting.applicantLocationRequirements;
    const locName = req.name || req.address?.addressCountry || req.address?.addressRegion;
    if (locName) location = String(locName);
  }

  if (!location && jobPosting.jobLocationType) {
    const type = String(jobPosting.jobLocationType).toLowerCase();
    if (type.includes("telecommute") || type.includes("remote")) {
      location = "Remote";
    }
  }

  return { title, company, location, datePosted, description };
}

async function fetchJobDetails({ url, config }) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.fetch.timeoutMs);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: {
        "user-agent": config.fetch.userAgent
      }
    });
    const status = res.status;
    const contentType = res.headers.get("content-type") || "";
    const html = await res.text();
    const jobPosting = extractJsonLdJobPosting(html);
    const fromLd = jobPostingToFields(jobPosting);
    const text = stripHtmlToText(html);
    const $ = cheerio.load(html);
    const extractedTitle =
      sanitizeJobTitleCandidate(fromLd?.title || "") ||
      extractFallbackJobTitleFromDocument($, res.url || url);
    const metaLocation =
      $('meta[name="jobLocation"]').attr("content") ||
      $('meta[property="jobLocation"]').attr("content") ||
      $('meta[name="location"]').attr("content") ||
      "";
    const applyUrl = extractApplyUrlFromHtml(html, res.url || url);
    const locationHint = fromLd?.location
      ? ""
      : metaLocation || extractLocationFromText(text);
    return {
      ok: res.ok,
      status,
      contentType,
      finalUrl: res.url || url,
      redirected: Boolean(res.redirected),
      extracted:
        extractedTitle || fromLd
          ? {
              title: extractedTitle,
              company: fromLd?.company || "",
              location: fromLd?.location || "",
              datePosted: fromLd?.datePosted || "",
              description: fromLd?.description || ""
            }
          : null,
      rawText: chunk(text, 20000),
      applyUrl,
      fetchedAt: nowIso(),
      locationHint
    };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      contentType: "",
      finalUrl: url,
      redirected: false,
      extracted: null,
      rawText: "",
      applyUrl: "",
      fetchedAt: nowIso(),
      error: String(err && err.message ? err.message : err)
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function buildCandidateProfile({ client, config, resumeText }) {
  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      headline: { type: "string" },
      domains: { type: "array", items: { type: "string" } },
      coreSkills: { type: "array", items: { type: "string" } },
      tools: { type: "array", items: { type: "string" } },
      keywords: { type: "array", items: { type: "string" } },
      seniority: { type: "string" }
    },
    required: ["headline", "domains", "coreSkills", "tools", "keywords", "seniority"]
  };

  const input = `Summarize the candidate into a compact JSON profile used for job matching.
Keep it factual and based only on the resume.

Target role:
${config.candidate.targetRole}

Resume:
${resumeText}

Output ONLY JSON matching schema.`;

  const response = await client.responses.create({
    model: config.analysis.model,
    input,
    text: {
      format: {
        type: "json_schema",
        name: "candidate_profile",
        strict: true,
        schema
      }
    }
  });

  return JSON.parse(response.output_text);
}

function toFitLevelCn(score) {
  const value = Number(score) || 0;
  if (value >= 78) return "强匹配";
  if (value >= 62) return "匹配";
  if (value >= 48) return "可能匹配";
  return "不匹配";
}

function enrichAnalysisDerivedFields({ analysis, job, config }) {
  const base = analysis && typeof analysis === "object" ? analysis : {};
  const signals = deriveTrackAndSignals(job, config);
  const fitTrack = String(base.fitTrack || "").trim() || signals.fitTrack;
  const jobCluster = isAdjacentScope(config)
    ? base.jobCluster || signals.jobCluster || TRACK_CLUSTER_LABEL[fitTrack]
    : base.jobCluster || TRACK_CLUSTER_LABEL[fitTrack] || signals.jobCluster;
  const industryTrackCn = isAdjacentScope(config)
    ? base.industryTrackCn || signals.industryTrackCn || TRACK_CN_LABEL[fitTrack]
    : base.industryTrackCn || TRACK_CN_LABEL[fitTrack] || signals.industryTrackCn;
  const primaryEvidenceCn = base.primaryEvidenceCn || signals.primaryEvidenceCn;
  const transferableScore = clampNumber(
    toFiniteNumber(base.transferableScore, signals.transferableScore),
    0,
    100
  );
  const domainScore = clampNumber(toFiniteNumber(base.domainScore, signals.domainScore), 0, 100);
  return {
    ...base,
    scopeProfile: base.scopeProfile || getScopeProfile(config),
    fitTrack,
    jobCluster,
    industryTrackCn,
    primaryEvidenceCn,
    transferableScore,
    domainScore,
    adjacentDirectionCn: base.adjacentDirectionCn || signals.adjacentDirectionCn || "",
    industryClusterCn: base.industryClusterCn || signals.industryClusterCn || ""
  };
}

function finalizeAnalysisResult({ analysis, job, config }) {
  const derived = enrichAnalysisDerivedFields({ analysis, job, config });
  const threshold = clampNumber(
    toFiniteNumber(config?.analysis?.recommendScoreThreshold, 60),
    0,
    100
  );
  const platformThreshold = clampNumber(
    toFiniteNumber(config?.analysis?.platformListingRecommendScoreThreshold, 68),
    0,
    100
  );
  const minTransferable = clampNumber(
    toFiniteNumber(config?.analysis?.minTransferableScore, 55),
    0,
    100
  );
  const transferableEnabled = config?.analysis?.transferableFitEnabled !== false;
  const rawScore = toFiniteNumber(derived.matchScore, 0);
  const score = clampNumber(Math.round(rawScore), 0, 100);
  const limitedPlatformListing = isLimitedPlatformListingJob(job, config);
  const deterministicJobSignal =
    !isGenericLocationOrCategoryTitle(job?.title || "") &&
    !isLikelyParkingHost(canonicalJobUrl(job) || job?.url || "") &&
    hasJobSignal({ title: job?.title || "", url: job?.url || "", summary: job?.summary || "" });
  const modelIsJobPosting =
    typeof derived.isJobPosting === "boolean"
      ? derived.isJobPosting
      : deterministicJobSignal;
  const isJobPosting =
    limitedPlatformListing && deterministicJobSignal ? true : Boolean(modelIsJobPosting && deterministicJobSignal);
  const strongDomain = derived.domainScore >= 55;
  const strongTransferable = transferableEnabled && derived.transferableScore >= minTransferable;
  const effectiveThreshold = limitedPlatformListing ? Math.max(threshold, platformThreshold) : threshold;
  const eligible = isJobPosting && score >= effectiveThreshold && (strongDomain || strongTransferable);
  const recommendedByModel = typeof derived.recommend === "boolean" ? derived.recommend : true;
  return {
    ...derived,
    matchScore: score,
    fitLevelCn: derived.fitLevelCn || toFitLevelCn(score),
    isJobPosting,
    recommend: Boolean(eligible && recommendedByModel)
  };
}

function compareJobsByPreference(a, b, config) {
  const aQuality = inferSourceQuality(a, config);
  const bQuality = inferSourceQuality(b, config);
  if (config?.filters?.preferDirectEmployerSite) {
    const rankDiff = sourceQualityRank(bQuality) - sourceQualityRank(aQuality);
    if (rankDiff !== 0) return rankDiff;
  }
  const aApply = isApplyableJobPage(a) ? 1 : 0;
  const bApply = isApplyableJobPage(b) ? 1 : 0;
  if (bApply !== aApply) return bApply - aApply;
  const aScore = toFiniteNumber(a?.analysis?.matchScore, -1);
  const bScore = toFiniteNumber(b?.analysis?.matchScore, -1);
  if (bScore !== aScore) return bScore - aScore;
  const aLen = String(a?.summary || a?.jd?.text || "").length;
  const bLen = String(b?.summary || b?.jd?.text || "").length;
  if (bLen !== aLen) return bLen - aLen;
  return String(b?.dateFound || "").localeCompare(String(a?.dateFound || ""));
}

function dedupeJobsByCanonical(jobs, config) {
  const grouped = new Map();
  for (const job of Array.isArray(jobs) ? jobs : []) {
    if (!job?.url) continue;
    const key = buildJobDedupeKey(job) || normalizeJobUrl(job.url);
    if (!key) continue;
    const list = grouped.get(key) || [];
    list.push(job);
    grouped.set(key, list);
  }

  const deduped = [];
  for (const list of grouped.values()) {
    if (!list.length) continue;
    list.sort((a, b) => compareJobsByPreference(a, b, config));
    const chosen = list[0];
    if (chosen.analysis) {
      chosen.analysis = enrichAnalysisDerivedFields({ analysis: chosen.analysis, job: chosen, config });
    }
    chosen.canonicalUrl = canonicalJobUrl(chosen) || normalizeJobUrl(chosen.url);
    chosen.sourceQuality = inferSourceQuality(chosen, config);
    chosen.regionTag = inferRegionTag(chosen) || chosen.regionTag || "";
    deduped.push(chosen);
  }
  return deduped;
}

function fallbackScoreAdjacentMbse({ job, config }) {
  const titleText = `${job.title || ""}`;
  const text = `${job.title || ""}\n${job.summary || ""}\n${job.jd?.text || ""}\n${
    job.jd?.rawText || ""
  }`.toLowerCase();
  const companyTagsText = Array.isArray(job.companyTags) ? job.companyTags.join(" ").toLowerCase() : "";
  const rolePattern =
    /\b(mbse|model based systems engineering|systems engineering|system engineer|requirements engineer|requirements management|sysml|traceability|verification|validation|v&v|integration|qualification|reliability|durability|digital twin|phm|condition monitoring|technical interface|owner engineering|failure analysis|diagnostic)\b|系统工程|需求工程|验证|集成|可靠性|数字孪生|技术接口|故障分析/i;
  const focusPattern =
    /\b(model|modeling|simulation|architecture|requirements|traceability|verification|validation|integration|test|qualification|reliability|durability|lifetime|digital twin|phm|condition monitoring|diagnostic|failure analysis|root cause|commissioning)\b|建模|仿真|架构|需求|可追溯|验证|集成|测试|鉴定|可靠性|寿命|数字孪生|状态监测|诊断|故障分析/i;
  const isJobPosting = hasJobSignal({
    title: job.title || "",
    url: job.url || "",
    summary: job.summary || ""
  });

  const groups = [
    {
      key: "mbse_systems",
      weight: 24,
      patterns: [/\b(mbse|model based systems engineering|systems engineering|system engineer|sysml|requirements engineer|requirements management|traceability|system architecture)\b|模型驱动|系统工程|需求工程|需求管理|可追溯|系统架构/i]
    },
    {
      key: "vv_integration",
      weight: 24,
      patterns: [/\b(verification|validation|v&v|integration engineer|integration test|qualification|commissioning|system test|verification engineer|validation engineer)\b|验证|确认|集成|联调|系统测试|鉴定/i]
    },
    {
      key: "reliability_diagnostics",
      weight: 22,
      patterns: [/\b(reliability|durability|lifetime|aging|diagnostic|diagnostics|failure analysis|root cause|fmea|dfmea|fault tree|rams)\b|可靠性|耐久|寿命|老化|诊断|故障分析|根因分析|故障树/i]
    },
    {
      key: "digital_twin_phm",
      weight: 20,
      patterns: [/\b(digital twin|digital-twin|phm|condition monitoring|asset health|prognostics|remaining useful life|rul|predictive maintenance)\b|数字孪生|状态监测|资产健康|寿命预测|预测性维护/i]
    },
    {
      key: "technical_interface",
      weight: 18,
      patterns: [/\b(owner'?s engineer|owner engineering|technical interface|interface engineer|technical project engineer|cross-functional technical lead|technical lead)\b|业主工程|技术接口|跨部门技术牵头|技术负责人/i]
    },
    {
      key: "automotive_complex",
      weight: 14,
      patterns: [/\b(automotive|vehicle|truck|bus|powertrain|battery|bms|drivetrain|cell|module|pack|rail)\b|汽车|整车|卡车|客车|动力总成|电池|电驱|轨交/i]
    },
    {
      key: "industrial_automation",
      weight: 14,
      patterns: [/\b(industrial automation|automation|robotics|machinery|compressor|turbine|pump|factory automation|plc|scada|process control)\b|工业自动化|机器人|机械设备|压缩机|涡轮|泵|过程控制/i]
    },
    {
      key: "aerospace_highend",
      weight: 14,
      patterns: [/\b(aerospace|aviation|aircraft|avionics|satellite|space|propulsion|defense|semiconductor equipment)\b|航空航天|飞机|航电|卫星|航天|推进|国防|半导体设备/i]
    },
    {
      key: "seniority",
      weight: 8,
      patterns: [/senior|staff|principal|lead|expert|高级|资深|专家/i]
    }
  ];

  let score = isJobPosting ? 28 : 8;
  const matched = [];
  for (const group of groups) {
    if (group.patterns.some((pattern) => pattern.test(text))) {
      score += group.weight;
      matched.push(group.key);
    }
  }

  const roleCoreMatched =
    matched.includes("mbse_systems") ||
    matched.includes("vv_integration") ||
    matched.includes("reliability_diagnostics") ||
    matched.includes("digital_twin_phm") ||
    matched.includes("technical_interface");
  const sectorContextMatched =
    matched.includes("automotive_complex") ||
    matched.includes("industrial_automation") ||
    matched.includes("aerospace_highend");
  const roleInText = rolePattern.test(text);
  const focusInText = focusPattern.test(text);
  const companyTagRelevant = /mbse|systems|requirements|traceability|verification|validation|integration|reliability|durability|digital_twin|phm|condition_monitoring|technical_interface|owner_engineering|automotive|industrial_automation|aerospace|high_end_manufacturing|complex_equipment/i.test(
    companyTagsText
  );
  const titleFocusEvidence =
    /\b(mbse|systems engineer|requirements engineer|verification|validation|v&v|integration|qualification|reliability|durability|digital twin|phm|technical interface|owner engineer|test engineer|failure analysis)\b|系统工程|需求工程|验证|集成|可靠性|数字孪生|技术接口|测试工程师|故障分析/i.test(
      titleText
    );
  const technicalTitle = /(engineer|architect|specialist|scientist|analyst|lead|expert|工程师|架构师|分析师|专家)/i.test(
    titleText
  );
  const unrelatedSoftwareRole = /frontend|backend|full[- ]?stack|devops|sre|mobile|app|web developer|software engineer|ai engineer|machine learning|computer vision|deep learning/i.test(
    titleText
  );
  const nonTargetRolePatterns = [
    /sales|account manager|account executive|business development|marketing|hr|human resources|recruiter|legal|finance|采购|procurement|buyer|supply chain|planner|pmo|project coordinator|program coordinator|quality system|quality assurance|compliance|regulatory|operator|maintenance technician|facility|facilities|technician/i
  ];
  const nonTargetRole = nonTargetRolePatterns.some((pattern) => pattern.test(`${titleText}\n${job.summary || ""}`));
  if (nonTargetRole) score -= 55;
  if (unrelatedSoftwareRole && !roleInText && !sectorContextMatched) score -= 65;

  const coreTechnicalRole = Boolean(
    technicalTitle && (titleFocusEvidence || roleCoreMatched || roleInText || focusInText)
  );
  if (!coreTechnicalRole) score -= 26;
  if (!roleCoreMatched && !roleInText && !companyTagRelevant) score -= 42;
  if (!focusInText && !titleFocusEvidence) score -= 16;
  if (!isJobPosting) score -= 20;

  score = Math.max(0, Math.min(95, score));
  const fitLevelCn = toFitLevelCn(score);
  const signals = deriveTrackAndSignals(job, config);
  const domainScore = signals.domainScore;
  const transferableScore = signals.transferableScore;
  const threshold = clampNumber(
    toFiniteNumber(config?.analysis?.recommendScoreThreshold, 60),
    0,
    100
  );
  const minTransferable = clampNumber(
    toFiniteNumber(config?.analysis?.minTransferableScore, 55),
    0,
    100
  );
  const strongDomain = domainScore >= 55 || roleCoreMatched || roleInText || companyTagRelevant;
  const strongTransferable =
    transferableScore >= minTransferable || sectorContextMatched || matched.includes("digital_twin_phm");
  const recommend = Boolean(
    isJobPosting &&
      coreTechnicalRole &&
      !nonTargetRole &&
      !(unrelatedSoftwareRole && !strongTransferable) &&
      (strongDomain || strongTransferable) &&
      (focusInText || titleFocusEvidence || roleCoreMatched) &&
      score >= threshold
  );

  const reasonsCn = [];
  if (matched.includes("mbse_systems")) reasonsCn.push("岗位强调 MBSE/系统工程/需求可追溯职责。");
  if (matched.includes("vv_integration")) reasonsCn.push("岗位包含验证、集成、鉴定或联调职责。");
  if (matched.includes("reliability_diagnostics")) reasonsCn.push("岗位涉及可靠性、寿命、诊断或故障分析。");
  if (matched.includes("digital_twin_phm")) reasonsCn.push("岗位覆盖数字孪生、PHM 或状态监测方向。");
  if (matched.includes("technical_interface")) reasonsCn.push("岗位带有技术接口/业主工程/跨团队技术牵头特征。");
  if (matched.includes("automotive_complex")) reasonsCn.push("岗位位于汽车/电驱/复杂装备语境，可迁移性较强。");
  if (matched.includes("industrial_automation")) reasonsCn.push("岗位位于工业设备与自动化语境，可迁移系统工程经验。");
  if (matched.includes("aerospace_highend")) reasonsCn.push("岗位位于航空航天/高端制造语境，系统工程要求较强。");
  if (reasonsCn.length === 0) reasonsCn.push("岗位与复杂系统工程/验证能力存在一定交集，但需人工复核 JD。");

  const gapsCn = [];
  if (!matched.includes("mbse_systems")) gapsCn.push("JD 未明确强调 MBSE/需求管理/可追溯职责。");
  if (!matched.includes("vv_integration")) gapsCn.push("JD 未突出验证、集成或鉴定职责。");
  if (!matched.includes("reliability_diagnostics")) gapsCn.push("JD 未明确强调可靠性/寿命/故障分析。");
  if (!matched.includes("digital_twin_phm")) gapsCn.push("JD 未体现数字孪生/PHM/状态监测要求。");
  if (!matched.includes("technical_interface")) gapsCn.push("JD 未明显体现技术接口或跨团队技术牵头职责。");
  if (!roleCoreMatched && !companyTagRelevant && !sectorContextMatched)
    gapsCn.push("岗位文本中缺少副线核心角色形状的直接证据。");
  if (nonTargetRole) gapsCn.push("岗位更偏销售/PMO/质量流程/运维，不是目标技术副线。");
  if (!isJobPosting) gapsCn.push("当前链接更像集合页或介绍页，非标准可投递 JD。");

  return {
    matchScore: score,
    fitLevelCn,
    fitTrack: signals.fitTrack,
    jobCluster: signals.jobCluster,
    industryTrackCn: signals.industryTrackCn,
    transferableScore,
    domainScore,
    primaryEvidenceCn: signals.primaryEvidenceCn,
    isJobPosting,
    jobPostingEvidenceCn: isJobPosting
      ? "URL 与标题具备具体岗位信号，像真实 JD 页面。"
      : "URL 或标题更像集合页/介绍页，缺少明确职位信号。",
    recommend,
    recommendReasonCn: recommend
      ? "岗位角色形状与 MBSE/系统验证/技术接口副线较匹配，建议投递。"
      : "岗位与副线角色形状重合度不足，或页面并非标准 JD，暂不推荐。",
    location: job.location || "",
    summaryCn: job.title
      ? `该岗位更偏复杂系统工程/验证副线，需结合官方 JD 确认职责边界。`
      : "岗位信息有限，建议先打开官方 JD 确认职责与任职要求。",
    reasonsCn,
    gapsCn,
    questionsCn: [
      "该岗位是否直接负责需求分解、验证闭环或系统集成？",
      "团队当前使用的系统工程/验证工具链是什么？",
      "岗位在技术接口、建模分析和测试验证之间的职责比例如何？"
    ],
    nextActionCn: recommend
      ? "建议优先投递，并在简历中突出系统建模、验证闭环与跨团队技术接口经验。"
      : "建议先确认是否为真实 JD 及核心技术职责，再决定是否投递。"
  };
}

function fallbackScoreJobFit({ job, config }) {
  if (isAdjacentScope(config)) return fallbackScoreAdjacentMbse({ job, config });
  const titleText = `${job.title || ""}`;
  const text = `${job.title || ""}\n${job.summary || ""}\n${job.jd?.text || ""}\n${
    job.jd?.rawText || ""
  }`.toLowerCase();
  const companyTagsText = Array.isArray(job.companyTags) ? job.companyTags.join(" ").toLowerCase() : "";
  const domainPattern =
    /\b(fuel cell|electrolyzer|electrolysis|hydrogen|electrochemical|membrane|mea|catalyst)\b|燃料电池|电解槽|氢能|电化学|膜电极|催化剂|\bpem\b|lt-?pem|ht-?pem/i;
  const transferablePattern =
    /\b(model[- ]based|mbse|systems engineering|digital twin|digital[- ]twin|phm|prognostics|health management|condition monitoring|state monitoring|lifetime prediction|remaining useful life|rul|test data|validation|verification|parameter identification|parameter estimation|system identification|battery|bms|energy storage|ess|powertrain|reliability|durability|lifetime|aging|ast|accelerated stress test)\b|模型驱动|系统工程|数字孪生|状态监测|寿命预测|参数辨识|参数识别|验证|方法学|电池|储能|电驱|可靠性|耐久|加速应力测试/i;
  const focusPattern =
    /\b(degradation|durability|reliability|lifetime|aging|model|models|modeling|model[- ]based|mbse|systems engineering|simulation|simulations|diagnostic|diagnostics|digital twin|digital[- ]twin|phm|prognostics|health management|condition monitoring|state monitoring|lifetime prediction|remaining useful life|rul|parameter identification|parameter estimation|system identification|validation|verification|methodology|test|tests|testing|test data|data platform|data pipeline|ast|accelerated stress test|stack|bop|control|controls)\b|性能|退化|寿命|可靠性|建模|模型驱动|系统工程|数字孪生|状态监测|寿命预测|参数辨识|参数识别|诊断|测试|验证|方法学|控制|堆/i;
  const isJobPosting = hasJobSignal({
    title: job.title || "",
    url: job.url || "",
    summary: job.summary || ""
  });

  const groups = [
    {
      key: "domain",
      weight: 24,
      patterns: [
        /\bfuel cell\b|燃料电池/i,
        /\belectrolyzer\b|\belectrolysis\b|电解槽|电解/i,
        /\bhydrogen\b|氢能/i,
        /\belectrochemical\b|电化学/i,
        /\bpem\b|lt-?pem|ht-?pem/i
      ]
    },
    {
      key: "degradation",
      weight: 24,
      patterns: [
        /\b(degradation|durability|reliability|lifetime|aging)\b|寿命|衰减|退化|可靠性/i
      ]
    },
    {
      key: "modeling",
      weight: 20,
      patterns: [
        /\b(model|models|modeling|simulation|simulations|cfd|calibration|parameter|parameters|diagnostic|diagnostics|test|tests|testing)\b|建模|仿真|参数辨识|参数识别|诊断|测试/i
      ]
    },
    {
      key: "model_based",
      weight: 16,
      patterns: [/\b(model[- ]based|mbse|systems engineering|model based systems engineering)\b|模型驱动|系统工程|模型化系统工程/i]
    },
    {
      key: "digital_phm",
      weight: 16,
      patterns: [
        /\b(digital twin|digital[- ]twin|phm|prognostics|health management|condition monitoring|state monitoring|lifetime prediction|remaining useful life|rul)\b|数字孪生|状态监测|寿命预测|健康管理/i
      ]
    },
    {
      key: "parameter_id",
      weight: 14,
      patterns: [/\b(parameter identification|parameter estimation|system identification|inverse modeling)\b|参数辨识|参数识别|参数估计|系统辨识/i]
    },
    {
      key: "ast_validation",
      weight: 14,
      patterns: [
        /\b(ast|accelerated stress test|verification|validation|v&v|methodology|test method|test methods|validation plan)\b|加速应力测试|验证方法学|方法学|验证/i
      ]
    },
    {
      key: "materials",
      weight: 12,
      patterns: [/\b(membrane|mea|catalyst|catalysts)\b|膜电极|催化剂|材料/i]
    },
    {
      key: "systems",
      weight: 10,
      patterns: [/\b(system|systems|control|controls|bop|stack)\b|系统|控制|堆/i]
    },
    {
      key: "battery_ess",
      weight: 16,
      patterns: [/\b(battery|bms|energy storage|ess|soh|soc|powertrain|e-?mobility)\b|电池|储能|电驱/i]
    },
    {
      key: "energy_digital",
      weight: 18,
      patterns: [/\b(digital twin|digital[- ]twin|phm|condition monitoring|asset health|predictive maintenance|rul)\b|数字孪生|状态监测|健康管理|寿命预测/i]
    },
    {
      key: "seniority",
      weight: 10,
      patterns: [/senior|staff|principal|lead|expert|高级|资深|专家/i]
    }
  ];

  let score = isJobPosting ? 28 : 8;
  const matched = [];
  for (const group of groups) {
    if (group.patterns.some((pattern) => pattern.test(text))) {
      score += group.weight;
      matched.push(group.key);
    }
  }

  const hasDomainEvidence = matched.includes("domain");
  const hasFocusEvidence =
    matched.includes("degradation") ||
    matched.includes("modeling") ||
    matched.includes("model_based") ||
    matched.includes("digital_phm") ||
    matched.includes("parameter_id") ||
    matched.includes("ast_validation") ||
    matched.includes("battery_ess") ||
    matched.includes("energy_digital") ||
    matched.includes("materials") ||
    matched.includes("systems");
  const domainInText = domainPattern.test(text);
  const transferableInText = transferablePattern.test(text);
  const focusInText = focusPattern.test(text);
  const companyTagRelevant = /electrolyzer|fuel_cell|hydrogen|electrochemical|membrane|mea|catalyst|degradation|durability|reliability|lifetime|model_based|mbse|digital_twin|phm|condition_monitoring|state_monitoring|lifetime_prediction|parameter_identification|verification|validation|ast/i.test(
    companyTagsText
  );
  const titleHasSpecializedTopic = domainPattern.test(titleText) && focusPattern.test(`${titleText}\n${job.summary || ""}`);
  const titleFocusEvidence =
    /\b(degradation|durability|reliability|lifetime|model|modeling|model[- ]based|mbse|systems engineering|simulation|diagnostic|digital twin|digital[- ]twin|phm|condition monitoring|state monitoring|lifetime prediction|parameter identification|ast|accelerated stress test|validation|verification|methodology|test|testing|electrochemical|fuel cell|electrolyzer|hydrogen|stack|bop|control|controls|membrane|mea|catalyst)\b|退化|寿命|可靠性|建模|模型驱动|系统工程|数字孪生|状态监测|寿命预测|参数辨识|参数识别|加速应力测试|验证方法学|方法学|仿真|诊断|测试|验证|电化学|燃料电池|电解槽|氢能|膜电极|催化剂|系统控制/i.test(
      titleText
    );
  const unrelatedSoftwareRole = /software|firmware|autonomous|adas|computer vision|vision|robot|robotics|algorithm|deep learning|ai engineer|frontend|backend|full[- ]?stack|devops|sre|mobile|app|cloud|platform/i.test(
    titleText
  );

  const nonTargetRolePatterns = [
    /sales|account manager|account executive|business development|marketing|hr|human resources|recruiter|legal|finance|财务|法务|销售|市场|it technology lead|operations manager|operations specialist|machine learning|foundation model|data infrastructure|backend|frontend|full[- ]?stack|devops|sre|network development|application security|procurement|buyer|supply chain|planner|product cost|facilit(y|ies)|facility|maintenance|operator|cmdb|pharmacovigilance|\bpv\b/i
  ];
  const nonTargetRole = nonTargetRolePatterns.some((pattern) => pattern.test(`${titleText}\n${job.summary || ""}`));
  if (nonTargetRole) {
    score -= 55;
  }
  if (unrelatedSoftwareRole && !domainInText && !transferableInText) {
    score -= 65;
  }
  const technicalTitle = /(engineer|scientist|researcher|analyst|specialist|architect|expert|工程师|研究员|分析师|专家)/i.test(
    titleText
  );
  const coreTechnicalRole = Boolean(
    technicalTitle &&
      (titleFocusEvidence ||
        titleHasSpecializedTopic ||
        (domainInText && focusInText) ||
        hasFocusEvidence ||
        hasDomainEvidence)
  );

  if (!coreTechnicalRole) score -= 30;
  if (!hasDomainEvidence && !companyTagRelevant && !domainInText && !transferableInText) score -= 45;
  if (!hasFocusEvidence && !titleHasSpecializedTopic && !focusInText) score -= 18;
  if (!isJobPosting) score -= 20;

  score = Math.max(0, Math.min(95, score));
  const fitLevelCn = toFitLevelCn(score);
  const signals = deriveTrackAndSignals(job, config);
  const domainScore = signals.domainScore;
  const transferableScore = signals.transferableScore;
  const threshold = clampNumber(
    toFiniteNumber(config?.analysis?.recommendScoreThreshold, 60),
    0,
    100
  );
  const minTransferable = clampNumber(
    toFiniteNumber(config?.analysis?.minTransferableScore, 55),
    0,
    100
  );
  const strongDomain = domainScore >= 55 || hasDomainEvidence || domainInText || companyTagRelevant;
  const strongTransferable =
    transferableScore >= minTransferable ||
    matched.includes("energy_digital") ||
    matched.includes("model_based") ||
    matched.includes("digital_phm") ||
    matched.includes("parameter_id") ||
    matched.includes("ast_validation") ||
    matched.includes("battery_ess") ||
    transferableInText;
  const recommend = Boolean(
    isJobPosting &&
      coreTechnicalRole &&
      !nonTargetRole &&
      !(unrelatedSoftwareRole && !strongTransferable) &&
      (strongDomain || strongTransferable) &&
      (hasFocusEvidence || titleHasSpecializedTopic || focusInText) &&
      (titleFocusEvidence || titleHasSpecializedTopic) &&
      score >= threshold
  );

  const reasonsCn = [];
  if (matched.includes("domain")) reasonsCn.push("岗位属于氢能/燃料电池/电解槽相关技术域。");
  if (matched.includes("degradation")) reasonsCn.push("岗位涉及退化、寿命或可靠性，与你的核心经验一致。");
  if (matched.includes("modeling")) reasonsCn.push("岗位包含建模/仿真/诊断或测试分析职责。");
  if (matched.includes("model_based")) reasonsCn.push("岗位强调模型驱动工程/MBSE，可承接你的系统建模经验。");
  if (matched.includes("digital_phm")) reasonsCn.push("岗位涉及数字孪生/PHM/状态监测与寿命预测方向。");
  if (matched.includes("parameter_id")) reasonsCn.push("岗位包含参数辨识/系统辨识等数据-模型闭环工作。");
  if (matched.includes("ast_validation")) reasonsCn.push("岗位涉及AST或验证方法学，与你的验证设计经验贴合。");
  if (matched.includes("energy_digital")) reasonsCn.push("岗位强调数字孪生/状态监测等能源系统数字化方向。");
  if (matched.includes("battery_ess")) reasonsCn.push("岗位与电池/储能/电驱可靠性分析能力可迁移匹配。");
  if (matched.includes("materials")) reasonsCn.push("岗位覆盖膜电极/催化剂/材料方向，可与你背景形成补充。");
  if (matched.includes("systems")) reasonsCn.push("岗位包含系统/控制/BOP/stack层面的工程职责。");
  if (companyTagRelevant)
    reasonsCn.push("公司标签与氢能电化学主线相关（如fuel_cell/electrolyzer/materials/testing）。");
  if (coreTechnicalRole) reasonsCn.push("岗位标题指向技术研发/工程角色，而非通用职能岗位。");
  if (reasonsCn.length === 0) reasonsCn.push("岗位信息与目标方向存在部分交集，但需进一步核实JD细节。");

  const gapsCn = [];
  if (!matched.includes("degradation")) gapsCn.push("JD未明确强调退化/寿命预测职责。");
  if (!matched.includes("modeling")) gapsCn.push("JD对建模与数据分析职责描述不充分。");
  if (!matched.includes("model_based")) gapsCn.push("JD未突出模型驱动工程/MBSE职责。");
  if (!matched.includes("digital_phm")) gapsCn.push("JD未体现数字孪生/PHM/状态监测与寿命预测要求。");
  if (!matched.includes("parameter_id")) gapsCn.push("JD未明确参数辨识/系统辨识相关任务。");
  if (!matched.includes("ast_validation")) gapsCn.push("JD未强调AST或验证方法学职责。");
  if (!hasDomainEvidence && !companyTagRelevant && !strongTransferable)
    gapsCn.push("岗位文本中缺少“氢能直匹配”或“可迁移能力匹配”的直接证据。");
  if (nonTargetRole) gapsCn.push("岗位偏销售/业务/通用职能，不是核心技术研发主线。");
  if (!coreTechnicalRole) gapsCn.push("岗位标题未体现技术专家导向的职责范围。");
  if (!isJobPosting) gapsCn.push("当前链接更像集合页/介绍页，非标准可投递JD。");

  return {
    matchScore: score,
    fitLevelCn,
    fitTrack: signals.fitTrack,
    jobCluster: signals.jobCluster,
    industryTrackCn: signals.industryTrackCn,
    transferableScore,
    domainScore,
    primaryEvidenceCn: signals.primaryEvidenceCn,
    isJobPosting,
    jobPostingEvidenceCn: isJobPosting
      ? "URL与标题具备岗位信号（job/careers/职位关键词）。"
      : "URL或标题更像产品/介绍/集合页，缺少明确职位描述。",
    recommend,
    recommendReasonCn: recommend
      ? "岗位方向与候选人主线技术栈较匹配，建议投递。"
      : "岗位与目标主线重合度不足或页面并非标准JD，暂不推荐。",
    location: job.location || "",
    summaryCn: job.title
      ? `该岗位与${job.company || "目标公司"}相关，需结合官方JD进一步确认职责边界。`
      : "岗位信息有限，建议先打开官方JD确认职责与任职要求。",
    reasonsCn,
    gapsCn,
    questionsCn: [
      "该岗位是否直接负责PEM fuel cell / electrolyzer的性能与寿命分析？",
      "建模与测试验证的职责比例分别是多少？",
      "团队当前主要使用哪些仿真与数据分析工具链？"
    ],
    nextActionCn: recommend
      ? "建议优先投递，并在简历中突出退化建模、寿命预测与AST设计经验。"
      : "建议先确认是否为真实JD及核心技术职责，再决定是否投递。"
  };
}

function buildLocalPreFilterDecision({ job, config }) {
  const heuristic = fallbackScoreJobFit({ job, config });
  if (config?.analysis?.preFilterEnabled === false) {
    return { enabled: false, keep: true, threshold: 0, heuristic };
  }
  const threshold = clampNumber(
    toFiniteNumber(config?.analysis?.preFilterScoreThreshold, 40),
    0,
    100
  );
  const score = clampNumber(toFiniteNumber(heuristic?.matchScore, 0), 0, 100);
  const keep = Boolean(heuristic?.recommend === true || score >= threshold);
  return { enabled: true, keep, threshold, heuristic };
}

function fitTrackPromptNote(config) {
  if (isAdjacentScope(config)) {
    return `- fitTrack 取值仍使用兼容枚举：
  - hydrogen_core = MBSE/系统工程/技术接口主方向
  - energy_digitalization = 数字孪生/PHM/状态监测
  - battery_ess_powertrain = 汽车/电驱/电池复杂装备语境
  - test_validation_reliability = V&V/集成验证/可靠性`;
  }
  return "- fitTrack 取值：hydrogen_core / energy_digitalization / battery_ess_powertrain / test_validation_reliability";
}

function buildLiteScoringPrompt({ config, job, jdText, jdLimit, dataAvailabilityNote }) {
  if (isAdjacentScope(config)) {
    return `你是复杂系统工程与验证岗位的招聘筛选器。请做“低token极简评估”，输出JSON字段：matchScore、recommend、isJobPosting、location、fitTrack、transferableScore、primaryEvidenceCn。
不要输出任何理由、解释、列表、额外文字。

候选人目标方向：
${config.candidate.targetRole}

${dataAvailabilityNote}

岗位：
Title: ${job.title}
Company: ${job.company}
Location: ${job.location}
URL: ${job.url}
Search Summary:
${chunk(job.summary || "", 1200)}
JD:
${chunk(jdText, jdLimit)}

规则：
- 核心看“角色形状”，优先识别 MBSE / Systems Engineering / SysML / Requirements / Traceability / Verification / Validation / Integration / Qualification / Reliability / Durability / Diagnostics / Digital Twin / PHM / Technical Interface / Owner Engineering。
- 不要求岗位必须属于氢能、电化学或电池行业；这些只算加分项，不是前提。
- 明显偏销售、BD、HR、采购、PMO、质量体系、纯软件互联网、纯AI算法、纯运维操作员的岗位要降分。
- matchScore 0-100
- recommend 仅当岗位与候选人副线方向相关、且是可申请岗位页或明确的职业平台具体职位页时为 true
- isJobPosting 表示是否是真实岗位JD页面；对于 LinkedIn 这类职业平台职位页，如果明显是具体岗位，也可为 true
- location 为空时返回空字符串
${fitTrackPromptNote(config)}
- transferableScore 0-100，表示可迁移能力匹配强度
- primaryEvidenceCn 用一句中文给出主匹配证据
只输出 JSON。`;
  }

  return `你是招聘筛选器。请做“低token极简评估”，输出JSON字段：matchScore、recommend、isJobPosting、location、fitTrack、transferableScore、primaryEvidenceCn。
不要输出任何理由、解释、列表、额外文字。

候选人目标方向：
${config.candidate.targetRole}

${dataAvailabilityNote}

岗位：
Title: ${job.title}
Company: ${job.company}
Location: ${job.location}
URL: ${job.url}
Search Summary:
${chunk(job.summary || "", 1200)}
JD:
${chunk(jdText, jdLimit)}

规则：
- matchScore 0-100
- recommend 仅当岗位与候选人方向相关、且是可申请岗位页或明确的职业平台具体职位页时为 true
- isJobPosting 表示是否是真实岗位JD页面；对于 LinkedIn 这类职业平台职位页，如果明显是具体岗位，也可为 true
- location 为空时返回空字符串
${fitTrackPromptNote(config)}
- transferableScore 0-100，表示可迁移能力匹配强度
- primaryEvidenceCn 用一句中文给出主匹配证据
只输出 JSON。`;
}

function buildFullScoringPrompt({
  config,
  candidateProfile,
  job,
  jdText,
  jdLimit,
  recommendThreshold,
  dataAvailabilityNote
}) {
  if (isAdjacentScope(config)) {
    return `你是复杂系统工程、MBSE、系统验证与技术接口方向的资深招聘专家。
必须使用 web_search 访问该岗位 URL（以及必要的公司招聘入口），结合网页内容与提供的 JD 文本，做出结构化评估。
注意：必须用中文输出；公司名/产品名/缩写保持英文。
判断标准：
A) 角色形状直匹配：MBSE / Systems Engineering / SysML / Requirements / Traceability / Verification / Validation / Integration / Qualification / Reliability / Durability / Diagnostics / Digital Twin / PHM / Technical Interface / Owner Engineering；
B) 行业语境加分：汽车与复杂装备、工业设备与自动化、航空航天与高端制造、能源与基础设施。
只要 A 明显成立，即使不是氢能、电化学或电池岗位，也可以给“匹配/可能匹配”并推荐或可考虑。
只有在职责明显偏销售、BD、HR、采购、PMO、质量体系、纯软件互联网/AI、纯运维操作员时，才给出“不匹配/不推荐”。

候选人画像（JSON）：
${JSON.stringify(candidateProfile)}

${dataAvailabilityNote}

岗位信息：
Title: ${job.title}
Company: ${job.company}
Location: ${job.location}
URL: ${job.url}
Search Summary:
${chunk(job.summary || "", 1500)}
JD text:
${chunk(jdText, jdLimit)}

请输出符合 schema 的 JSON，字段含义如下：
- matchScore: 0-100
- fitLevelCn: 强匹配/匹配/可能匹配/不匹配
- isJobPosting: 该URL是否为真实可投递岗位JD页面（不是产品页/新闻页/公司介绍页/聚合镜像页）；对于 LinkedIn 这类职业平台上的具体职位页，也可以算岗位页
- jobPostingEvidenceCn: 你判定为“岗位页/非岗位页”的依据（中文，简短）
- recommend: 是否推荐申请
- recommendReasonCn: 推荐/不推荐的简短理由
- location: 岗位地点（尽量从网页中提取；多地/远程请写 Remote/Multiple/Global）
${fitTrackPromptNote(config)}
- transferableScore: 0-100，可迁移能力匹配强度
- primaryEvidenceCn: 主匹配证据（中文一句）
- summaryCn: 该岗位一句话中文总结
- reasonsCn: 匹配点（中文）
- gapsCn: 主要差距（中文）
- questionsCn: 建议对HR/用人经理提问的问题（中文）
- nextActionCn: 下一步建议（中文）
推荐规则：只有当 isJobPosting=true 且 fitLevelCn 为“强匹配/匹配/可能匹配”且 matchScore ≥ ${recommendThreshold} 时，才能 recommend=true；否则 recommend=false。

只输出 JSON。`;
  }

  return `你是氢能/电化学能源系统方向的资深招聘专家。
必须使用 web_search 访问该岗位 URL（以及必要的公司招聘入口），结合网页内容与提供的 JD 文本，做出结构化评估。
注意：必须用中文输出；公司名/产品名/缩写保持英文。
判断标准：采用双通道匹配：
A) 领域直匹配（燃料电池/电解槽/电化学能源/材料/催化/膜电极）；
B) 可迁移能力匹配（Durability与Degradation/Model-Based Engineering/数字孪生与PHM（状态监测+寿命预测）/试验数据体系与参数辨识/AST与验证方法学/系统控制/电池储能电驱）。
若 A 或 B 明显成立且职责重叠，请给出“匹配/可能匹配”并推荐或可考虑。
只有在领域明显不相关或职责完全偏离时，才给出“不匹配/不推荐”。

候选人画像（JSON）：
${JSON.stringify(candidateProfile)}

${dataAvailabilityNote}

岗位信息：
Title: ${job.title}
Company: ${job.company}
Location: ${job.location}
URL: ${job.url}
Search Summary:
${chunk(job.summary || "", 1500)}
JD text:
${chunk(jdText, jdLimit)}

请输出符合 schema 的 JSON，字段含义如下：
- matchScore: 0-100
- fitLevelCn: 强匹配/匹配/可能匹配/不匹配
- isJobPosting: 该URL是否为真实可投递岗位JD页面（不是产品页/新闻页/公司介绍页/聚合镜像页）；对于 LinkedIn 这类职业平台上的具体职位页，也可以算岗位页
- jobPostingEvidenceCn: 你判定为“岗位页/非岗位页”的依据（中文，简短）
- recommend: 是否推荐申请
- recommendReasonCn: 推荐/不推荐的简短理由
- location: 岗位地点（尽量从网页中提取；多地/远程请写 Remote/Multiple/Global）
${fitTrackPromptNote(config)}
- transferableScore: 0-100，可迁移能力匹配强度
- primaryEvidenceCn: 主匹配证据（中文一句）
- summaryCn: 该岗位一句话中文总结
- reasonsCn: 匹配点（中文）
- gapsCn: 主要差距（中文）
- questionsCn: 建议对HR/用人经理提问的问题（中文）
- nextActionCn: 下一步建议（中文）
推荐规则：只有当 isJobPosting=true 且 fitLevelCn 为“强匹配/匹配/可能匹配”且 matchScore ≥ ${recommendThreshold} 时，才能 recommend=true；否则 recommend=false。

只输出 JSON。`;
}

async function scoreJobFit({ client, config, candidateProfile, job }) {
  const jdText = job.jd?.text || job.jd?.rawText || job.summary || job.snippet || "";
  const lowTokenMode = Boolean(config?.analysis?.lowTokenMode);
  const useWebSearch = Boolean(config?.analysis?.scoringUseWebSearch);
  const recommendThreshold = clampNumber(
    toFiniteNumber(config?.analysis?.recommendScoreThreshold, 60),
    0,
    100
  );
  const limitedPlatformListing = isLimitedPlatformListingJob(job, config);
  const platformLabel = platformListingLabelForUrl(job?.url || "", config);
  const dataAvailabilityNote = limitedPlatformListing
    ? `数据来源说明：这是来自${platformLabel || "平台"}的有限信息职位线索，不能登录，也不抓取完整JD。请仅根据标题、公司、地点、搜索摘要做保守评估。`
    : "";

  if (lowTokenMode) {
    const liteSchema = {
      type: "object",
      additionalProperties: false,
      properties: {
        matchScore: { type: "integer", minimum: 0, maximum: 100 },
        recommend: { type: "boolean" },
        isJobPosting: { type: "boolean" },
        location: { type: "string" },
        fitTrack: { type: "string" },
        transferableScore: { type: "integer", minimum: 0, maximum: 100 },
        primaryEvidenceCn: { type: "string" }
      },
      required: [
        "matchScore",
        "recommend",
        "isJobPosting",
        "location",
        "fitTrack",
        "transferableScore",
        "primaryEvidenceCn"
      ]
    };
    const jdLimit = Math.max(600, Number(config?.analysis?.lowTokenJdMaxChars ?? 1800));
    const input = buildLiteScoringPrompt({
      config,
      job,
      jdText,
      jdLimit,
      dataAvailabilityNote
    });

    const request = {
      model: config.analysis.model,
      input,
      text: {
        format: {
          type: "json_schema",
          name: "job_fit_score_lite",
          strict: true,
          schema: liteSchema
        }
      }
    };
    if (useWebSearch) {
      request.tools = [{ type: "web_search" }];
    }

    const response = await client.responses.create(request);
    const parsed = parseStructuredResponseJson(response, "Job fit scoring lite");
    const rawScore = Number(parsed?.matchScore);
    const score = Number.isFinite(rawScore) ? Math.max(0, Math.min(100, Math.round(rawScore))) : 0;
    const isJobPosting =
      typeof parsed?.isJobPosting === "boolean"
        ? parsed.isJobPosting
        : hasJobSignal({ title: job.title || "", url: job.url || "", summary: job.summary || "" });
    const recommend =
      typeof parsed?.recommend === "boolean"
        ? Boolean(parsed.recommend && isJobPosting && score >= recommendThreshold)
        : Boolean(isJobPosting && score >= recommendThreshold);
    const derived = deriveTrackAndSignals(job, config);
    const parsedFitTrack = String(parsed?.fitTrack || "").trim() || derived.fitTrack;
    return {
      matchScore: score,
      fitLevelCn: toFitLevelCn(score),
      fitTrack: parsedFitTrack,
      jobCluster: isAdjacentScope(config)
        ? derived.jobCluster
        : TRACK_CLUSTER_LABEL[parsedFitTrack] || derived.jobCluster,
      industryTrackCn: isAdjacentScope(config)
        ? derived.industryTrackCn
        : TRACK_CN_LABEL[parsedFitTrack] || derived.industryTrackCn,
      transferableScore: clampNumber(
        toFiniteNumber(parsed?.transferableScore, derived.transferableScore),
        0,
        100
      ),
      domainScore: derived.domainScore,
      primaryEvidenceCn: String(parsed?.primaryEvidenceCn || derived.primaryEvidenceCn || ""),
      adjacentDirectionCn: derived.adjacentDirectionCn || "",
      industryClusterCn: derived.industryClusterCn || "",
      isJobPosting,
      jobPostingEvidenceCn: isJobPosting ? "低token模式判定为岗位JD页面。" : "低token模式判定为非岗位JD页面。",
      recommend,
      recommendReasonCn: "",
      location: String(parsed?.location || job.location || ""),
      summaryCn: "",
      reasonsCn: [],
      gapsCn: [],
      questionsCn: [],
      nextActionCn: ""
    };
  }

  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      matchScore: { type: "integer", minimum: 0, maximum: 100 },
      fitLevelCn: { type: "string", enum: ["强匹配", "匹配", "可能匹配", "不匹配"] },
      isJobPosting: { type: "boolean" },
      jobPostingEvidenceCn: { type: "string" },
      recommend: { type: "boolean" },
      recommendReasonCn: { type: "string" },
      location: { type: "string" },
      fitTrack: {
        type: "string",
        enum: [
          "hydrogen_core",
          "energy_digitalization",
          "battery_ess_powertrain",
          "test_validation_reliability"
        ]
      },
      transferableScore: { type: "integer", minimum: 0, maximum: 100 },
      primaryEvidenceCn: { type: "string" },
      summaryCn: { type: "string" },
      reasonsCn: { type: "array", items: { type: "string" } },
      gapsCn: { type: "array", items: { type: "string" } },
      questionsCn: { type: "array", items: { type: "string" } },
      nextActionCn: { type: "string" }
    },
    required: [
      "matchScore",
      "fitLevelCn",
      "isJobPosting",
      "jobPostingEvidenceCn",
      "recommend",
      "recommendReasonCn",
      "location",
      "fitTrack",
      "transferableScore",
      "primaryEvidenceCn",
      "summaryCn",
      "reasonsCn",
      "gapsCn",
      "questionsCn",
      "nextActionCn"
    ]
  };
  const jdLimit = Math.max(1200, Number(config?.analysis?.scoringJdMaxChars ?? 12000));
  const input = buildFullScoringPrompt({
    config,
    candidateProfile,
    job,
    jdText,
    jdLimit,
    recommendThreshold,
    dataAvailabilityNote
  });

  const request = {
    model: config.analysis.model,
    input,
    text: {
      format: {
        type: "json_schema",
        name: "job_fit_score",
        strict: true,
        schema
      }
    }
  };
  if (useWebSearch) {
    request.tools = [{ type: "web_search" }];
  }
  const response = await client.responses.create(request);
  return parseStructuredResponseJson(response, "Job fit scoring");
}

async function postVerifyRecommendedJob({ client, config, job }) {
  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      isValidJobPage: { type: "boolean" },
      recommend: { type: "boolean" },
      location: { type: "string" },
      finalUrl: { type: "string" }
    },
    required: ["isValidJobPage", "recommend", "location", "finalUrl"]
  };
  const jdText = job.jd?.text || job.jd?.rawText || job.summary || "";
  const jdLimit = Math.max(500, Number(config?.analysis?.postVerifyJdMaxChars ?? 1200));
  const recommendRule = isAdjacentScope(config)
    ? "角色形状满足 MBSE / Systems Engineering / Verification / Validation / Integration / Reliability / Digital Twin / PHM / Technical Interface 等副线方向。行业不限，氢能/电池只算加分项。"
    : "岗位与候选人主线满足“领域直匹配或可迁移能力匹配”（氢能电化学/模型驱动工程MBSE/数字孪生PHM状态监测寿命预测/试验数据与参数辨识/AST与验证方法学/电池储能电驱可靠性等）。";
  const input = `你是岗位复核器。请只做二次复核，输出 JSON。

候选人目标：
${config.candidate.targetRole}

岗位：
Title: ${job.title}
Company: ${job.company}
Location: ${job.location}
URL: ${job.url}
JD:
${chunk(jdText, jdLimit)}

判定规则：
1) isValidJobPage=true 仅当该链接是“真实可投递岗位JD页”（不是 careers 首页/职位列表入口/新闻/聚合页/失效页/反爬拦截页）。
2) recommend=true 仅当 isValidJobPage=true 且岗位与候选人目标方向匹配：${recommendRule}
3) finalUrl 返回你确认后的最终岗位URL，优先返回 employer/ATS 的具体岗位详情/投递页；不要返回 careers 首页、搜索列表页、地区筛选页或职业聚合页。无法确认就返回原URL。
4) location 尽量给出岗位地点，未知可空字符串。

只输出 JSON。`;

  const request = {
    model: config.analysis.postVerifyModel,
    input,
    text: {
      format: {
        type: "json_schema",
        name: "job_post_verify",
        strict: true,
        schema
      }
    }
  };
  if (config.analysis.postVerifyUseWebSearch) {
    request.tools = [{ type: "web_search" }];
  }
  const response = await client.responses.create(request);
  const parsed = parseStructuredResponseJson(response, "Post verify recommended job");
  return {
    isValidJobPage: parsed?.isValidJobPage === true,
    recommend: parsed?.recommend === true,
    location: String(parsed?.location || ""),
    finalUrl: String(parsed?.finalUrl || job.url || "")
  };
}

async function translateJobFields({ client, config, job }) {
  if (!config.translation.enable) return null;

  const schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      summaryCn: { type: "string" },
      reasonsCn: { type: "array", items: { type: "string" } },
      gapsCn: { type: "array", items: { type: "string" } },
      questionsCn: { type: "array", items: { type: "string" } },
      nextActionCn: { type: "string" },
      fitLevelCn: { type: "string" }
    },
    required: [
      "summaryCn",
      "reasonsCn",
      "gapsCn",
      "questionsCn",
      "nextActionCn",
      "fitLevelCn"
    ]
  };

  const input = `Translate (or summarize then translate) the following fields into Chinese (Simplified).
Keep company names, product names, acronyms, and technical symbols in original language.
If a field is empty, return an empty string/array.
Use professional, concise Chinese.

Fields:
Summary: ${job.summary || ""}
JD Text (if Summary is empty, use this to write a short Chinese summary): ${chunk(
    job.jd?.text || job.jd?.rawText || "",
    6000
  )}
Reasons: ${(job.analysis?.reasonsCn || []).join(" | ")}
Gaps: ${(job.analysis?.gapsCn || []).join(" | ")}
Questions: ${(job.analysis?.questionsCn || []).join(" | ")}
NextAction: ${job.analysis?.nextActionCn || ""}
FitLevel: ${job.analysis?.fitLevelCn || ""}

Output ONLY JSON matching schema.`;

  const response = await client.responses.create({
    model: config.translation.model,
    input,
    text: {
      format: {
        type: "json_schema",
        name: "job_translation",
        strict: true,
        schema
      }
    }
  });

  const data = JSON.parse(response.output_text);
  return data;
}

function excelColumns(config) {
  const columns = [
    { header: "职位链接", key: "url", width: 12 },
    { header: "职位名称", key: "title", width: 38 },
    { header: "公司名称", key: "company", width: 24 },
    { header: "工作地点", key: "location", width: 20 },
    { header: "入表日期", key: "dateFound", width: 12 },
    ...(isAdjacentScope(config)
      ? [
          { header: "副线方向", key: "adjacentDirectionCn", width: 20 },
          { header: "行业簇", key: "industryClusterCn", width: 18 }
        ]
      : []),
    { header: "岗位概述（中文）", key: "summaryCn", width: isAdjacentScope(config) ? 44 : 54 },
    { header: "匹配说明（中文）", key: "primaryEvidenceCn", width: 34 },
    { header: "匹配程度", key: "fitLevelCn", width: 12 },
    { header: "匹配分", key: "matchScore", width: 10 },
    { header: "清单标签", key: "listTags", width: 14 },
    { header: "感兴趣", key: "interest", width: 12 },
    { header: "投递状态", key: "appliedCn", width: 12 },
    { header: "投递日期", key: "appliedDate", width: 12 },
    { header: "跟进状态", key: "responseStatus", width: 12 },
    { header: "备注", key: "notesCn", width: 26 },
    { header: "推荐", key: "recommend", width: 10, hidden: true },
    { header: "推荐理由（中文）", key: "recommendReasonCn", width: 36, hidden: true },
    { header: "发布日期", key: "datePosted", width: 12, hidden: true },
    { header: "匹配轨道", key: "fitTrack", width: 20, hidden: true },
    { header: "岗位簇", key: "jobCluster", width: 20, hidden: true },
    { header: "可迁移匹配分", key: "transferableScore", width: 14, hidden: true },
    { header: "公司标签", key: "companyTags", width: 28, hidden: true },
    { header: "来源", key: "source", width: 24, hidden: true },
    { header: "来源质量", key: "sourceQuality", width: 16, hidden: true },
    { header: "地区标签", key: "regionTag", width: 12, hidden: true },
    { header: "规范链接", key: "canonicalUrl", width: 60, hidden: true },
    { header: "岗位原文概述", key: "summary", width: 50, hidden: true },
    { header: "是否岗位页", key: "isJobPosting", width: 12, hidden: true },
    { header: "岗位页判断依据", key: "jobPostingEvidenceCn", width: 40, hidden: true },
    { header: "差距说明（中文）", key: "gapsCn", width: 60, hidden: true },
    { header: "提问建议（中文）", key: "questionsCn", width: 60, hidden: true },
    { header: "下一步建议（中文）", key: "nextActionCn", width: 30, hidden: true },
    { header: "状态标记", key: "status", width: 12, hidden: true },
    { header: "不感兴趣", key: "notInterested", width: 12, hidden: true },
    { header: "兼容_Applied", key: "applied", width: 12, hidden: true },
    { header: "兼容_Notes", key: "notes", width: 30, hidden: true },
    { header: "Scope Profile", key: "scopeProfile", width: 18, hidden: true },
    { header: "副线方向_隐藏", key: "adjacentDirectionCnHidden", width: 18, hidden: true },
    { header: "行业簇_隐藏", key: "industryClusterCnHidden", width: 18, hidden: true }
  ];
  return columns;
}

function cellValueToText(cellValue) {
  if (cellValue == null) return "";
  if (typeof cellValue === "string" || typeof cellValue === "number" || typeof cellValue === "boolean")
    return String(cellValue);
  if (cellValue instanceof Date) return toHumanDate(cellValue.toISOString());
  if (typeof cellValue === "object") {
    if (typeof cellValue.hyperlink === "string" && cellValue.hyperlink.trim())
      return cellValue.hyperlink.trim();
    if (typeof cellValue.text === "string" && cellValue.text.trim()) return cellValue.text.trim();
    if (typeof cellValue.result === "string" && cellValue.result.trim()) return cellValue.result.trim();
  }
  return String(cellValue);
}

function getRowValueByAliases(row, headers, names) {
  const aliases = Array.isArray(names) ? names : [names];
  for (const name of aliases) {
    const idx = headers.get(String(name || "").trim());
    if (!idx) continue;
    const val = cellValueToText(row.getCell(idx).value);
    if (val !== "") return val;
  }
  return "";
}

function toAppliedCnDisplayValue(manual = {}) {
  if (manual.appliedCn) return String(manual.appliedCn).trim();
  const legacy = String(manual.applied || "").trim();
  if (!legacy) return "";
  const mapping = {
    "未投": "未投递",
    "已投": "已投递",
    "面试": "面试中",
    "拒": "已拒"
  };
  return mapping[legacy] || legacy;
}

function toResponseStatusDisplayValue(manual = {}) {
  const current = String(manual.responseStatus || "").trim();
  if (current) return current;
  const status = String(manual.status || "").trim();
  if (status === "已失效") return status;
  return "";
}

function toNotesCnDisplayValue(manual = {}) {
  return String(manual.notesCn || manual.notes || "").trim();
}

function buildSheetSummaryCn(job) {
  const summaryCn =
    String(job?.analysis?.summaryCn || job?.translation?.summaryCn || "").trim();
  if (summaryCn) return summaryCn;
  const evidence = String(job?.analysis?.primaryEvidenceCn || "").trim();
  const recommendReason = String(job?.analysis?.recommendReasonCn || "").trim();
  if (evidence && recommendReason) return `${evidence} ${recommendReason}`;
  if (evidence) return evidence;
  if (recommendReason) return recommendReason;
  return String(job?.summary || "").trim();
}

async function readExistingManualFields(xlsxPath) {
  try {
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.readFile(xlsxPath);
    const sheet = workbook.getWorksheet("Jobs");
    if (!sheet) return new Map();
    const headers = new Map();
    sheet.getRow(1).eachCell((cell, colNumber) => {
      headers.set(String(cell.value || "").trim(), colNumber);
    });

    const map = new Map();
    sheet.eachRow((row, rowNumber) => {
      if (rowNumber === 1) return;
      const url = getRowValueByAliases(row, headers, ["职位链接", "URL"]);
      const canonicalUrl = getRowValueByAliases(row, headers, ["规范链接", "Canonical URL"]);
      const manual = {
        interest: getRowValueByAliases(row, headers, ["感兴趣", "Interest"]),
        applied: getRowValueByAliases(row, headers, ["兼容_Applied", "Applied"]),
        appliedDate: getRowValueByAliases(row, headers, ["投递日期", "Applied Date"]),
        status: getRowValueByAliases(row, headers, ["状态标记", "Status"]),
        notes: getRowValueByAliases(row, headers, ["兼容_Notes", "Notes"]),
        appliedCn: getRowValueByAliases(row, headers, ["投递状态", "已投递"]),
        responseStatus: getRowValueByAliases(row, headers, ["跟进状态", "回复状态"]),
        notInterested: getRowValueByAliases(row, headers, ["不感兴趣"]),
        notesCn: getRowValueByAliases(row, headers, ["备注"])
      };
      const aliases = new Set();
      const normalizedUrl = normalizeJobUrl(url);
      const normalizedCanonical = normalizeJobUrl(canonicalUrl);
      if (normalizedUrl) aliases.add(normalizedUrl);
      if (normalizedCanonical) aliases.add(normalizedCanonical);
      const composite = buildJobCompositeKey({
        company: getRowValueByAliases(row, headers, ["公司名称", "Company"]),
        title: getRowValueByAliases(row, headers, ["职位名称", "Title"]),
        location: getRowValueByAliases(row, headers, ["工作地点", "Location"])
      });
      if (composite) aliases.add(composite);
      for (const key of aliases) {
        map.set(key, {
          ...(map.get(key) || {}),
          ...manual
        });
      }
    });
    return map;
  } catch (err) {
    if (err && err.code === "ENOENT") return new Map();
    return new Map();
  }
}

async function readExistingRows(xlsxPath) {
  try {
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.readFile(xlsxPath);
    const sheet = workbook.getWorksheet("Jobs");
    if (!sheet) return new Map();
    const headers = new Map();
    sheet.getRow(1).eachCell((cell, colNumber) => {
      headers.set(String(cell.value || "").trim(), colNumber);
    });

    const rows = new Map();
    sheet.eachRow((row, rowNumber) => {
      if (rowNumber === 1) return;
      const url = normalizeJobUrl(getRowValueByAliases(row, headers, ["职位链接", "URL"]));
      if (!url) return;
      rows.set(url, {
        title: getRowValueByAliases(row, headers, ["职位名称", "Title"]),
        company: getRowValueByAliases(row, headers, ["公司名称", "Company"]),
        companyTags: getRowValueByAliases(row, headers, ["公司标签", "Company Tags"]),
        jobCluster: getRowValueByAliases(row, headers, ["岗位簇"]),
        fitTrack: getRowValueByAliases(row, headers, ["匹配轨道", "行业轨道"]),
        transferableScore: getRowValueByAliases(row, headers, ["可迁移匹配分"]),
        adjacentDirectionCn: getRowValueByAliases(row, headers, ["副线方向", "副线方向_隐藏"]),
        industryClusterCn: getRowValueByAliases(row, headers, ["行业簇", "行业簇_隐藏"]),
        primaryEvidenceCn: getRowValueByAliases(row, headers, ["匹配说明（中文）", "主匹配证据"]),
        sourceQuality: getRowValueByAliases(row, headers, ["来源质量"]),
        regionTag: getRowValueByAliases(row, headers, ["地区标签"]),
        location: getRowValueByAliases(row, headers, ["工作地点", "Location"]),
        datePosted: getRowValueByAliases(row, headers, ["发布日期", "Date Posted"]),
        dateFound: getRowValueByAliases(row, headers, ["入表日期", "发现日期", "Found At"]),
        source: getRowValueByAliases(row, headers, ["来源", "Source"]),
        listTags: getRowValueByAliases(row, headers, ["清单标签", "List Tags"]),
        canonicalUrl: getRowValueByAliases(row, headers, ["规范链接", "Canonical URL"]),
        url,
        summary: getRowValueByAliases(row, headers, ["岗位原文概述", "Summary"]),
        summaryCn: getRowValueByAliases(row, headers, ["岗位概述（中文）", "Summary CN"]),
        matchScore: getRowValueByAliases(row, headers, ["匹配分", "Match Score"]),
        fitLevelCn: getRowValueByAliases(row, headers, ["匹配程度", "Fit Level CN"]),
        isJobPosting: getRowValueByAliases(row, headers, ["是否岗位页", "Is Job Posting"]),
        jobPostingEvidenceCn: getRowValueByAliases(row, headers, ["岗位页判断依据", "Job Evidence CN"]),
        recommend: getRowValueByAliases(row, headers, ["推荐", "Recommend"]),
        recommendReasonCn: getRowValueByAliases(row, headers, ["推荐理由（中文）", "Recommend Reason CN"]),
        reasonsCn: getRowValueByAliases(row, headers, ["匹配说明（中文）", "Reasons CN"]),
        gapsCn: getRowValueByAliases(row, headers, ["差距说明（中文）", "Gaps CN"]),
        questionsCn: getRowValueByAliases(row, headers, ["提问建议（中文）", "Questions CN"]),
        nextActionCn: getRowValueByAliases(row, headers, ["下一步建议（中文）", "Next Action CN"]),
        interest: getRowValueByAliases(row, headers, ["感兴趣", "Interest"]),
        applied: getRowValueByAliases(row, headers, ["兼容_Applied", "Applied"]),
        appliedDate: getRowValueByAliases(row, headers, ["投递日期", "Applied Date"]),
        status: getRowValueByAliases(row, headers, ["状态标记", "Status"]),
        notes: getRowValueByAliases(row, headers, ["兼容_Notes", "Notes"]),
        appliedCn: getRowValueByAliases(row, headers, ["投递状态", "已投递"]),
        responseStatus: getRowValueByAliases(row, headers, ["跟进状态", "回复状态"]),
        notInterested: getRowValueByAliases(row, headers, ["不感兴趣"]),
        notesCn: getRowValueByAliases(row, headers, ["备注"]),
        scopeProfile: getRowValueByAliases(row, headers, ["Scope Profile"])
      });
    });
    return rows;
  } catch (err) {
    if (err && err.code === "ENOENT") return new Map();
    return new Map();
  }
}

function parseRecommendValue(value) {
  const v = String(value || "").trim();
  if (!v) return null;
  if (v === "是" || v.toLowerCase() === "true" || v.toLowerCase() === "yes") return true;
  if (v === "否" || v.toLowerCase() === "false" || v.toLowerCase() === "no") return false;
  return null;
}

function parseBooleanValue(value) {
  const v = String(value || "").trim();
  if (!v) return null;
  if (v === "是" || v.toLowerCase() === "true" || v.toLowerCase() === "yes") return true;
  if (v === "否" || v.toLowerCase() === "false" || v.toLowerCase() === "no") return false;
  return null;
}

function rowToJob(row, config) {
  if (!row || !row.url) return null;
  const score = Number(row.matchScore);
  const transferableScore = Number(row.transferableScore);
  const fitTrack = String(row.fitTrack || "").trim();
  const rowScope = String(row.scopeProfile || "").trim().toLowerCase();
  const adjacentRow =
    rowScope === SCOPE_PROFILE.ADJACENT_MBSE || isAdjacentScope(config);
  return {
    url: normalizeJobUrl(row.url),
    canonicalUrl: normalizeJobUrl(row.canonicalUrl) || normalizeJobUrl(row.url),
    title: row.title || "",
    company: row.company || "",
    companyTags: row.companyTags
      ? String(row.companyTags)
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
      : [],
    location: row.location || "",
    datePosted: row.datePosted || "",
    dateFound: row.dateFound || "",
    source: row.source || "",
    sourceQuality: row.sourceQuality || "",
    regionTag: row.regionTag || "",
    sourceType: row.sourceType || "",
    listTags: row.listTags
      ? String(row.listTags)
          .split("|")
          .map((s) => s.trim())
          .filter(Boolean)
      : [],
    summary: row.summary || "",
    analysis: {
      summaryCn: row.summaryCn || "",
      matchScore: Number.isFinite(score) ? score : undefined,
      fitLevelCn: row.fitLevelCn || "",
      fitTrack: fitTrack || "hydrogen_core",
      jobCluster: row.jobCluster || TRACK_CLUSTER_LABEL[fitTrack] || TRACK_CLUSTER_LABEL.hydrogen_core,
      industryTrackCn: adjacentRow
        ? "副线：MBSE/系统验证/技术接口"
        : TRACK_CN_LABEL[fitTrack] || TRACK_CN_LABEL.hydrogen_core,
      transferableScore: Number.isFinite(transferableScore) ? transferableScore : undefined,
      primaryEvidenceCn: row.primaryEvidenceCn || "",
      adjacentDirectionCn: row.adjacentDirectionCn || "",
      industryClusterCn: row.industryClusterCn || "",
      scopeProfile: row.scopeProfile || getScopeProfile(config),
      isJobPosting: parseBooleanValue(row.isJobPosting),
      jobPostingEvidenceCn: row.jobPostingEvidenceCn || "",
      recommend: parseRecommendValue(row.recommend),
      recommendReasonCn: row.recommendReasonCn || "",
      reasonsCn: row.reasonsCn
        ? String(row.reasonsCn)
            .split("|")
            .map((s) => s.trim())
            .filter(Boolean)
        : [],
      gapsCn: row.gapsCn
        ? String(row.gapsCn)
            .split("|")
            .map((s) => s.trim())
            .filter(Boolean)
        : [],
      questionsCn: row.questionsCn
        ? String(row.questionsCn)
            .split("|")
            .map((s) => s.trim())
            .filter(Boolean)
        : [],
      nextActionCn: row.nextActionCn || ""
    }
  };
}

function hasManualTracking(row) {
  if (!row) return false;
  const interest = String(row.interest || "").trim();
  const applied = String(row.applied || "").trim();
  const appliedDate = String(row.appliedDate || "").trim();
  const status = String(row.status || "").trim();
  const notes = String(row.notes || "").trim();
  const appliedCn = String(row.appliedCn || "").trim();
  const responseStatus = String(row.responseStatus || "").trim();
  const notInterested = String(row.notInterested || "").trim();
  const notesCn = String(row.notesCn || "").trim();
  const validInterest = ["感兴趣", "一般", "不感兴趣"].includes(interest);
  return (
    validInterest ||
    applied.length > 0 ||
    appliedDate.length > 0 ||
    status.length > 0 ||
    notes.length > 0 ||
    appliedCn.length > 0 ||
    responseStatus.length > 0 ||
    notInterested.length > 0 ||
    notesCn.length > 0
  );
}

function mergeManualFields(...maps) {
  const merged = new Map();
  for (const source of maps) {
    if (!(source instanceof Map)) continue;
    for (const [url, row] of source.entries()) {
      if (!url) continue;
      const prev = merged.get(url) || {};
      merged.set(url, {
        interest: row?.interest || prev.interest || "",
        applied: row?.applied || prev.applied || "",
        appliedDate: row?.appliedDate || prev.appliedDate || "",
        status: row?.status || prev.status || "",
        notes: row?.notes || prev.notes || "",
        appliedCn: row?.appliedCn || prev.appliedCn || "",
        responseStatus: row?.responseStatus || prev.responseStatus || "",
        notInterested: row?.notInterested || prev.notInterested || "",
        notesCn: row?.notesCn || prev.notesCn || ""
      });
    }
  }
  return merged;
}

function inferTrackFromQuery(query, config) {
  const q = String(query || "").toLowerCase();
  if (isAdjacentScope(config)) {
    if (/\b(automotive|vehicle|powertrain|battery|bms|drivetrain|ev)\b|汽车|动力总成|电池|电驱/i.test(q))
      return "battery_ess_powertrain";
    if (/\b(digital twin|phm|condition monitoring|asset health|predictive maintenance|rul)\b|数字孪生|状态监测|健康管理|寿命预测/i.test(q))
      return "energy_digitalization";
    if (/\b(verification|validation|v&v|integration|qualification|reliability|durability|test engineer|failure analysis)\b|验证|集成|鉴定|可靠性|耐久|测试|故障分析/i.test(q))
      return "test_validation_reliability";
    if (/\b(mbse|systems engineering|system engineer|sysml|requirements|traceability|technical interface|owner engineer)\b|系统工程|需求|可追溯|技术接口|业主工程/i.test(q))
      return "hydrogen_core";
    return "hydrogen_core";
  }
  if (TRACK_PATTERNS.battery_ess_powertrain.test(q)) return "battery_ess_powertrain";
  if (TRACK_PATTERNS.energy_digitalization.test(q)) return "energy_digitalization";
  if (TRACK_PATTERNS.test_validation_reliability.test(q)) return "test_validation_reliability";
  if (TRACK_PATTERNS.hydrogen_core.test(q)) return "hydrogen_core";
  return "hydrogen_core";
}

function normalizeTrackMix(mix) {
  const defaults = trackConfigDefaults();
  const candidate = { ...defaults, ...(mix && typeof mix === "object" ? mix : {}) };
  const normalized = {};
  let total = 0;
  for (const key of TRACK_KEYS) {
    const value = Math.max(0, toFiniteNumber(candidate[key], defaults[key]));
    normalized[key] = value;
    total += value;
  }
  if (total <= 0) return defaults;
  for (const key of TRACK_KEYS) normalized[key] = normalized[key] / total;
  return normalized;
}

function computeTrackFeedbackMultipliers(existingRecommendedRows) {
  const stats = new Map();
  for (const key of TRACK_KEYS) {
    stats.set(key, { positive: 0, negative: 0 });
  }
  if (!(existingRecommendedRows instanceof Map)) return stats;
  for (const row of existingRecommendedRows.values()) {
    const track =
      String(row?.fitTrack || "")
        .trim()
        .toLowerCase() || "hydrogen_core";
    const key = TRACK_KEYS.includes(track) ? track : "hydrogen_core";
    const item = stats.get(key) || { positive: 0, negative: 0 };
    const applied = String(row?.applied || row?.appliedCn || "").trim();
    const interest = String(row?.interest || "").trim();
    const notInterested = String(row?.notInterested || "").trim();
    const status = String(row?.status || row?.responseStatus || "").trim();
    const isPositive =
      ["面试", "面试中", "Offer", "已投", "已投递"].includes(applied) ||
      interest === "感兴趣" ||
      ["面试中", "已回复", "积极", "Offer"].includes(status);
    const isNegative =
      interest === "不感兴趣" ||
      notInterested === "是" ||
      ["拒", "已拒", "已失效", "无回复"].includes(applied) ||
      ["已拒", "已失效", "无回复", "拒绝"].includes(status);
    if (isPositive) item.positive += 1;
    if (isNegative) item.negative += 1;
    stats.set(key, item);
  }
  return stats;
}

function applyFeedbackToTrackMix(baseMix, existingRecommendedRows) {
  const mix = normalizeTrackMix(baseMix);
  const feedback = computeTrackFeedbackMultipliers(existingRecommendedRows);
  const adjusted = {};
  let total = 0;
  for (const key of TRACK_KEYS) {
    const stat = feedback.get(key) || { positive: 0, negative: 0 };
    const confidence = stat.positive + stat.negative;
    const rawDelta =
      confidence > 0 ? (stat.positive - stat.negative) / Math.max(1, confidence) : 0;
    const multiplier = clampNumber(1 + rawDelta * 0.3, 0.75, 1.35);
    adjusted[key] = mix[key] * multiplier;
    total += adjusted[key];
  }
  if (total <= 0) return mix;
  for (const key of TRACK_KEYS) adjusted[key] = adjusted[key] / total;
  return adjusted;
}

function selectQueriesByTrackMix(rawQueries, { trackMix, limit, config }) {
  const list = Array.isArray(rawQueries) ? rawQueries.filter(Boolean) : [];
  if (list.length === 0) return [];
  const target = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : list.length;
  if (target <= 0) return [];

  const grouped = new Map();
  for (const key of TRACK_KEYS) grouped.set(key, []);
  for (const query of list) {
    const track = inferTrackFromQuery(query, config);
    grouped.get(track).push(query);
  }

  const selected = [];
  const weights = normalizeTrackMix(trackMix);
  const allocation = {};
  let allocated = 0;
  for (const key of TRACK_KEYS) {
    const count = Math.min(grouped.get(key).length, Math.floor(target * weights[key]));
    allocation[key] = count;
    allocated += count;
  }

  let remaining = target - allocated;
  while (remaining > 0) {
    let progressed = false;
    for (const key of TRACK_KEYS) {
      if (remaining <= 0) break;
      if (allocation[key] < grouped.get(key).length) {
        allocation[key] += 1;
        remaining -= 1;
        progressed = true;
      }
    }
    if (!progressed) break;
  }

  for (const key of TRACK_KEYS) {
    selected.push(...grouped.get(key).slice(0, allocation[key]));
  }
  if (selected.length < target) {
    const existing = new Set(selected);
    for (const query of list) {
      if (selected.length >= target) break;
      if (existing.has(query)) continue;
      selected.push(query);
      existing.add(query);
    }
  }
  return selected.slice(0, target);
}

function applyDataValidation(sheet, columnKey, allowedValues) {
  const colIndex = sheet.columns.findIndex((c) => c.key === columnKey) + 1;
  if (colIndex <= 0) return;
  const formula = `"${allowedValues.join(",")}"`;
  for (let row = 2; row <= sheet.rowCount; row += 1) {
    sheet.getCell(row, colIndex).dataValidation = {
      type: "list",
      allowBlank: true,
      formulae: [formula]
    };
  }
}

async function writeExcel({ xlsxPath, jobs, manualByUrl, config }) {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Jobs", { views: [{ state: "frozen", ySplit: 1 }] });
  sheet.columns = excelColumns(config);
  sheet.autoFilter = {
    from: { row: 1, column: 1 },
    to: { row: 1, column: sheet.columns.length }
  };

  for (const job of jobs) {
    const manualKey =
      normalizeJobUrl(job.url) ||
      buildJobDedupeKey(job) ||
      buildJobCompositeKey(job);
    const manualCanonicalKey = canonicalJobUrl(job) || "";
    const manualCompositeKey = buildJobCompositeKey(job);
    const manual =
      manualByUrl.get(manualKey) ||
      manualByUrl.get(manualCanonicalKey) ||
      manualByUrl.get(manualCompositeKey) ||
      {};
    const enrichedAnalysis = enrichAnalysisDerivedFields({
      analysis: job.analysis || {},
      job,
      config
    });
    const sourceQuality = job.sourceQuality || inferSourceQuality(job, config);
    const regionTag = job.regionTag || inferRegionTag(job);
    const canonicalUrl = canonicalJobUrl(job) || normalizeJobUrl(job.url);
    const finalJobUrl = chooseOutputJobUrl(job, config) || canonicalUrl || normalizeJobUrl(job.url);
    const trackerDate = toHumanDate(job.dateFound);
    sheet.addRow({
      title: job.title || "",
      company: job.company || "",
      companyTags: Array.isArray(job.companyTags) ? job.companyTags.join(", ") : "",
      jobCluster: enrichedAnalysis.jobCluster || "",
      fitTrack: enrichedAnalysis.fitTrack || "",
      transferableScore: enrichedAnalysis.transferableScore ?? "",
      primaryEvidenceCn: enrichedAnalysis.primaryEvidenceCn || "",
      sourceQuality,
      regionTag,
      location: job.location || "",
      datePosted: toHumanDate(job.datePosted),
      dateFound: trackerDate,
      adjacentDirectionCn: enrichedAnalysis.adjacentDirectionCn || "",
      industryClusterCn: enrichedAnalysis.industryClusterCn || "",
      source: job.source || "",
      listTags: Array.isArray(job.listTags) ? job.listTags.join(" | ") : job.listTags || "",
      canonicalUrl: canonicalUrl ? { text: canonicalUrl, hyperlink: canonicalUrl } : "",
      url: finalJobUrl ? { text: "Open Job", hyperlink: finalJobUrl } : "",
      summary: job.summary || "",
      summaryCn: buildSheetSummaryCn(job),
      matchScore: job.analysis?.matchScore ?? "",
      fitLevelCn: job.analysis?.fitLevelCn || job.translation?.fitLevelCn || "",
      isJobPosting:
        job.analysis?.isJobPosting === true
          ? "是"
          : job.analysis?.isJobPosting === false
            ? "否"
            : "",
      jobPostingEvidenceCn: job.analysis?.jobPostingEvidenceCn || "",
      recommend: job.analysis?.recommend === true ? "是" : job.analysis?.recommend === false ? "否" : "",
      recommendReasonCn: job.analysis?.recommendReasonCn || "",
      reasonsCn: (job.analysis?.reasonsCn || job.translation?.reasonsCn || []).join(" | "),
      gapsCn: (job.analysis?.gapsCn || job.translation?.gapsCn || []).join(" | "),
      questionsCn: (job.analysis?.questionsCn || job.translation?.questionsCn || []).join(" | "),
      nextActionCn: job.analysis?.nextActionCn || job.translation?.nextActionCn || "",
      interest: manual.interest || "",
      applied: manual.applied || "",
      appliedDate: manual.appliedDate || "",
      status: manual.status || "",
      notes: manual.notes || "",
      appliedCn: toAppliedCnDisplayValue(manual),
      responseStatus: toResponseStatusDisplayValue(manual),
      notInterested: manual.notInterested || "",
      notesCn: toNotesCnDisplayValue(manual),
      scopeProfile: getScopeProfile(config),
      adjacentDirectionCnHidden: enrichedAnalysis.adjacentDirectionCn || "",
      industryClusterCnHidden: enrichedAnalysis.industryClusterCn || ""
    });
  }

  applyDataValidation(sheet, "interest", ["感兴趣", "一般", "不感兴趣"]);
  applyDataValidation(sheet, "applied", ["未投", "已投", "面试", "拒", "Offer"]);
  applyDataValidation(sheet, "status", ["正常", "已失效"]);
  applyDataValidation(sheet, "appliedCn", ["未投递", "已投递", "面试中", "已拒", "Offer"]);
  applyDataValidation(sheet, "responseStatus", ["未回复", "已回复", "面试中", "已拒", "Offer", "已失效"]);
  applyDataValidation(sheet, "notInterested", ["否", "是"]);

  sheet.getRow(1).font = { bold: true };
  sheet.columns.forEach((col) => {
    col.alignment = { vertical: "top", wrapText: true };
  });
  sheet.getColumn("url").font = { color: { argb: "FF0563C1" }, underline: true };
  try {
    await workbook.xlsx.writeFile(xlsxPath);
    return { path: xlsxPath, locked: false };
  } catch (err) {
    if (err && (err.code === "EBUSY" || err.code === "EPERM")) {
      const altPath = xlsxPath.replace(/\\.xlsx$/i, "") + ".new.xlsx";
      await workbook.xlsx.writeFile(altPath);
      console.log(
        `[${nowIso()}] Excel file locked. Wrote updated file to ${altPath}. Close the original and rename if needed.`
      );
      return { path: altPath, locked: true };
    }
    throw err;
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const configPath = path.resolve(args.configPath);
  await ensureConfigExists(configPath);
  await loadDotEnvIfPresent(path.dirname(configPath));
  const { config: rawConfig } = await loadConfig(configPath);
  const config = withDefaults(rawConfig);
  const baseDir = path.dirname(configPath);
  const runStartedAt = Date.now();

  const apiKey = (process.env.OPENAI_API_KEY || "").trim();
  const baseURL =
    (process.env.OPENAI_BASE_URL || process.env.OPENAI_API_BASE || "").trim() || undefined;
  const organization = (process.env.OPENAI_ORGANIZATION || "").trim() || undefined;
  const project = (process.env.OPENAI_PROJECT || "").trim() || undefined;

  const useWebSearch =
    config.sources.enableWebSearch && !args.disableWebSearch && !args.offline;
  const useCompanySources = config.sources.enableCompanySources && !args.offline;
  const willScore = !args.offline && !args.dryRun;
  const willTranslate = config.translation.enable && !args.offline && !args.dryRun;
  const strictScoring = Boolean(args.strictScoring || config.analysis?.strictScoring === true);
  const lowTokenMode = Boolean(args.lowTokenMode || config.analysis?.lowTokenMode === true);
  config.analysis.lowTokenMode = lowTokenMode;
  const willDiscoverCompanies =
    !args.offline &&
    config.sources.enableCompanySources &&
    (args.discoverCompanies || config.sources.requireCompanyDiscovery);
  const needsOpenAI = useWebSearch || willDiscoverCompanies || willScore || willTranslate;

  if (strictScoring && (args.offline || args.dryRun)) {
    throw new Error("`--strict-scoring` requires online scoring mode (not --offline/--dry-run).");
  }

  if (needsOpenAI && !apiKey) {
    throw new Error(
      "OPENAI_API_KEY is missing. Set it in your environment, or run with --offline to only export existing jobs.json to Excel."
    );
  }

  const client =
    args.offline || !needsOpenAI
      ? null
      : new OpenAI({
          apiKey,
          baseURL,
          organization,
          project
        });

  const resumePath = path.resolve(baseDir, config.candidate.resumePath);
  const resumeText = await fs.readFile(resumePath, "utf8");

  const outputLegacyXlsx = path.resolve(baseDir, config.output.xlsxPath || "./jobs.xlsx");
  const outputTrackerXlsx = path.resolve(baseDir, config.output.trackerXlsxPath);
  const outputJson = path.resolve(baseDir, config.output.jsonPath);
  const outputFoundJson = path.resolve(
    baseDir,
    config.output.foundJsonPath || "./jobs_found.json"
  );
  const outputResumePendingJson = path.resolve(
    baseDir,
    config.output.resumePendingPath || "./jobs_resume_pending.json"
  );
  const outputRecommendedJson = path.resolve(
    baseDir,
    config.output.recommendedJsonPath || "./jobs_recommended.json"
  );
  const outputCnEuropeJson = path.resolve(
    baseDir,
    config.output.cnEuropeJsonPath || "./jobs_cn_europe.json"
  );
  const companiesPath = path.resolve(
    baseDir,
    args.companiesPath || config.sources.companiesPath
  );

  const existing = (await readJsonIfExists(outputJson)) || { jobs: [] };
  const existingData = args.reset ? { jobs: [] } : existing;
  const existingByUrl = new Map();
  for (const j of Array.isArray(existingData.jobs) ? existingData.jobs : []) {
    const normalizedUrl = normalizeJobUrl(j?.url || "");
    if (!normalizedUrl) continue;
    existingByUrl.set(normalizedUrl, { ...j, url: normalizedUrl });
  }
  const resumePendingPayload =
    args.reset || args.dryRun
      ? { jobs: [] }
      : (await readJsonIfExists(outputResumePendingJson)) || { jobs: [] };
  const carryoverPending = uniqueQueuedJobs(
    extractJobsList(resumePendingPayload).filter((job) => needsAnalysis(job)),
    config
  ).filter((job) => needsAnalysis(existingByUrl.get(job.url) || job));

  const legacyManualByUrl = args.reset ? new Map() : await readExistingManualFields(outputLegacyXlsx);
  const trackerManualByUrl = args.reset ? new Map() : await readExistingManualFields(outputTrackerXlsx);
  const manualByUrl = mergeManualFields(legacyManualByUrl, trackerManualByUrl);
  const existingRecommendedRows = args.reset ? new Map() : await readExistingRows(outputTrackerXlsx);

  let scoringDisabled = args.offline || args.dryRun || !willScore;
  let translationDisabled = args.offline || args.dryRun || !config.translation.enable;
  let candidateProfile = null;
  if (!scoringDisabled) {
    if (lowTokenMode) {
      console.log(
        `[${nowIso()}] Low-token scoring mode enabled: skip candidate profile build and long explanation fields.`
      );
    } else {
      try {
        console.log(`[${nowIso()}] Building candidate profile...`);
        candidateProfile = await buildCandidateProfile({ client, config, resumeText });
      } catch (err) {
        if (strictScoring) {
          throw new Error(
            `[STRICT] Candidate profile failed, cannot run high-quality scoring: ${formatOpenAIError(err)}`
          );
        }
        scoringDisabled = true;
        console.log(
          `[${nowIso()}] Candidate profile failed, skip scoring this run: ${String(err?.message || err)}`
        );
      }
    }
  }

  const discovered = [];
  if (useWebSearch) {
    const rawQueries = args.query ? [args.query] : config.search.queries;
    const maxQueries =
      typeof args.maxQueries === "number" && Number.isFinite(args.maxQueries)
        ? Math.max(0, args.maxQueries)
        : undefined;
    const baseMix = normalizeTrackMix(config?.search?.trackMix || trackConfigDefaults());
    const effectiveTrackMix =
      config?.search?.feedbackWeightEnabled === false
        ? baseMix
        : applyFeedbackToTrackMix(baseMix, existingRecommendedRows);
    const queries = selectQueriesByTrackMix(rawQueries, {
      trackMix: effectiveTrackMix,
      limit: maxQueries,
      config
    });
    if (queries.length > 0) {
      const distribution = TRACK_KEYS.map((key) => {
        const count = queries.filter((q) => inferTrackFromQuery(q, config) === key).length;
        return `${key}:${count}`;
      }).join(" | ");
      console.log(`[${nowIso()}] Query mix => ${distribution}`);
    }
    const webSearchLimit = pLimit(
      Math.max(1, Math.floor(toFiniteNumber(config?.search?.webSearchConcurrency, 3)))
    );
    const searchResults = await Promise.all(
      queries.map((query) =>
        webSearchLimit(async () => {
          try {
            console.log(`[${nowIso()}] Web search: ${query}`);
            const jobs = await openaiSearchJobs({ client, config, query });
            return { jobs, error: null };
          } catch (err) {
            return { jobs: [], error: err };
          }
        })
      )
    );
    for (const result of searchResults) {
      if (result?.error) {
        if (strictScoring && isQuotaOrRateLimitError(result.error)) {
          throw new Error(
            `[STRICT] Web search failed due OpenAI quota/rate limit: ${formatOpenAIError(result.error)}`
          );
        }
        console.log(
          `[${nowIso()}] Web search query failed, skip: ${String(result.error?.message || result.error)}`
        );
        continue;
      }
      for (const j of Array.isArray(result?.jobs) ? result.jobs : []) {
        const platformLabel = platformListingLabelForUrl(j?.url || "", config);
        discovered.push({
          ...j,
          source: platformLabel ? `web_search:${platformLabel}` : "web_search",
          sourceType: platformLabel ? `platform_listing:${platformLabel.toLowerCase()}` : "web_search"
        });
      }
    }
  }

  if (useCompanySources) {
    console.log(`[${nowIso()}] Company sources: loading company list...`);
    const discoveredUrlSet = new Set(
      discovered.map((job) => normalizeJobUrl(job?.url || "")).filter(Boolean)
    );
    const knownJobUrls = new Set([...existingByUrl.keys(), ...discoveredUrlSet]);
    const companyJobs = await collectCompanyJobs({
      client,
      config,
      args,
      baseDir,
      forceDiscover: willDiscoverCompanies,
      disableCompanyDiscovery: args.disableCompanyDiscovery,
      runStartedAt,
      seenJobUrls: knownJobUrls
    });
    console.log(`[${nowIso()}] Company sources: collected ${companyJobs.length} jobs.`);
    for (const j of companyJobs) discovered.push(j);
  }

  const normalized = [];
  for (const j of discovered) {
    const url = normalizeUrl(j.url);
    const isCompany = j.sourceType === "company";
    if (!url || (!isCompany && shouldBlockUrl(url, config))) continue;
    if (!hasJobSignal({ title: j.title || "", url, summary: j.summary || "" })) continue;
    normalized.push({
      title: String(j.title || "").trim(),
      company: String(j.company || "").trim(),
      location: String(j.location || "").trim(),
      url,
      datePosted: j.datePosted || "",
      summary: j.summary || "",
      availabilityHint: j.availabilityHint || "",
      source: j.source || "",
      sourceType: j.sourceType || "",
      companyTags: Array.isArray(j.companyTags) ? j.companyTags.filter(Boolean) : []
    });
  }

  const dedup = new Map();
  for (const j of normalized) {
    const key = buildJobDedupeKey(j) || j.url;
    if (!key) continue;
    const existing = dedup.get(key);
    if (!existing) {
      dedup.set(key, j);
      continue;
    }
    const picked = compareJobsByPreference(existing, j, config) > 0 ? j : existing;
    dedup.set(key, picked);
  }

  const filteredDedup = new Map();
  for (const job of dedup.values()) {
    if (!job?.url) continue;
    if (shouldBlockUrl(job.url, config)) continue;
    const byUrl = normalizeJobUrl(job.url);
    filteredDedup.set(byUrl, {
      ...job,
      canonicalUrl: canonicalJobUrl(job) || byUrl,
      sourceQuality: inferSourceQuality(job, config),
      regionTag: inferRegionTag(job)
    });
  }

  const newJobs = [];
  for (const j of filteredDedup.values()) {
    if (!existingByUrl.has(j.url)) newJobs.push(j);
  }

  const limitedNew = args.maxNewJobs ? newJobs.slice(0, args.maxNewJobs) : newJobs;
  const missingAnalysis = Array.from(filteredDedup.values()).filter((j) => {
    const existingJob = existingByUrl.get(j.url);
    return existingJob && needsAnalysis(existingJob);
  });

  const foundJobsOut = Array.from(filteredDedup.values()).map((j) => ({
    title: j.title,
    company: j.company,
    location: j.location,
    url: j.url,
    canonicalUrl: j.canonicalUrl || canonicalJobUrl(j) || normalizeJobUrl(j.url),
    source: j.source || "",
    sourceQuality: j.sourceQuality || inferSourceQuality(j, config),
    regionTag: j.regionTag || inferRegionTag(j),
    fitTrack: deriveTrackAndSignals(j, config).fitTrack,
    companyTags: j.companyTags || [],
    alreadyAnalyzed: hasCompletedAnalysis(existingByUrl.get(j.url))
  }));
  await writeJsonAtomic(outputFoundJson, {
    generatedAt: nowIso(),
    jobs: foundJobsOut
  });
  if (args.offline) {
    console.log(
      `[${nowIso()}] Offline mode: loaded ${existingByUrl.size} jobs from ${outputJson}.`
    );
  } else if (args.dryRun) {
    console.log(
      `[${nowIso()}] Dry-run mode: will run web search, but skip JD fetch, scoring, and XLSX export.`
    );
  } else {
    console.log(
      `[${nowIso()}] Discovered ${filteredDedup.size} unique, ${limitedNew.length} new, ${carryoverPending.length} pending carryover (existing ${existingByUrl.size}).`
    );
  }

  const limit = pLimit(4);
  const rawJobsToProcess = args.offline
    ? Array.from(existingByUrl.values())
    : args.reanalyze || args.retranslate
      ? uniqueQueuedJobs(
          [
            ...carryoverPending,
            ...Array.from(existingByUrl.values()),
            ...Array.from(filteredDedup.values())
          ],
          config
        )
      : uniqueQueuedJobs([...carryoverPending, ...limitedNew, ...missingAnalysis], config);
  const analysisBudget =
    typeof config.analysis.maxJobsToAnalyzePerRun === "number" &&
    Number.isFinite(config.analysis.maxJobsToAnalyzePerRun)
      ? Math.max(0, config.analysis.maxJobsToAnalyzePerRun)
      : Infinity;
  const jdFetchBudget = Math.max(
    0,
    Math.floor(toFiniteNumber(config?.analysis?.jdFetchMaxJobsPerRun, 10))
  );
  const normalizedJobsToProcess = args.reanalyze || args.retranslate
    ? rawJobsToProcess
    : uniqueQueuedJobs(
        [
          ...rawJobsToProcess.filter((job) => !isSignalOnlyJob(job)),
          ...rawJobsToProcess.filter((job) => isPromotableSignalJob(job)).slice(
            0,
            Math.min(3, analysisBudget, jdFetchBudget)
          ),
          ...rawJobsToProcess.filter((job) => isSignalOnlyJob(job) && !isPromotableSignalJob(job))
        ],
        config
      );
  const promotedSignalUrls = new Set(
    normalizedJobsToProcess.filter((job) => isSignalOnlyJob(job) && isPromotableSignalJob(job)).map((job) => job.url)
  );
  const promotedSignalJobs = rawJobsToProcess.filter((job) => promotedSignalUrls.has(job.url));
  const analysisEligibleJobs = args.reanalyze
    ? normalizedJobsToProcess
    : uniqueQueuedJobs(
        [
          ...rawJobsToProcess.filter((job) => !isSignalOnlyJob(job)),
          ...promotedSignalJobs
        ],
        config
      );
  if (!args.offline && !args.reanalyze && !args.retranslate) {
    console.log(
      `[${nowIso()}] Main-stage queue: ${analysisEligibleJobs.length} analysis candidates, promoted signal jobs: ${promotedSignalUrls.size}.`
    );
  }
  const analysisCandidates = args.reanalyze
    ? normalizedJobsToProcess
    : analysisEligibleJobs.filter((job) => needsAnalysis(existingByUrl.get(job.url) || job));
  const analyzeUrls =
    args.offline || args.dryRun
      ? new Set()
      : args.reanalyze
        ? new Set(normalizedJobsToProcess.map((j) => j.url))
        : new Set(analysisCandidates.slice(0, analysisBudget).map((j) => j.url));
  const jdFetchUrls =
    args.offline || args.dryRun
      ? new Set()
      : args.reanalyze
        ? new Set(normalizedJobsToProcess.slice(0, jdFetchBudget).map((j) => j.url))
        : new Set(
            analysisCandidates
              .slice(0, Math.min(analysisBudget, jdFetchBudget))
              .map((j) => j.url)
          );
  let runtimeScoringDisabled = scoringDisabled;
  let runtimeTranslationDisabled = translationDisabled;

  const processed = await Promise.all(
    normalizedJobsToProcess.map((j) =>
      limit(async () => {
        const base = existingByUrl.get(j.url) || {
          url: j.url,
          dateFound: nowIso()
        };

        const merged = {
          ...base,
          title: preferJobTitle(base.title, j.title),
          company: j.company || base.company || "",
          location: j.location || base.location || "",
          dateFound: base.dateFound || nowIso(),
          datePosted: j.datePosted || base.datePosted || "",
          summary: j.summary || base.summary || "",
          availabilityHint: j.availabilityHint || base.availabilityHint || ""
        };
        merged.canonicalUrl = canonicalJobUrl(merged) || normalizeJobUrl(merged.url);
        merged.regionTag = inferRegionTag(merged) || merged.regionTag || "";
        if (!merged.location && merged.jd?.rawText) {
          merged.location = extractLocationFromText(merged.jd.rawText) || merged.location;
        }
        const combinedTags = new Set([
          ...(Array.isArray(base.companyTags) ? base.companyTags : []),
          ...(Array.isArray(j.companyTags) ? j.companyTags : [])
        ]);
        merged.companyTags = Array.from(combinedTags);
        if (j.source || base.source) {
          const sourceSet = new Set([base.source, j.source].filter(Boolean));
          merged.source = Array.from(sourceSet).join(" | ");
        }
        if (j.sourceType || base.sourceType) {
          const sourceTypeSet = new Set(
            [base.sourceType, j.sourceType].map((x) => String(x || "").trim()).filter(Boolean)
          );
          merged.sourceType = Array.from(sourceTypeSet).join(" | ");
        }

        const localPreFilter =
          !args.dryRun && !args.offline && !args.reanalyze
            ? buildLocalPreFilterDecision({ job: merged, config })
            : { enabled: false, keep: true, threshold: 0, heuristic: null };
        if (localPreFilter.enabled && !localPreFilter.keep && !merged.analysis) {
          const finalized = finalizeAnalysisResult({
            analysis: localPreFilter.heuristic,
            job: merged,
            config
          });
          merged.analysis = {
            ...finalized,
            updatedAt: nowIso(),
            fallback: true,
            prefilterRejected: true,
            prefilterScore: clampNumber(
              toFiniteNumber(localPreFilter.heuristic?.matchScore, 0),
              0,
              100
            )
          };
          if (!merged.location && localPreFilter.heuristic?.location) {
            merged.location = localPreFilter.heuristic.location;
          }
        }

        if (
          !args.dryRun &&
          !args.offline &&
          !merged.analysis?.prefilterRejected &&
          !isLimitedPlatformListingJob(merged, config) &&
          (!merged.jd || !merged.jd.rawText) &&
          jdFetchUrls.has(merged.url)
        ) {
          const details = await fetchJobDetails({ url: merged.url, config });
          const extracted = details.extracted || {};
          merged.jd = {
            fetchedAt: details.fetchedAt,
            ok: details.ok,
            status: details.status,
            finalUrl: details.finalUrl || merged.url,
            redirected: Boolean(details.redirected),
            text: extracted.description || "",
            rawText: details.rawText,
            applyUrl: details.applyUrl || ""
          };
          merged.title = preferJobTitle(merged.title, extracted.title);
          merged.company = extracted.company || merged.company;
          merged.location = extracted.location || merged.location;
          merged.datePosted = extracted.datePosted || merged.datePosted || "";
          merged.summary = extracted.description
            ? chunk(extracted.description, 400)
            : chunk(details.rawText, 400);
          if (!merged.location && details.locationHint) {
            merged.location = details.locationHint;
          }
          merged.canonicalUrl = canonicalJobUrl(merged) || normalizeJobUrl(merged.url);
          merged.regionTag = inferRegionTag(merged) || merged.regionTag || "";
        }

        if (
          !args.dryRun &&
          !args.offline &&
          !merged.analysis?.prefilterRejected &&
          analyzeUrls.has(merged.url) &&
          (args.reanalyze || needsAnalysis(merged))
        ) {
          if (runtimeScoringDisabled) {
            if (strictScoring) {
              throw new Error(
                "[STRICT] Scoring disabled after quota/rate-limit failure; high-quality scoring aborted."
              );
            }
            const fallback = fallbackScoreJobFit({ job: merged, config });
            const finalized = finalizeAnalysisResult({
              analysis: fallback,
              job: merged,
              config
            });
            merged.analysis = { ...finalized, updatedAt: nowIso(), fallback: true };
            if (!merged.location && fallback.location) merged.location = fallback.location;
          } else {
            try {
              const analysis = await scoreJobFit({
                client,
                config,
                candidateProfile,
                job: merged
              });
              const finalized = finalizeAnalysisResult({
                analysis,
                job: merged,
                config
              });
              merged.analysis = { ...finalized, updatedAt: nowIso() };
              if (!merged.location && analysis.location) {
                merged.location = analysis.location;
              }
            } catch (err) {
              if (strictScoring && isQuotaOrRateLimitError(err)) {
                throw new Error(
                  `[STRICT] Job scoring failed due OpenAI quota/rate limit: ${formatOpenAIError(err)}`
                );
              }
              merged.analysisError = String(err?.message || err);
              if (isQuotaOrRateLimitError(err)) {
                runtimeScoringDisabled = true;
                console.log(
                  `[${nowIso()}] Scoring disabled for remaining jobs due quota/rate limit.`
                );
              }
              const fallback = fallbackScoreJobFit({ job: merged, config });
              const finalized = finalizeAnalysisResult({
                analysis: fallback,
                job: merged,
                config
              });
              merged.analysis = { ...finalized, updatedAt: nowIso(), fallback: true };
              if (!merged.location && fallback.location) merged.location = fallback.location;
            }
          }
        }

        const shouldTranslate =
          !args.dryRun &&
          !args.offline &&
          config.translation.enable &&
          !runtimeTranslationDisabled &&
          !merged.analysis?.prefilterRejected &&
          (args.reanalyze || args.retranslate || !merged.translation) &&
          (merged.summary || merged.analysis);

        if (shouldTranslate) {
          try {
            const translation = await translateJobFields({ client, config, job: merged });
            merged.translation = { ...translation, updatedAt: nowIso() };
          } catch (err) {
            merged.translationError = String(err?.message || err);
            if (isQuotaOrRateLimitError(err)) {
              runtimeTranslationDisabled = true;
              console.log(
                `[${nowIso()}] Translation disabled for remaining jobs due quota/rate limit.`
              );
            }
          }
        }

        if (merged.analysis) {
          const enriched = enrichAnalysisDerivedFields({ analysis: merged.analysis, job: merged, config });
          merged.analysis = { ...enriched };
        }
        merged.sourceQuality = inferSourceQuality(merged, config);
        merged.canonicalUrl = canonicalJobUrl(merged) || normalizeJobUrl(merged.url);
        merged.regionTag = inferRegionTag(merged) || merged.regionTag || "";

        return merged;
      })
    )
  );

  let suppressedSignalOnlyCount = 0;
  if (!args.offline && !args.reanalyze && !args.retranslate) {
    for (const job of processed) {
      if (!isSignalOnlyJob(job)) continue;
      if (promotedSignalUrls.has(job.url)) continue;
      if (hasCompletedAnalysis(job)) continue;
      job.analysis = {
        ...(job.analysis || {}),
        updatedAt: nowIso(),
        fallback: true,
        prefilterRejected: true,
        signalOnlyNoise: true
      };
      suppressedSignalOnlyCount += 1;
    }
  }

  const prefilterRejectedCount = processed.filter(
    (job) => job?.analysis?.prefilterRejected === true && !job?.analysis?.signalOnlyNoise
  ).length;
  if (prefilterRejectedCount > 0) {
    console.log(
      `[${nowIso()}] Local prefilter skipped GPT scoring for ${prefilterRejectedCount} jobs.`
    );
  }
  if (suppressedSignalOnlyCount > 0) {
    console.log(
      `[${nowIso()}] Suppressed ${suppressedSignalOnlyCount} signal-only jobs from resume queue.`
    );
  }

  for (const j of processed) existingByUrl.set(j.url, j);

  if (!args.offline && !args.dryRun) {
    const mainStagePendingJobs = sortResumePendingJobs(
      Array.from(existingByUrl.values()).filter(
        (job) => !hasCompletedAnalysis(job) && !isSignalOnlyJob(job)
      )
    );
    const signalOnlyPendingJobs = Array.from(existingByUrl.values()).filter(
      (job) => !hasCompletedAnalysis(job) && isSignalOnlyJob(job)
    );
    const resumePendingMeta =
      resumePendingPayload &&
      typeof resumePendingPayload === "object" &&
      !Array.isArray(resumePendingPayload)
        ? resumePendingPayload
        : {};
    const resumePendingVersion = Number.isFinite(Number(resumePendingMeta.version))
      ? Number(resumePendingMeta.version)
      : 2;
    await writeJsonAtomic(outputResumePendingJson, {
      ...resumePendingMeta,
      version: resumePendingVersion,
      generatedAt: nowIso(),
      jobs: mainStagePendingJobs,
      summary: {
        mainStagePendingCount: mainStagePendingJobs.length,
        signalOnlyPendingCount: signalOnlyPendingJobs.length,
        signalOnlySuppressedCount: suppressedSignalOnlyCount,
        promotedSignalCount: promotedSignalUrls.size
      }
    });
  }

  const filteredJobs = Array.from(existingByUrl.values()).filter(
    (job) =>
      !isStale(job.datePosted, config.filters.maxPostAgeDays) &&
      passesRegionMode(job, config) &&
      !isUnavailableJob(job, config) &&
      !shouldBlockUrl(job.url, config) &&
      hasJobSignal({
        title: job.title || "",
        url: job.url || "",
        summary: job.summary || job.jd?.text || ""
      })
  );
  const allJobs = dedupeJobsByCanonical(filteredJobs, config);
  allJobs.sort((a, b) => {
    const sa = a.analysis?.matchScore ?? -1;
    const sb = b.analysis?.matchScore ?? -1;
    if (sb !== sa) return sb - sa;
    return String(b.dateFound || "").localeCompare(String(a.dateFound || ""));
  });

  const out = { version: 1, generatedAt: nowIso(), jobs: allJobs };
  await writeJsonAtomic(outputJson, out);

  let excelInfo = null;
  if (!args.dryRun) {
    const candidateOutputJobs = allJobs.filter(
      (j) =>
        j.analysis?.recommend === true ||
        isChinaHydrogenEuropeJob(j, config)
    );
    await recheckCandidateLinkHealth({ jobs: candidateOutputJobs, config });

    const preliminaryRecommendedJobs = allJobs.filter(
      (j) =>
        j.analysis?.recommend === true &&
        !isUnavailableJob(j, config) &&
        (isOutputablePlatformListingJob(j, config) ||
          ((isApplyableJobPage(j) || hasLikelyActiveJobSignal(j, config)) &&
            (j.analysis?.isJobPosting === true ||
              (j.analysis?.isJobPosting == null &&
                hasJobSignal({
                  title: j.title || "",
                  url: j.url || "",
                  summary: j.summary || ""
                })))))
    );

    const preliminaryCnEuropeJobs = allJobs.filter(
      (job) =>
        isChinaHydrogenEuropeJob(job, config) &&
        !isUnavailableJob(job, config) &&
        (isApplyableJobPage(job) || hasLikelyActiveJobSignal(job, config))
    );
    const postVerifyEnabled = Boolean(
      !args.offline &&
        !args.dryRun &&
        client &&
        config.analysis?.postVerifyEnabled === true
    );
    const postVerifyRequireChecked = Boolean(
      config.analysis?.postVerifyRequireChecked ?? true
    );
    const postVerifyMap = new Map();
    if (postVerifyEnabled) {
      const verifyPool = new Map();
      for (const job of preliminaryRecommendedJobs) {
        if (isLimitedPlatformListingJob(job, config)) continue;
        verifyPool.set(job.url, job);
      }
      for (const job of preliminaryCnEuropeJobs) verifyPool.set(job.url, job);
      const verifyList = Array.from(verifyPool.values());
      const maxVerify = Math.max(
        0,
        Number(config.analysis?.postVerifyMaxJobsPerRun ?? verifyList.length)
      );
      const targets = verifyList.slice(0, maxVerify);
      if (targets.length > 0) {
        console.log(
          `[${nowIso()}] Post-verify shortlisted jobs: ${targets.length} (model ${config.analysis.postVerifyModel}).`
        );
        const limitVerify = pLimit(3);
        await Promise.all(
          targets.map((job) =>
            limitVerify(async () => {
              try {
                const verified = await postVerifyRecommendedJob({
                  client,
                  config,
                  job
                });
                const payload = { ...verified, checkedAt: nowIso() };
                postVerifyMap.set(job.url, payload);
                job.analysis = { ...(job.analysis || {}), postVerify: payload };
                if (!job.location && verified.location) {
                  job.location = verified.location;
                }
              } catch (err) {
                const payload = {
                  isValidJobPage: false,
                  recommend: false,
                  location: "",
                  finalUrl: job.url,
                  error: String(err?.message || err),
                  checkedAt: nowIso()
                };
                postVerifyMap.set(job.url, payload);
                job.analysis = { ...(job.analysis || {}), postVerify: payload };
              }
            })
          )
        );
        const passCount = Array.from(postVerifyMap.values()).filter(
          (v) => v.isValidJobPage === true && v.recommend === true
        ).length;
        console.log(
          `[${nowIso()}] Post-verify pass ${passCount}/${postVerifyMap.size}.`
        );
      }
    }

    const passPostVerify = (job, requireRecommend) => {
      if (isLimitedPlatformListingJob(job, config)) return true;
      if (!postVerifyEnabled) return true;
      const verified = postVerifyMap.get(job.url) || job.analysis?.postVerify;
      if (!verified) return !postVerifyRequireChecked;
      if (verified.isValidJobPage !== true) return false;
      if (requireRecommend) return verified.recommend === true;
      return true;
    };

    const recommendedOnlyJobs = preliminaryRecommendedJobs.filter(
      (job) => passPostVerify(job, true) && passesFinalOutputCheck(job, config)
    );
    const cnEuropeJobs = preliminaryCnEuropeJobs.filter(
      (job) => passPostVerify(job, false) && passesFinalOutputCheck(job, config)
    );
    const combinedMap = new Map();
    for (const job of recommendedOnlyJobs) {
      const key = buildJobDedupeKey(job) || job.url;
      const tags = ["推荐"];
      const platformTag = platformListingTag(job, config);
      if (platformTag) tags.push(platformTag);
      const withMeta = {
        ...job,
        canonicalUrl: canonicalJobUrl(job) || normalizeJobUrl(job.url),
        sourceQuality: inferSourceQuality(job, config),
        regionTag: inferRegionTag(job) || job.regionTag || "",
        listTags: tags
      };
      combinedMap.set(key, withMeta);
    }
    for (const job of cnEuropeJobs) {
      const key = buildJobDedupeKey(job) || job.url;
      const current = combinedMap.get(key);
      if (current) {
        const tags = new Set([...(current.listTags || []), "中资赴欧"]);
        combinedMap.set(key, { ...current, listTags: Array.from(tags) });
      } else {
        combinedMap.set(key, {
          ...job,
          canonicalUrl: canonicalJobUrl(job) || normalizeJobUrl(job.url),
          sourceQuality: inferSourceQuality(job, config),
          regionTag: inferRegionTag(job) || job.regionTag || "",
          listTags: ["中资赴欧"]
        });
      }
    }

    let unifiedRecommendedJobs = Array.from(combinedMap.values());
    if (config.output.recommendedMode === "append") {
      const trackerNow = nowIso();
      const trackerToday = toHumanDate(trackerNow);
      const merged = new Map();
      const allJobsByKey = new Map(
        allJobs
          .map((job) => [buildJobDedupeKey(job) || normalizeJobUrl(job.url), job])
          .filter(([key]) => key)
      );
      let prunedRecentInvalidRows = 0;
      for (const row of existingRecommendedRows.values()) {
        const job = rowToJob(row, config);
        if (!job) continue;
        const key = buildJobDedupeKey(job) || normalizeJobUrl(job.url);
        if (!key) continue;
        const currentDateFound = String(job.dateFound || row.dateFound || "").trim();
        const richJob = allJobsByKey.get(key) || job;
        if (
          currentDateFound === trackerToday &&
          !hasManualTracking(row) &&
          !passesFinalOutputCheck(richJob, config)
        ) {
          prunedRecentInvalidRows += 1;
          continue;
        }
        if (!job.dateFound) job.dateFound = trackerNow;
        merged.set(key, job);
      }
      if (prunedRecentInvalidRows > 0) {
        console.log(
          `[${nowIso()}] Final output filter removed ${prunedRecentInvalidRows} recent invalid tracker rows.`
        );
      }

      const historicalJobs = allJobs.filter((job) => shouldRestoreHistoricalRecommendedJob(job, config));
      historicalJobs.sort((a, b) => String(a.dateFound || "").localeCompare(String(b.dateFound || "")));
      for (const job of historicalJobs) {
        const key = buildJobDedupeKey(job) || normalizeJobUrl(job.url);
        if (!key) continue;
        const existingJob = merged.get(key);
        const candidate = {
          ...job,
          dateFound: existingJob?.dateFound || job.dateFound || trackerNow
        };
        if (!existingJob && !passesFinalOutputCheck(candidate, config)) {
          continue;
        }
        if (!existingJob) {
          merged.set(key, candidate);
          continue;
        }
        if (!passesFinalOutputCheck(candidate, config)) {
          if (!existingJob.dateFound) {
            merged.set(key, { ...existingJob, dateFound: trackerNow });
          }
          continue;
        }
        if (compareJobsByPreference(existingJob, candidate, config) > 0) {
          merged.set(key, candidate);
        } else if (!existingJob.dateFound) {
          merged.set(key, { ...existingJob, dateFound: trackerNow });
        }
      }

      const newJobs = unifiedRecommendedJobs.filter((job) => job?.url);
      newJobs.sort((a, b) => {
        const sa = a.analysis?.matchScore ?? -1;
        const sb = b.analysis?.matchScore ?? -1;
        if (sb !== sa) return sb - sa;
        return String(b.dateFound || "").localeCompare(String(a.dateFound || ""));
      });
      for (const job of newJobs) {
        const key = buildJobDedupeKey(job) || normalizeJobUrl(job.url);
        if (!key) continue;
        const existingJob = merged.get(key);
        if (!existingJob) {
          merged.set(key, { ...job, dateFound: trackerNow });
          continue;
        }
        const candidate = {
          ...job,
          dateFound: existingJob.dateFound || job.dateFound || trackerNow
        };
        if (compareJobsByPreference(existingJob, candidate, config) > 0) {
          merged.set(key, candidate);
        } else if (!existingJob.dateFound) {
          merged.set(key, { ...existingJob, dateFound: trackerNow });
        }
      }
      unifiedRecommendedJobs = Array.from(merged.values());
    } else {
      unifiedRecommendedJobs.sort((a, b) => {
        const sa = a.analysis?.matchScore ?? -1;
        const sb = b.analysis?.matchScore ?? -1;
        if (sb !== sa) return sb - sa;
        return String(b.dateFound || "").localeCompare(String(a.dateFound || ""));
      });
    }
    await writeJsonAtomic(outputRecommendedJson, {
      generatedAt: nowIso(),
      jobs: unifiedRecommendedJobs
    });
    excelInfo = await writeExcel({
      xlsxPath: outputTrackerXlsx,
      jobs: unifiedRecommendedJobs,
      manualByUrl,
      config
    });

    cnEuropeJobs.sort((a, b) => {
      const sa = a.analysis?.matchScore ?? -1;
      const sb = b.analysis?.matchScore ?? -1;
      if (sb !== sa) return sb - sa;
      return String(b.dateFound || "").localeCompare(String(a.dateFound || ""));
    });
    await writeJsonAtomic(outputCnEuropeJson, {
      generatedAt: nowIso(),
      jobs: cnEuropeJobs
    });
  }

  console.log(
    `[${nowIso()}] Done. JSON: ${outputJson} | Found: ${outputFoundJson}${
      args.dryRun
        ? ""
        : ` | Tracker XLSX: ${excelInfo?.path || outputTrackerXlsx} | CN-Europe JSON: ${outputCnEuropeJson}`
    }`
  );
}

await main();
