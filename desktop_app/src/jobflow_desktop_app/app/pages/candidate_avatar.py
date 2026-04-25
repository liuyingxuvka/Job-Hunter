from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ..theme import UI_COLORS


def candidate_avatar_icon() -> QIcon:
    icon = QIcon()
    icon.addPixmap(candidate_avatar_pixmap(selected=False), QIcon.Normal)
    icon.addPixmap(candidate_avatar_pixmap(selected=True), QIcon.Selected)
    return icon


def candidate_avatar_pixmap(*, selected: bool, size: int = 34) -> QPixmap:
    if selected:
        return draw_candidate_avatar(
            stroke=UI_COLORS["text_inverse"],
            fill=QColor(255, 255, 255, 42),
            size=size,
        )
    return draw_candidate_avatar(
        stroke=UI_COLORS["accent_secondary"],
        fill="#e8f1f7",
        size=size,
    )


def draw_candidate_avatar(*, stroke: str, fill: str | QColor, size: int = 34) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    if size != 34:
        scale = size / 34
        painter.scale(scale, scale)
    painter.setPen(QPen(QColor(stroke), 1.6))
    painter.setBrush(QColor(fill) if isinstance(fill, str) else fill)
    painter.drawEllipse(2, 2, 30, 30)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(12, 8, 10, 10)
    painter.drawArc(8, 17, 18, 12, 20 * 16, 140 * 16)
    painter.end()
    return pixmap
