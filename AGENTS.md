# Job-Hunter Repository Guide For AI Agents

This file records the repository-specific engineering preferences for future AI agents working in this project.

It supplements, rather than replaces, broader system or user instructions.

## Project intent

Job-Hunter is a local desktop workspace for maintaining a long-term job-search pipeline.

The product is not meant to be a pile of temporary scripts. The preferred direction is:

- a clear desktop application boundary
- a Python-native search pipeline
- persistent local state
- understandable orchestration
- maintainable module boundaries

For higher-level architecture context, also read:

- `docs/ARCHITECTURE.md`
- `docs/DESKTOP_APP_MODULE_MAP.md`
- `docs/PRODUCT_POSITIONING.md`

## Core engineering preferences

When changing this repository, optimize for:

1. Clear structure.
   Each file should have one primary responsibility.

2. Simple boundaries.
   Prefer direct imports of real modules over compatibility shells, re-export layers, or thin forwarding wrappers.

3. Low conceptual overhead.
   If a flow becomes hard to explain in plain language, it is probably too complicated.

4. Easy maintenance.
   Future humans and future AI agents should be able to understand the code quickly without reconstructing hidden intent.

5. Reviewable changes.
   Prefer root-cause refactors and small helper extraction over sprawling rewrites.

## Structure rules

- Do not re-introduce `services/legacy_*`-style compatibility layers unless there is a strong migration reason.
- Avoid creating “empty shell” modules whose only job is import-and-reexport.
- Prefer thin facades over mixed “god files”, but only when the extracted helpers own real logic.
- Keep UI, orchestration, state bookkeeping, persistence, and AI integration separate.
- Do not let page modules absorb search-engine logic or database schema logic.
- Do not let search orchestration depend on UI classes.
- For large, stable prompt prose, prefer packaged prompt assets under `desktop_app/src/jobflow_desktop_app/resources/prompts/` over giant inline string literals or docs-only copies; keep schemas, builders, and runtime substitution in Python.

## Simplicity rules for runtime logic

The project prefers simple mathematical and decision logic.

- Prefer small numbers of meaningful parameters over large parameter surfaces.
- Prefer explicit, understandable heuristics over layered scoring formulas that are hard to audit.
- Avoid repeated recomputation, nested fallback chains, or opaque weighting unless clearly necessary.
- If a scoring rule or search rule cannot be explained in a few sentences, simplify it.
- Favor deterministic, inspectable logic where possible.

In short:

- simple formulas
- simple state transitions
- simple stopping rules
- simple retry rules
- simple data flow

## Refactor policy

A refactor is good here when it does at least one of the following:

- reduces file responsibility overlap
- removes historical naming or migration leftovers
- eliminates stale compatibility layers
- makes a search/session/stage flow easier to explain
- strengthens tests around a newly exposed seam

A refactor is not automatically good just because it creates more files.

Do not split files further when the remaining code is mostly stable façade code, Qt wiring, or other glue that is already easy to follow.

## Naming policy

- Prefer neutral, current names over historical names like `legacy_*`.
- Keep runtime paths and public class names aligned with the current architecture.
- If a migration requires a temporary alias, treat it as transitional and remove it once consumers move.

## Testing policy

After meaningful structural work:

1. Run targeted regressions for the affected modules.
2. Add direct helper-level tests when logic was extracted into a new seam.
3. Run the strongest practical broader suite before calling the work done.

For this repository, `unittest` is the default test framework.

## Current architectural direction

The repository has already been moving toward:

- `search/orchestration/` as the canonical search orchestration home
- `search/state/` as the canonical state/progress bookkeeping home
- smaller focused helpers around search session runtime, resume gating, source fetchers, and stage execution
- `search_runs/` as the canonical runtime run directory

Agents should preserve and reinforce this direction instead of re-fragmenting it.

## Practical decision rule

When choosing between two implementations, prefer the one that is:

- easier to explain
- easier to test
- easier to maintain
- less dependent on compatibility glue
- less mathematically over-engineered

If in doubt, choose the simpler design.
