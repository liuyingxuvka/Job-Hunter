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

## 2026-04-29 - Recommendation Persistence Visible History

- Task id: `recommendation-persistence-visible-history-20260429`
- Project: Job-Hunter
- Task summary: preserve jobs that have already entered the recommendation table while labeling stale or no-longer-current target-role bindings.
- Trigger reason: the workflow changes target-role update/delete behavior, visible recommendation lifecycle, rescore overwrite behavior, JSON analysis state, runtime write guards, and prompt-side dedupe quality.
- Model files:
  - `.flowguard/target_role_reset/model.py`
  - `.flowguard/target_role_reset/run_checks.py`
- Commands run:
  - `python -c "import flowguard; print(flowguard.SCHEMA_VERSION)"`
  - `python .flowguard/target_role_reset/run_checks.py`
  - `python -m unittest desktop_app.tests.test_target_role_cleanup`
  - `python -m unittest desktop_app.tests.test_candidate_job_pool`
  - `python -m unittest desktop_app.tests.test_job_search_runner_records desktop_app.tests.test_search_results_row_rendering desktop_app.tests.test_search_results_live_state desktop_app.tests.test_role_recommendations_prompts`
  - `python -m unittest desktop_app.tests.test_target_role_cleanup desktop_app.tests.test_candidate_job_pool desktop_app.tests.test_search_runtime_mirror desktop_app.tests.test_runtime_job_sync`
  - `python -m unittest desktop_app.tests.test_search_results_regressions desktop_app.tests.test_job_search_runner_regressions desktop_app.tests.test_target_direction_regressions desktop_app.tests.test_role_recommendations_prompts`
  - `python -m compileall -q desktop_app\src\jobflow_desktop_app .flowguard\target_role_reset`
  - `python -m unittest discover desktop_app\tests`
- Findings:
  - Correct model passed 399 sequences across 4 initial states and 4536 traces.
  - The safe policy is `keep-visible-labeled`: once a job has been shown in the recommendation table, target-role edits/deletes must not silently remove it.
  - Visible stale or no-longer-current rows must carry a UI-visible status such as `needs_rescore` or `not_current_fit`; otherwise old recommendation reasons look current.
  - Unshown stale rows can still be reset to pending because there is no user-visible recommendation history to preserve.
  - Current rescore reject should preserve an already visible row and mark it `not_current_fit`, rather than overwriting `pass/pass` visibility with reject.
  - Recommended-output refresh exclusion should preserve an already visible row and mark it `historical_only`, rather than changing `output_status` to reject.
  - Runtime job-analysis writes still need the missing-profile guard so preserved historical display state does not reintroduce foreign-key failures.
  - Prompt-side target-role dedupe was strengthened with distinct hiring-lane and job-board query-lane requirements, without adding hard semantic rejection logic.
- Counterexamples:
  - Resetting visible stale recommendations violates `shown_recommendations_stay_visible`.
  - Keeping stale visible rows without a historical/current-fit label violates `visible_stale_bindings_are_labeled`.
  - Overwriting a previously visible recommendation with a current rescore reject violates `shown_recommendations_stay_visible`.
  - Overwriting a previously visible recommendation during recommended-output refresh violates `shown_recommendations_stay_visible`.
  - Removing the runtime write guard can still reproduce `no_fk_failures`.
- Skipped steps:
  - Packaged Windows build was not run in this pass; validation used FlowGuard and Python regression tests.
  - Interactive GUI QA was not rerun after code edits; the next packaged-app desktop test should cover the real UI labels.
  - Production conformance replay remains a future improvement; current coverage is model exploration plus focused DB/runtime unit tests.
- Friction points:
  - The earlier `target_role_reset` model encoded cleanup-to-pending semantics too narrowly and had to distinguish shown recommendations from unshown stale rows.
  - A final output refresh path had to be modeled separately from current rescore rejects because it can also remove rows from the visible recommendation table.
  - Prompt-only dedupe can reduce drift but cannot guarantee semantic uniqueness without future hard or review-only checks.
