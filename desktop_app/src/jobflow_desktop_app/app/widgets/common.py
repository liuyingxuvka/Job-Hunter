from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

def styled_button(text: str, variant: str = "secondary") -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", variant)
    return button


def make_card() -> QFrame:
    frame = QFrame()
    frame.setProperty("card", True)
    frame.setFrameShape(QFrame.StyledPanel)
    return frame


def make_scroll_area(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


def make_page_title(title: str, subtitle: str) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    title_label = QLabel(title)
    title_label.setObjectName("PageTitle")
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("PageSubtitle")
    subtitle_label.setWordWrap(True)

    layout.addWidget(title_label)
    layout.addWidget(subtitle_label)
    return wrapper


def _t(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh

