param(
  [string]$PythonExe = "",
  [string]$Version = "",
  [string]$OutputRoot = "",
  [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Join-Path $repoRoot "desktop_app"
$privacyAuditPath = Join-Path $repoRoot "scripts\privacy_audit.ps1"
$buildRoot = Join-Path $repoRoot "build\release"
$distRoot = Join-Path $repoRoot "dist\release"
$pyInstallerWork = Join-Path $buildRoot "pyinstaller-work"
$pyInstallerDist = Join-Path $buildRoot "pyinstaller-dist"
$appName = "Jobflow Desktop"
$productName = "Job-Hunter"
$exeAuthorName = "Yingxu Liu"
$iconPath = Join-Path $desktopRoot "assets\app_icon.ico"

function Resolve-PythonExe {
  param([string]$RequestedPath)

  if ($RequestedPath -and (Test-Path -LiteralPath $RequestedPath)) {
    return $RequestedPath
  }

  $candidates = @(
    (Join-Path $desktopRoot ".venv\Scripts\python.exe"),
    $env:JOBFLOW_PYTHON_PATH
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
  $candidates = @($candidates)

  if ($candidates.Count -gt 0) {
    return $candidates[0]
  }

  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($pythonCommand -and $pythonCommand.Path) {
    return $pythonCommand.Path
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher -and $pyLauncher.Path) {
    return "$($pyLauncher.Path) -3"
  }

  throw "Python executable not found. Provide -PythonExe or install Python 3.10+."
}

function Invoke-Python {
  param(
    [string]$PythonSpec,
    [string[]]$Arguments
  )

  if ($PythonSpec -match "\s-3$") {
    $parts = $PythonSpec.Split(" ", 2)
    & $parts[0] "-3" @Arguments
    return
  }

  & $PythonSpec @Arguments
}

function Resolve-Version {
  param([string]$RequestedVersion)

  if ($RequestedVersion) {
    return $RequestedVersion
  }

  $pyprojectPath = Join-Path $desktopRoot "pyproject.toml"
  $content = Get-Content -LiteralPath $pyprojectPath -Raw
  $match = [regex]::Match($content, 'version = "(\d+\.\d+\.\d+)"')
  if (-not $match.Success) {
    throw "Could not resolve version from $pyprojectPath"
  }
  return $match.Groups[1].Value
}

function Convert-VersionTuple {
  param([string]$VersionText)

  $parts = @($VersionText.Split(".") | Where-Object { $_ -ne "" })
  while ($parts.Count -lt 4) {
    $parts += "0"
  }
  return @($parts | Select-Object -First 4 | ForEach-Object { [int]$_ })
}

function Copy-DirectoryContents {
  param(
    [string]$Source,
    [string]$Destination
  )

  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  $items = Get-ChildItem -LiteralPath $Source -Force
  if (-not $items) {
    return
  }
  Copy-Item -Path ($items | ForEach-Object { $_.FullName }) -Destination $Destination -Recurse -Force
}

$pythonSpec = Resolve-PythonExe -RequestedPath $PythonExe
$releaseVersion = Resolve-Version -RequestedVersion $Version
$versionTuple = Convert-VersionTuple -VersionText $releaseVersion
$packageStem = "Job-Hunter-$releaseVersion-win64"
$stageRoot = if ($OutputRoot) { Join-Path $OutputRoot $packageStem } else { Join-Path $buildRoot $packageStem }
$zipPath = Join-Path $distRoot "$packageStem.zip"
$checksumPath = "$zipPath.sha256"
$versionInfoPath = Join-Path $buildRoot "windows-version-info.txt"

if (-not (Test-Path -LiteralPath $privacyAuditPath)) {
  throw "privacy_audit.ps1 not found at $privacyAuditPath"
}
if (-not (Test-Path -LiteralPath $iconPath)) {
  throw "Application icon not found at $iconPath"
}

Invoke-Python -PythonSpec $pythonSpec -Arguments @("-m", "PyInstaller", "--version") | Out-Null

if (Test-Path -LiteralPath $stageRoot) {
  Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
if (Test-Path -LiteralPath $pyInstallerWork) {
  Remove-Item -LiteralPath $pyInstallerWork -Recurse -Force
}
if (Test-Path -LiteralPath $pyInstallerDist) {
  Remove-Item -LiteralPath $pyInstallerDist -Recurse -Force
}
if (Test-Path -LiteralPath $versionInfoPath) {
  Remove-Item -LiteralPath $versionInfoPath -Force
}

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null

$versionInfoContent = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($versionTuple[0]), $($versionTuple[1]), $($versionTuple[2]), $($versionTuple[3])),
    prodvers=($($versionTuple[0]), $($versionTuple[1]), $($versionTuple[2]), $($versionTuple[3])),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          "040904B0",
          [
            StringStruct("CompanyName", "$exeAuthorName"),
            StringStruct("FileDescription", "$appName"),
            StringStruct("FileVersion", "$releaseVersion"),
            StringStruct("InternalName", "$appName"),
            StringStruct("OriginalFilename", "$appName.exe"),
            StringStruct("ProductName", "$productName"),
            StringStruct("ProductVersion", "$releaseVersion"),
            StringStruct("LegalCopyright", "Copyright (c) $(Get-Date -Format yyyy) $exeAuthorName")
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct("Translation", [1033, 1200])])
  ]
)
"@
Set-Content -LiteralPath $versionInfoPath -Value $versionInfoContent -Encoding utf8

