from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

UI_COLORS = {
    "bg_app": "#f3f6f8",
    "bg_statusbar": "#e8eef4",
    "bg_card": "#ffffff",
    "bg_subtle": "#f7f9fb",
    "bg_header": "#eef3f7",
    "border": "#dbe3eb",
    "border_muted": "#d1dbe5",
    "border_strong": "#bdcbd8",
    "text_primary": "#1f2933",
    "text_heading": "#102a43",
    "text_muted": "#52606d",
    "text_soft": "#d9e2ec",
    "text_inverse": "#ffffff",
    "accent_primary": "#102a43",
    "accent_secondary": "#1f7a8c",
    "accent_success": "#0f7b6c",
    "accent_success_hover": "#0d6c5f",
    "accent_warning": "#d97706",
    "accent_error": "#b42318",
    "selection_bg": "#e8f1f7",
}

RUNTIME_STATUS_DOT_COLORS = {
    "idle": "#94a3b8",
    "checking": UI_COLORS["accent_secondary"],
    "ready": UI_COLORS["accent_success"],
    "switched": UI_COLORS["accent_success"],
    "missing": UI_COLORS["accent_error"],
    "warning": UI_COLORS["accent_warning"],
    "invalid": UI_COLORS["accent_error"],
    "model_unverified": UI_COLORS["accent_warning"],
    "success": UI_COLORS["accent_success"],
    "error": UI_COLORS["accent_error"],
}

TOAST_LEVEL_COLORS = {
    "info": {"background": UI_COLORS["accent_primary"], "border": UI_COLORS["accent_secondary"]},
    "success": {"background": UI_COLORS["accent_success"], "border": UI_COLORS["accent_success_hover"]},
    "warning": {"background": "#8d5f00", "border": UI_COLORS["accent_warning"]},
    "error": {"background": "#7a1f1f", "border": UI_COLORS["accent_error"]},
}

REVIEW_STATUS_COLORS = {
    "pending": {"fg": "#334155", "bg": "#FFFFFF", "border": "#94A3B8"},
    "focus": {"fg": "#1D4ED8", "bg": "#DBEAFE", "border": "#2563EB"},
    "applied": {"fg": "#166534", "bg": "#D1FAE5", "border": UI_COLORS["accent_success"]},
    "offered": {"fg": "#92400E", "bg": "#FEF3C7", "border": UI_COLORS["accent_warning"]},
    "rejected": {"fg": "#991B1B", "bg": "#FEE2E2", "border": "#DC2626"},
    "dropped": {"fg": "#334155", "bg": "#E5E7EB", "border": "#64748B"},
}


