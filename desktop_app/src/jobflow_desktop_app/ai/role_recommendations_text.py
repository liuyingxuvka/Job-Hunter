from __future__ import annotations

import json
import re

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
    "工程师",
    "经理",
    "专家",
    "分析师",
    "顾问",
    "主管",
    "总监",
    "专员",
    "研究员",
)

CHINESE_ROLE_LEVEL_WORDS = (
    "高级",
    "资深",
    "初级",
    "助理",
    "主任",
    "首席",
    "实习",
)

CHINESE_BROAD_ROLE_QUALIFIERS = {
    "软件",
    "系统",
    "硬件",
    "机械",
    "电气",
    "电子",
    "数据",
    "产品",
    "项目",
    "测试",
    "质量",
    "业务",
    "技术",
    "研发",
    "运营",
    "工艺",
    "过程",
    "控制",
    "仿真",
    "算法",
    "安全",
    "研究",
    "平台",
    "应用",
    "解决方案",
    "架构",
    "服务",
    "市场",
    "供应链",
    "能源",
    "汽车",
    "电池",
    "制造",
    "化工",
    "材料",
    "嵌入式",
}


def decode_bilingual_description(raw_text: str) -> tuple[str, str]:
    text = str(raw_text or "").strip()
    if not text:
        return "", ""
    try:
        payload = json.loads(text)
    except Exception:
        return _decode_single_language_fallback_text(text)
    if not isinstance(payload, dict):
        return _decode_single_language_fallback_text(text)

    zh = str(payload.get("zh", "")).strip()
    en = str(payload.get("en", "")).strip()
    if zh or en:
        return zh, en
    return _decode_single_language_fallback_text(text)


def _decode_single_language_fallback_text(text: str) -> tuple[str, str]:
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
        return _decode_single_language_fallback_text(fallback)
    try:
        payload = json.loads(text)
    except Exception:
        return _decode_single_language_fallback_text(text)
    if not isinstance(payload, dict):
        return _decode_single_language_fallback_text(text)

    zh = str(payload.get("zh", "")).strip()
    en = str(payload.get("en", "")).strip()
    if zh or en:
        return zh, en
    if fallback:
        return _decode_single_language_fallback_text(fallback)
    return _decode_single_language_fallback_text(text)


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
    return ""


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

    compact_cn = re.sub(r"\s+", "", text)
    if compact_cn and re.fullmatch(r"[\u3400-\u9fff]+", compact_cn):
        for generic_cn in GENERIC_ROLE_WORDS_CN:
            if compact_cn == generic_cn:
                return True
            if compact_cn.endswith(generic_cn):
                prefix = compact_cn[: -len(generic_cn)]
                if not prefix:
                    return True
                if prefix in CHINESE_ROLE_LEVEL_WORDS:
                    return True
                if prefix in CHINESE_BROAD_ROLE_QUALIFIERS:
                    return True
                if any(prefix == f"{level}{qualifier}" for level in CHINESE_ROLE_LEVEL_WORDS for qualifier in CHINESE_BROAD_ROLE_QUALIFIERS):
                    return True
    return False


__all__ = [
    "decode_bilingual_description",
    "decode_bilingual_role_name",
    "description_for_prompt",
    "description_query_lines",
    "encode_bilingual_description",
    "encode_bilingual_role_name",
    "infer_scope_profile",
    "is_generic_role_name",
    "role_name_query_lines",
    "select_bilingual_description",
    "select_bilingual_role_name",
]
