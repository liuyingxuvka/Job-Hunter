from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QComboBox


def status_palette(status_code: str) -> dict[str, str]:
    palette = {
        "pending": {"fg": "#4B5563", "bg": "#F3F4F6", "border": "#9CA3AF"},
        "focus": {"fg": "#1D4ED8", "bg": "#DBEAFE", "border": "#3B82F6"},
        "applied": {"fg": "#166534", "bg": "#DCFCE7", "border": "#22C55E"},
        "offered": {"fg": "#92400E", "bg": "#FEF3C7", "border": "#F59E0B"},
        "rejected": {"fg": "#991B1B", "bg": "#FEE2E2", "border": "#EF4444"},
        "dropped": {"fg": "#334155", "bg": "#E2E8F0", "border": "#94A3B8"},
    }
    return palette.get(status_code, palette["pending"])


def decorate_status_combo_items(combo: QComboBox, status_codes: tuple[str, ...]) -> None:
    model = combo.model()
    if model is None:
        return
    for index, status_code in enumerate(status_codes):
        color_pack = status_palette(status_code)
        model_index = model.index(index, 0)
        model.setData(model_index, QBrush(QColor(color_pack["fg"])), Qt.ForegroundRole)
        model.setData(model_index, QBrush(QColor(color_pack["bg"])), Qt.BackgroundRole)


def apply_status_combo_style(combo: QComboBox, status_code: str) -> None:
    color_pack = status_palette(status_code)
    combo.setStyleSheet(
        f"""
        QComboBox {{
            color: {color_pack["fg"]};
            background-color: {color_pack["bg"]};
            border: 1px solid {color_pack["border"]};
            border-radius: 6px;
            padding: 2px 8px;
            min-height: 24px;
        }}
        QComboBox QAbstractItemView {{
            color: #0F172A;
            background: #FFFFFF;
            border: 1px solid {color_pack["border"]};
            selection-background-color: {color_pack["bg"]};
            selection-color: {color_pack["fg"]};
        }}
        """
    )


def normalize_status_code(value: str, status_codes: tuple[str, ...]) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in status_codes:
        return normalized
    map_by_label = {
        "待定": "pending",
        "pending": "pending",
        "重点": "focus",
        "focus": "focus",
        "已投递": "applied",
        "applied": "applied",
        "已得到 offer": "offered",
        "已得到offer": "offered",
        "已得到 Offer": "offered",
        "offer received": "offered",
        "offered": "offered",
        "已被拒绝": "rejected",
        "rejected": "rejected",
        "已放弃": "dropped",
        "dropped": "dropped",
    }
    return map_by_label.get(str(value).strip()) or map_by_label.get(normalized)


__all__ = [
    "apply_status_combo_style",
    "decorate_status_combo_items",
    "normalize_status_code",
    "status_palette",
]
