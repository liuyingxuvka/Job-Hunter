from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db.repositories.candidates import CandidateRecord
from ..db.repositories.settings import OpenAISettings


SYSTEM_PROMPT = """
You are a rigorous job-positioning advisor.

Your task is to recommend a small set of role directions for this candidate.
Do not generate companies. Do not generate search keywords.

Rules:
1. Return strict JSON only. No Markdown. No extra explanation.
2. role.name_en must be an English recruiting title, but specific enough to show the direction.
   It may include a short qualifier in parentheses.
3. role.name_zh should be a concise Chinese title aligned with role.name_en.
4. role.description_zh must be Chinese and contain 2-3 sentences, not one sentence.
   It should explain key responsibilities, fit rationale, and why this direction is worth pursuing.
5. role.description_en must be English and contain 2-3 sentences, aligned with description_zh.
6. If "existing roles" are provided in the user prompt, only propose additional roles that are clearly new.
   Do not repeat or paraphrase existing roles.
7. Keep recommendations focused and high-value.
8. role.name_en must NOT be generic labels like "Engineer", "Manager", "Specialist", "Analyst" alone.
9. role.name_en should include at least one concrete domain or capability qualifier
   (for example platform/technology/method/industry context).
10. If the candidate context is specific, role names should reflect that specificity instead of broad job families.
11. Avoid broad titles such as "Systems Engineer", "Software Engineer", "Project Manager", "Data Analyst"
    unless you add a strong specialization qualifier (domain, technology, method, or product context).
12. Prefer title patterns like "<Domain> <Function> <Role>" or "<Role> (<specific qualifier>)".

JSON format:
{
  "roles": [
    {
      "name_en": "Systems Engineer (Requirements & Interface Management)",
      "name_zh": "系统工程师（需求与接口管理）",
      "description_zh": "句子1。句子2。句子3。",
      "description_en": "Sentence 1. Sentence 2. Sentence 3."
    }
  ]
}
""".strip()

TRANSLATE_PROMPT = """
You are a precise technical translator.

Task:
Translate the provided role description into the target language.

Rules:
1. Return strict JSON only. No Markdown.
2. Keep the technical meaning and scope accurate.
3. Output 2-3 complete sentences in the target language.
4. Do not invent facts not present in the source text.

JSON format:
{
  "description_translated": "..."
}
""".strip()

ROLE_NAME_TRANSLATE_PROMPT = """
You are a precise technical translator for job titles.

Task:
Translate the provided role title into the target language.

Rules:
1. Return strict JSON only. No Markdown.
2. Keep technical meaning and specificity.
3. Keep the title concise and recruiting-ready.

JSON format:
{
  "name_translated": "..."
}
""".strip()

MANUAL_ROLE_ENRICH_PROMPT = """
You are a rigorous job-positioning advisor.

Task:
Given a candidate context and a user-provided role intent (name + rough notes),
produce one refined role that is specific and practical.

Rules:
1. Return strict JSON only. No Markdown.
2. Keep the user intent, but make role.name_en more specific and recruiting-ready.
3. role.name_en must NOT be generic labels like "Engineer", "Manager", "Specialist", "Analyst" alone.
4. role.name_zh should be concise Chinese and aligned with role.name_en.
5. role.description_zh must be Chinese and contain 2-3 concrete sentences.
6. role.description_en must be English and contain 2-3 concrete sentences aligned with description_zh.
7. Do not generate companies or search keywords.
8. Avoid broad titles such as "Systems Engineer", "Software Engineer", "Project Manager", "Data Analyst"
   unless a clear specialization qualifier is included.

JSON format:
{
  "name_en": "Systems Integration & Test Engineer (HIL/SIL Automation)",
  "name_zh": "系统集成与测试工程师（HIL/SIL 自动化）",
  "description_zh": "句子1。句子2。句子3。",
  "description_en": "Sentence 1. Sentence 2. Sentence 3."
}
""".strip()

CANDIDATE_SEMANTIC_PROFILE_PROMPT = """
You are a rigorous candidate-profile analyst.

Task:
Read the candidate context and extract a structured semantic profile that will be reused for:
1. company discovery
2. search query generation
3. AI role recommendation

Rules:
1. Return strict JSON only. No Markdown. No extra explanation.
2. Focus on business domains, technical focus areas, demonstrated experience themes, and realistic future directions.
3. Keep "demonstrated background" separate from "future target directions".
4. Arrays must contain short phrases, not sentences.
5. Prefer domain/business/technology phrases over job titles.
6. Do not use generic titles such as engineer, scientist, manager, analyst as standalone keywords.
7. Output an ENGLISH-ONLY reusable business phrase library for search and company discovery.
8. `core_business_areas` should stay close to what the candidate has already demonstrated.
9. `adjacent_business_areas` should be plausible expansions, not random jumps.
10. `exploration_business_areas` can be broader, but still defensible.
11. `strong_capabilities` should capture methods / technical strengths, but not generic job titles.
12. `avoid_business_areas` should list misleading directions that might be over-inferred from isolated tools or weak signals.
13. Prefer 2-5 word English phrases that can be used directly in search.
14. Build a large reusable phrase library, but keep quality high.
15. Phrase-count targets:
    - `core_business_areas`: up to 45
    - `adjacent_business_areas`: up to 30
    - `exploration_business_areas`: up to 15
    - `strong_capabilities`: up to 10
16. Do not output Chinese search phrases.

JSON format:
{
  "summary": "One short paragraph.",
  "background_keywords": ["..."],
  "target_direction_keywords": ["..."],
  "core_business_areas": ["..."],
  "adjacent_business_areas": ["..."],
  "exploration_business_areas": ["..."],
  "avoid_business_areas": ["..."],
  "strong_capabilities": ["..."],
  "seniority_signals": ["..."]
}
""".strip()

