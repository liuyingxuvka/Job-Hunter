from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable, Sequence


PACKAGE_RELEVANT_PREFIXES = (
    "desktop_app/src/",
    "desktop_app/assets/",
)
PACKAGE_RELEVANT_FILES = {
    "desktop_app/packaging_entry.py",
    "desktop_app/pyproject.toml",
    "README_RELEASE.txt",
    "START_JOBFLOW_DESKTOP.cmd",
    "scripts/build_windows_release.ps1",
    "scripts/privacy_audit.ps1",
}
IGNORED_PREFIXES = (
    ".git/",
    ".flowguard/",
    "build/",
    "dist/",
    "runtime/",
    "kb/history/",
    "desktop_app/src/jobflow_desktop_app.egg-info/",
)
IGNORED_NAMES = {"__pycache__"}
SOURCE_SUFFIXES = {
    ".py",
    ".txt",
    ".sql",
    ".toml",
    ".ico",
    ".cmd",
    ".ps1",
    ".md",
}
EXE_LINE_RE = re.compile(r"^- EXE path: `([^`]+)`", re.MULTILINE)
DB_LINE_RE = re.compile(r"^- Packaged app database: `([^`]+)`", re.MULTILINE)
LAST_REPLACEMENT_RE = re.compile(r"^- Last local packaged replacement: .*$", re.MULTILINE)
EXE_HASH_RE = re.compile(r"^- Replaced EXE SHA256: `[^`]*`$", re.MULTILINE)
ZIP_HASH_RE = re.compile(r"^- Last local release zip SHA256: `[^`]*`$", re.MULTILINE)


@dataclass(frozen=True)
class ChangedPath:
    path: str
    status: str
    package_relevant: bool
    exists: bool
    newest_mtime: float | None


@dataclass(frozen=True)
class PreflightDecision:
    status: str
    reason: str
    current_exe_path: str
    current_exe_exists: bool
    current_exe_mtime: str
    newest_source_mtime: str
    local_change_state: str
    package_state: str
    package_relevant_changes: list[str]
    active_change_paths: list[str]
    ignored_change_paths: list[str]
    planned_package_root: str
    planned_exe_path: str
    report_path: str
    message_zh: str


@dataclass(frozen=True)
class ApplyResult:
    decision: PreflightDecision
    built: bool
    package_root: str
    exe_path: str
    db_path: str
    zip_path: str
    sha256: str


