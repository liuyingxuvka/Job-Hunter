# Changelog

All notable changes to this repository will be documented in this file.

This project currently follows a lightweight semantic versioning approach:

- `patch` for small fixes, documentation updates, and low-risk maintenance
- `minor` for new user-facing capabilities or meaningful workflow expansion
- `major` for breaking changes or large architectural shifts

## [Unreleased]

## [0.8.5] - 2026-04-27

### Changed

- Migrated job search runtime state from duplicate run buckets to the durable candidate job pool, so discovered, scored, pending, and final recommended jobs share one candidate-scoped source of truth.
- Dropped the old `search_run_jobs` runtime table after startup backfill and moved legacy compatibility to a narrowly scoped migration path.
- Rebuilt recommendation output refresh so only the final output set marks jobs as visible recommendations, while AI screening stamps remain preserved for audit/debugging.

## [0.8.4] - 2026-04-26

### Fixed

- Fixed Jobflow Desktop search cancellation so stopped searches are persisted as cancelled/done and cannot be overwritten back into a running state by late worker callbacks.
- Fixed recommendation output consistency by requiring concrete final/apply job URLs for recommendation output and by materializing current `outputEligibility` stamps after append-mode historical recommendation merges.
- Fixed recommendation deduplication to prefer confirmed canonical job URLs, reducing repeated entries from generic source/search pages.

### Changed

- Simplified AI scoring context around factual career and education history, so prompt-based scoring can down-rank unsuitable intern, postdoc, and generic pages without adding brittle hard filters.
- Polished the public README positioning around reusable company memory, verified role discovery, and the long-term job-search workspace model.

## [0.8.3] - 2026-04-25

### Changed

- 优化岗位搜索链路可维护性、增加直接岗位发现流程并补充公司池与岗位评分可迭代关系的用户价值说明；本次为发布说明级更新
## [0.8.2] - 2026-04-25

### Added

- Added separate fast-model and high-quality-model OpenAI settings, with AI validation requiring both saved models to be available and usable.
- Added runtime model routing so high-volume job preranking and display translation can use the fast model while target-role recommendation, semantic profile extraction, manual role enrichment, company fit, formal job scoring, and post-verify use the high-quality model.
- Added the daily packaged-app QA loop document for the local test, repair, rebuild, and retest workflow.

### Changed

- Updated the AI settings dialog to show the model-list load action before the fast and high-quality model selectors, with a compact button layout that avoids clipping.
- Limited search duration choices to one, two, three, and four hours, with one hour as the default supported daily run length.
- Tightened AI target-role recommendation prompts toward concise market-facing job-board titles.

### Fixed

- Fixed manual target-role add so missing role type validation keeps the dialog open and preserves the draft.
- Fixed manual target-role AI enrichment so timeout or failure saves a draft role from the user's input instead of losing the attempt.
- Removed personal maintainer name/email defaults from the Windows package metadata and support dialog fallback.

## [0.8.1] - 2026-04-24

### Added

- Added a candidate rename action on the candidate selection page, preserving all existing candidate data while refreshing the selected list item.

### Changed

- Refined the compact candidate workspace layout so basics and target-role action buttons live inside their related white cards instead of detached footer areas.
- Tightened the basics page by removing the redundant visible name field, compressing preferred-location display, and aligning the left/right edit regions more consistently.
- Polished the candidate directory, compact workspace, target-role, and search-result styling for denser spacing, clearer selection states, and more consistent color usage.

## [0.8.0] - 2026-04-23

### Added

- Added the compact candidate workspace as the canonical desktop workflow, with denser basics, target-role, and job-search screens optimized for the real work area.
- Added job-list checkbox deletion with confirmation, recycle-bin restore flow, localized job display fields, and clearer review/status table styling.
- Added target-role recommendation mix handling for core, adjacent, and exploratory roles, plus manual role-type selection for AI enrichment.

### Changed

- Removed the old workspace UI path and prototype launchers so the application now opens a single current workspace implementation.
- Simplified search-result UI construction so the compact page builds its layout directly while reusing shared search behavior.
- Tightened candidate-directory and workspace text, button labels, color usage, table density, and save-action placement for clearer user flow.

