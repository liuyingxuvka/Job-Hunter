from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobflow_desktop_app.bootstrap import migrate_packaged_runtime_if_needed
from jobflow_desktop_app.paths import AppPaths, build_app_paths


class PackagedPathTests(unittest.TestCase):
    def test_source_build_keeps_updates_under_runtime(self) -> None:
        paths = build_app_paths()

        self.assertFalse(paths.is_packaged)
        self.assertEqual(paths.updates_dir, paths.runtime_dir / "updates")

    def test_packaged_build_uses_local_app_data_for_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "install"
            local_app_data = root / "local-app-data"
            packaged_desktop_root = install_root / "desktop_app"
            packaged_desktop_root.mkdir(parents=True)
            exe_path = install_root / "Jobflow Desktop.exe"
            exe_path.write_text("exe", encoding="utf-8")

            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(exe_path)),
                patch.dict("os.environ", {"LOCALAPPDATA": str(local_app_data)}, clear=False),
            ):
                paths = build_app_paths()

            self.assertTrue(paths.is_packaged)
            self.assertEqual(paths.install_root, install_root)
            self.assertEqual(paths.project_root, packaged_desktop_root)
            self.assertEqual(paths.runtime_dir, local_app_data / "Job-Hunter" / "runtime")
            self.assertEqual(paths.bundled_runtime_dir, packaged_desktop_root / "runtime")

    def test_packaged_runtime_migration_copies_missing_files_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_runtime = root / "install" / "desktop_app" / "runtime"
            new_runtime = root / "appdata" / "runtime"
            (bundled_runtime / "data").mkdir(parents=True)
            (new_runtime / "data").mkdir(parents=True)
            (bundled_runtime / "data" / "jobflow_desktop.db").write_text("old-db", encoding="utf-8")
            (bundled_runtime / "data" / "demo_candidate_resume.md").write_text("demo", encoding="utf-8")
            (new_runtime / "data" / "jobflow_desktop.db").write_text("new-db", encoding="utf-8")
            paths = AppPaths(
                project_root=root / "install" / "desktop_app",
                runtime_dir=new_runtime,
                data_dir=new_runtime / "data",
                exports_dir=new_runtime / "exports",
                logs_dir=new_runtime / "logs",
                db_path=new_runtime / "data" / "jobflow_desktop.db",
                schema_path=root / "schema.sql",
                updates_dir=root / "appdata" / "updates",
                bundled_runtime_dir=bundled_runtime,
                is_packaged=True,
            )

            migrate_packaged_runtime_if_needed(paths)

            self.assertEqual((new_runtime / "data" / "jobflow_desktop.db").read_text(encoding="utf-8"), "new-db")
            self.assertEqual((new_runtime / "data" / "demo_candidate_resume.md").read_text(encoding="utf-8"), "demo")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