SEMANTIC_PROFILE_SCHEMA_VERSION = 2


STRONG_ADJACENT_SCOPE_KEYWORDS = (
    "mbse",
    "sysml",
    "verification",
    "validation",
    "v&v",
    "requirements",
    "traceability",
    "reliability",
    "durability",
    "digital twin",
    "condition monitoring",
    "asset health",
    "technical interface",
    "owner engineer",
    "qualification",
    "diagnostics",
    "failure analysis",
)

SOFT_ADJACENT_SCOPE_KEYWORDS = (
    "systems engineer",
    "system engineer",
    "integration",
)

HYDROGEN_SCOPE_KEYWORDS = (
    "hydrogen",
    "electrolyzer",
    "electrolysis",
    "fuel cell",
    "pem",
    "alkaline",
    "ammonia",
    "electrochemical",
)

GENERIC_ROLE_WORDS = {
    "engineer",
    "manager",
    "specialist",
    "analyst",
    "developer",
    "architect",
    "consultant",
    "technician",
    "lead",
    "director",
}

ROLE_LEVEL_WORDS = {
    "senior",
    "junior",
    "lead",
    "principal",
    "staff",
    "sr",
    "jr",
}

BROAD_ROLE_QUALIFIERS = {
    "system",
    "systems",
    "software",
    "hardware",
    "mechanical",
    "electrical",
    "project",
    "program",
    "operations",
    "process",
    "test",
    "quality",
    "data",
    "product",
    "business",
    "research",
    "technical",
}

GENERIC_ROLE_WORDS_CN = (
    "å·¥ç¨‹å¸ˆ",
    "ç»ç†",
    "ä¸“å®¶",
    "åˆ†æžå¸ˆ",
    "é¡¾é—®",
    "ä¸»ç®¡",
    "æ€»ç›‘",
)


@dataclass(frozen=True)
class TargetRoleSuggestion:
    name: str
    description_zh: str
    description_en: str
    scope_profile: str
    name_zh: str = ""
    name_en: str = ""

    @property
    def description(self) -> str:
        """Backward-compatible accessor for older call sites."""
        return self.description_zh or self.description_en


class RoleRecommendationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResumeReadResult:
    text: str = ""
    error: str = ""
    source_type: str = ""


@dataclass(frozen=True)
class CandidateSemanticProfile:
    source_signature: str = ""
    summary: str = ""
    background_keywords: tuple[str, ...] = ()
    target_direction_keywords: tuple[str, ...] = ()
    core_business_areas: tuple[str, ...] = ()
    adjacent_business_areas: tuple[str, ...] = ()
    exploration_business_areas: tuple[str, ...] = ()
    avoid_business_areas: tuple[str, ...] = ()
    strong_capabilities: tuple[str, ...] = ()
    seniority_signals: tuple[str, ...] = ()

    def is_usable(self) -> bool:
        return bool(
            self.core_business_areas
            or self.adjacent_business_areas
            or self.exploration_business_areas
            or self.background_keywords
            or self.target_direction_keywords
            or self.strong_capabilities
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_signature": self.source_signature,
            "summary": self.summary,
            "background_keywords": list(self.background_keywords),
            "target_direction_keywords": list(self.target_direction_keywords),
            "core_business_areas": list(self.core_business_areas),
            "adjacent_business_areas": list(self.adjacent_business_areas),
            "exploration_business_areas": list(self.exploration_business_areas),
            "avoid_business_areas": list(self.avoid_business_areas),
            "strong_capabilities": list(self.strong_capabilities),
            "seniority_signals": list(self.seniority_signals),
        }

    def company_discovery_phrase_library_en(self) -> tuple[str, ...]:
        return _normalize_semantic_list(
            [
                *self.core_business_areas,
                *self.adjacent_business_areas,
                *self.exploration_business_areas,
            ],
            max_items=100,
        )

    def job_search_phrase_library_en(self) -> tuple[str, ...]:
        return _normalize_semantic_list(
            [
                *self.core_business_areas,
                *self.adjacent_business_areas,
                *self.exploration_business_areas,
                *self.strong_capabilities,
            ],
            max_items=100,
        )


def decode_bilingual_description(raw_text: str) -> tuple[str, str]:
    text = str(raw_text or "").strip()
    if not text:
        return "", ""
    try:
        payload = json.loads(text)
    except Exception:
        return _decode_legacy_single_language_text(text)
    if not isinstance(payload, dict):
        return _decode_legacy_single_language_text(text)

    zh = str(payload.get("zh", "")).strip()
    en = str(payload.get("en", "")).strip()
    if zh or en:
        return zh, en
    return _decode_legacy_single_language_text(text)


def _decode_legacy_single_language_text(text: str) -> tuple[str, str]:
    normalized = str(text or "").strip()
    if not normalized:
        return "", ""
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", normalized))
    has_latin = bool(re.search(r"[A-Za-z]", normalized))
    if has_cjk and not has_latin:
        return normalized, ""
    if has_latin and not has_cjk:
        return "", normalized
    return normalized, ""


