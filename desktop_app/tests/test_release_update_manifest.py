from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class ReleaseUpdateManifestTests(unittest.TestCase):
    def test_windows_release_build_writes_update_manifest(self) -> None:
        script = (REPO_ROOT / "scripts" / "build_windows_release.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("update-manifest.json", script)
        self.assertIn("ConvertTo-Json", script)
        self.assertIn("sha256", script)
        self.assertIn("Get-Sha256Hex", script)
        self.assertIn("System.Security.Cryptography.SHA256", script)
        self.assertNotIn("Get-FileHash", script)

    def test_release_workflow_uploads_update_manifest_assets(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8-sig")

        self.assertIn("dist/release/*.json", workflow)
        self.assertIn("release-assets/*.json", workflow)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