- Next actions:
  - Run the next packaged-app QA to verify visible historical/current-fit labels in the real desktop UI.
  - If recommendation persistence changes again, add a replay adapter that projects real `candidate_jobs` rows into the FlowGuard state model.

## 2026-04-30 - Final Output Detail Verification

- Task id: `final-output-detail-verification-20260430`
- Project: Job-Hunter
- Task summary: require current detail-page verification before new jobs enter final recommendations.
- Trigger reason: the workflow changes recommendation output visibility, cached eligibility stamps, detail-page evidence requirements, and historical recommendation preservation.
- Model files:
  - `.flowguard/final_output_verification/model.py`
  - `.flowguard/final_output_verification/run_checks.py`
- Commands run:
  - `python -c "import flowguard; print(flowguard.SCHEMA_VERSION)"`
  - `python .flowguard/final_output_verification/run_checks.py`
  - `python -m unittest desktop_app.tests.test_final_output desktop_app.tests.test_runtime_config_builder desktop_app.tests.test_stage_executor_resume_pending desktop_app.tests.test_job_search_runner_manual_tracking`
  - `python -m unittest desktop_app.tests.test_candidate_job_pool desktop_app.tests.test_job_search_runner_db_reads desktop_app.tests.test_job_search_runner_records desktop_app.tests.test_job_search_runner_unit desktop_app.tests.test_direct_job_discovery_stage desktop_app.tests.test_search_results_regressions`
  - `python -m compileall -q desktop_app\src\jobflow_desktop_app .flowguard\final_output_verification`
  - `python -m unittest discover desktop_app\tests`
- Findings:
  - Correct model passed 78 explored traces with no invariant violations.
  - Broken no-verify output exposed expired, generic, and unreachable new recommendations entering final output without a valid detail-page stamp.
  - Broken apply-link output exposed apply-form links becoming the primary click target, violating the product rule that users open a job detail page.
  - Broken historical recheck exposed routine rechecking of already visible historical recommendations.
  - Production code now enables post-verify by default for main and resume/finalize runs, requires checked post-verify for new final output, rejects skipped post-verify when checks are required, and preserves already visible historical recommendations without routine recheck.
- Counterexamples:
  - New expired detail page with an apply link became visible when final output ignored the detail verification stamp.
  - New valid detail page used apply as the primary output when the output policy preferred apply links.
  - Historical visible recommendation was rechecked during output refresh.
- Skipped steps:
  - Production conformance replay was skipped; focused unit tests cover final output, runtime config, pool, runner, direct-discovery, and search-results projections.
  - Interactive desktop GUI QA was not run in this pass.
- Friction points:
  - One PowerShell quoting attempt broke while recording the adoption-finish command; executable checks themselves passed and a corrected adoption-finish entry was recorded.
- Next actions:
  - Run a small real search smoke with fresh results to observe postVerify cost and final recommendation yield.


## final-output-detail-verification-20260430 - Require current detail-page verification before new jobs enter final recommendations

- Project: Job-Hunter
- Trigger reason: The change affects recommendation output visibility, cached eligibility stamps, and historical recommendation preservation.
- Status: in_progress
- Skill decision: used_flowguard
- Started: 2026-04-30T07:34:01+00:00
- Ended: 2026-04-30T07:34:01+00:00
- Duration seconds: 0.000
- Commands OK: True

### Model Files
- none recorded

### Commands
- none recorded

### Findings
- none recorded

### Counterexamples
- none recorded

### Friction Points
- none recorded

### Skipped Steps
- none recorded

### Next Actions
- none recorded


## final-output-detail-verification-20260430 - Require current detail-page verification before new jobs enter final recommendations

- Project: Job-Hunter
- Trigger reason: The change affects recommendation output visibility, cached eligibility stamps, and historical recommendation preservation.
- Status: completed
- Skill decision: used_flowguard
- Started: 2026-04-30T07:45:08+00:00
- Ended: 2026-04-30T07:45:08+00:00
- Duration seconds: 0.000
- Commands OK: False

