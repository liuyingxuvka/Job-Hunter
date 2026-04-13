from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


APP_STYLESHEET = """
QWidget {
  background: #edf2f7;
  color: #1f2933;
  font-family: "Segoe UI", "Microsoft YaHei";
  font-size: 13px;
}

QLabel {
  background: transparent;
}

QMainWindow {
  background: #edf2f7;
}

QStatusBar {
  background: #e4ebf2;
  color: #52606d;
  border-top: 1px solid #d3dbe3;
}

QListWidget {
  background: #ffffff;
  border: 1px solid #d8e1ea;
  border-radius: 12px;
  outline: none;
  padding: 6px;
}

QListWidget::item {
  border-radius: 10px;
  padding: 10px 12px;
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
  min-height: 46px;
}

QListWidget#TargetRoleList::item {
  min-height: 46px;
}

QListWidget#TargetRoleList::indicator {
  width: 14px;
  height: 14px;
}

QListWidget#TargetRoleList::indicator:unchecked,
QListWidget#TargetRoleList::indicator:unchecked:selected {
  image: url(assets/icons/checkbox_unchecked.svg);
}

QListWidget#TargetRoleList::indicator:checked,
QListWidget#TargetRoleList::indicator:checked:selected {
  image: url(assets/icons/checkbox_checked_black.svg);
}

QFrame[card="true"] {
  background: #ffffff;
  border: 1px solid #d8e1ea;
  border-radius: 16px;
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
  font-size: 20px;
  font-weight: 700;
  color: #102a43;
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
  background: #f8fafc;
  border: 1px solid #cfd8e3;
  border-radius: 10px;
  padding: 8px 10px;
}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QTableWidget:focus {
  border: 1px solid #1f7a8c;
}

QComboBox::drop-down {
  border: none;
  width: 26px;
}

QPushButton {
  border-radius: 10px;
  padding: 8px 12px;
  border: 1px solid #bfd0dd;
  background: #ffffff;
  color: #102a43;
  font-weight: 600;
}

QPushButton:hover {
  background: #f3f7fb;
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
  background: #f8fafc;
  color: #334e68;
  border: 1px solid #d8e1ea;
  padding: 8px 12px;
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
  background: #e9eff5;
  color: #334e68;
  border: none;
  border-right: 1px solid #d8e1ea;
  border-bottom: 1px solid #d8e1ea;
  padding: 8px;
  font-weight: 600;
}

QCheckBox {
  spacing: 8px;
}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#edf2f7"))
    palette.setColor(QPalette.WindowText, QColor("#1f2933"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f8fafc"))
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipText, QColor("#102a43"))
    palette.setColor(QPalette.Text, QColor("#1f2933"))
    palette.setColor(QPalette.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ButtonText, QColor("#102a43"))
    palette.setColor(QPalette.Highlight, QColor("#1f7a8c"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLESHEET)
