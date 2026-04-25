param(
  [ValidateSet("repo", "staged", "package")]
  [string]$Scope = "repo",
  [string]$PackageRoot = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
function Resolve-GitExecutable {
  $gitCommand = Get-Command git -ErrorAction SilentlyContinue
  $gitCandidate = @(
    $gitCommand.Path,
    $gitCommand.Source,
    $gitCommand.Definition
  ) | Where-Object { $_ } | Select-Object -First 1

  if ($gitCandidate) {
    return $gitCandidate
  }

  if ($env:OS -eq "Windows_NT") {
    $windowsGitExe = "C:\Program Files\Git\cmd\git.exe"
    if (Test-Path -LiteralPath $windowsGitExe) {
      return $windowsGitExe
    }

    throw "Git executable not found in PATH or at $windowsGitExe"
  }

  throw "Git executable not found in PATH."
}

$gitExe = Resolve-GitExecutable

$blockedPathPatterns = @(
  "desktop_app/runtime/data/jobflow_desktop.db*",
  "desktop_app/runtime/backups/*",
  "desktop_app/runtime/exports/*",
  "desktop_app/runtime/logs/*",
  "desktop_app/runtime/search_runs/*",
  "desktop_app/runtime/tools/*"
)

$packageBlockedPathPatterns = @(
  "desktop_app/runtime/data/*"
)

$allowedPathPatterns = @(
  "desktop_app/runtime/data/demo_candidate_resume.md",
  "desktop_app/runtime/backups/.gitignore",
  "desktop_app/runtime/exports/.gitkeep",
  "desktop_app/runtime/logs/.gitkeep",
  "desktop_app/runtime/search_runs/.gitkeep"
)

$contentScanExcludePaths = @(
  "scripts/privacy_audit.ps1"
)

$contentScanExcludePrefixes = @(
  "_internal/"
)

$contentRules = @(
  @{
    Label = "possible OpenAI secret"
    Regex = "sk-(proj-)?[A-Za-z0-9_-]{20,}"
  },
  @{
    Label = "absolute Windows user path"
    Regex = "[A-Za-z]:\\Users\\[^\\\s`"']+"
  },
  @{
    Label = "absolute macOS user path"
    Regex = "/Users/[^/\s`"']+"
  },
  @{
    Label = "personal maintainer identity"
    Regex = "(Yingxu Liu|liu\.yingxu\.vka@gmail\.com)"
  }
)

$textExtensions = @(
  ".bat",
  ".cmd",
  ".cfg",
  ".gitignore",
  ".ini",
  ".js",
  ".json",
  ".md",
  ".mjs",
  ".ps1",
  ".py",
  ".toml",
  ".ts",
  ".tsx",
  ".txt",
  ".yaml",
  ".yml"
)

function Get-GitPaths {
  param([string]$TargetScope)

  $output = switch ($TargetScope) {
    "staged" { & $gitExe -C $repoRoot diff --cached --name-only --diff-filter=ACMRTUXB }
    default { & $gitExe -C $repoRoot ls-files }
  }

  if (-not $output) {
    return @()
  }

  $paths = @($output | Where-Object { $_ } | ForEach-Object { $_.Trim() -replace "\\", "/" })
  if ($TargetScope -eq "repo") {
    $paths = @(
      $paths | Where-Object {
        $absolutePath = Join-Path -Path $repoRoot -ChildPath ($_ -replace "/", "\")
        Test-Path -LiteralPath $absolutePath
      }
    )
  }

  return $paths
}

function Get-PackagePaths {
  param([string]$TargetRoot)

  if (-not $TargetRoot) {
    throw "PackageRoot is required when Scope is 'package'."
  }

  if (-not (Test-Path -LiteralPath $TargetRoot -PathType Container)) {
    throw "Package root not found at $TargetRoot"
  }

  $resolvedRoot = (Resolve-Path -LiteralPath $TargetRoot).Path
  $items = Get-ChildItem -LiteralPath $resolvedRoot -Recurse -File
  if (-not $items) {
    return @()
  }

  return @($items | ForEach-Object {
      $_.FullName.Substring($resolvedRoot.Length).TrimStart('\', '/') -replace "\\", "/"
    } | Where-Object { $_ })
}

function Get-TargetPaths {
  param(
    [string]$TargetScope,
    [string]$TargetRoot
  )

  switch ($TargetScope) {
    "package" { return Get-PackagePaths -TargetRoot $TargetRoot }
    default { return Get-GitPaths -TargetScope $TargetScope }
  }
}

function Test-AllowedPath {
  param([string]$Path)

  foreach ($pattern in $allowedPathPatterns) {
    if ($Path -like $pattern) {
      return $true
    }
  }
  return $false
}

function Test-BlockedPath {
  param(
    [string]$Path,
    [string]$TargetScope
  )

  if (Test-AllowedPath -Path $Path) {
    return $false
  }

  $patterns = @($blockedPathPatterns)
  if ($TargetScope -eq "package") {
    $patterns += $packageBlockedPathPatterns
  }

  foreach ($pattern in $patterns) {
    if ($Path -like $pattern) {
      return $true
    }
  }
  return $false
}

function Test-TextFilePath {
  param([string]$Path)

  if ($Path -match "(^|/)\.[^/]+$") {
    return $true
  }

  $extension = [System.IO.Path]::GetExtension($Path)
  return $textExtensions -contains $extension
}

function Test-ContentScanExcludedPath {
  param([string]$Path)

  if ($contentScanExcludePaths -contains $Path) {
    return $true
  }

  foreach ($prefix in $contentScanExcludePrefixes) {
    if ($Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $true
    }
  }

  return $false
}

$scanRoot = $repoRoot
if ($Scope -eq "package") {
  if (-not $PackageRoot) {
    throw "PackageRoot is required when Scope is 'package'."
  }
  $scanRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
}

$paths = Get-TargetPaths -TargetScope $Scope -TargetRoot $PackageRoot
$pathViolations = @($paths | Where-Object { Test-BlockedPath -Path $_ -TargetScope $Scope })

$contentViolations = @()
foreach ($path in $paths) {
  if (Test-ContentScanExcludedPath -Path $path) {
    continue
  }

  if (-not (Test-TextFilePath -Path $path)) {
    continue
  }

  $absolutePath = $scanRoot
  foreach ($segment in ($path -split "/")) {
    $absolutePath = Join-Path -Path $absolutePath -ChildPath $segment
  }
  if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
    continue
  }

  foreach ($rule in $contentRules) {
    $matches = Select-String -Path $absolutePath -Pattern $rule.Regex -AllMatches
    foreach ($match in $matches) {
      $snippet = $match.Line.Trim()
      if ($snippet.Length -gt 160) {
        $snippet = $snippet.Substring(0, 160)
      }
      $contentViolations += [PSCustomObject]@{
        Path = $path
        Line = $match.LineNumber
        Rule = $rule.Label
        Snippet = $snippet
      }
    }
  }
}

if ($Scope -eq "package") {
  foreach ($path in $paths) {
    if (-not $path.EndsWith(".exe", [System.StringComparison]::OrdinalIgnoreCase)) {
      continue
    }

    $absolutePath = $scanRoot
    foreach ($segment in ($path -split "/")) {
      $absolutePath = Join-Path -Path $absolutePath -ChildPath $segment
    }
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
      continue
    }

    $versionInfo = (Get-Item -LiteralPath $absolutePath).VersionInfo
    $metadataValues = @(
      $versionInfo.CompanyName,
      $versionInfo.LegalCopyright
    ) | Where-Object { $_ }

    foreach ($metadataValue in $metadataValues) {
      if ($metadataValue -match "(Yingxu Liu|liu\.yingxu\.vka@gmail\.com)") {
        $contentViolations += [PSCustomObject]@{
          Path = $path
          Line = 0
          Rule = "personal maintainer identity in executable metadata"
          Snippet = $metadataValue
        }
      }
    }
  }
}

if ($pathViolations.Count -gt 0) {
  Write-Output "Blocked paths detected in scope '$Scope':"
  foreach ($path in $pathViolations) {
    Write-Output "  - $path"
  }
}

if ($contentViolations.Count -gt 0) {
  Write-Output "Suspicious tracked content detected in scope '$Scope':"
  foreach ($violation in $contentViolations) {
    Write-Output ("  - {0}:{1} [{2}] {3}" -f $violation.Path, $violation.Line, $violation.Rule, $violation.Snippet)
  }
}

if ($pathViolations.Count -gt 0 -or $contentViolations.Count -gt 0) {
  throw "Privacy audit failed."
}

Write-Output "Privacy audit passed for scope '$Scope'."
