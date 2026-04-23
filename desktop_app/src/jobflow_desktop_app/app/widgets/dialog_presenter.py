from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


class QtDialogPresenter:
    def information(self, parent: QWidget, title: str, message: str) -> None:
        QMessageBox.information(parent, title, message)

    def warning(self, parent: QWidget, title: str, message: str) -> None:
        QMessageBox.warning(parent, title, message)

    def confirm(self, parent: QWidget, title: str, message: str) -> bool:
        return QMessageBox.question(parent, title, message) == QMessageBox.Yes