### Model Files
- .flowguard/final_output_verification/model.py
- .flowguard/final_output_verification/run_checks.py

### Commands
- OK (0.000s): `python -c "import flowguard; print(flowguard.SCHEMA_VERSION)"`
- OK (0.000s): `python .flowguard/final_output_verification/run_checks.py`
- OK (0.000s): `python -m unittest desktop_app.tests.test_final_output desktop_app.tests.test_runtime_config_builder desktop_app.tests.test_stage_executor_resume_pending desktop_app.tests.test_job_search_runner_manual_tracking`
- OK (0.000s): `python -m unittest desktop_app.tests.test_candidate_job_pool desktop_app.tests.test_job_search_runner_db_reads desktop_app.tests.test_job_search_runner_records desktop_app.tests.test_job_search_runner_unit desktop_app.tests.test_direct_job_discovery_stage desktop_app.tests.test_search_results_regressions`
- OK (0.000s): `python -m compileall -q desktop_app\src\jobflow_desktop_app .flowguard\final_output_verification`
- OK (0.000s): `python -m unittest discover desktop_app\tests`
- FAIL (0.000s): `python -m flowguard adoption-finish ... --command "python -c \"import flowguard; print(flowguard.SCHEMA_VERSION)\""`

### Findings
- Correct model passed 78 explored traces with no invariant violations.
- Broken no-verify output exposed expired/generic/unreachable new recommendations entering final output without a valid detail-page stamp.
- Broken apply-link output exposed apply-form links becoming the primary click target, which violates the product requirement that the user opens the job detail page.
- Broken historical recheck exposed routine rechecking of already visible historical recommendations.

### Counterexamples
- New expired detail page with an apply link became visible when final output ignored the detail verification stamp.
- New valid detail page used apply as primary output when the output policy preferred apply links.
- Historical visible recommendation was rechecked during output refresh.

### Friction Points
- PowerShell quoting broke the first adoption-finish attempt that embedded a python -c command.

### Skipped Steps
- Production conformance replay was skipped; focused unit tests cover the final_output, runtime-config, pool, runner, direct-discovery, and search-results projections.
- Interactive desktop GUI QA was not run in this pass.

### Next Actions
- Run a small real search smoke with fresh results to observe postVerify cost and final recommendation yield.


## role-scope-prompt-20260430 - Tighten AI target-role scope labels around search radius

- Project: Job-Hunter
- Trigger reason: The change affects AI target-role recommendation behavior and visible core/adjacent/exploratory labels.
- Status: completed
- Skill decision: used_flowguard
- Started: 2026-04-30T09:13:00+00:00
- Ended: 2026-04-30T09:31:45+00:00

### Model Files
- .flowguard/role_scope_prompt/model.py
- .flowguard/role_scope_prompt/run_checks.py

### Commands
- OK: `python -c "import flowguard; print(flowguard.SCHEMA_VERSION)"`
- OK: `python .flowguard/role_scope_prompt/run_checks.py`
- OK: `python scripts/role_recommendation_sandbox.py --candidate-id 2 --runs 3 --timeout 180 --save-prompt --out-dir runtime/role_scope_prompt_sandbox/baseline_real`
- OK: `python scripts/role_recommendation_sandbox.py --candidate-id 2 --runs 5 --timeout 180 --save-prompt --out-dir runtime/role_scope_prompt_sandbox/iteration1`
- OK: `python scripts/role_recommendation_sandbox.py --candidate-id 2 --runs 5 --timeout 180 --save-prompt --out-dir runtime/role_scope_prompt_sandbox/iteration2`
- BLOCKED: `python scripts/role_recommendation_sandbox.py --candidate-id 2 --runs 5 --timeout 180 --save-prompt --out-dir runtime/role_scope_prompt_sandbox/iteration3`
- OK: `python -m unittest desktop_app.tests.test_role_recommendations_prompts desktop_app.tests.test_target_direction_recommendations desktop_app.tests.test_role_recommendations_parse desktop_app.tests.test_role_recommendations_text`
- OK: `python -m compileall -q desktop_app\src\jobflow_desktop_app .flowguard\role_scope_prompt`

