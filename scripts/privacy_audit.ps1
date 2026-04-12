param(
  [ValidateSet("repo", "staged")]
  [string]$Scope = "repo"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$gitExe = "C:\Program Files\Git\cmd\git.exe"

if (-not (Test-Path -LiteralPath $gitExe)) {
  throw "Git executable not found at $gitExe"
}

$blockedPathPatterns = @(
  "legacy_jobflow_reference/config.json",
  "legacy_jobflow_reference/config.adjacent.json",
  "legacy_jobflow_reference/companies.json",
  "legacy_jobflow_reference/companies_adjacent.json",
  "legacy_jobflow_reference/resume.md",
  "legacy_jobflow_reference/jobs*.json",
  "legacy_jobflow_reference/jobs*.xlsx",
  "legacy_jobflow_reference/config.generated*.json",
  "legacy_jobflow_reference/resume.generated.md",
  "legacy_jobflow_reference/companies.candidate.json",
  "desktop_app/runtime/data/jobflow_desktop.db*",
  "desktop_app/runtime/backups/*",
  "desktop_app/runtime/exports/*",
  "desktop_app/runtime/logs/*",
  "desktop_app/runtime/legacy_runs/*"
)

$allowedPathPatterns = @(
  "desktop_app/runtime/backups/.gitignore",
  "desktop_app/runtime/exports/.gitkeep",
  "desktop_app/runtime/logs/.gitkeep",
  "desktop_app/runtime/legacy_runs/.gitkeep"
)

$contentScanExcludePaths = @(
  "scripts/privacy_audit.ps1"
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

  $command = switch ($TargetScope) {
    "staged" { "diff --cached --name-only --diff-filter=ACMRTUXB" }
    default { "ls-files" }
  }

  $output = & $gitExe -C $repoRoot $command.Split(" ")
  if (-not $output) {
    return @()
  }

  return @($output | Where-Object { $_ } | ForEach-Object { $_.Trim() -replace "\\", "/" })
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
  param([string]$Path)

  if (Test-AllowedPath -Path $Path) {
    return $false
  }

  foreach ($pattern in $blockedPathPatterns) {
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

  return $contentScanExcludePaths -contains $Path
}

$paths = Get-GitPaths -TargetScope $Scope
$pathViolations = @($paths | Where-Object { Test-BlockedPath -Path $_ })

$contentViolations = @()
foreach ($path in $paths) {
  if (Test-ContentScanExcludedPath -Path $path) {
    continue
  }

  if (-not (Test-TextFilePath -Path $path)) {
    continue
  }

  $absolutePath = Join-Path $repoRoot ($path -replace "/", "\")
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

if ($pathViolations.Count -gt 0) {
  Write-Output "Blocked paths detected in git scope '$Scope':"
  foreach ($path in $pathViolations) {
    Write-Output "  - $path"
  }
}

if ($contentViolations.Count -gt 0) {
  Write-Output "Suspicious tracked content detected in git scope '$Scope':"
  foreach ($violation in $contentViolations) {
    Write-Output ("  - {0}:{1} [{2}] {3}" -f $violation.Path, $violation.Line, $violation.Rule, $violation.Snippet)
  }
}

if ($pathViolations.Count -gt 0 -or $contentViolations.Count -gt 0) {
  throw "Privacy audit failed."
}

Write-Output "Privacy audit passed for scope '$Scope'."
