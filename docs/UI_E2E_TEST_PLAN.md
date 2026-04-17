# UI / E2E Test Plan

This document tracks the highest-value desktop UI and end-to-end regression cases for Job Hunter.

## Automated Coverage

The following offscreen `unittest + PySide6.QtTest` cases are already implemented under `desktop_app/tests/`:

1. `test_candidate_directory_page.py`
   - Create candidate
   - Open workspace
   - Delete candidate

2. `test_main_window_smoke.py`
   - Open workspace from the candidate directory
   - Verify the workspace page and status bar update

3. `test_target_direction_step.py`
   - Manual role add without API
   - Save role details
   - Reload without unexpected write-back from bilingual UI normalization

4. `test_search_results_step.py`
   - Block search when AI status is red
   - Render results from a fake runner
   - Delete a result row
   - Persist hidden rows across reload

Run them with:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\desktop_app\.venv\Scripts\python.exe -m unittest discover -s .\desktop_app\tests -v
```

## Manual Priority Cases

These are the next high-value manual regression passes. They remain important because they exercise async behavior, real runtime state, and cross-step timing.

1. Empty candidate list -> create first candidate -> open workspace
   - Expected: opens workspace directly, no blank page, all three steps bind to the new candidate.

2. Delete the only candidate -> create a new one -> open workspace
   - Expected: no stale "please select a candidate" state.

3. Delete candidate with data
   - Expected: database rows cascade-delete correctly and UI returns to empty state.

4. No API key -> try Step 3 search
   - Expected: blocked immediately, no countdown, no background search.

5. Invalid / missing / model-unverified AI state -> try Step 3 search
   - Expected: blocked immediately.

6. Step 2 AI busy -> try Step 3 search
   - Expected: blocked with explicit "Step 2 is still processing" message.

7. No active target roles -> try Step 3 search
   - Expected: blocked until at least one role is enabled.

8. Candidate A/B isolation
   - Expected: roles, saved statuses, and hidden rows do not leak between candidates.

9. Start -> stop -> queue next round during shutdown
   - Expected: button text, countdown, and status messages remain coherent; queued round starts after shutdown completes.

10. Background refresh with no new jobs
   - Expected: list does not jump or redraw unnecessarily.

11. Status persistence
   - Expected: `focus / applied / dropped` survive app restart for the same candidate only.

12. Hidden-row persistence
   - Expected: deleted rows stay hidden across reloads and restart.

13. Resume-pending recovery
   - Expected: interrupted jobs are resumed first on next search.

14. Resume-pending stats consistency
   - Expected: "company pool / discovered / scored / pending" match the SQLite runtime state exactly.

## Known Fragile Areas

1. Hidden-row identity
   - Hidden rows are keyed by URL first, then by a fallback composite key. If the URL changes or fallback fields drift, a logically identical job can reappear.

2. Persistence boundary changes
- Search runtime and review state now live in SQLite. `runtime/search_runs/` is only a transient per-candidate workspace for exports and working files, not a fallback state source.

3. Candidate deletion leftovers
   - Candidate deletion currently removes database rows, but runtime files and candidate-scoped UI settings may still need explicit cleanup verification.

4. Search async control flow
   - The `start / stop / queued restart` path is the easiest place for race conditions. Keep this in every release smoke pass.
