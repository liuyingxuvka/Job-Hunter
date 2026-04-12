from __future__ import annotations

from datetime import datetime
import sys
import threading
import traceback

from .bootstrap import bootstrap_application


def _install_exception_logging(log_path: str) -> None:
    target = str(log_path or "").strip()
    if not target:
        return

    def _append_log(title: str, lines: list[str]) -> None:
        timestamp = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        payload = [f"[{timestamp}] {title}", *lines, ""]
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write("\n".join(payload))
        except Exception:
            pass

    def _handle_exception(exc_type: type[BaseException], exc_value: BaseException, exc_traceback: object) -> None:
        formatted = traceback.format_exception(exc_type, exc_value, exc_traceback)
        _append_log("Unhandled exception", [line.rstrip("\n") for line in formatted])
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
        formatted = traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        thread_name = getattr(args.thread, "name", "unknown")
        _append_log(
            f"Unhandled thread exception ({thread_name})",
            [line.rstrip("\n") for line in formatted],
        )
        threading.__excepthook__(args)

    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception


def main() -> int:
    try:
        from .app.main_window import run_desktop_app
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            message = (
                "PySide6 is not installed.\n"
                "Create a local virtual environment and run:\n"
                "  python -m pip install -e .\n"
            )
            print(message)
            return 1
        raise

    context = bootstrap_application()
    _install_exception_logging(str(context.paths.logs_dir / "crash.log"))
    return run_desktop_app(context)


if __name__ == "__main__":
    raise SystemExit(main())