$entryScript = Join-Path $desktopRoot "packaging_entry.py"
Invoke-Python -PythonSpec $pythonSpec -Arguments @(
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--windowed",
  "--name",
  $appName,
  "--icon",
  $iconPath,
  "--version-file",
  $versionInfoPath,
  "--distpath",
  $pyInstallerDist,
  "--workpath",
  $pyInstallerWork,
  "--specpath",
  $buildRoot,
  "--paths",
  (Join-Path $desktopRoot "src"),
  "--collect-data",
  "jobflow_desktop_app",
  "--collect-submodules",
  "jobflow_desktop_app",
  $entryScript
)

$builtAppRoot = Join-Path $pyInstallerDist $appName
if (-not (Test-Path -LiteralPath $builtAppRoot)) {
  throw "PyInstaller output directory not found at $builtAppRoot"
}

Copy-DirectoryContents -Source $builtAppRoot -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "README_RELEASE.txt") -Destination (Join-Path $stageRoot "README_RELEASE.txt") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "START_JOBFLOW_DESKTOP.cmd") -Destination (Join-Path $stageRoot "START_JOBFLOW_DESKTOP.cmd") -Force

$packagedDesktopRoot = Join-Path $stageRoot "desktop_app"
$packagedAssetsRoot = Join-Path $packagedDesktopRoot "assets"
$packagedRuntimeRoot = Join-Path $packagedDesktopRoot "runtime"
$packagedSchemaRoot = Join-Path $packagedDesktopRoot "src\jobflow_desktop_app\db"

Copy-DirectoryContents -Source (Join-Path $desktopRoot "assets") -Destination $packagedAssetsRoot

foreach ($relativeDir in @("backups", "data", "exports", "search_runs", "logs")) {
  New-Item -ItemType Directory -Path (Join-Path $packagedRuntimeRoot $relativeDir) -Force | Out-Null
}

$demoResumePath = Join-Path $desktopRoot "runtime\data\demo_candidate_resume.md"
if (Test-Path -LiteralPath $demoResumePath -PathType Leaf) {
  Copy-Item -LiteralPath $demoResumePath -Destination (Join-Path $packagedRuntimeRoot "data\demo_candidate_resume.md") -Force
}

New-Item -ItemType Directory -Path $packagedSchemaRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $desktopRoot "src\jobflow_desktop_app\db\schema.sql") -Destination (Join-Path $packagedSchemaRoot "schema.sql") -Force

$packagedExe = Join-Path $stageRoot "$appName.exe"
if (-not (Test-Path -LiteralPath $packagedExe)) {
  throw "Packaged executable not found at $packagedExe"
}

& $privacyAuditPath -Scope package -PackageRoot $stageRoot

if (-not $SkipZip) {
  if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
  }
  if (Test-Path -LiteralPath $checksumPath) {
    Remove-Item -LiteralPath $checksumPath -Force
  }

  Push-Location (Split-Path -Parent $stageRoot)
  try {
    Compress-Archive -Path $packageStem -DestinationPath $zipPath -Force
  } finally {
    Pop-Location
  }

  $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
  Set-Content -LiteralPath $checksumPath -Value "$hash *$(Split-Path -Leaf $zipPath)" -Encoding ascii
}

Write-Output "Release package root: $stageRoot"
if (-not $SkipZip) {
  Write-Output "Release zip: $zipPath"
  Write-Output "Release checksum: $checksumPath"
}