def encode_bilingual_description(description_zh: str, description_en: str) -> str:
    payload = {
        "v": 1,
        "zh": str(description_zh or "").strip(),
        "en": str(description_en or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False)


def decode_bilingual_role_name(raw_text: str, fallback_name: str = "") -> tuple[str, str]:
    text = str(raw_text or "").strip()
    fallback = str(fallback_name or "").strip()
    if not text:
        return _decode_legacy_single_language_text(fallback)
    try:
        payload = json.loads(text)
    except Exception:
        return _decode_legacy_single_language_text(text)
    if not isinstance(payload, dict):
        return _decode_legacy_single_language_text(text)

    zh = str(payload.get("zh", "")).strip()
    en = str(payload.get("en", "")).strip()
    if zh or en:
        return zh, en
    if fallback:
        return _decode_legacy_single_language_text(fallback)
    return _decode_legacy_single_language_text(text)


def encode_bilingual_role_name(name_zh: str, name_en: str) -> str:
    payload = {
        "v": 1,
        "zh": str(name_zh or "").strip(),
        "en": str(name_en or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False)


def select_bilingual_role_name(raw_text: str, language: str = "zh", fallback_name: str = "") -> str:
    zh, en = decode_bilingual_role_name(raw_text, fallback_name=fallback_name)
    if str(language or "").strip().lower() == "en":
        return en or zh or fallback_name
    return zh or en or fallback_name


def role_name_query_lines(raw_text: str, fallback_name: str = "") -> list[str]:
    zh, en = decode_bilingual_role_name(raw_text, fallback_name=fallback_name)
    ordered: list[str] = []
    seen: set[str] = set()
    for value in (en, zh, fallback_name):
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def select_bilingual_description(raw_text: str, language: str = "zh") -> str:
    zh, en = decode_bilingual_description(raw_text)
    if str(language or "").strip().lower() == "en":
        return en or zh
    return zh or en


def description_for_prompt(raw_text: str) -> str:
    zh, en = decode_bilingual_description(raw_text)
    if zh and en:
        if zh == en:
            return zh
        return f"ZH: {zh}\nEN: {en}"
    return zh or en


def description_query_lines(raw_text: str) -> list[str]:
    zh, en = decode_bilingual_description(raw_text)
    candidates: list[str] = []
    seen: set[str] = set()
    for block in (en, zh):
        for line in str(block or "").splitlines():
            text = line.strip()
            if not text:
                continue
            normalized = text.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(text)
    return candidates


def infer_scope_profile(name: str, description: str) -> str:
    haystack = f"{name}\n{description}".lower()
    if any(keyword in haystack for keyword in STRONG_ADJACENT_SCOPE_KEYWORDS):
        return "adjacent_mbse"
    if any(keyword in haystack for keyword in HYDROGEN_SCOPE_KEYWORDS):
        return "hydrogen_mainline"
    if any(keyword in haystack for keyword in SOFT_ADJACENT_SCOPE_KEYWORDS):
        return "adjacent_mbse"
    return "hydrogen_mainline"


def is_generic_role_name(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return True
    lowered = text.casefold()
    if lowered in GENERIC_ROLE_WORDS:
        return True

    collapsed = re.sub(r"\([^)]*\)", " ", text)
    collapsed = re.sub(r"[^A-Za-z0-9\u3400-\u9fff]+", " ", collapsed).strip()
    if not collapsed:
        return True

    words = [item for item in collapsed.casefold().split() if item]
    if len(words) == 1 and words[0] in GENERIC_ROLE_WORDS:
        return True
    if len(words) == 2 and words[0] in ROLE_LEVEL_WORDS and words[1] in GENERIC_ROLE_WORDS:
        return True

    core_words = [word for word in words if word not in ROLE_LEVEL_WORDS]
    if not core_words:
        return True

    generic_core = [word for word in core_words if word in GENERIC_ROLE_WORDS]
    if generic_core:
        non_generic_core = [word for word in core_words if word not in GENERIC_ROLE_WORDS]
        if not non_generic_core:
            return True
        if len(core_words) <= 2 and all(
            (word in GENERIC_ROLE_WORDS) or (word in BROAD_ROLE_QUALIFIERS)
            for word in core_words
        ):
            return True
        if len(non_generic_core) == 1 and non_generic_core[0] in BROAD_ROLE_QUALIFIERS and len(core_words) <= 3:
            return True

    if any(token in text for token in GENERIC_ROLE_WORDS_CN) and len(re.sub(r"\s+", "", text)) <= 8:
        if not any(ch in text for ch in "()/-&"):
            return True
    return False


def _read_text_resume(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_docx_resume(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    xml_text = xml_bytes.decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", xml_text)
    return re.sub(r"\s+", " ", text).strip()


def _read_pdf_resume(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ModuleNotFoundError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        return ""


def load_resume_excerpt_result(resume_path: str, max_chars: int | None = 6000) -> ResumeReadResult:
    raw_path = str(resume_path or "").strip()
    path = Path(raw_path)
    if not raw_path:
        return ResumeReadResult()
    if not path.exists() or not path.is_file():
        return ResumeReadResult(
            error=f"Resume file could not be found: {raw_path}",
            source_type=path.suffix.lower(),
        )

    suffix = path.suffix.lower()
    try:
        if suffix in {".md", ".txt"}:
            text = _read_text_resume(path)
        elif suffix == ".docx":
            text = _read_docx_resume(path)
        elif suffix == ".pdf":
            try:
                import pypdf  # noqa: F401
            except ModuleNotFoundError:
                return ResumeReadResult(
                    error=(
                        "Resume PDF parsing is unavailable in this build because the 'pypdf' package is not installed. "
                        "Install pypdf, or convert the resume to .docx, .md, or .txt first."
                    ),
                    source_type=suffix,
                )
            text = _read_pdf_resume(path)
        else:
            text = _read_text_resume(path)
    except Exception as exc:
        return ResumeReadResult(
            error=f"Resume file could not be read: {exc}",
            source_type=suffix,
        )

    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        if suffix == ".pdf":
            return ResumeReadResult(
                error=(
                    "Resume PDF contains no extractable text. It may be a scanned or image-only PDF. "
                    "Please OCR it, or convert it to .docx, .md, or .txt before asking for AI role recommendations."
                ),
                source_type=suffix,
            )
        return ResumeReadResult(
            error=f"Resume file is empty or unreadable: {raw_path}",
            source_type=suffix,
        )

    if max_chars is not None and max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n...[truncated]"
    return ResumeReadResult(text=text, source_type=suffix)


def load_resume_excerpt(resume_path: str, max_chars: int | None = 6000) -> str:
    return load_resume_excerpt_result(resume_path, max_chars=max_chars).text


def manual_background_summary(candidate: CandidateRecord) -> str:
    return candidate.notes.strip()


def build_missing_background_error(
    *,
    action_name: str,
    candidate: CandidateRecord,
    resume_result: ResumeReadResult,
) -> str:
    if resume_result.text or manual_background_summary(candidate):
        return ""

    field_name = "Professional Background / 专业摘要"
    if candidate.active_resume_path.strip():
        return (
            f"{action_name} needs usable candidate background information. "
            f"The resume could not be read and '{field_name}' is empty. "
            f"Please upload a readable resume, or fill in '{field_name}' with work history, domain focus, or core strengths first."
        )
    return (
        f"{action_name} needs usable candidate background information. "
        f"No readable resume was provided and '{field_name}' is empty. "
        f"Please upload a resume, or fill in '{field_name}' with work history, domain focus, or core strengths first."
    )


def _normalize_semantic_list(
    raw_value: Any,
    *,
    max_items: int,
    max_length: int = 72,
) -> tuple[str, ...]:
    if not isinstance(raw_value, list):
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        text = text.strip(" \t\r\n,;|，；、。.!?：:()[]{}<>\"'`")
        if not text:
            continue
        if len(text) > max_length:
            text = text[:max_length].rstrip(" ,;|，；、。.!?：:()[]{}<>\"'`")
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
        if len(ordered) >= max_items:
            break
    return tuple(ordered)


def build_candidate_semantic_profile_source_signature(
    candidate: CandidateRecord,
    resume_result: ResumeReadResult,
) -> str:
    payload = {
        "semantic_profile_schema_version": SEMANTIC_PROFILE_SCHEMA_VERSION,
        "candidate": {
            "name": candidate.name.strip(),
            "base_location": candidate.base_location.strip(),
            "preferred_locations": candidate.preferred_locations.strip(),
            "target_directions": candidate.target_directions.strip(),
            "notes": manual_background_summary(candidate),
            "active_resume_path": candidate.active_resume_path.strip(),
        },
        "resume": {
            "text": resume_result.text,
            "error": resume_result.error,
            "source_type": resume_result.source_type,
        },
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parse_candidate_semantic_profile(
    payload_text: str,
    *,
    source_signature: str = "",
) -> CandidateSemanticProfile | None:
    text = _extract_json_object_text(payload_text)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    summary = re.sub(r"\s+", " ", str(payload.get("summary") or "").strip())
    if len(summary) > 320:
        summary = summary[:320].rstrip() + "..."

    profile = CandidateSemanticProfile(
        source_signature=str(payload.get("source_signature") or source_signature or "").strip(),
        summary=summary,
        background_keywords=_normalize_semantic_list(payload.get("background_keywords"), max_items=20),
        target_direction_keywords=_normalize_semantic_list(payload.get("target_direction_keywords"), max_items=20),
        core_business_areas=_normalize_semantic_list(payload.get("core_business_areas"), max_items=45),
        adjacent_business_areas=_normalize_semantic_list(payload.get("adjacent_business_areas"), max_items=30),
        exploration_business_areas=_normalize_semantic_list(payload.get("exploration_business_areas"), max_items=15),
        avoid_business_areas=_normalize_semantic_list(payload.get("avoid_business_areas"), max_items=10),
        strong_capabilities=_normalize_semantic_list(payload.get("strong_capabilities"), max_items=10),
        seniority_signals=_normalize_semantic_list(payload.get("seniority_signals"), max_items=8),
    )
    if not profile.is_usable():
        return None
    if not profile.source_signature and source_signature:
        profile = CandidateSemanticProfile(
            source_signature=source_signature,
            summary=profile.summary,
            background_keywords=profile.background_keywords,
            target_direction_keywords=profile.target_direction_keywords,
            core_business_areas=profile.core_business_areas,
            adjacent_business_areas=profile.adjacent_business_areas,
            exploration_business_areas=profile.exploration_business_areas,
            avoid_business_areas=profile.avoid_business_areas,
            strong_capabilities=profile.strong_capabilities,
            seniority_signals=profile.seniority_signals,
        )
    return profile


def load_candidate_semantic_profile_cache(
    cache_path: Path | None,
    *,
    source_signature: str,
) -> CandidateSemanticProfile | None:
    if cache_path is None or not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    profile = parse_candidate_semantic_profile(
        json.dumps(payload, ensure_ascii=False),
        source_signature=source_signature,
    )
    if profile is None or not profile.is_usable():
        return None
    if profile.source_signature and profile.source_signature != source_signature:
        return None
    return profile


def save_candidate_semantic_profile_cache(
    cache_path: Path | None,
    profile: CandidateSemanticProfile,
) -> None:
    if cache_path is None:
        return
    cache_path.write_text(
        json.dumps(profile.to_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_candidate_semantic_profile_prompt(
    candidate: CandidateRecord,
    *,
    resume_result: ResumeReadResult | None = None,
) -> str:
    resolved_resume = resume_result or load_resume_excerpt_result(candidate.active_resume_path, max_chars=12000)
    background_summary = manual_background_summary(candidate)
    parts = [
        f"Candidate name: {candidate.name}",
        f"Current location: {candidate.base_location or 'N/A'}",
        "Preferred locations:",
        candidate.preferred_locations.strip() or "N/A",
        "Future target directions (self-described):",
        candidate.target_directions.strip() or "N/A",
        "Professional background summary (manual):",
        background_summary or "N/A",
        "Important extraction intent:",
        "- Extract demonstrated business/technical experience from the resume and manual summary.",
        "- Separately extract future-oriented target directions from the self-described target directions field.",
        "- Prefer English business domains, product/platform areas, technical themes, and credible adjacent directions.",
        "- Do not collapse everything into generic job titles.",
        "- Build a reusable English phrase library for company discovery and job-search planning.",
    ]
    if candidate.active_resume_path.strip() and resolved_resume.text:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume excerpt:",
                resolved_resume.text,
            ]
        )
    elif candidate.active_resume_path.strip() and resolved_resume.error:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume read status:",
                resolved_resume.error,
                "If the resume text is unavailable, rely on the manual professional background summary and target directions.",
            ]
        )
    parts.append("Return strict JSON only.")
    return "\n".join(parts)


def semantic_profile_prompt_lines(profile: CandidateSemanticProfile | None) -> list[str]:
    if profile is None or not profile.is_usable():
        return []
    sections: list[tuple[str, tuple[str, ...] | str, int | None]] = [
        ("AI semantic summary:", profile.summary),
        ("AI extracted core business areas:", profile.core_business_areas, 24),
        ("AI extracted adjacent business areas:", profile.adjacent_business_areas, 16),
        ("AI extracted exploration business areas:", profile.exploration_business_areas, 8),
        ("AI extracted background keywords:", profile.background_keywords, 14),
        ("AI extracted target-direction keywords:", profile.target_direction_keywords, 14),
        ("AI extracted strong capabilities:", profile.strong_capabilities, 10),
        ("AI extracted seniority signals:", profile.seniority_signals, 8),
    ]
    lines: list[str] = []
    lines.extend(
        [
            "AI English business phrase library size:",
            f"- company-discovery phrases: {len(profile.company_discovery_phrase_library_en())}",
            f"- job-search phrases: {len(profile.job_search_phrase_library_en())}",
        ]
    )
    for entry in sections:
        label = entry[0]
        value = entry[1]
        limit = entry[2] if len(entry) >= 3 else None
        if isinstance(value, tuple):
            if not value:
                continue
            lines.append(label)
            shown = value[: limit or len(value)]
            lines.extend(f"- {item}" for item in shown)
            if limit is not None and len(value) > limit:
                lines.append(f"- ... ({len(value) - limit} more)")
            continue
        if str(value or "").strip():
            lines.extend([label, str(value).strip()])
    if profile.avoid_business_areas:
        lines.append("AI extracted avoid / misleading directions:")
        shown_avoid = profile.avoid_business_areas[:8]
        lines.extend(f"- {item}" for item in shown_avoid)
        if len(profile.avoid_business_areas) > 8:
            lines.append(f"- ... ({len(profile.avoid_business_areas) - 8} more)")
    return lines


def build_role_recommendation_prompt(
    candidate: CandidateRecord,
    existing_roles: list[tuple[str, str]] | None = None,
    resume_result: ResumeReadResult | None = None,
    semantic_profile: CandidateSemanticProfile | None = None,
) -> str:
    resolved_resume = resume_result or load_resume_excerpt_result(candidate.active_resume_path)
    resume_excerpt = resolved_resume.text
    background_summary = manual_background_summary(candidate)
    normalized_existing: list[tuple[str, str]] = []
    for role in existing_roles or []:
        if not isinstance(role, tuple) or len(role) != 2:
            continue
        role_name = str(role[0] or "").strip()
        role_desc = str(role[1] or "").strip()
        if not role_name:
            continue
        normalized_existing.append((role_name, role_desc))

    parts = [
        f"Candidate name: {candidate.name}",
        f"Current location: {candidate.base_location or 'N/A'}",
        "Preferred locations:",
        candidate.preferred_locations.strip() or "N/A",
        "Current target directions (self-described):",
        candidate.target_directions.strip() or "N/A",
        "Professional background summary (manual):",
        background_summary or "N/A",
    ]
    parts.extend(semantic_profile_prompt_lines(semantic_profile))

    if candidate.active_resume_path.strip() and resume_excerpt:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume excerpt:",
                resume_excerpt,
            ]
        )
    elif candidate.active_resume_path.strip() and resolved_resume.error:
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume read status:",
                "Resume text is unavailable. Use the manual professional background summary and the other candidate context as the primary source of truth.",
            ]
        )

    if normalized_existing:
        parts.append("existing roles (must not repeat):")
        for role_name, role_desc in normalized_existing:
            if role_desc:
                parts.append(f"- {role_name}: {role_desc}")
            else:
                parts.append(f"- {role_name}")
        parts.append("Please add only 1-2 NEW role directions beyond existing roles.")
    else:
        parts.append("Please return up to 3 role directions.")

    parts.extend(
        [
            "role.name_en should be specific, not overly generic.",
            "Avoid generic role titles like Engineer/Manager/Specialist without domain qualifiers.",
            "Avoid broad titles like Systems Engineer / Software Engineer / Project Manager unless strongly specialized.",
            "Prefer titles that include concrete domain or method context.",
            "Prioritize the candidate's demonstrated domain continuity from resume, notes, and self-described directions.",
            "Do not over-index on isolated software/tool keywords if they are not central to the candidate's main work.",
            "If the resume context points to hydrogen, electrochemical systems, aging, degradation, durability, reliability, diagnostics, or lifetime topics, keep the recommendations anchored there unless the candidate explicitly says otherwise.",
            "Provide both role.name_en and role.name_zh.",
            "role.description_zh must be Chinese and 2-3 sentences with concrete details.",
            "role.description_en must be English and 2-3 sentences with concrete details.",
            "Return strict JSON only.",
        ]
    )
    return "\n".join(parts)


def _extract_output_text(response_payload: dict[str, Any]) -> str:
    raw_output_text = response_payload.get("output_text")
    if isinstance(raw_output_text, str) and raw_output_text.strip():
        return raw_output_text.strip()

    texts: list[str] = []
    for output_item in response_payload.get("output", []) or []:
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []) or []:
            if not isinstance(content_item, dict):
                continue
            text_value = content_item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                texts.append(text_value.strip())
    return "\n".join(texts).strip()


