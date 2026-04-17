$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Resolve-PythonCommand {
  $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return @{
      Exe = $venvPython
      Prefix = @()
    }
  }

  if ($env:JOBFLOW_PYTHON_PATH -and (Test-Path -LiteralPath $env:JOBFLOW_PYTHON_PATH)) {
    return @{
      Exe = $env:JOBFLOW_PYTHON_PATH
      Prefix = @()
    }
  }

  $pythonFromPath = Get-Command python -ErrorAction SilentlyContinue
  if ($pythonFromPath -and $pythonFromPath.Path) {
    return @{
      Exe = $pythonFromPath.Path
      Prefix = @()
    }
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher -and $pyLauncher.Path) {
    return @{
      Exe = $pyLauncher.Path
      Prefix = @("-3")
    }
  }

  $windowsStorePython = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.*\python.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1
  if ($windowsStorePython) {
    return @{
      Exe = $windowsStorePython.FullName
      Prefix = @()
    }
  }

  $commonPython = Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1
  if ($commonPython) {
    return @{
      Exe = $commonPython.FullName
      Prefix = @()
    }
  }

  throw "Python executable not found. Set JOBFLOW_PYTHON_PATH or install Python 3.10+."
}

if (-not $env:OPENAI_API_KEY -and $env:AZURE_OPENAI_API_KEY) {
  $env:OPENAI_API_KEY = $env:AZURE_OPENAI_API_KEY
}
if (-not $env:JOBFLOW_OPENAI_MODEL) {
  if ($env:AZURE_OPENAI_MODEL) {
    $env:JOBFLOW_OPENAI_MODEL = $env:AZURE_OPENAI_MODEL
  } elseif ($env:AZURE_OPENAI_DEPLOYMENT) {
    $env:JOBFLOW_OPENAI_MODEL = $env:AZURE_OPENAI_DEPLOYMENT
  }
}

$src = Join-Path $projectRoot "src"
$deps = Join-Path $projectRoot ".deps"
$env:PYTHONPATH = "$src;$deps"

$pythonCommand = Resolve-PythonCommand
$pythonArgs = @()
$pythonArgs += $pythonCommand.Prefix
$pythonArgs += "-m"
$pythonArgs += "jobflow_desktop_app.main"

& $pythonCommand.Exe @pythonArgs
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
