from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_SRC_ROOT = REPO_ROOT / "desktop_app" / "src" / "jobflow_desktop_app"


class PythonRuntimeBoundaryTests(unittest.TestCase):
    def test_obsolete_reference_assets_are_removed(self) -> None:
        self.assertFalse(
            (REPO_ROOT / "legacy_jobflow_reference").exists(),
            "obsolete jobflow reference assets should not remain in the repository root.",
        )

        tools_root = REPO_ROOT / "desktop_app" / "runtime" / "tools"
        lingering_tool_files = [path for path in tools_root.rglob("*") if path.is_file()] if tools_root.exists() else []
        self.assertEqual(
            lingering_tool_files,
            [],
            "desktop_app/runtime/tools should not contain a bundled Node or other legacy runtime files.",
        )

    def test_current_source_and_entry_points_do_not_reference_removed_node_runtime(self) -> None:
        banned_tokens = (
            "legacy_jobflow_reference/",
            "legacy_jobflow_reference\\",
            "legacy_runs",
            "jobflow.mjs",
            "node.exe",
            "setup-node",
        )

        paths_to_scan = list(DESKTOP_SRC_ROOT.rglob("*.py"))
        paths_to_scan += [
            REPO_ROOT / ".github" / "workflows" / "release.yml",
            REPO_ROOT / "README.md",
            REPO_ROOT / "CONTRIBUTING.md",
            REPO_ROOT / "scripts" / "build_windows_release.ps1",
            REPO_ROOT / "scripts" / "privacy_audit.ps1",
            REPO_ROOT / "desktop_app" / "README.md",
            REPO_ROOT / "desktop_app" / "run_release.ps1",
            REPO_ROOT / "desktop_app" / "packaging_entry.py",
            REPO_ROOT / "docs" / "AI_INTEGRATION.md",
            REPO_ROOT / "docs" / "ARCHITECTURE.md",
            REPO_ROOT / "docs" / "GITHUB_REPO_SETUP.md",
            REPO_ROOT / "docs" / "REPOSITORY_BOUNDARY.md",
            REPO_ROOT / "docs" / "ROADMAP.md",
        ]

        for path in paths_to_scan:
            text = path.read_text(encoding="utf-8-sig")
            for token in banned_tokens:
                self.assertNotIn(token, text, f"{path} should not reference legacy runtime token {token!r}.")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