def _extract_json_object_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    if text.startswith("{"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_role_suggestions(payload_text: str, max_items: int = 3) -> list[TargetRoleSuggestion]:
    text = payload_text.strip()
    if not text:
        return []

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

    data = json.loads(text)
    raw_roles = data.get("roles", [])
    suggestions: list[TargetRoleSuggestion] = []
    seen_names: set[str] = set()
    for raw_role in raw_roles:
        if not isinstance(raw_role, dict):
            continue
        name_en = str(raw_role.get("name_en", "")).strip()
        name_zh = str(raw_role.get("name_zh", "")).strip()
        name = (
            name_en
            or str(raw_role.get("name", "")).strip()
            or name_zh
        )
        legacy_description = str(raw_role.get("description", "")).strip()
        description_zh = str(raw_role.get("description_zh", "")).strip() or legacy_description
        description_en = str(raw_role.get("description_en", "")).strip() or legacy_description
        if not name:
            continue
        if is_generic_role_name(name):
            continue
        if name_en and is_generic_role_name(name_en):
            continue
        normalized = name.casefold()
        if normalized in seen_names:
            continue
        seen_names.add(normalized)
        summary_for_scope = f"{description_zh}\n{description_en}".strip()
        suggestions.append(
            TargetRoleSuggestion(
                name=name,
                description_zh=description_zh,
                description_en=description_en,
                scope_profile=infer_scope_profile(name, summary_for_scope),
                name_zh=name_zh,
                name_en=name_en or name,
            )
        )
        if len(suggestions) >= max_items:
            break
    return suggestions


def parse_refined_manual_role(
    payload_text: str,
    fallback_name: str,
    fallback_description: str,
) -> TargetRoleSuggestion | None:
    text = _extract_json_object_text(payload_text)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    role_payload = payload.get("role")
    if isinstance(role_payload, dict):
        payload = role_payload

    name_en = str(payload.get("name_en") or "").strip()
    name_zh = str(payload.get("name_zh") or "").strip()
    name = (
        name_en
        or str(payload.get("name") or "").strip()
        or name_zh
        or fallback_name.strip()
    )
    legacy_description = str(payload.get("description") or "").strip()
    description_zh = str(payload.get("description_zh") or "").strip() or legacy_description
    description_en = str(payload.get("description_en") or "").strip() or legacy_description
    if not description_zh and fallback_description:
        description_zh = fallback_description.strip()
    if not description_en and fallback_description and re.search(r"[A-Za-z]", fallback_description or ""):
        description_en = fallback_description.strip()
    if not name:
        return None
    if is_generic_role_name(name):
        return None
    if name_en and is_generic_role_name(name_en):
        return None
    summary = f"{description_zh}\n{description_en}".strip()
    return TargetRoleSuggestion(
        name=name,
        description_zh=description_zh,
        description_en=description_en,
        scope_profile=infer_scope_profile(name, summary),
        name_zh=name_zh,
        name_en=name_en or name,
    )


class OpenAIRoleRecommendationService:
    default_api_url = "https://api.openai.com/v1/responses"

    @staticmethod
    def resolve_api_url(api_base_url: str = "") -> str:
        base = api_base_url.strip()
        if not base:
            return OpenAIRoleRecommendationService.default_api_url
        normalized = base.rstrip("/")
        if normalized.endswith("/responses"):
            return normalized
        if normalized.endswith("/openai/v1"):
            return f"{normalized}/responses"
        if normalized.endswith("/v1"):
            return f"{normalized}/responses"
        if normalized.endswith("/openai"):
            return f"{normalized}/v1/responses"
        return f"{normalized}/v1/responses"

    def extract_candidate_semantic_profile(
        self,
        candidate: CandidateRecord,
        settings: OpenAISettings,
        api_base_url: str = "",
        cache_path: Path | None = None,
    ) -> CandidateSemanticProfile:
        resume_result = load_resume_excerpt_result(candidate.active_resume_path, max_chars=12000)
        source_signature = build_candidate_semantic_profile_source_signature(candidate, resume_result)
        cached_profile = load_candidate_semantic_profile_cache(
            cache_path,
            source_signature=source_signature,
        )
        if cached_profile is not None:
            return cached_profile

        background_error = build_missing_background_error(
            action_name="AI semantic profile extraction",
            candidate=candidate,
            resume_result=resume_result,
        )
        if background_error:
            raise RoleRecommendationError(background_error)
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")

        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": CANDIDATE_SEMANTIC_PROFILE_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_candidate_semantic_profile_prompt(
                                candidate,
                                resume_result=resume_result,
                            ),
                        }
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.resolve_api_url(api_base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RoleRecommendationError(f"OpenAI API request failed: HTTP {exc.code}. {detail}") from exc
        except urllib.error.URLError as exc:
            raise RoleRecommendationError(f"Unable to connect OpenAI API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RoleRecommendationError("OpenAI API request timed out.") from exc

        output_text = _extract_output_text(response_payload)
        profile = parse_candidate_semantic_profile(
            output_text,
            source_signature=source_signature,
        )
        if profile is None or not profile.is_usable():
            raise RoleRecommendationError("AI did not return a usable candidate semantic profile.")
        save_candidate_semantic_profile_cache(cache_path, profile)
        return profile

    def recommend_roles(
        self,
        candidate: CandidateRecord,
        settings: OpenAISettings,
        api_base_url: str = "",
        max_items: int = 3,
        existing_roles: list[tuple[str, str]] | None = None,
    ) -> list[TargetRoleSuggestion]:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")

        resume_result = load_resume_excerpt_result(candidate.active_resume_path)
        background_error = build_missing_background_error(
            action_name="AI role recommendations",
            candidate=candidate,
            resume_result=resume_result,
        )
        if background_error:
            raise RoleRecommendationError(background_error)

        existing_names = {
            str(role[0] or "").strip().casefold()
            for role in (existing_roles or [])
            if isinstance(role, tuple) and len(role) == 2 and str(role[0] or "").strip()
        }
        semantic_profile: CandidateSemanticProfile | None = None
        try:
            semantic_profile = self.extract_candidate_semantic_profile(
                candidate=candidate,
                settings=settings,
                api_base_url=api_base_url,
            )
        except RoleRecommendationError:
            semantic_profile = None

        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": SYSTEM_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": build_role_recommendation_prompt(
                                candidate,
                                existing_roles=existing_roles,
                                resume_result=resume_result,
                                semantic_profile=semantic_profile,
                            ),
                        }
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.resolve_api_url(api_base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RoleRecommendationError(f"OpenAI API request failed: HTTP {exc.code}. {detail}") from exc
        except urllib.error.URLError as exc:
            raise RoleRecommendationError(f"Unable to connect OpenAI API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RoleRecommendationError("OpenAI API request timed out.") from exc

        output_text = _extract_output_text(response_payload)
        try:
            suggestions = parse_role_suggestions(output_text, max_items=max_items)
        except json.JSONDecodeError as exc:
            raise RoleRecommendationError("AI response is not parseable JSON.") from exc

        if existing_names:
            suggestions = [
                suggestion
                for suggestion in suggestions
                if suggestion.name.strip().casefold() not in existing_names
            ]

        if not suggestions:
            raise RoleRecommendationError("AI did not return usable role suggestions.")
        return suggestions

    def enrich_manual_role(
        self,
        candidate: CandidateRecord,
        settings: OpenAISettings,
        role_name: str,
        rough_description: str = "",
        api_base_url: str = "",
    ) -> TargetRoleSuggestion:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")
        intent_name = str(role_name or "").strip()
        if not intent_name:
            raise RoleRecommendationError("Role name is required.")

        resume_result = load_resume_excerpt_result(candidate.active_resume_path, max_chars=3500)
        background_error = build_missing_background_error(
            action_name="AI role refinement",
            candidate=candidate,
            resume_result=resume_result,
        )
        if background_error:
            raise RoleRecommendationError(background_error)
        resume_excerpt = resume_result.text
        background_summary = manual_background_summary(candidate)
        semantic_profile: CandidateSemanticProfile | None = None
        try:
            semantic_profile = self.extract_candidate_semantic_profile(
                candidate=candidate,
                settings=settings,
                api_base_url=api_base_url,
            )
        except RoleRecommendationError:
            semantic_profile = None
        user_prompt_parts = [
            f"Candidate name: {candidate.name}",
            f"Current location: {candidate.base_location or 'N/A'}",
            "Preferred locations:",
            candidate.preferred_locations.strip() or "N/A",
            "Current target directions:",
            candidate.target_directions.strip() or "N/A",
            "Professional background summary (manual):",
            background_summary or "N/A",
            "User provided role intent:",
            f"- Role name: {intent_name}",
            f"- Rough description: {str(rough_description or '').strip() or 'N/A'}",
            "Keep the refined role close to the candidate's demonstrated main domain instead of over-weighting isolated tool keywords.",
        ]
        user_prompt_parts.extend(semantic_profile_prompt_lines(semantic_profile))
        if candidate.active_resume_path.strip() and resume_excerpt:
            user_prompt_parts.extend(
                [
                    f"Resume path: {candidate.active_resume_path.strip()}",
                    "Resume excerpt:",
                    resume_excerpt,
                ]
            )
        elif candidate.active_resume_path.strip() and resume_result.error:
            user_prompt_parts.extend(
                [
                    f"Resume path: {candidate.active_resume_path.strip()}",
                    "Resume read status:",
                    "Resume text is unavailable. Use the manual professional background summary and the other candidate context as the primary source of truth.",
                ]
            )
        user_prompt_parts.append("Return strict JSON only.")
        user_prompt = "\n".join(user_prompt_parts)

        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": MANUAL_ROLE_ENRICH_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.resolve_api_url(api_base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RoleRecommendationError(f"OpenAI API request failed: HTTP {exc.code}. {detail}") from exc
        except urllib.error.URLError as exc:
            raise RoleRecommendationError(f"Unable to connect OpenAI API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RoleRecommendationError("OpenAI API request timed out.") from exc

        output_text = _extract_output_text(response_payload)
        suggestion = parse_refined_manual_role(
            output_text,
            fallback_name=intent_name,
            fallback_description=str(rough_description or ""),
        )
        if suggestion is None:
            raise RoleRecommendationError("AI did not return a usable refined role.")
        return suggestion

    def translate_role_name(
        self,
        role_name: str,
        target_language: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")
        source_name = str(role_name or "").strip()
        if not source_name:
            return ""

        language = "zh" if str(target_language or "").strip().lower().startswith("zh") else "en"
        user_prompt = "\n".join(
            [
                f"Source role title: {source_name}",
                f"Target language: {language}",
                "Return strict JSON only.",
            ]
        )
        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": ROLE_NAME_TRANSLATE_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.resolve_api_url(api_base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RoleRecommendationError(f"OpenAI API request failed: HTTP {exc.code}. {detail}") from exc
        except urllib.error.URLError as exc:
            raise RoleRecommendationError(f"Unable to connect OpenAI API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RoleRecommendationError("OpenAI API request timed out.") from exc

        output_text = _extract_output_text(response_payload)
        json_text = _extract_json_object_text(output_text)
        try:
            payload_json = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise RoleRecommendationError("AI role-name translation response is not parseable JSON.") from exc
        if not isinstance(payload_json, dict):
            raise RoleRecommendationError("AI role-name translation response has invalid format.")
        translated = str(payload_json.get("name_translated") or "").strip()
        if not translated:
            raise RoleRecommendationError("AI role-name translation response did not include name_translated.")
        return translated

    def translate_description(
        self,
        role_name: str,
        source_description: str,
        target_language: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        if not settings.api_key.strip():
            raise RoleRecommendationError("OpenAI API Key is required.")
        source_text = str(source_description or "").strip()
        if not source_text:
            return ""

        language = "zh" if str(target_language or "").strip().lower().startswith("zh") else "en"
        user_prompt = "\n".join(
            [
                f"Role name: {role_name.strip() or 'N/A'}",
                "Source description:",
                source_text,
                f"Target language: {language}",
                "Return strict JSON only.",
            ]
        )
        payload = {
            "model": settings.model.strip() or "gpt-5",
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": TRANSLATE_PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        request = urllib.request.Request(
            self.resolve_api_url(api_base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RoleRecommendationError(f"OpenAI API request failed: HTTP {exc.code}. {detail}") from exc
        except urllib.error.URLError as exc:
            raise RoleRecommendationError(f"Unable to connect OpenAI API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RoleRecommendationError("OpenAI API request timed out.") from exc

        output_text = _extract_output_text(response_payload)
        json_text = _extract_json_object_text(output_text)
        try:
            payload_json = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise RoleRecommendationError("AI translation response is not parseable JSON.") from exc
        if not isinstance(payload_json, dict):
            raise RoleRecommendationError("AI translation response has invalid format.")
        translated = str(
            payload_json.get("description_translated")
            or payload_json.get("description_en")
            or payload_json.get("description_zh")
            or ""
        ).strip()
        if not translated:
            raise RoleRecommendationError("AI translation response did not include translated text.")
        return translated

    def translate_description_to_english(
        self,
        role_name: str,
        description_zh: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        return self.translate_description(
            role_name=role_name,
            source_description=description_zh,
            target_language="en",
            settings=settings,
            api_base_url=api_base_url,
        )

    def translate_description_to_chinese(
        self,
        role_name: str,
        description_en: str,
        settings: OpenAISettings,
        api_base_url: str = "",
    ) -> str:
        return self.translate_description(
            role_name=role_name,
            source_description=description_en,
            target_language="zh",
            settings=settings,
            api_base_url=api_base_url,
        )

