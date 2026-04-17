from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QFrame, QLabel, QProgressDialog, QVBoxLayout, QWidget

from . import search_results_controls_state


@dataclass
class SearchSessionState:
    phase: str = "idle"
    owner_candidate_id: int | None = None
    worker_thread: QThread | None = None
    cancel_event: threading.Event | None = None
    started_monotonic: float | None = None
    duration_seconds: int = 0
    stop_requested: bool = False
    queued_restart: bool = False
    queued_restart_candidate_id: int | None = None
    queued_restart_duration_label: str = ""

    def is_worker_running(self) -> bool:
        return isinstance(self.worker_thread, QThread) and self.worker_thread.isRunning()

    def is_active(self) -> bool:
        return self.normalized_phase() != "idle" and self.is_worker_running()

    def owns(self, candidate_id: int | None) -> bool:
        return candidate_id is not None and self.owner_candidate_id == candidate_id and self.is_active()

    def queued_for(self, candidate_id: int | None) -> bool:
        return (
            self.queued_restart
            and candidate_id is not None
            and self.queued_restart_candidate_id == candidate_id
        )

    def normalized_phase(self) -> str:
        return str(self.phase or "idle").strip() or "idle"


def begin_search_session(
    page,
    candidate_id: int,
    *,
    cancel_event: threading.Event,
    duration_seconds: int,
    started_monotonic: float,
    preserve_started: bool = False,
) -> None:
    session = page._search_session
    session.owner_candidate_id = int(candidate_id)
    session.cancel_event = cancel_event
    session.duration_seconds = max(0, int(duration_seconds))
    session.stop_requested = False
    if not preserve_started or session.started_monotonic is None:
        session.started_monotonic = started_monotonic
    clear_queued_search_restart(page)
    set_search_phase(page, "running")


def request_search_stop(page) -> None:
    page._search_session.stop_requested = True
    set_search_phase(page, "stopping")


def queue_search_restart_state(
    page,
    candidate_id: int,
    *,
    duration_seconds: int,
    duration_label: str,
    started_monotonic: float,
) -> None:
    session = page._search_session
    session.queued_restart = True
    session.queued_restart_candidate_id = int(candidate_id)
    session.queued_restart_duration_label = str(duration_label or "")
    session.duration_seconds = max(0, int(duration_seconds))
    session.started_monotonic = started_monotonic
    set_search_phase(page, "queued_restart")


def clear_queued_search_restart(page) -> None:
    page._search_session.queued_restart = False
    page._search_session.queued_restart_candidate_id = None
    page._search_session.queued_restart_duration_label = ""


def set_search_phase(page, phase: str) -> None:
    page._search_session.phase = str(phase or "idle").strip() or "idle"


def reset_search_countdown_state(page) -> None:
    page._search_session.started_monotonic = None
    page._search_session.duration_seconds = 0
    page._search_countdown_timer.stop()
    set_search_countdown_seconds(page, 0)


def reset_search_runtime_state(page) -> None:
    reset_search_countdown_state(page)
    page._search_session.stop_requested = False
    page._search_session.cancel_event = None
    page._search_session.owner_candidate_id = None
    page._search_session.worker_thread = None
    set_search_phase(page, "idle")
    clear_queued_search_restart(page)


def selected_search_duration_seconds(page) -> int:
    return search_results_controls_state.selected_search_duration_seconds(
        page.search_duration_combo.currentData(),
    )


def selected_search_duration_label(page) -> str:
    return search_results_controls_state.selected_search_duration_label(
        page.ui_language,
        page.search_duration_combo.currentText(),
    )


def set_search_countdown_seconds(page, seconds: int) -> None:
    page.search_countdown_value_label.setText(page._format_countdown_text(seconds))


def refresh_search_countdown(page) -> None:
    session = page._search_session
    remaining = search_results_controls_state.remaining_countdown_seconds(
        owns_running_search=session.owns(page.current_candidate_id),
        owns_queued_restart=session.queued_for(page.current_candidate_id),
        started_monotonic=session.started_monotonic,
        duration_seconds=session.duration_seconds,
    )
    set_search_countdown_seconds(page, remaining)


def set_search_button_running(page, running: bool, *, queued: bool = False) -> None:
    page.refresh_button.setText(
        search_results_controls_state.search_button_text(
            page.ui_language,
            running=running,
            queued=queued,
        )
    )
    page.refresh_button.setEnabled(True)


def set_busy_task_message(page, text: str) -> None:
    dialog = getattr(page, "_busy_task_dialog", None)
    if isinstance(dialog, QProgressDialog) and str(text or "").strip():
        dialog.setLabelText(text)


def hide_notification_toast(page) -> None:
    toast = page._notification_toast
    page._notification_toast = None
    if isinstance(toast, QFrame):
        toast.hide()
        toast.deleteLater()


def show_notification_toast(page, text: str, level: str = "info", duration_ms: int = 5000) -> None:
    message = str(text or "").strip()
    if not message:
        return
    page._notification_timer.stop()
    hide_notification_toast(page)

    host = page.window() if isinstance(page.window(), QWidget) else page
    background = "#102a43"
    border = "#1f7a8c"
    if level == "success":
        background = "#0f5132"
        border = "#198754"
    elif level == "warning":
        background = "#5c3900"
        border = "#f59f00"
    elif level == "error":
        background = "#58151c"
        border = "#dc3545"

    toast = QFrame(host)
    toast.setFrameShape(QFrame.StyledPanel)
    toast.setStyleSheet(
        f"background: {background}; border: 1px solid {border}; border-radius: 12px; color: #f8fafc;"
    )
    layout = QVBoxLayout(toast)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(0)
    label = QLabel(message, toast)
    label.setWordWrap(True)
    label.setStyleSheet("color: #f8fafc; background: transparent;")
    layout.addWidget(label)
    toast.setMaximumWidth(420)
    toast.adjustSize()
    host_rect = host.rect()
    x = max(16, host_rect.width() - toast.width() - 20)
    y = max(16, host_rect.height() - toast.height() - 28)
    toast.move(x, y)
    toast.show()
    toast.raise_()
    page._notification_toast = toast
    page._notification_timer.start(max(1500, int(duration_ms)))

    if hasattr(host, "statusBar"):
        try:
            host.statusBar().showMessage(message, max(1500, int(duration_ms)))
        except Exception:
            pass


def shutdown_background_work(page, wait_ms: int = 8000) -> None:
    page._stop_live_results_updates()
    page._search_countdown_timer.stop()
    page._notification_timer.stop()
    hide_notification_toast(page)
    cancel_event = page._search_session.cancel_event
    if cancel_event is not None:
        cancel_event.set()
    running_thread = page._search_session.worker_thread
    if isinstance(running_thread, QThread) and running_thread.isRunning():
        running_thread.wait(max(0, int(wait_ms)))
    page._search_session.cancel_event = None
    page._search_session.worker_thread = None
    clear_queued_search_restart(page)


__all__ = [
    "begin_search_session",
    "SearchSessionState",
    "clear_queued_search_restart",
    "hide_notification_toast",
    "queue_search_restart_state",
    "refresh_search_countdown",
    "reset_search_countdown_state",
    "reset_search_runtime_state",
    "request_search_stop",
    "selected_search_duration_label",
    "selected_search_duration_seconds",
    "set_search_phase",
    "set_busy_task_message",
    "set_search_button_running",
    "set_search_countdown_seconds",
    "show_notification_toast",
    "shutdown_background_work",
]
