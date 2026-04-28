from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

from ..paths import AppPaths
from .github_releases import (
    ReleaseLookupError,
    fetch_latest_release,
    resolve_release_artifacts,
)
from .state import UpdateState, UpdateStateStore, utc_now_text
from .versioning import compare_versions, is_newer_version


class UpdatePreparationError(RuntimeError):
    pass


def _download_file(url: str, destination: Path, *, timeout_seconds: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "Job-Hunter-Updater"})
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
            with temp_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except HTTPError as exc:
        raise UpdatePreparationError(f"Download failed with HTTP {exc.code}: {destination.name}") from exc
    except URLError as exc:
        raise UpdatePreparationError(f"Download failed: {exc.reason}") from exc
    except OSError as exc:
        raise UpdatePreparationError(f"Download failed: {exc}") from exc
    temp_path.replace(destination)


def _parse_sha256_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdatePreparationError("Could not read downloaded checksum file.") from exc
    first_token = text.split()[0].strip().lower() if text.split() else ""
    if len(first_token) != 64 or any(char not in "0123456789abcdef" for char in first_token):
        raise UpdatePreparationError("Downloaded checksum file did not contain a valid SHA256 value.")
    return first_token


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise UpdatePreparationError("Could not hash downloaded update package.") from exc
    return digest.hexdigest().lower()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _remove_directory_under(root: Path, target: Path) -> None:
    if not target.exists():
        return
    if not _is_relative_to(target, root):
        raise UpdatePreparationError(f"Refusing to remove path outside update cache: {target}")
    shutil.rmtree(target)


def _extract_package(zip_path: Path, prepared_root: Path, *, version: str) -> Path:
    destination = prepared_root / version
    _remove_directory_under(prepared_root, destination)
    destination.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                target = destination / member.filename
                if not _is_relative_to(target, destination):
                    raise UpdatePreparationError("Update package contains an unsafe path.")
            archive.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise UpdatePreparationError("Downloaded update package was not a valid zip file.") from exc
    except OSError as exc:
        raise UpdatePreparationError(f"Could not extract update package: {exc}") from exc

    candidates = [path for path in destination.iterdir() if path.is_dir()]
    if len(candidates) == 1 and (candidates[0] / "Jobflow Desktop.exe").exists():
        return candidates[0]
    if (destination / "Jobflow Desktop.exe").exists():
        return destination
    raise UpdatePreparationError("Extracted update package did not contain Jobflow Desktop.exe.")


def _prepared_package_looks_valid(prepared_dir: str) -> bool:
    if not prepared_dir:
        return False
    root = Path(prepared_dir)
    return root.exists() and (root / "Jobflow Desktop.exe").exists()


def _fail(store: UpdateStateStore, state: UpdateState, message: str) -> UpdateState:
    return store.save(
        state.with_changes(
            status="failed",
            error_message=message,
        )
    )


def check_and_prepare_update(
    paths: AppPaths,
    *,
    current_version: str,
    timeout_seconds: int = 10,
) -> UpdateState:
    store = UpdateStateStore(paths)
    state = store.save(
        store.load(current_version=current_version).with_changes(
            status="checking",
            current_version=current_version,
            error_message="",
        )
    )

    try:
        release = fetch_latest_release(timeout_seconds=timeout_seconds)
    except ReleaseLookupError as exc:
        return _fail(store, state, str(exc))

    checked_at = utc_now_text()
    if not is_newer_version(release.version, current_version):
        return store.save(
            state.with_changes(
                status="up_to_date",
                current_version=current_version,
                latest_version=release.version,
                release_url=release.html_url,
                checked_at=checked_at,
                error_message="",
            )
        )

    if (
        state.prepared_version == release.version
        and _prepared_package_looks_valid(state.prepared_dir)
    ):
        return store.save(
            state.with_changes(
                status="prepared",
                current_version=current_version,
                latest_version=release.version,
                release_url=release.html_url,
                checked_at=checked_at,
                error_message="",
            )
        )

    if state.prepared_version:
        try:
            prepared_is_stale = compare_versions(release.version, state.prepared_version) > 0
        except ValueError:
            prepared_is_stale = True
        if prepared_is_stale:
            state = store.save(
                state.with_changes(
                    status="stale",
                    current_version=current_version,
                    latest_version=release.version,
                    release_url=release.html_url,
                    checked_at=checked_at,
                )
            )

    try:
        artifacts = resolve_release_artifacts(release)
    except ReleaseLookupError as exc:
        return _fail(store, state, str(exc))

    state = store.save(
        state.with_changes(
            status="downloading",
            current_version=current_version,
            latest_version=release.version,
            release_url=release.html_url,
            checked_at=checked_at,
            error_message="",
        )
    )

    updates_dir = Path(paths.updates_dir)
    downloads_dir = updates_dir / "downloads"
    prepared_root = updates_dir / "prepared"
    package_path = downloads_dir / artifacts.package.name
    checksum_path = downloads_dir / artifacts.checksum.name

    try:
        _download_file(artifacts.package.download_url, package_path, timeout_seconds=timeout_seconds)
        _download_file(artifacts.checksum.download_url, checksum_path, timeout_seconds=timeout_seconds)
        expected_hash = _parse_sha256_file(checksum_path)
        actual_hash = _sha256(package_path)
        if actual_hash != expected_hash:
            raise UpdatePreparationError("Downloaded update package did not match its SHA256 checksum.")
        prepared_dir = _extract_package(package_path, prepared_root, version=release.version)
    except UpdatePreparationError as exc:
        return _fail(store, state, str(exc))

    return store.save(
        state.with_changes(
            status="prepared",
            current_version=current_version,
            latest_version=release.version,
            downloaded_version=release.version,
            prepared_version=release.version,
            release_url=release.html_url,
            package_path=str(package_path),
            checksum_path=str(checksum_path),
            prepared_dir=str(prepared_dir),
            sha256=actual_hash,
            checked_at=checked_at,
            error_message="",
        )
    )