APP_STYLESHEET = """
QWidget {
  background: #f3f6f8;
  color: #1f2933;
  font-family: "Segoe UI", "Microsoft YaHei";
  font-size: 13px;
}

QWidget[transparentBg="true"] {
  background: transparent;
}

QLabel {
  background: transparent;
}

QMainWindow {
  background: #f3f6f8;
}

QStatusBar {
  background: #e8eef4;
  color: #52606d;
  border-top: 1px solid #d3dbe3;
}

QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
  background: transparent;
}

QListWidget {
  background: #ffffff;
  border: 1px solid #dbe3eb;
  border-radius: 14px;
  outline: none;
  padding: 6px;
}

QListWidget::item {
  border-radius: 10px;
  padding: 7px 10px;
  margin: 2px 0;
}

QListWidget::item:selected {
  background: #0f7b6c;
  color: #ffffff;
}

QListWidget#SidebarList {
  background: #102a43;
  border: none;
  border-radius: 14px;
  color: #d9e2ec;
  padding: 10px 8px;
}

QListWidget#SidebarList::item {
  border-radius: 12px;
  padding: 12px 14px;
}

QListWidget#SidebarList::item:selected {
  background: #1f7a8c;
  color: #ffffff;
}

QListWidget#EntityList::item {
  min-height: 44px;
  border: 1px solid transparent;
}

QListWidget#EntityList::item:selected {
  background: #0f7b6c;
  color: #ffffff;
  border: 1px solid #0f7b6c;
}

QListWidget#TargetRoleList {
  padding: 2px;
}

QListWidget#TargetRoleList::item {
  min-height: 24px;
  padding: 2px 8px;
  margin: 1px 0;
  border-radius: 8px;
}

QListWidget#TargetRoleList::item:selected {
  background: transparent;
  color: transparent;
}

QListWidget#CompactLocationList {
  padding: 4px;
  border-radius: 12px;
}

QListWidget#CompactLocationList::item {
  min-height: 22px;
  padding: 3px 8px;
  margin: 1px 0;
  border: 1px solid transparent;
  border-radius: 8px;
}

QListWidget#CompactLocationList::item:selected {
  background: #e8f1f7;
  color: #102a43;
  border: 1px solid #1f7a8c;
}

QFrame[card="true"] {
  background: #ffffff;
  border: 1px solid #dbe3eb;
  border-radius: 14px;
}

QFrame#WorkspaceHero {
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #102a43, stop:1 #1f7a8c);
  border: none;
}

QFrame#WorkspaceHero QWidget,
QFrame#WorkspaceHero QLabel {
  background: transparent;
}

QLabel#PageTitle {
  font-size: 19px;
  font-weight: 700;
  color: #102a43;
}

QLabel#SectionTitle {
  font-size: 17px;
  font-weight: 700;
  color: #102a43;
}

QLabel#SectionSubtitle {
  color: #52606d;
}

QLabel#FieldLabel {
  font-weight: 600;
  color: #102a43;
}

QLabel#InlineStatusLabel {
  color: #102a43;
  font-weight: 600;
}

QLabel#InlineMetaLabel {
  color: #52606d;
}

QLabel#InlineSuccessLabel {
  color: #0f7b6c;
  font-weight: 600;
}

QLabel#InlineWarningLabel {
  color: #d97706;
  font-weight: 600;
}

QLabel#InlineErrorLabel {
  color: #b42318;
  font-weight: 600;
}

QLabel#PageSubtitle, QLabel#MutedLabel {
  color: #52606d;
}

QLabel#HeroEyebrow {
  color: #d9e2ec;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 1px;
  text-transform: uppercase;
}

QLabel#HeroTitle {
  color: #ffffff;
  font-size: 24px;
  font-weight: 700;
}

QLabel#HeroMeta {
  color: #d9e2ec;
  font-size: 13px;
}

QLabel#StatTitle {
  color: #52606d;
  font-size: 12px;
}

QLabel#StatValue {
  color: #102a43;
  font-size: 28px;
  font-weight: 700;
}

QLineEdit, QPlainTextEdit, QComboBox, QTableWidget {
  background: #f7f9fb;
  border: 1px solid #d1dbe5;
  border-radius: 10px;
  padding: 6px 9px;
}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QTableWidget:focus {
  border: 1px solid #1f7a8c;
}

QComboBox::drop-down {
  border: none;
  width: 26px;
}

QPushButton {
  border-radius: 9px;
  padding: 6px 11px;
  border: 1px solid #bdcbd8;
  background: #ffffff;
  color: #102a43;
  font-weight: 600;
  min-height: 28px;
}

QPushButton:hover {
  background: #f3f7fb;
}

QPushButton:focus {
  border: 1px solid #1f7a8c;
}

QPushButton:disabled {
  background: #d9e2ec;
  color: #7b8794;
  border: 1px solid #c5d0db;
}

QPushButton[variant="primary"] {
  background: #0f7b6c;
  color: #ffffff;
  border: 1px solid #0f7b6c;
}

QPushButton[variant="primary"]:hover {
  background: #0d6c5f;
}

QPushButton[variant="primary"]:disabled {
  background: #d9e2ec;
  color: #7b8794;
  border: 1px solid #c5d0db;
}

QPushButton[variant="danger"] {
  background: #ffffff;
  color: #b42318;
  border: 1px solid #efb5af;
}

QPushButton[variant="danger"]:hover {
  background: #fff3f1;
}

QPushButton[variant="danger"]:disabled {
  background: #f3f4f6;
  color: #9aa5b1;
  border: 1px solid #d9e2ec;
}

QPushButton[variant="step"] {
  background: #f7f9fb;
  color: #334e68;
  border: 1px solid #dbe3eb;
  padding: 5px 10px;
  min-height: 28px;
}

QPushButton[variant="step"][activeStep="true"] {
  background: #102a43;
  color: #ffffff;
  border: 1px solid #102a43;
}

QPushButton[variant="hero"] {
  background: transparent;
  color: #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.65);
}

QPushButton[variant="hero"]:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
}

QTableWidget {
  gridline-color: #d8e1ea;
}

QHeaderView::section {
  background: #eef3f7;
  color: #334e68;
  border: none;
  border-right: 1px solid #dbe3eb;
  border-bottom: 1px solid #dbe3eb;
  padding: 7px;
  font-weight: 600;
}

QCheckBox {
  spacing: 8px;
}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f3f6f8"))
    palette.setColor(QPalette.WindowText, QColor("#1f2933"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f7f9fb"))
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipText, QColor("#102a43"))
    palette.setColor(QPalette.Text, QColor("#1f2933"))
    palette.setColor(QPalette.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ButtonText, QColor("#102a43"))
    palette.setColor(QPalette.Highlight, QColor("#1f7a8c"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLESHEET)


__all__ = [
    "APP_STYLESHEET",
    "REVIEW_STATUS_COLORS",
    "RUNTIME_STATUS_DOT_COLORS",
    "TOAST_LEVEL_COLORS",
    "UI_COLORS",
    "apply_theme",
]
