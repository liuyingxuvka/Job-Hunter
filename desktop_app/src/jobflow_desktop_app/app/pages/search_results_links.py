from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from ..widgets.common import _t


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def link_host_text(url: str, fallback: str) -> str:
    text = str(url or "").strip()
    if not text:
        return fallback
    parsed = urlparse(text)
    host = str(parsed.netloc or "").replace("www.", "").strip()
    if host:
        return host if len(host) <= 32 else f"{host[:31]}…"
    path = str(parsed.path or "").strip("/")
    if path:
        tail = path.rsplit("/", 1)[-1].strip()
        if tail:
            return tail if len(tail) <= 32 else f"{tail[:31]}…"
    return fallback


def make_link_widget(url: str, label: str) -> QLabel:
    safe_url = str(url or "").strip()
    safe_label = str(label or "").strip() or safe_url
    if safe_url:
        widget = QLabel(f'<a href="{escape(safe_url, quote=True)}">{escape(safe_label)}</a>')
        widget.setOpenExternalLinks(True)
        widget.setTextFormat(Qt.RichText)
    else:
        widget = QLabel(safe_label)
        widget.setTextFormat(Qt.PlainText)
    widget.setTextInteractionFlags(Qt.TextBrowserInteraction)
    widget.setAlignment(Qt.AlignCenter)
    widget.setToolTip(safe_url or safe_label)
    return widget


def make_link_cell(ui_language: str, url: str, prefer_detail_label: bool) -> QLabel:
    if not str(url or "").strip():
        return make_link_widget("", "-")
    fallback_label = _t(ui_language, "打开链接", "Open link")
    label = link_host_text(url, fallback_label)
    if prefer_detail_label:
        label = _t(ui_language, f"详情 · {label}", f"Details · {label}")
    return make_link_widget(url, label)


def job_link_details(job: Any) -> tuple[str, str, str]:
    detail_url = first_non_empty(
        getattr(job, "source_url", ""),
        getattr(job, "sourceUrl", ""),
        getattr(job, "original_url", ""),
        getattr(job, "originalUrl", ""),
        getattr(job, "canonicalUrl", ""),
        getattr(job, "url", ""),
    )
    final_url = first_non_empty(
        getattr(job, "final_url", ""),
        getattr(job, "finalUrl", ""),
        getattr(job, "apply_url", ""),
        getattr(job, "applyUrl", ""),
        getattr(job, "apply_link", ""),
        getattr(job, "applyLink", ""),
        getattr(job, "application_url", ""),
        getattr(job, "applicationUrl", ""),
        getattr(job, "canonicalUrl", ""),
        getattr(job, "url", ""),
        detail_url,
    )
    if not detail_url:
        detail_url = final_url
    if not final_url:
        final_url = detail_url
    link_status = first_non_empty(
        getattr(job, "link_status", ""),
        getattr(job, "linkStatus", ""),
        getattr(job, "final_url_status", ""),
        getattr(job, "finalUrlStatus", ""),
        getattr(job, "apply_url_status", ""),
        getattr(job, "applyUrlStatus", ""),
        getattr(job, "source_url_status", ""),
        getattr(job, "sourceUrlStatus", ""),
        getattr(job, "url_status", ""),
        getattr(job, "urlStatus", ""),
        getattr(job, "link_state", ""),
        getattr(job, "linkState", ""),
        getattr(job, "link_state_cn", ""),
        getattr(job, "linkStateCn", ""),
        getattr(job, "link_state_label", ""),
        getattr(job, "linkStateLabel", ""),
    )
    return detail_url, final_url, link_status


__all__ = [
    "first_non_empty",
    "job_link_details",
    "link_host_text",
    "make_link_cell",
    "make_link_widget",
]
