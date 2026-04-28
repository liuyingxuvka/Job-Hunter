from __future__ import annotations

import unittest

from jobflow_desktop_app.updates.github_releases import (
    ReleaseAsset,
    ReleaseInfo,
    release_from_payload,
    resolve_release_artifacts,
)


class GitHubReleaseUpdateTests(unittest.TestCase):
    def test_release_payload_extracts_version_and_assets(self) -> None:
        release = release_from_payload(
            {
                "tag_name": "v0.8.7",
                "html_url": "https://example.com/release",
                "assets": [
                    {
                        "name": "Job-Hunter-0.8.7-win64.zip",
                        "browser_download_url": "https://example.com/app.zip",
                        "size": 123,
                    }
                ],
            }
        )

        self.assertEqual(release.version, "0.8.7")
        self.assertEqual(release.assets[0].name, "Job-Hunter-0.8.7-win64.zip")

    def test_release_artifacts_prefer_expected_windows_names(self) -> None:
        release = ReleaseInfo(
            version="0.8.7",
            html_url="https://example.com/release",
            assets=(
                ReleaseAsset(
                    name="Job-Hunter-0.8.7-win64.zip",
                    download_url="https://example.com/zip",
                ),
                ReleaseAsset(
                    name="Job-Hunter-0.8.7-win64.zip.sha256",
                    download_url="https://example.com/sha",
                ),
            ),
        )

        artifacts = resolve_release_artifacts(release)

        self.assertEqual(artifacts.package.name, "Job-Hunter-0.8.7-win64.zip")
        self.assertEqual(artifacts.checksum.name, "Job-Hunter-0.8.7-win64.zip.sha256")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
