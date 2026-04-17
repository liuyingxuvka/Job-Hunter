from __future__ import annotations

import sys
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

class _BackgroundTaskWorker(QObject):
    finished = Signal(object, object)

    def __init__(self, task: Callable[[], Any]) -> None:
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            result = self._task()
            self.finished.emit(result, None)
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.finished.emit(None, exc)


class _BusyTaskRelay(QObject):
    def __init__(self, callback: Callable[[Any, object], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._callback = callback

    @Slot(object, object)
    def on_finished(self, result: Any, error: object) -> None:
        self._callback(result, error)


def _background_thread_parent() -> QObject | None:
    app = QApplication.instance()
    return app if isinstance(app, QObject) else None


def _report_unhandled_ui_exception(owner: QWidget, title: str, exc: BaseException) -> None:
    try:
        sys.excepthook(type(exc), exc, exc.__traceback__)
    except Exception:
        pass
    language = "en" if getattr(owner, "ui_language", "zh") == "en" else "zh"
    detail = str(exc or "").strip() or exc.__class__.__name__
    try:
        QMessageBox.warning(
            owner,
            title,
            _t(
                language,
                f"界面处理时发生未处理异常：{detail}\n\n日志已经写入 crash.log，请把这次操作步骤告诉我继续定位。",
                f"An unhandled UI error occurred: {detail}\n\nThe details were written to crash.log. Please share the exact steps so this can be diagnosed further.",
            ),
        )
    except Exception:
        pass


def run_busy_task(
    owner: QWidget,
    *,
    title: str,
    message: str,
    task: Callable[[], Any],
    on_success: Callable[[Any], None],
    on_error: Callable[[Exception], None] | None = None,
    on_finally: Callable[[], None] | None = None,
    timeout_ms: int | None = None,
    show_dialog: bool = True,
) -> bool:
    running_thread = getattr(owner, "_busy_task_thread", None)
    if isinstance(running_thread, QThread) and running_thread.isRunning():
        return False

    dialog: QProgressDialog | None = None
    if show_dialog:
        dialog = QProgressDialog(message, "", 0, 0, owner)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.setCancelButton(None)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setRange(0, 0)
        dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()

    worker = _BackgroundTaskWorker(task)
    thread = QThread(_background_thread_parent())
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    completed = False
    timeout_timer: QTimer | None = None

    def _invoke_callback(callback: Callable[..., Any] | None, *args: Any) -> bool:
        if callback is None:
            return True
        try:
            callback(*args)
            return True
        except Exception as exc:
            _report_unhandled_ui_exception(owner, title, exc)
            return False

    def _finish(result: Any, error: object) -> None:
        nonlocal completed
        if completed:
            return
        completed = True
        if timeout_timer is not None:
            timeout_timer.stop()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        setattr(owner, "_busy_task_thread", None)
        setattr(owner, "_busy_task_worker", None)
        setattr(owner, "_busy_task_dialog", None)
        setattr(owner, "_busy_task_relay", None)
        if not _invoke_callback(on_finally):
            return
        if error is not None:
            if on_error is not None:
                if isinstance(error, Exception):
                    _invoke_callback(on_error, error)
                else:
                    _invoke_callback(on_error, RuntimeError(str(error)))
            return
        _invoke_callback(on_success, result)

    if timeout_ms is not None and timeout_ms > 0:
        timeout_timer = QTimer(owner)
        timeout_timer.setSingleShot(True)

        def _on_timeout() -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
            setattr(owner, "_busy_task_thread", None)
            setattr(owner, "_busy_task_worker", None)
            setattr(owner, "_busy_task_dialog", None)
            setattr(owner, "_busy_task_relay", None)
            if not _invoke_callback(on_finally):
                return
            if on_error is not None:
                _invoke_callback(
                    on_error,
                    RuntimeError("Operation timed out. Check network or API settings and retry.")
                )

        timeout_timer.timeout.connect(_on_timeout)
        timeout_timer.start(int(timeout_ms))

    relay = _BusyTaskRelay(_finish, owner)
    worker.finished.connect(relay.on_finished, Qt.QueuedConnection)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    setattr(owner, "_busy_task_dialog", dialog)
    setattr(owner, "_busy_task_worker", worker)
    setattr(owner, "_busy_task_thread", thread)
    setattr(owner, "_busy_task_relay", relay)
    thread.start()
    return True

