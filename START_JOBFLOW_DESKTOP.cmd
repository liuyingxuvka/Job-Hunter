@echo off
setlocal
set "ROOT=%~dp0"
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%desktop_app\run_release.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Failed to start Jobflow Desktop App. Exit code: %EXIT_CODE%
  pause
)
exit /b %EXIT_CODE%