## [0.7.0] - 2026-04-22

### Changed

- Promoted this release line to 0.7.0 to reflect the expanded AI-assisted company-first search workflow, stronger runtime state handling, and the stabilized Windows packaging path.
## [0.6.1] - 2026-04-22

### Added

- Added AI-assisted rescue for known job-detail pages when normal detail fetching falls into anti-bot or interstitial shells, so concrete roles can still be analyzed instead of being dropped immediately.

### Changed

- Refined the Python-native company-first search pipeline around company-first discovery, company fit retries, non-ATS source coverage, and work-unit state handling so multi-session search runs progress more predictably.
- Simplified runtime state ownership by removing stale fields, dead projections, and duplicated lifecycle helpers that were no longer driving the active search pipeline.

### Fixed

- Fixed the Windows release packaging contract so packaged builds now include the newer `search_discovery` and `search_ranking` prompt assets used by the active search flow.
- Fixed a results-page UI regression where stale blocker text could remain visible after AI prerequisite states were cleared.

## [0.6.0] - 2026-04-18

### Added

- Added a Python-native search module tree under `jobflow_desktop_app/search/` with focused orchestration, company sourcing, runtime-state, output, and stage-execution modules instead of keeping the search path behind the old monolithic legacy engine files.
- Added a dedicated `jobflow_desktop_app/ai/` package, packaged prompt assets, repository-specific `AGENTS.md` guidance, and a broad regression suite under `desktop_app/tests/` that now covers search orchestration, runtime persistence, UI control flow, and AI prompt parsing behavior.
- Added new runtime persistence seams for search runs, candidate company pools, per-run job buckets, semantic profiles, and richer review-state keys in SQLite.

### Changed

- Reorganized the desktop UI into direct page, dialog, widget, and context modules so the main window now imports real page implementations instead of a single forwarding `pages.py` mega-file.
- Removed the old `legacy_jobflow_reference/` execution path and portable Node runtime from the active repository and Windows package flow; the desktop app and release process now assume a Python-native search pipeline end to end.
- Renamed the candidate-scoped runtime workspace from `runtime/legacy_runs/` to `runtime/search_runs/` and tightened repository boundary docs, `.gitignore`, and privacy auditing around the new runtime layout.
- Hardened database bootstrap and schema migrations for older local databases by backfilling newer runtime/review columns and migrating candidate company storage away from the old pool-name shape.
- Updated the GitHub release workflow to validate that the pushed tag version matches `desktop_app/pyproject.toml`, and fixed the privacy audit so deleting blocked runtime artifacts no longer fails the release gate.

## [0.5.0] - 2026-04-13

### Added

- Added company-fit reranking and richer company-discovery metadata in the legacy engine, including company identity merging, source evidence retention, registry-aware keys, and signal-count accumulation across discovered companies.
- Added region and location-preference parsing in the legacy engine so company prioritization and downstream discovery can react more directly to target geography.

### Changed

- Reworked the adaptive legacy search loop to resume unfinished jobs first, refresh resume-pending payloads more safely, and continue timed search rounds with cleaner idle backoff when discovery temporarily stalls.
- Improved the desktop workflow coordination between Step 2 target-role AI work and Step 3 search execution so searches stay blocked while AI enrichment is still running, with clearer busy-state messages and safer background-work shutdown.
- Hardened search-profile persistence to sanitize incomplete text values and malformed query payloads before saving instead of crashing on partial UI state.
- Added public `companyFit` defaults to the legacy example configs and clarified in the legacy README that company-fit scores are run-local reranking signals, not permanent stored labels.
- Refined desktop button disabled-state styling and routed unhandled UI callback failures into `crash.log` with user-facing diagnostics instead of failing silently.
- Updated the Windows packaging metadata so the packaged `.exe` carries branded project information in its embedded version metadata.

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
- Added repository-facing agent integration documentation, GitHub issue intake for automation requests, and repository support metadata.
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




