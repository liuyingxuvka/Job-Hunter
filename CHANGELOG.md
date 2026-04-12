# Changelog

All notable changes to this repository will be documented in this file.

This project currently follows a lightweight semantic versioning approach:

- `patch` for small fixes, documentation updates, and low-risk maintenance
- `minor` for new user-facing capabilities or meaningful workflow expansion
- `major` for breaking changes or large architectural shifts

## [Unreleased]

## [0.4.0] - 2026-04-12

### Added

- Added AI-derived candidate semantic profiles that extract reusable business domains, strengths, and target directions from candidate context for downstream search and recommendation flows.
- Added semantic business phrase libraries and weighted discovery-anchor planning so company discovery and search-query generation can work from business areas instead of only direct role-name terms.

### Changed

- Expanded the legacy search runtime to reuse AI semantic profiles for company discovery, query planning, and search-track mixing across main, resume-pending, and web-signal stages.
- Improved legacy job-detail extraction by filtering noisy pseudo-titles more aggressively and recovering missing job titles from metadata, headings, and URL slugs when structured data is weak.
- Refined search-results status copy so the desktop UI emphasizes workflow state instead of repeatedly restating visible result counts.
- Updated desktop application icon assets.
- Fixed the Windows release build script so local packaging can resolve a single discovered Python executable correctly without requiring an explicit `-PythonExe` override.

## [0.3.1] - 2026-04-12

### Added

- Added branded Windows application icon assets for the desktop app and packaged executable.
- Added crash logging for uncaught desktop runtime exceptions so failures leave a local `crash.log` under the runtime logs directory.

### Changed

- Improved desktop shutdown behavior so workspace background tasks and AI validation threads are stopped more cleanly during window close, UI rebuilds, and language switches.
- Updated Windows runtime resolution to prefer bundled Node and npm binaries before falling back to system PATH, which makes the packaged app more predictable across machines.
- Changed Windows legacy subprocess launches to avoid flashing console windows during background operations.
- Updated the Windows release build script to require and embed the application icon into the packaged `Jobflow Desktop.exe`.

## [0.3.0] - 2026-04-12

### Added

- Added an experimental `jobflow-agent` CLI that exposes JSON-based overview, candidate/profile inspection, and AI role recommendation entry points for headless automation.
- Added repository-facing agent integration documentation, GitHub issue intake for automation requests, and GitHub Sponsors funding metadata.
- Added `pypdf` support and package metadata updates so desktop installs can parse text-based PDF resumes more reliably and surface better project metadata.

### Changed

- Improved workspace navigation so opening the desktop workspace reliably follows the selected candidate instead of silently failing when the current candidate state is stale.
- Reframed the candidate notes field as a professional background summary and used that summary as structured AI context when recommending or refining target roles.
- Hardened resume handling for both AI prompts and legacy search runs by normalizing readable resumes, surfacing clearer errors for unreadable files, and falling back to structured candidate summaries when needed.
- Documented that user-facing version releases should ship the matching Windows package and `.sha256` asset through GitHub Releases, not just the source commit.

## [0.2.1] - 2026-04-12

### Added

- Added a Windows release packaging workflow that builds a downloadable `Job-Hunter-<version>-win64.zip` archive plus `.sha256` checksum for GitHub Releases.
- Added release-package privacy auditing so packaged assets exclude local databases, exports, logs, backups, and search outputs while still shipping demo/default content.

### Changed

- Made the desktop app bundle-aware so the packaged executable can resolve runtime directories, assets, schema files, and the legacy engine layout after extraction.
- Updated repository and desktop-app documentation to distinguish source-checkout startup from the packaged Windows release workflow.

## [0.2.0] - 2026-04-12

### Added

- Added staged background search execution with progress reporting, cancellation support, and pending-job resume handling in the desktop workspace.
- Added adaptive company discovery controls in the legacy search engine, including concurrency limits, company cooldowns, and carryover handling for unfinished analysis.

### Changed

- Added workspace-level AI health feedback so the app can surface API/model readiness and restore saved model choices more clearly.
- Changed the search workflow to start from a cleaner candidate company pool instead of pre-baked demo company seeds.
- Removed `company_seed_list` seeding from demo profiles and profile persistence.
- Improved Windows startup script Python auto-detection for local venv and common local Python installs.

## [0.1.3] - 2026-04-12

### Changed

- Hardened repository privacy boundaries, replaced local working files with safe templates, and added automated privacy checks.

## [0.1.2] - 2026-04-11

### Added

- Added a GitHub Actions workflow to create a GitHub Release automatically when a semantic version tag is pushed.

### Changed

- Made repository docs and collaboration templates bilingual, and expanded contribution guidance.
- Synced the package runtime version in `desktop_app/src/jobflow_desktop_app/__init__.py` with `desktop_app/pyproject.toml`.
- Updated `scripts/release.ps1` so future version bumps also sync the package runtime version.

## [0.1.1] - 2026-04-11

### Added

- Added a root repository README with clear product positioning, target users, workflow, and project structure.
- Added `docs/` project documentation for product positioning, architecture, roadmap, and GitHub repository setup guidance.
- Added `CONTRIBUTING.md` and GitHub issue / pull request templates.
- Added a local PowerShell release helper script at `scripts/release.ps1`.

### Changed

- Updated `desktop_app/README.md` to match the current codebase state and remove references to missing files or scripts.
- Established a lightweight repository-level changelog and versioning process for future updates.

## [0.1.0] - 2026-04-11

### Added

- Initial desktop application scaffold based on PySide6.
- Local SQLite data model for candidates, search profiles, settings, jobs, analyses, and review states.
- Legacy job discovery engine integration through `legacy_jobflow_reference/`.




