from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_RELEASE_API_URL = "https://api.github.com/repos/liuyingxuvka/Job-Hunter/releases/latest"


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    html_url: str
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True)
class ReleaseArtifacts:
    package: ReleaseAsset
    checksum: ReleaseAsset


class ReleaseLookupError(RuntimeError):
    pass


def normalize_release_version(tag_or_name: str) -> str:
    text = str(tag_or_name or "").strip()
    if text.startswith("v"):
        text = text[1:]
    return text


def release_from_payload(payload: dict[str, Any]) -> ReleaseInfo:
    version = normalize_release_version(str(payload.get("tag_name") or payload.get("name") or ""))
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        raise ReleaseLookupError("Latest GitHub release did not contain a semantic version tag.")

    raw_assets = payload.get("assets") or []
    assets: list[ReleaseAsset] = []
    if isinstance(raw_assets, list):
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            download_url = str(item.get("browser_download_url") or "").strip()
            if not name or not download_url:
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            assets.append(ReleaseAsset(name=name, download_url=download_url, size=size))

    return ReleaseInfo(
        version=version,
        html_url=str(payload.get("html_url") or "").strip(),
        assets=tuple(assets),
    )


def fetch_latest_release(*, api_url: str = DEFAULT_RELEASE_API_URL, timeout_seconds: int = 8) -> ReleaseInfo:
    request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Job-Hunter-Updater",
        },
    )
    try:
        with urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
            raw_payload = response.read()
    except HTTPError as exc:
        raise ReleaseLookupError(f"GitHub release check failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise ReleaseLookupError(f"GitHub release check failed: {exc.reason}") from exc
    except OSError as exc:
        raise ReleaseLookupError(f"GitHub release check failed: {exc}") from exc

    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseLookupError("GitHub release response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ReleaseLookupError("GitHub release response had an unexpected shape.")
    return release_from_payload(payload)


def resolve_release_artifacts(release: ReleaseInfo) -> ReleaseArtifacts:
    package_name = f"Job-Hunter-{release.version}-win64.zip"
    checksum_name = f"{package_name}.sha256"
    by_name = {asset.name: asset for asset in release.assets}

    package = by_name.get(package_name)
    checksum = by_name.get(checksum_name)
    if package is None:
        package = next(
            (
                asset
                for asset in release.assets
                if asset.name.endswith(".zip") and release.version in asset.name and "win64" in asset.name
            ),
            None,
        )
    if checksum is None and package is not None:
        checksum = by_name.get(f"{package.name}.sha256")
    if checksum is None:
        checksum = next(
            (
                asset
                for asset in release.assets
                if asset.name.endswith(".sha256") and release.version in asset.name
            ),
            None,
        )

    if package is None or checksum is None:
        raise ReleaseLookupError("Latest release is missing the Windows zip package or sha256 checksum asset.")
    return ReleaseArtifacts(package=package, checksum=checksum)
