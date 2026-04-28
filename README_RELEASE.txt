Jobflow Desktop Release Package
===============================

1) Preferred: double-click `Jobflow Desktop.exe` to launch the packaged desktop app.
2) Fallback: double-click `START_JOBFLOW_DESKTOP.cmd` if Windows blocks direct `.exe` launch heuristically.
3) You can provide API settings either inside the app UI or by environment variables:
   - OPENAI_API_KEY
   - OPENAI_BASE_URL (optional)
   - JOBFLOW_OPENAI_MODEL (optional)
   - AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT (optional)

Notes:
- This package is intended for non-developer Windows users.
- On first launch, the app seeds only demo/default content, including the demo candidate resume.
- Runtime logs, exports, backups, user databases, and run outputs are stored under your local Windows user profile, not inside the replaceable app package.
- The package includes only demo/default content. It does not ship real user, customer, or search-history data.
- The app checks for GitHub Release updates quietly on startup. If an update is prepared, use the workspace-header update capsule to install now or leave it for a later restart.
