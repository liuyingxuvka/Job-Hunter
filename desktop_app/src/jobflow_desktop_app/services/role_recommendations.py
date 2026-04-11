from __future__ import annotations

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


def load_resume_excerpt(resume_path: str, max_chars: int = 6000) -> str:
    path = Path(resume_path.strip())
    if not resume_path.strip() or not path.exists() or not path.is_file():
        return ""

    suffix = path.suffix.lower()
    try:
        if suffix in {".md", ".txt"}:
            text = _read_text_resume(path)
        elif suffix == ".docx":
            text = _read_docx_resume(path)
        elif suffix == ".pdf":
            text = _read_pdf_resume(path)
        else:
            text = _read_text_resume(path)
    except Exception:
        return ""

    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n...[truncated]"
    return text


def build_role_recommendation_prompt(
    candidate: CandidateRecord,
    existing_roles: list[tuple[str, str]] | None = None,
) -> str:
    resume_excerpt = load_resume_excerpt(candidate.active_resume_path)
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
        "Notes:",
        candidate.notes.strip() or "N/A",
    ]

    if candidate.active_resume_path.strip():
        parts.extend(
            [
                f"Resume path: {candidate.active_resume_path.strip()}",
                "Resume excerpt:",
                resume_excerpt or "Resume text could not be read.",
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

        existing_names = {
            str(role[0] or "").strip().casefold()
            for role in (existing_roles or [])
            if isinstance(role, tuple) and len(role) == 2 and str(role[0] or "").strip()
        }

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

        resume_excerpt = load_resume_excerpt(candidate.active_resume_path, max_chars=3500)
        user_prompt_parts = [
            f"Candidate name: {candidate.name}",
            f"Current location: {candidate.base_location or 'N/A'}",
            "Preferred locations:",
            candidate.preferred_locations.strip() or "N/A",
            "Current target directions:",
            candidate.target_directions.strip() or "N/A",
            "Notes:",
            candidate.notes.strip() or "N/A",
            "User provided role intent:",
            f"- Role name: {intent_name}",
            f"- Rough description: {str(rough_description or '').strip() or 'N/A'}",
        ]
        if candidate.active_resume_path.strip():
            user_prompt_parts.extend(
                [
                    f"Resume path: {candidate.active_resume_path.strip()}",
                    "Resume excerpt:",
                    resume_excerpt or "Resume text could not be read.",
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

