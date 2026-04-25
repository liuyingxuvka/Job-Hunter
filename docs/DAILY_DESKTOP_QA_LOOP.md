# Daily Desktop QA Loop

This document defines the recurring Jobflow Desktop testing and improvement loop.

The goal is to use the packaged Windows app like a real daily job-search tool, capture product issues, make small scoped fixes, rebuild the app, and test the next build against the same local user data.

## Privacy Boundary

Do not commit personal runtime data.

Private files include:

- packaged-app SQLite databases
- resumes and resume-derived text
- screenshots that expose personal profile details
- daily exploratory reports under `runtime/daily_app_tests/`
- local app replacement folders under `runtime/local_app/`
- private profile pointers under `runtime/private/`

Public docs can describe the workflow, defect classes, and fix policy. They must not copy resume text, private notes, API keys, or personal contact details.

## Local Private Profile

Each real-user testing setup should have a local profile pointer file under:

`runtime/private/`

That file records where the current packaged EXE, local database, resume file, and daily reports live. It is ignored by Git and exists only on the user's machine.

Agents should read the local profile pointer before running the packaged app. If it is missing, recreate it by inspecting the current packaged app data and asking the user only when identity cannot be confirmed safely.

## Daily Test Run

Each scheduled test should:

1. Run predictive KB preflight.
2. Read `AGENTS.md`, this document, and the local private profile pointer.
3. Open the current packaged `Jobflow Desktop.exe`.
4. If yesterday ended with a rebuilt package, first verify the repaired UI/flow before starting a new search run.
5. Confirm the active candidate identity from app data without exposing private resume contents.
6. State the day's test focus in the report before interacting deeply.
7. Prefer real GUI use over direct database inspection, but use the database to verify persistence and counts.
8. Capture screenshots for important states.
9. Use one hour as the default daily timebox. Choose two, three, or four hours only when the day's focus needs a longer run.
10. Stop at the planned timebox unless the user explicitly asks for a longer run.
11. Save a concise Chinese report under `runtime/daily_app_tests/YYYY-MM-DD/`.
12. Run KB postflight and record reusable lessons.

## Default Focus Rotation

Prefer this rotation unless the user asks otherwise:

1. AI target-role recommendation quality.
2. AI-scored job recommendation quality.
3. Result duplication and noisy source filtering.
4. Manual role add/edit and validation feedback.
5. Search stop/resume behavior.
6. One-hour run completeness and scoring latency.
7. Packaging/data-preservation regression check.

Every report should include whether the run reached scored/recommended jobs. If it did not, record how long it ran, which stage it stayed in, and whether stopping scored the current found set.

## Report Format

Each report should include:

- test focus
- app build/path tested
- private data source pointer used, without copying private contents
- actions performed
- screenshots captured
- raw counts found/scored/recommended
- AI target-role quality judgment
- AI job recommendation quality judgment, when available
- duplicate/noisy-result observations
- defects with severity
- small-fix recommendations
- next test idea

## Report Maintenance Policy

Reports are local working records, not public release notes.

- Keep one folder per calendar day under `runtime/daily_app_tests/YYYY-MM-DD/`.
- Start the day with that day's test focus before deep app interaction.
- During the run, append observations to the same day's `report.md` instead of creating disconnected notes.
- After each repair batch, append a `Repair Update` section to the same day's report with the fixes, validation, package path, hashes when relevant, and unresolved follow-up checks.
- If a repair creates or replaces a packaged EXE, update the local private profile pointer under `runtime/private/` so the next run uses the correct executable and database path.
- Tomorrow's report should not overwrite today's report. It should reference yesterday's unresolved follow-up checks, then record fresh evidence in `runtime/daily_app_tests/YYYY-MM-DD/report.md`.
- Public docs may summarize the workflow and defect classes, but private screenshots, resume-derived observations, local paths, and daily reports stay ignored by Git.

## Repair Policy

Treat reports as a repair backlog, not as standalone notes.

After the user reviews a report, select a small batch of one to three fixes. Avoid broad architecture rewrites unless a narrow fix is impossible.

Good fix batches are:

- easy to explain
- easy to test
- isolated to the smallest relevant modules
- directly tied to report evidence
- safe for the user's local database

Do not combine unrelated UI redesign, search orchestration changes, packaging changes, and data migration work in one batch unless the user explicitly asks for that.

## Build And Replace Policy

After a fix batch:

1. Run targeted tests for the affected modules.
2. Run the strongest practical broader check.
3. Build a new packaged EXE.
4. Preserve or migrate the user's local database and resume pointers.
5. Replace the local test app path only after the build is validated.
6. Update the local private profile pointer if the app path changes.
7. Run a smoke test against the packaged EXE.

Do not publish a GitHub release for every local test build. Public releases require a separate privacy pass to ensure personal databases, resumes, screenshots, and local paths are excluded.

## First Fix Priorities From 2026-04-24

The first observed run suggests these small fixes, in this order:

1. Keep the daily search timebox useful: the app should offer one, two, three, or four hours, default to one hour, and continue tracking whether stop-time scoring is still needed.
2. Improve manual role add feedback so submit either saves, shows progress, or explains failure.
3. Filter generic listing pages, stale PDFs, non-specific job titles, and obviously mismatched technician roles before recommendation.
4. Make AI target-role recommendations produce market-facing role titles plus technical explanations, instead of only narrow idealized titles.

## Next Packaged-App Regression From 2026-04-25

The 2026-04-24 local package was rebuilt and replaced in the user's stable download folder after the two-tier model repair and package privacy metadata repair. The next scheduled run should verify these items first:

1. The tested EXE opens from the stable local path and still uses the user's existing database.
2. Settings shows `模型列表` before `快速模型` and `高质量模型`.
3. The `检测并加载模型` button is compact and not clipped.
4. Both fast and high-quality model choices are present and AI validation only passes when both are usable.
5. Windows package metadata remains neutral project metadata rather than a personal maintainer identity.
6. AI target-role recommendation and manual role completion still work after the packaged replacement.

After this regression check, continue with the day's normal job-search test focus.
