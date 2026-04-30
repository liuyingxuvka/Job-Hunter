from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "daily_desktop_qa_preflight.py"
SPEC = importlib.util.spec_from_file_location("daily_desktop_qa_preflight", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


class DailyDesktopQaPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / "desktop_app" / "src" / "jobflow_desktop_app").mkdir(parents=True)
        (self.repo / "desktop_app" / "pyproject.toml").parent.mkdir(parents=True, exist_ok=True)
        self.pyproject = self.repo / "desktop_app" / "pyproject.toml"
        self.pyproject.write_text('version = "1.2.3"\n', encoding="utf-8")
        self.source = self.repo / "desktop_app" / "src" / "jobflow_desktop_app" / "main.py"
        self.source.write_text("print('hello')\n", encoding="utf-8")
        self.exe = self.repo / "runtime" / "local_app" / "current" / "Jobflow Desktop.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("exe", encoding="utf-8")
        self.profile = self.repo / "runtime" / "private" / "yingxu_profile_context.md"
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text(
            f"# Local Private Profile\n\n- EXE path: `{self.exe.as_posix()}`\n"
            f"- Packaged app database: `{(self.exe.parent / 'desktop_app/runtime/data/jobflow_desktop.db').as_posix()}`\n"
            "- Last local packaged replacement: yesterday\n"
            "- Replaced EXE SHA256: `OLD`\n"
            "- Last local release zip SHA256: `OLDZIP`\n",
            encoding="utf-8",
        )

    def _set_mtime(self, path: Path, timestamp: float) -> None:
        os.utime(path, (timestamp, timestamp))

    def test_fresh_package_uses_current_exe(self) -> None:
        now = datetime.fromtimestamp(2000, timezone.utc)
        self._set_mtime(self.source, 1000)
        self._set_mtime(self.pyproject, 1000)
        self._set_mtime(self.exe, 1500)

        decision = preflight.evaluate_preflight(
            self.repo,
            self.profile,
            now=now,
            stability_minutes=20,
            status_entries=[],
        )

        self.assertEqual(decision.status, "use_current")
        self.assertEqual(decision.package_state, "fresh")
        self.assertEqual(decision.local_change_state, "none")

    def test_stable_package_relevant_change_requires_rebuild(self) -> None:
        now = datetime.fromtimestamp(4000, timezone.utc)
        self._set_mtime(self.exe, 1000)
        self._set_mtime(self.pyproject, 1000)
        self._set_mtime(self.source, 2000)

        decision = preflight.evaluate_preflight(
            self.repo,
            self.profile,
            now=now,
            stability_minutes=20,
            status_entries=[("M", "desktop_app/src/jobflow_desktop_app/main.py")],
        )

        self.assertEqual(decision.status, "needs_rebuild")
        self.assertEqual(decision.package_state, "stale")
        self.assertEqual(decision.local_change_state, "stable")
        self.assertEqual(decision.package_relevant_changes, ["desktop_app/src/jobflow_desktop_app/main.py"])

    def test_recent_package_relevant_change_blocks_daily_run(self) -> None:
        now = datetime.fromtimestamp(4000, timezone.utc)
        self._set_mtime(self.exe, 1000)
        self._set_mtime(self.pyproject, 1000)
        self._set_mtime(self.source, 3950)

        decision = preflight.evaluate_preflight(
            self.repo,
            self.profile,
            now=now,
            stability_minutes=20,
            status_entries=[("M", "desktop_app/src/jobflow_desktop_app/main.py")],
        )

        self.assertEqual(decision.status, "blocked_in_progress")
        self.assertEqual(decision.local_change_state, "active")
        self.assertEqual(decision.active_change_paths, ["desktop_app/src/jobflow_desktop_app/main.py"])

    def test_unrelated_active_changes_do_not_force_rebuild(self) -> None:
        now = datetime.fromtimestamp(4000, timezone.utc)
        self._set_mtime(self.source, 1000)
        self._set_mtime(self.pyproject, 1000)
        self._set_mtime(self.exe, 2000)
        flowguard_file = self.repo / ".flowguard" / "model.py"
        flowguard_file.parent.mkdir()
        flowguard_file.write_text("x=1\n", encoding="utf-8")
        self._set_mtime(flowguard_file, 3990)

        decision = preflight.evaluate_preflight(
            self.repo,
            self.profile,
            now=now,
            stability_minutes=20,
            status_entries=[("??", ".flowguard/model.py")],
        )

        self.assertEqual(decision.status, "use_current")
        self.assertEqual(decision.local_change_state, "none")
        self.assertEqual(decision.ignored_change_paths, [".flowguard/model.py"])

    def test_profile_update_replaces_package_pointer_and_hashes(self) -> None:
        new_root = self.repo / "runtime" / "local_app" / "smoke-1" / "Job-Hunter-1.2.3-win64"
        new_exe = new_root / "Jobflow Desktop.exe"
        new_db = new_root / "desktop_app" / "runtime" / "data" / "jobflow_desktop.db"
        zip_path = self.repo / "dist" / "release" / "Job-Hunter-1.2.3-win64.zip"
        new_db.parent.mkdir(parents=True)
        zip_path.parent.mkdir(parents=True)
        new_exe.parent.mkdir(parents=True, exist_ok=True)
        new_exe.write_text("new exe", encoding="utf-8")
        new_db.write_text("db", encoding="utf-8")
        zip_path.write_text("zip", encoding="utf-8")

        preflight.update_profile(
            self.profile,
            exe_path=new_exe,
            db_path=new_db,
            zip_path=zip_path,
            now=datetime.fromtimestamp(5000, timezone.utc),
        )

        text = self.profile.read_text(encoding="utf-8")
        self.assertIn(f"- EXE path: `{new_exe.as_posix()}`", text)
        self.assertIn(f"- Packaged app database: `{new_db.as_posix()}`", text)
        self.assertIn("local freshness rebuild", text)
        self.assertNotIn("`OLD`", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
