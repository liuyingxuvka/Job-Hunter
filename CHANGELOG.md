# Changelog

All notable changes to this repository will be documented in this file.

This project currently follows a lightweight semantic versioning approach:

- `patch` for small fixes, documentation updates, and low-risk maintenance
- `minor` for new user-facing capabilities or meaningful workflow expansion
- `major` for breaking changes or large architectural shifts

## [Unreleased]

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


