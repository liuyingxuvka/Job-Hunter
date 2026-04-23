from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QListWidget, QStyle, QStyleOptionViewItem, QStyledItemDelegate

from ..theme import UI_COLORS


def is_checked_state(value) -> bool:
    try:
        normalized = getattr(value, "value", value)
        return int(normalized) == Qt.Checked.value
    except (TypeError, ValueError):
        return False


class TargetRoleListDelegate(QStyledItemDelegate):
    """Compact, explicit checkbox rendering for target-role rows."""

    BOX_SIZE = 16
    LEFT_PADDING = 12
    TEXT_GAP = 10
    ROW_MIN_HEIGHT = 30
    CHECKBOX_HIT_PADDING = 14

    @staticmethod
    def row_colors(*, is_selected: bool, is_checked: bool, is_enabled: bool) -> dict[str, QColor | None]:
        if not is_enabled:
            return {
                "bg": QColor(UI_COLORS["bg_subtle"]) if is_selected else None,
                "border": QColor(UI_COLORS["border"]) if is_selected else None,
                "text": QColor(UI_COLORS["border_muted"]),
            }
        if is_selected:
            return {
                "bg": QColor(UI_COLORS["accent_success"]),
                "border": QColor(UI_COLORS["accent_success_hover"]),
                "text": QColor(UI_COLORS["text_inverse"]),
            }
        return {
            "bg": None,
            "border": None,
            "text": QColor(UI_COLORS["text_primary"]),
        }

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), self.ROW_MIN_HEIGHT))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        row_rect = option.rect.adjusted(4, 1, -4, -1)
        is_selected = bool(option.state & QStyle.State_Selected)
        is_enabled = bool(option.state & QStyle.State_Enabled)
        text = str(index.data(Qt.DisplayRole) or "")
        check_state = index.data(Qt.CheckStateRole)
        is_checked = is_checked_state(check_state)
        colors = self.row_colors(is_selected=is_selected, is_checked=is_checked, is_enabled=is_enabled)

        if colors["bg"] is not None:
            border = colors["border"] if colors["border"] is not None else Qt.NoPen
            painter.setPen(QPen(border, 1.2) if isinstance(border, QColor) else Qt.NoPen)
            painter.setBrush(colors["bg"])
            painter.drawRoundedRect(row_rect, 8, 8)

        checkbox_rect = self.checkbox_rect(option)
        box_fill = QColor(UI_COLORS["bg_card"])
        box_border = QColor(UI_COLORS["text_primary"] if is_checked else UI_COLORS["text_muted"])
        if not is_enabled:
            box_fill = QColor(UI_COLORS["bg_subtle"])
            box_border = QColor(UI_COLORS["border_muted"])

        painter.setPen(QPen(box_border, 1.5))
        painter.setBrush(box_fill)
        painter.drawRect(checkbox_rect)

        if is_checked:
            tick_pen = QPen(QColor(UI_COLORS["text_primary"]), 2.6)
            tick_pen.setCapStyle(Qt.RoundCap)
            tick_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(tick_pen)
            p1 = QPoint(checkbox_rect.left() + 3, checkbox_rect.center().y())
            p2 = QPoint(checkbox_rect.left() + 7, checkbox_rect.bottom() - 4)
            p3 = QPoint(checkbox_rect.right() - 2, checkbox_rect.top() + 4)
            painter.drawLine(p1, p2)
            painter.drawLine(p2, p3)

        text_rect = QRect(
            checkbox_rect.right() + self.TEXT_GAP,
            row_rect.top(),
            max(0, row_rect.right() - checkbox_rect.right() - self.TEXT_GAP - 8),
            row_rect.height(),
        )
        text_color = colors["text"] if isinstance(colors["text"], QColor) else QColor(UI_COLORS["text_primary"])
        painter.setPen(text_color)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)

        painter.restore()

    def editorEvent(self, event, model, option: QStyleOptionViewItem, index) -> bool:  # type: ignore[override]
        return super().editorEvent(event, model, option, index)

    def checkbox_rect(self, option: QStyleOptionViewItem) -> QRect:
        row_rect = option.rect.adjusted(4, 1, -4, -1)
        y = row_rect.center().y() - self.BOX_SIZE // 2
        return QRect(row_rect.left() + self.LEFT_PADDING, y, self.BOX_SIZE, self.BOX_SIZE)


class TargetRoleListWidget(QListWidget):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._swallow_checkbox_release = False

    def _checkbox_hit(self, viewport_pos: QPoint):
        item = self.itemAt(viewport_pos)
        delegate = self.itemDelegate()
        if item is None or not isinstance(delegate, TargetRoleListDelegate):
            return None, None
        option = QStyleOptionViewItem()
        option.rect = self.visualItemRect(item)
        checkbox_rect = delegate.checkbox_rect(option)
        hit_rect = checkbox_rect.adjusted(
            -delegate.CHECKBOX_HIT_PADDING,
            -4,
            delegate.CHECKBOX_HIT_PADDING,
            4,
        )
        if hit_rect.contains(viewport_pos):
            return item, delegate
        return None, delegate

    def viewportEvent(self, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            item, _delegate = self._checkbox_hit(event.position().toPoint())
            if item is not None:
                self.setCurrentItem(item)
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
                self._swallow_checkbox_release = True
                event.accept()
                return True
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._swallow_checkbox_release:
                self._swallow_checkbox_release = False
                event.accept()
                return True
        return super().viewportEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Space:
            item = self.currentItem()
            if item is not None:
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
                event.accept()
                return
        super().keyPressEvent(event)
