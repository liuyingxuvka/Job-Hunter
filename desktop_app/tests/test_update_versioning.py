from __future__ import annotations

import unittest

from jobflow_desktop_app.updates.versioning import compare_versions, is_newer_version, parse_version


class UpdateVersioningTests(unittest.TestCase):
    def test_parse_version_accepts_plain_and_tagged_semver(self) -> None:
        self.assertEqual(parse_version("0.8.6"), (0, 8, 6))
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))

    def test_compare_versions_uses_numeric_order(self) -> None:
        self.assertGreater(compare_versions("0.10.0", "0.9.9"), 0)
        self.assertEqual(compare_versions("1.0.0", "1.0.0"), 0)
        self.assertLess(compare_versions("0.8.6", "0.8.7"), 0)

    def test_is_newer_version_rejects_invalid_values(self) -> None:
        self.assertTrue(is_newer_version("0.8.7", "0.8.6"))
        self.assertFalse(is_newer_version("latest", "0.8.6"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
