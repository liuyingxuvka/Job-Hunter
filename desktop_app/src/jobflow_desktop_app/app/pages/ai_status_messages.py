from __future__ import annotations

from ..widgets.common import _t


def compact_ai_blocking_issue(ui_language: str, level: str, message: str = "") -> str:
    """Short reason for disabled AI actions; detailed guidance stays in the header."""
    normalized_level = str(level or "").strip().lower()
    raw_message = str(message or "").strip()
    if normalized_level == "missing":
        return _t(ui_language, "AI Key 未配置", "AI key not configured")
    if normalized_level == "invalid":
        return _t(ui_language, "AI Key 校验失败", "AI key validation failed")
    if normalized_level == "model_unverified":
        return _t(ui_language, "模型未通过验证", "Model settings need re-check")
    if normalized_level == "checking":
        return _t(ui_language, "AI 正在验证，请稍候", "AI is checking; please wait")
    if normalized_level == "idle":
        return _t(ui_language, "AI 等待验证", "AI waiting for validation")
    if normalized_level in {"warning", "error"}:
        if raw_message and len(raw_message) <= 48:
            return raw_message
        return _t(ui_language, "AI 暂不可用，请检查设置", "AI unavailable; check Settings")
    if raw_message:
        return _first_sentence(raw_message, limit=48)
    return _t(ui_language, "AI 暂不可用，请检查设置", "AI unavailable; check Settings")


def _first_sentence(text: str, *, limit: int) -> str:
    raw = str(text or "").strip().replace("\n", " ")
    for separator in ("。", ". "):
        if separator in raw:
            raw = raw.split(separator, 1)[0].strip()
            break
    if len(raw) > limit:
        return raw[: max(0, limit - 1)].rstrip() + "…"
    return raw


__all__ = ["compact_ai_blocking_issue"]
