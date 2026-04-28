from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

from jobflow_desktop_app.paths import AppPaths
from jobflow_desktop_app.updates.github_releases import ReleaseAsset, ReleaseInfo
from jobflow_desktop_app.updates.prepare import check_and_prepare_update
from jobflow_desktop_app.updates.state import UpdateState, UpdateStateStore


def _make_paths(root: Path) -> AppPaths:
    return AppPaths(
        project_root=root,
        runtime_dir=root / "runtime",
        data_dir=root / "runtime" / "data",
        exports_dir=root / "runtime" / "exports",
        logs_dir=root / "runtime" / "logs",
        db_path=root / "runtime" / "data" / "jobflow_desktop.db",
        schema_path=root / "schema.sql",
        updates_dir=root / "updates",
        is_packaged=True,
    )


def _write_release_assets(root: Path, version: str) -> ReleaseInfo:
    asset_root = root / "assets"
    package_name = f"Job-Hunter-{version}-win64.zip"
    package_path = asset_root / package_name
    checksum_path = asset_root / f"{package_name}.sha256"
    asset_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr(f"Job-Hunter-{version}-win64/Jobflow Desktop.exe", "exe")
        archive.writestr(f"Job-Hunter-{version}-win64/README_RELEASE.txt", "readme")
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    checksum_path.write_text(f"{digest} *{package_name}\n", encoding="ascii")
    return ReleaseInfo(
        version=version,
        html_url="https://example.com/release",
        assets=(
            ReleaseAsset(name=package_name, download_url=package_path.as_uri()),
            ReleaseAsset(name=f"{package_name}.sha256", download_url=checksum_path.as_uri()),
        ),
    )


class UpdatePrepareTests(unittest.TestCase):
    def test_check_and_prepare_downloads_verifies_and_extracts_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _make_paths(root)
            release = _write_release_assets(root, "0.8.7")

            with patch("jobflow_desktop_app.updates.prepare.fetch_latest_release", return_value=release):
                state = check_and_prepare_update(paths, current_version="0.8.6")

            self.assertEqual(state.status, "prepared")
            self.assertEqual(state.prepared_version, "0.8.7")
            self.assertTrue((Path(state.prepared_dir) / "Jobflow Desktop.exe").exists())

    def test_prepared_older_version_is_replaced_by_latest_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _make_paths(root)
            stale_prepared = root / "updates" / "prepared" / "0.8.7" / "Job-Hunter-0.8.7-win64"
            stale_prepared.mkdir(parents=True, exist_ok=True)
            (stale_prepared / "Jobflow Desktop.exe").write_text("old", encoding="utf-8")
            UpdateStateStore(paths).save(
                UpdateState.idle(current_version="0.8.6").with_changes(
                    status="prepared",
                    latest_version="0.8.7",
                    prepared_version="0.8.7",
                    prepared_dir=str(stale_prepared),
                )
            )
            release = _write_release_assets(root, "0.8.8")

            with patch("jobflow_desktop_app.updates.prepare.fetch_latest_release", return_value=release):
                state = check_and_prepare_update(paths, current_version="0.8.6")

            self.assertEqual(state.status, "prepared")
            self.assertEqual(state.prepared_version, "0.8.8")
            self.assertTrue((Path(state.prepared_dir) / "Jobflow Desktop.exe").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
