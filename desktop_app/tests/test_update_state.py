from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jobflow_desktop_app.paths import AppPaths
from jobflow_desktop_app.updates.state import UpdateState, UpdateStateStore


class UpdateStateTests(unittest.TestCase):
    def test_state_store_round_trips_update_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = AppPaths(
                project_root=root,
                runtime_dir=root / "runtime",
                data_dir=root / "runtime" / "data",
                exports_dir=root / "runtime" / "exports",
                logs_dir=root / "runtime" / "logs",
                db_path=root / "runtime" / "data" / "jobflow_desktop.db",
                schema_path=root / "schema.sql",
                updates_dir=root / "updates",
            )
            store = UpdateStateStore(paths)

            saved = store.save(
                UpdateState.idle(current_version="0.8.6").with_changes(
                    status="prepared",
                    latest_version="0.8.7",
                    prepared_version="0.8.7",
                    prepared_dir=str(root / "prepared"),
                )
            )
            loaded = store.load(current_version="0.8.6")

            self.assertEqual(saved.status, "prepared")
            self.assertEqual(loaded.prepared_version, "0.8.7")
            self.assertEqual(loaded.current_version, "0.8.6")
            self.assertTrue(store.state_path.exists())

    def test_unknown_status_falls_back_to_idle(self) -> None:
        state = UpdateState.from_mapping({"status": "surprise"}, current_version="0.8.6")

        self.assertEqual(state.status, "idle")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
