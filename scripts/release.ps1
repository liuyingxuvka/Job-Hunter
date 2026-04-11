param(
  [ValidateSet("patch", "minor", "major")]
  [string]$Bump = "patch",
  [string]$Date = "",
  [string]$Summary = "",
  [switch]$Tag
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pyprojectPath = Join-Path $repoRoot "desktop_app\pyproject.toml"
$changelogPath = Join-Path $repoRoot "CHANGELOG.md"
$gitExe = "C:\Program Files\Git\cmd\git.exe"

if (-not (Test-Path -LiteralPath $pyprojectPath)) {
  throw "pyproject.toml not found at $pyprojectPath"
}

if (-not (Test-Path -LiteralPath $changelogPath)) {
  throw "CHANGELOG.md not found at $changelogPath"
}

function Get-CurrentVersion {
  param([string]$Content)
  $match = [regex]::Match($Content, 'version = "(\d+)\.(\d+)\.(\d+)"')
  if (-not $match.Success) {
    throw "Could not find semantic version in pyproject.toml"
  }
  return [PSCustomObject]@{
    Major = [int]$match.Groups[1].Value
    Minor = [int]$match.Groups[2].Value
    Patch = [int]$match.Groups[3].Value
    Raw = $match.Groups[0].Value
  }
}

function Get-BumpedVersion {
  param(
    [int]$Major,
    [int]$Minor,
    [int]$Patch,
    [string]$Type
  )
  switch ($Type) {
    "major" { return "{0}.{1}.{2}" -f ($Major + 1), 0, 0 }
    "minor" { return "{0}.{1}.{2}" -f $Major, ($Minor + 1), 0 }
    default { return "{0}.{1}.{2}" -f $Major, $Minor, ($Patch + 1) }
  }
}

function Get-ReleaseDate {
  param([string]$RequestedDate)
  if ($RequestedDate) {
    return $RequestedDate
  }
  return (Get-Date).ToString("yyyy-MM-dd")
}

$pyprojectContent = Get-Content -LiteralPath $pyprojectPath -Raw
$currentVersion = Get-CurrentVersion -Content $pyprojectContent
$nextVersion = Get-BumpedVersion -Major $currentVersion.Major -Minor $currentVersion.Minor -Patch $currentVersion.Patch -Type $Bump
$releaseDate = Get-ReleaseDate -RequestedDate $Date
$summaryLine = if ($Summary) { "- $Summary" } else { "- Describe this release." }

$updatedPyproject = [regex]::Replace(
  $pyprojectContent,
  'version = "\d+\.\d+\.\d+"',
  ('version = "{0}"' -f $nextVersion),
  1
)
Set-Content -LiteralPath $pyprojectPath -Value $updatedPyproject -Encoding UTF8

$changelogContent = Get-Content -LiteralPath $changelogPath -Raw
$newSection = @"
## [$nextVersion] - $releaseDate

### Changed

$summaryLine

"@

$updatedChangelog = [regex]::Replace(
  $changelogContent,
  '(?ms)^## \[Unreleased\]\s*\r?\n',
  "## [Unreleased]`r`n`r`n$newSection",
  1
)
Set-Content -LiteralPath $changelogPath -Value $updatedChangelog -Encoding UTF8

Write-Output "Updated version: $($currentVersion.Major).$($currentVersion.Minor).$($currentVersion.Patch) -> $nextVersion"
Write-Output "Updated changelog: $changelogPath"

if ($Tag) {
  if (-not (Test-Path -LiteralPath $gitExe)) {
    throw "Git executable not found at $gitExe"
  }
  & $gitExe -C $repoRoot tag "v$nextVersion"
  Write-Output "Created git tag v$nextVersion"
}
