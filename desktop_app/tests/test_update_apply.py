from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobflow_desktop_app.paths import AppPaths
from jobflow_desktop_app.updates.apply import launch_prepared_update
from jobflow_desktop_app.updates.state import UpdateState, UpdateStateStore


class UpdateApplyTests(unittest.TestCase):
    def test_launch_prepared_update_writes_state_and_external_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "install"
            prepared_root = root / "updates" / "prepared" / "0.8.7" / "Job-Hunter-0.8.7-win64"
            install_root.mkdir(parents=True, exist_ok=True)
            prepared_root.mkdir(parents=True, exist_ok=True)
            (install_root / "Jobflow Desktop.exe").write_text("old", encoding="utf-8")
            (prepared_root / "Jobflow Desktop.exe").write_text("new", encoding="utf-8")
            paths = AppPaths(
                project_root=install_root / "desktop_app",
                runtime_dir=root / "runtime",
                data_dir=root / "runtime" / "data",
                exports_dir=root / "runtime" / "exports",
                logs_dir=root / "runtime" / "logs",
                db_path=root / "runtime" / "data" / "jobflow_desktop.db",
                schema_path=install_root / "desktop_app" / "src" / "jobflow_desktop_app" / "db" / "schema.sql",
                install_root=install_root,
                updates_dir=root / "updates",
                is_packaged=True,
            )
            state = UpdateStateStore(paths).save(
                UpdateState.idle(current_version="0.8.6").with_changes(
                    status="prepared",
                    prepared_version="0.8.7",
                    prepared_dir=str(prepared_root),
                )
            )

            with patch("jobflow_desktop_app.updates.apply.subprocess.Popen") as popen:
                launch_prepared_update(paths, state, current_pid=1234)

            launched_command = popen.call_args.args[0]
            self.assertIn("-TargetRoot", launched_command)
            self.assertIn(str(install_root), launched_command)
            self.assertTrue((root / "updates" / "apply_update.ps1").exists())
            self.assertEqual(UpdateStateStore(paths).load(current_version="0.8.6").status, "applying")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