class ValidationFailed(RuntimeError):
    def __init__(self, command: list[str], returncode: int | str, output: str) -> None:
        super().__init__("Pre-build validation failed.")
        self.command = command
        self.returncode = returncode
        self.output = output


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_from_timestamp(value: float | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat()


def _normalize_rel(path: str | Path) -> str:
    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _path_is_ignored(rel_path: str) -> bool:
    rel_path = _normalize_rel(rel_path)
    if any(part in IGNORED_NAMES for part in rel_path.split("/")):
        return True
    return any(rel_path == prefix.rstrip("/") or rel_path.startswith(prefix) for prefix in IGNORED_PREFIXES)


def _path_is_package_relevant(rel_path: str) -> bool:
    rel_path = _normalize_rel(rel_path)
    if _path_is_ignored(rel_path):
        return False
    if rel_path in PACKAGE_RELEVANT_FILES:
        return True
    return any(rel_path.startswith(prefix) for prefix in PACKAGE_RELEVANT_PREFIXES)


def _source_file_is_relevant(path: Path, repo_root: Path) -> bool:
    try:
        rel = _normalize_rel(path.relative_to(repo_root))
    except ValueError:
        return False
    if not _path_is_package_relevant(rel):
        return False
    if path.is_dir():
        return False
    return path.suffix.lower() in SOURCE_SUFFIXES


def _newest_mtime_for_path(path: Path) -> float | None:
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_mtime
    newest = path.stat().st_mtime
    for child in path.rglob("*"):
        if child.is_file() and child.name not in IGNORED_NAMES:
            newest = max(newest, child.stat().st_mtime)
    return newest


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def run_git_status(repo_root: Path) -> list[tuple[str, str]]:
    git_exe = shutil.which("git")
    if not git_exe:
        raise FileNotFoundError("git executable was not found on PATH.")
    completed = subprocess.run(
        [git_exe, "status", "--porcelain=v1", "-z"],
        cwd=repo_root,
        text=False,
        capture_output=True,
        check=True,
    )
    tokens = completed.stdout.decode("utf-8", errors="replace").split("\0")
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue
        status = token[:2]
        path = token[3:]
        if "R" in status or "C" in status:
            index += 1
            if index < len(tokens) and tokens[index]:
                path = tokens[index]
        result.append((status.strip() or "?", _normalize_rel(path)))
        index += 1
    return result


def collect_changed_paths(repo_root: Path, status_entries: Sequence[tuple[str, str]] | None = None) -> list[ChangedPath]:
    entries = list(status_entries) if status_entries is not None else run_git_status(repo_root)
    changes: list[ChangedPath] = []
    for status, rel_path in entries:
        rel_path = _normalize_rel(rel_path)
        path = repo_root / rel_path
        changes.append(
            ChangedPath(
                path=rel_path,
                status=status,
                package_relevant=_path_is_package_relevant(rel_path),
                exists=path.exists(),
                newest_mtime=_newest_mtime_for_path(path),
            )
        )
    return changes


def newest_package_source_mtime(repo_root: Path) -> float | None:
    candidates: list[float] = []
    for prefix in PACKAGE_RELEVANT_PREFIXES:
        root = repo_root / prefix
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if _source_file_is_relevant(path, repo_root):
                candidates.append(path.stat().st_mtime)
    for rel_path in PACKAGE_RELEVANT_FILES:
        path = repo_root / rel_path
        if path.exists() and path.is_file():
            candidates.append(path.stat().st_mtime)
    return max(candidates) if candidates else None


def read_profile_paths(profile_path: Path) -> dict[str, str]:
    text = profile_path.read_text(encoding="utf-8")
    exe_match = EXE_LINE_RE.search(text)
    db_match = DB_LINE_RE.search(text)
    return {
        "exe_path": exe_match.group(1) if exe_match else "",
        "db_path": db_match.group(1) if db_match else "",
    }


def _default_user_db_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Job-Hunter" / "runtime" / "data" / "jobflow_desktop.db"
    return Path.home() / "AppData" / "Local" / "Job-Hunter" / "runtime" / "data" / "jobflow_desktop.db"


def _planned_package_root(repo_root: Path, version: str, now: datetime) -> Path:
    stamp = now.astimezone().strftime("%Y%m%d-%H%M%S")
    return repo_root / "runtime" / "local_app" / f"smoke-{stamp}" / f"Job-Hunter-{version}-win64"


def _read_version(repo_root: Path) -> str:
    text = (repo_root / "desktop_app" / "pyproject.toml").read_text(encoding="utf-8-sig")
    match = re.search(r'version = "(\d+\.\d+\.\d+)"', text)
    if not match:
        raise ValueError("Could not read desktop_app version from pyproject.toml.")
    return match.group(1)


def evaluate_preflight(
    repo_root: Path,
    profile_path: Path,
    *,
    stability_minutes: int = 20,
    now: datetime | None = None,
    status_entries: Sequence[tuple[str, str]] | None = None,
) -> PreflightDecision:
    now = now or _utc_now()
    profile = read_profile_paths(profile_path)
    exe_path = Path(profile.get("exe_path", "")).expanduser()
    exe_exists = bool(profile.get("exe_path")) and exe_path.exists()
    exe_mtime = exe_path.stat().st_mtime if exe_exists else None
    source_mtime = newest_package_source_mtime(repo_root)
    changes = collect_changed_paths(repo_root, status_entries=status_entries)
    package_changes = [change for change in changes if change.package_relevant]
    ignored_changes = [change.path for change in changes if not change.package_relevant]
    lock_path = repo_root / ".git" / "index.lock"
    stability_seconds = max(0, int(stability_minutes)) * 60
    now_ts = now.timestamp()
    active_paths: list[str] = []
    if lock_path.exists():
        active_paths.append(".git/index.lock")
    for change in package_changes:
        if change.newest_mtime is None:
            active_paths.append(change.path)
            continue
        if now_ts - change.newest_mtime < stability_seconds:
            active_paths.append(change.path)

    if active_paths:
        local_change_state = "active"
    elif package_changes:
        local_change_state = "stable"
    else:
        local_change_state = "none"

    if not exe_exists:
        package_state = "missing"
    elif source_mtime is not None and exe_mtime is not None and source_mtime > exe_mtime + 1:
        package_state = "stale"
    else:
        package_state = "fresh"

    version = _read_version(repo_root)
    planned_root = _planned_package_root(repo_root, version, now)
    planned_exe = planned_root / "Jobflow Desktop.exe"
    report_path = repo_root / "runtime" / "daily_app_tests" / now.astimezone().strftime("%Y-%m-%d") / "local_freshness_preflight.json"

    if local_change_state == "active":
        status = "blocked_in_progress"
        reason = "local_package_relevant_changes_are_still_recent_or_locked"
        message = "检测到本地安装包相关代码仍在更改，今天的每日体检先不启动旧包，也不打包半成品。"
    elif local_change_state == "stable" or package_state in {"stale", "missing"}:
        status = "needs_rebuild"
        reason = "stable_local_changes_or_package_stale"
        message = "检测到本地已有稳定改动或当前测试包落后，应先重新打包并切换到本地最新版。"
    else:
        status = "use_current"
        reason = "current_package_is_fresh"
        message = "当前测试包比本地源码新，且没有需要进入安装包的本地改动，可以继续使用当前包。"

    return PreflightDecision(
        status=status,
        reason=reason,
        current_exe_path=str(exe_path) if profile.get("exe_path") else "",
        current_exe_exists=exe_exists,
        current_exe_mtime=_iso_from_timestamp(exe_mtime),
        newest_source_mtime=_iso_from_timestamp(source_mtime),
        local_change_state=local_change_state,
        package_state=package_state,
        package_relevant_changes=[change.path for change in package_changes],
        active_change_paths=active_paths,
        ignored_change_paths=ignored_changes,
        planned_package_root=str(planned_root),
        planned_exe_path=str(planned_exe),
        report_path=str(report_path),
        message_zh=message,
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _run_build(repo_root: Path, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    script = repo_root / "scripts" / "build_windows_release.ps1"
    powershell_exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    subprocess.run(
        [
            powershell_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-OutputRoot",
            str(output_root),
        ],
        cwd=repo_root,
        check=True,
    )


def _run_prebuild_validation(repo_root: Path, *, timeout_seconds: int = 600) -> None:
    command = [sys.executable, "-m", "unittest", "discover", "desktop_app\\tests"]
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=max(30, int(timeout_seconds)),
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + ("\n" if exc.stdout and exc.stderr else "") + (exc.stderr or "")
        raise ValidationFailed(command, "timeout", str(output)[-8000:]) from exc
    if completed.returncode != 0:
        output = (completed.stdout or "") + ("\n" if completed.stdout and completed.stderr else "") + (completed.stderr or "")
        raise ValidationFailed(command, completed.returncode, output[-8000:])


def _copy_preserved_db(old_db: Path, new_package_root: Path) -> Path:
    appdata_db = _default_user_db_path()
    if appdata_db.exists():
        return appdata_db
    new_db = new_package_root / "desktop_app" / "runtime" / "data" / "jobflow_desktop.db"
    if old_db.exists() and not new_db.exists():
        new_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_db, new_db)
    return new_db


def update_profile(profile_path: Path, *, exe_path: Path, db_path: Path, zip_path: Path, now: datetime) -> None:
    text = profile_path.read_text(encoding="utf-8")
    exe_hash = _hash_file(exe_path) if exe_path.exists() else ""
    zip_hash = _hash_file(zip_path) if zip_path.exists() else ""
    date_text = now.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    history_line = (
        f"- {date_text} local freshness rebuild: daily QA preflight detected stable local changes, "
        f"built a fresh local package, and switched this private pointer before running the packaged app."
    )

    def replace_or_append(pattern: re.Pattern[str], replacement: str, original: str) -> str:
        if pattern.search(original):
            return pattern.sub(replacement, original)
        return original.rstrip() + "\n" + replacement + "\n"

    text = replace_or_append(EXE_LINE_RE, f"- EXE path: `{exe_path.as_posix()}`", text)
    text = replace_or_append(DB_LINE_RE, f"- Packaged app database: `{db_path.as_posix()}`", text)
    text = replace_or_append(
        LAST_REPLACEMENT_RE,
        f"- Last local packaged replacement: {date_text}, local freshness preflight rebuild",
        text,
    )
    text = replace_or_append(EXE_HASH_RE, f"- Replaced EXE SHA256: `{exe_hash}`", text)
    text = replace_or_append(ZIP_HASH_RE, f"- Last local release zip SHA256: `{zip_hash}`", text)
    if history_line not in text:
        text = text.rstrip() + "\n" + history_line + "\n"
    profile_path.write_text(text, encoding="utf-8")


def apply_preflight(
    decision: PreflightDecision,
    repo_root: Path,
    profile_path: Path,
    *,
    now: datetime | None = None,
    validation_timeout_seconds: int = 600,
) -> ApplyResult:
    now = now or _utc_now()
    if decision.status == "blocked_in_progress":
        _write_json(Path(decision.report_path), asdict(decision))
        return ApplyResult(
            decision=decision,
            built=False,
            package_root="",
            exe_path=decision.current_exe_path,
            db_path="",
            zip_path="",
            sha256="",
        )
    if decision.status == "use_current":
        _write_json(Path(decision.report_path), asdict(decision))
        return ApplyResult(
            decision=decision,
            built=False,
            package_root=str(Path(decision.current_exe_path).parent) if decision.current_exe_path else "",
            exe_path=decision.current_exe_path,
            db_path=read_profile_paths(profile_path).get("db_path", ""),
            zip_path="",
            sha256="",
        )

    version = _read_version(repo_root)
    package_parent = Path(decision.planned_package_root).parent
    _run_prebuild_validation(repo_root, timeout_seconds=validation_timeout_seconds)
    _run_build(repo_root, package_parent)
    package_root = package_parent / f"Job-Hunter-{version}-win64"
    exe_path = package_root / "Jobflow Desktop.exe"
    if not exe_path.exists():
        raise FileNotFoundError(f"Expected rebuilt EXE was not created: {exe_path}")
    profile = read_profile_paths(profile_path)
    db_path = _copy_preserved_db(Path(profile.get("db_path", "")), package_root)
    zip_path = repo_root / "dist" / "release" / f"Job-Hunter-{version}-win64.zip"
    update_profile(profile_path, exe_path=exe_path, db_path=db_path, zip_path=zip_path, now=now)
    refreshed = PreflightDecision(
        **{
            **asdict(decision),
            "status": "rebuilt",
            "reason": "local_package_rebuilt_and_profile_updated",
            "planned_package_root": str(package_root),
            "planned_exe_path": str(exe_path),
            "message_zh": "已把稳定的本地改动重新打包为本地测试最新版，并更新每日体检指针。",
        }
    )
    _write_json(Path(decision.report_path), asdict(refreshed))
    return ApplyResult(
        decision=refreshed,
        built=True,
        package_root=str(package_root),
        exe_path=str(exe_path),
        db_path=str(db_path),
        zip_path=str(zip_path) if zip_path.exists() else "",
        sha256=_hash_file(exe_path),
    )


def _print_payload(payload: object, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if isinstance(payload, ApplyResult):
        decision = payload.decision
    elif isinstance(payload, PreflightDecision):
        decision = payload
    else:
        print(payload)
        return
    print(decision.message_zh)
    print(f"status: {decision.status}")
    if decision.active_change_paths:
        print("active changes:")
        for path in decision.active_change_paths:
            print(f"- {path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight local package freshness before daily Jobflow Desktop QA.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--profile", default="runtime/private/yingxu_profile_context.md")
    parser.add_argument("--stability-minutes", type=int, default=20)
    parser.add_argument("--validation-timeout-seconds", type=int, default=600)
    parser.add_argument("--apply", action="store_true", help="Build/switch the local package when the preflight requires it.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    profile_path = Path(args.profile)
    if not profile_path.is_absolute():
        profile_path = repo_root / profile_path
    decision = evaluate_preflight(
        repo_root,
        profile_path,
        stability_minutes=args.stability_minutes,
    )
    if args.apply:
        try:
            result = apply_preflight(
                decision,
                repo_root,
                profile_path,
                validation_timeout_seconds=args.validation_timeout_seconds,
            )
        except ValidationFailed as exc:
            payload = {
                "status": "validation_failed",
                "returncode": exc.returncode,
                "command": exc.command,
                "output_tail": exc.output,
                "decision": asdict(decision),
            }
            _write_json(Path(decision.report_path), payload)
            _print_payload(payload, json_output=args.json)
            return 1
        except subprocess.CalledProcessError as exc:
            payload = {"status": "build_failed", "returncode": exc.returncode, "decision": asdict(decision)}
            _write_json(Path(decision.report_path), payload)
            _print_payload(payload, json_output=args.json)
            return 1
        _print_payload(asdict(result), json_output=args.json)
        return 2 if result.decision.status == "blocked_in_progress" else 0
    _write_json(Path(decision.report_path), asdict(decision))
    _print_payload(asdict(decision) if args.json else decision, json_output=args.json)
    return 2 if decision.status == "blocked_in_progress" else 0


if __name__ == "__main__":
    raise SystemExit(main())