### Findings
- Correct model passed 136 explored traces with no invariant violations.
- Broken function-shift policy exposed the old failure mode: mainline evidence could be demoted when the practical work setting changed.
- Broken mix-forced policy exposed the quota failure mode: requested role mix could force a wrong scope label.
- Broken restrictive-nearby policy exposed the opposite failure mode: nearby transferable domains could be blocked instead of treated as adjacent.
- Real sandbox baseline and two prompt iterations confirmed the old prompt overused adjacent/exploratory for same-radius roles; the final real sandbox was blocked by OpenAI `insufficient_quota`.

### Skipped Steps
- Production conformance replay is not applicable; this was a prompt-only behavioral boundary without durable state writes.
- Final live LLM sandbox after the last prompt tightening was not run because the API returned HTTP 429 `insufficient_quota`.

### Next Actions
- When API quota is available, rerun the role recommendation sandbox once against the final prompt and inspect whether same-domain technical/professional roles stay core while nearby transferable domains land in adjacent.


## final-output-detail-verification-20260430 - Require current detail-page verification before new jobs enter final recommendations

- Project: Job-Hunter
- Trigger reason: The change affects recommendation output visibility, cached eligibility stamps, and historical recommendation preservation.
- Status: completed
- Skill decision: used_flowguard
- Started: 2026-04-30T07:45:31+00:00
- Ended: 2026-04-30T07:45:31+00:00
- Duration seconds: 0.000
- Commands OK: True

### Model Files
- .flowguard/final_output_verification/model.py
- .flowguard/final_output_verification/run_checks.py

### Commands
- OK (0.000s): `python -c "import flowguard; print(flowguard.SCHEMA_VERSION)"`
- OK (0.000s): `python .flowguard/final_output_verification/run_checks.py`
- OK (0.000s): `python -m unittest desktop_app.tests.test_final_output desktop_app.tests.test_runtime_config_builder desktop_app.tests.test_stage_executor_resume_pending desktop_app.tests.test_job_search_runner_manual_tracking`
- OK (0.000s): `python -m unittest desktop_app.tests.test_candidate_job_pool desktop_app.tests.test_job_search_runner_db_reads desktop_app.tests.test_job_search_runner_records desktop_app.tests.test_job_search_runner_unit desktop_app.tests.test_direct_job_discovery_stage desktop_app.tests.test_search_results_regressions`
- OK (0.000s): `python -m compileall -q desktop_app\src\jobflow_desktop_app .flowguard\final_output_verification`
- OK (0.000s): `python -m unittest discover desktop_app\tests`

### Findings
- Correct model passed 78 explored traces with no invariant violations.
- Broken no-verify output exposed expired/generic/unreachable new recommendations entering final output without a valid detail-page stamp.
- Broken apply-link output exposed apply-form links becoming the primary click target, which violates the product requirement that the user opens the job detail page.
- Broken historical recheck exposed routine rechecking of already visible historical recommendations.

### Counterexamples
- New expired detail page with an apply link became visible when final output ignored the detail verification stamp.
- New valid detail page used apply as primary output when the output policy preferred apply links.
- Historical visible recommendation was rechecked during output refresh.

### Friction Points
- PowerShell quoting broke one adoption-finish attempt, but executable checks themselves passed.

### Skipped Steps
- Production conformance replay was skipped; focused unit tests cover the final_output, runtime-config, pool, runner, direct-discovery, and search-results projections.
- Interactive desktop GUI QA was not run in this pass.

### Next Actions
- Run a small real search smoke with fresh results to observe postVerify cost and final recommendation yield.


## daily-qa-local-freshness - Add local source freshness gate to Jobflow Desktop daily QA

