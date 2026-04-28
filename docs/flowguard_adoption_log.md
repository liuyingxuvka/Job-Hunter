# Flowguard Adoption Log

## 2026-04-28 - Jobflow Search Loop

- Task id: `jobflow-search-loop-model-20260428`
- Project: Job-Hunter
- Task summary: evaluate `model-first-function-flow` on the timed Jobflow search session loop.
- Trigger reason: the workflow has repeated rounds, retries, uncertain AI outputs, stateful stop conditions, and side effects through jobs/companies/recommendations.
- Model files:
  - `.flowguard/job_search_loop/model.py`
  - `.flowguard/job_search_loop/run_checks.py`
- Commands run:
  - `PYTHONPATH=<local FlowGuard source> python .flowguard/job_search_loop/run_checks.py`
- Findings:
  - The correct model passed exhaustive exploration for direct discovery, existing company sources, company discovery, and stop decision sequences.
  - A broken one-empty-round stop variant failed `no_stop_before_three_empty_rounds`, reproducing the earlier early-stop bug class.
  - A broken skip-company-discovery variant failed `discovery_attempted_for_each_completed_round`, reproducing the old architecture where existing company work could suppress discovery.
  - Scenario review passed for three empty rounds, progress reset, and sources-progress-still-discovers-company cases.
  - Loop review found no stuck bottom SCCs in the bounded abstract graph.
  - Progress review reported `potential_nontermination` and `missing_progress_guarantee` because productivity and timebox expiration are external fairness inputs rather than internally forced progress.
- Counterexamples:
  - Single empty round stopping immediately.
  - Direct/source progress causing company discovery to be skipped.
- Skipped steps:
  - Production conformance replay was skipped in this first adoption pass; the model is not yet wired to replay mocked `search_session_orchestrator` traces.
  - FunctionContract checks were skipped because the pass focused on round-level behavior, not API projection/refinement.
- Friction points:
  - `flowguard` was available as a local source checkout but was not importable from the project environment until `PYTHONPATH` was set.
  - The skill path did not explain dependency discovery or how to locate the local FlowGuard checkout.
  - Progress/fairness review is useful but needs interpretation so expected timebox-dependence is not mistaken for a failing implementation.
- Next actions:
  - Add a thin conformance replay adapter for mocked `search_session_orchestrator` stages if this model becomes part of normal development.
  - Decide whether the production loop should rotate a visible strategy token on each empty round; the model currently treats retry variation as outside the abstract state.

## 2026-04-28 - Target Role Reset Cleanup

- Task id: `target-role-reset-cleanup-20260428`
- Project: Job-Hunter
- Task summary: model and fix stale target-role bindings after `search_profiles` replacement/deletion.
- Trigger reason: the workflow has profile lifecycle changes, JSON cache invalidation, foreign-key writes, repeated runtime persistence, and user review fields that must be preserved.
- Model files:
  - `.flowguard/target_role_reset/model.py`
  - `.flowguard/target_role_reset/run_checks.py`
- Commands run:
  - `python -c "import flowguard; print(flowguard.SCHEMA_VERSION)"`
  - `python .flowguard/target_role_reset/run_checks.py`
  - `python -m unittest desktop_app.tests.test_target_role_cleanup`
  - `python -m unittest desktop_app.tests.test_runtime_job_sync desktop_app.tests.test_search_runtime_mirror desktop_app.tests.test_target_direction_regressions desktop_app.tests.test_role_recommendations_prompts`
  - `python -m unittest desktop_app.tests.test_search_results_regressions desktop_app.tests.test_job_search_runner_regressions`
  - `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -SkipZip`
- Findings:
  - Correct model passed 456 explored traces with no invariant violations.
  - Broken no-JSON-cleanup model failed `no_stale_json_bindings`.
  - Broken no-cleanup/no-write-guard model failed `no_fk_failures`.
  - Production fix cleans stale `candidate_jobs` role-bound analysis on profile deletion and bootstrap, skips missing-profile `job_analyses` writes, and sanitizes incoming runtime pool payloads.
  - Packaged startup cleanup reduced real LocalAppData stale candidate-job rows from 24 to 0, with `PRAGMA foreign_key_check` still clean.
- Counterexamples:
  - Profile replacement with FK cascade but stale `candidate_jobs` JSON retained a deleted profile id.
  - Unsafe external delete with relation cleanup still retained stale JSON without the new cleanup.
  - Runtime write from stale JSON without a guard reproduced the foreign-key failure class.
- Skipped steps:
  - Full interactive GUI search was not run in this pass; the user planned to run the final real app flow together with other concurrent changes.
  - Loop/stuck review was skipped because this model has no retry loop or cyclic wait state.
  - FunctionContract checks were skipped; focused unit tests cover the production projection boundaries instead.
- Friction points:
  - Scenario review initially counted the intentionally dirty pre-bootstrap database as an invariant violation before any repair step; invariants were scoped to post-step states.
  - Candidate job pool upsert preserves old analysis when incoming rows are pending, so stale cleanup must run as direct repair rather than ordinary pending upsert.
- Next actions:
  - Add a production conformance replay if this cleanup boundary evolves further.
  - Run the full user-facing app search flow after other concurrent changes settle.
