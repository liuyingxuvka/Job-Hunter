from __future__ import annotations

from pathlib import Path
import os
import subprocess

from ..paths import AppPaths
from .state import UpdateState, UpdateStateStore


class UpdateApplyError(RuntimeError):
    pass


_UPDATER_SCRIPT = r'''
param(
  [Parameter(Mandatory=$true)][string]$TargetRoot,
  [Parameter(Mandatory=$true)][string]$PreparedRoot,
  [Parameter(Mandatory=$true)][string]$StatePath,
  [Parameter(Mandatory=$true)][string]$ExeName,
  [int]$CurrentPid = 0
)

$ErrorActionPreference = "Stop"

function Set-UpdateState {
  param(
    [string]$Status,
    [string]$ErrorMessage = ""
  )

  $payload = [pscustomobject]@{}
  if (Test-Path -LiteralPath $StatePath) {
    try {
      $payload = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    } catch {
      $payload = [pscustomobject]@{}
    }
  }
  $payload | Add-Member -NotePropertyName status -NotePropertyValue $Status -Force
  $payload | Add-Member -NotePropertyName error_message -NotePropertyValue $ErrorMessage -Force
  $payload | Add-Member -NotePropertyName updated_at -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("s") + "+00:00") -Force
  $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

$backupRoot = ""

try {
  Set-UpdateState -Status "applying"

  if ($CurrentPid -gt 0) {
    Wait-Process -Id $CurrentPid -Timeout 90 -ErrorAction SilentlyContinue
  }

  $preparedExe = Join-Path $PreparedRoot $ExeName
  if (-not (Test-Path -LiteralPath $preparedExe -PathType Leaf)) {
    throw "Prepared update does not contain $ExeName."
  }
  if (-not (Test-Path -LiteralPath $TargetRoot -PathType Container)) {
    throw "Current installation directory was not found."
  }

  $targetParent = Split-Path -Parent $TargetRoot
  $targetName = Split-Path -Leaf $TargetRoot
  $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backupRoot = Join-Path $targetParent "$targetName.backup-$timestamp"

  Move-Item -LiteralPath $TargetRoot -Destination $backupRoot -Force
  New-Item -ItemType Directory -Path $TargetRoot -Force | Out-Null
  Get-ChildItem -LiteralPath $PreparedRoot -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $TargetRoot -Recurse -Force
  }

  Set-UpdateState -Status "idle"
  Start-Process -FilePath (Join-Path $TargetRoot $ExeName)
} catch {
  $message = $_.Exception.Message
  try {
    if ((-not (Test-Path -LiteralPath $TargetRoot)) -and $backupRoot -and (Test-Path -LiteralPath $backupRoot)) {
      Move-Item -LiteralPath $backupRoot -Destination $TargetRoot -Force
    }
  } catch {
    $message = "$message Rollback also failed: $($_.Exception.Message)"
  }
  Set-UpdateState -Status "failed" -ErrorMessage $message
  if (Test-Path -LiteralPath (Join-Path $TargetRoot $ExeName) -PathType Leaf) {
    Start-Process -FilePath (Join-Path $TargetRoot $ExeName)
  }
}
'''


def launch_prepared_update(paths: AppPaths, state: UpdateState, *, current_pid: int | None = None) -> None:
    if not paths.is_packaged:
        raise UpdateApplyError("Updates can only be applied from the packaged Windows app.")
    if state.status != "prepared":
        raise UpdateApplyError("No prepared update is ready to install.")

    prepared_root = Path(state.prepared_dir)
    if not prepared_root.exists() or not (prepared_root / "Jobflow Desktop.exe").exists():
        raise UpdateApplyError("The prepared update package is missing or incomplete.")

    target_root = Path(paths.install_root)
    if not target_root.exists():
        raise UpdateApplyError("The current installation directory was not found.")

    store = UpdateStateStore(paths)
    store.save(state.with_changes(status="applying", error_message=""))

    script_path = Path(paths.updates_dir) / "apply_update.ps1"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_UPDATER_SCRIPT.strip() + "\n", encoding="utf-8")

    pid = int(current_pid if current_pid is not None else os.getpid())
    command = [
        "powershell",
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-TargetRoot",
        str(target_root),
        "-PreparedRoot",
        str(prepared_root),
        "-StatePath",
        str(store.state_path),
        "-ExeName",
        "Jobflow Desktop.exe",
        "-CurrentPid",
        str(pid),
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        command,
        cwd=str(script_path.parent),
        close_fds=True,
        creationflags=creation_flags,
    )
