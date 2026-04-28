from __future__ import annotations

import re


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(version: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(str(version or "").strip())
    if not match:
        raise ValueError(f"Unsupported version value: {version!r}")
    return tuple(int(part) for part in match.groups())


def compare_versions(left: str, right: str) -> int:
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    if left_parts == right_parts:
        return 0
    return 1 if left_parts > right_parts else -1


def is_newer_version(candidate: str, current: str) -> bool:
    try:
        return compare_versions(candidate, current) > 0
    except ValueError:
        return False
