from __future__ import annotations

import sys

from .bootstrap import bootstrap_application


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
    return run_desktop_app(context)


if __name__ == "__main__":
    raise SystemExit(main())