- Project: Job-Hunter
- Trigger reason: Daily packaged-app QA must choose between current package, rebuilt local package, or stop when peer/local edits are in progress.
- Status: completed
- Skill decision: used_flowguard
- Started: 2026-04-30T10:29:55+00:00
- Ended: 2026-04-30T10:29:55+00:00
- Duration seconds: 0.000
- Commands OK: True

### Model Files
- .flowguard/daily_qa_preflight/model.py
- .flowguard/daily_qa_preflight/run_checks.py

### Commands
- OK (0.000s): `python .flowguard/daily_qa_preflight/run_checks.py`
- OK (0.000s): `python -m unittest desktop_app.tests.test_daily_desktop_qa_preflight desktop_app.tests.test_release_update_manifest`
- OK (0.000s): `python -m compileall -q scripts\daily_desktop_qa_preflight.py .flowguard\daily_qa_preflight`

### Findings
- Correct model passed seven abstract preflight signals; broken GitHub-only and build-during-active variants produced the expected counterexamples.
- Dry-run preflight currently reports needs_rebuild because local package-relevant source changes are stable and newer than the current packaged EXE.

### Counterexamples
- none recorded

### Friction Points
- none recorded

### Skipped Steps
- Packaging and GUI launch were not run in this implementation pass; the automation will run them on its next scheduled/apply run after validation passes.

### Next Actions
- Next daily QA run should use scripts/daily_desktop_qa_preflight.py --apply --json and stop if validation/build fails or local edits are active.


## job-validation-flow-20260430 - Model early link validation plus final evidence gate

- Project: Job-Hunter
- Trigger reason: The proposed search-flow change affects token cost, job validity verification, output eligibility, and recommendation visibility.
- Status: completed
- Skill decision: used_flowguard

### Model Files
- .flowguard/job_validation_flow/model.py
- .flowguard/job_validation_flow/run_checks.py

### Commands
- OK: `python -c "import flowguard; print(flowguard.SCHEMA_VERSION)"`
- OK: `python .flowguard/job_validation_flow/run_checks.py`

### Findings
- The current direct-discovery pattern can score and recommend live-looking jobs but still produce an empty final list when postVerify is skipped.
- A final-only verification strategy avoids bad visible links, but wastes scoring and binding work on clearly invalid links.
- The modeled best path is early hard-invalid dropping plus evidence collection, followed by a final gate that accepts strong prior evidence or runs postVerify for uncertain recommended jobs.

### Skipped Steps
- No production code was changed in this design pass.
- Conformance replay against production state was skipped because this model only compares proposed control-flow policies.

### Next Actions
- If approved, update direct discovery and company-source paths so early validation writes reusable detail-page evidence and the final output gate consumes that evidence instead of requiring a separate postVerify field in all cases.

### Implementation Follow-Up - 2026-04-30
- Implemented the modeled path in production code: direct-discovery jobs now fetch deterministic detail evidence before scoring, hard-invalid links are rejected before scoring, reachable dynamic pages can use postVerify fallback, and final output refresh performs a live HTTP recheck of the chosen output URL.
- Additional smoke finding: historical output preservation could keep stale 404 rows visible even after the rebuilt final set excluded them. The repository now treats the freshly rebuilt and live-rechecked output set as authoritative.
- Validation:
  - OK: `python .flowguard/job_validation_flow/run_checks.py`
  - OK: `python -m unittest desktop_app.tests.test_direct_job_discovery_stage desktop_app.tests.test_company_sources desktop_app.tests.test_search_session_orchestrator desktop_app.tests.test_search_session_resume_gate desktop_app.tests.test_job_search_runner_manual_tracking desktop_app.tests.test_job_search_runner_unit desktop_app.tests.test_job_search_runner_records desktop_app.tests.test_final_output desktop_app.tests.test_candidate_job_pool`
  - OK: real 10-minute smoke run `search_run_id=20`; direct discovery rejected 2 invalid links before scoring, final refresh produced 6 visible recommendations, and manual probe of all 6 displayed links returned HTTP 200.
