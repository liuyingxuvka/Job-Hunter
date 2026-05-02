"""Run FlowGuard checks for recommendation visibility consolidation."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from flowguard import Explorer, Scenario, ScenarioExpectation, review_scenarios
except ModuleNotFoundError as exc:  # pragma: no cover - local adoption helper
    raise SystemExit("flowguard is not importable in this Python environment.") from exc

import model


def run_explorer(
    name: str,
    workflow,
    *,
    required_labels: tuple[str, ...] = (),
) -> object:
    report = Explorer(
        workflow=workflow,
        initial_states=(model.initial_state(),),
        external_inputs=model.EXTERNAL_INPUTS,
        invariants=model.INVARIANTS,
        max_sequence_length=model.MAX_SEQUENCE_LENGTH,
        terminal_predicate=model.terminal_predicate,
        required_labels=required_labels,
    ).explore()
    print(f"=== {name} ===")
    print(report.format_text())
    print()
    return report


def run_expected_broken(
    name: str,
    workflow,
    expected_invariants: tuple[str, ...],
) -> bool:
    report = run_explorer(name, workflow)
    names = {violation.invariant_name for violation in report.violations}
    if report.ok:
        print(f"Expected {name} to fail, but it passed.")
        return False
    missing = [name for name in expected_invariants if name not in names]
    if missing:
        print(
            f"Expected {name} to fail {tuple(missing)!r}, "
            f"observed {tuple(sorted(names))!r}."
        )
        return False
    print(f"Expected violation observed for {name}: {', '.join(expected_invariants)}")
    print()
    return True


def run_correct_model() -> object:
    return run_explorer(
        "corrected unified visibility equivalence",
        model.build_workflow(),
        required_labels=(
            "equivalent_fresh_visible",
            "equivalent_fresh_hidden",
            "equivalent_pool_visible",
            "equivalent_pool_hidden",
            "equivalent_no_config_pool_visible",
            "equivalent_stale_policy_hidden",
            "equivalent_historical_visible",
            "equivalent_historical_hidden",
            "equivalent_duplicate_hidden",
        ),
    )


def run_broken_model_checks() -> bool:
    return run_expected_broken(
        "broken naive recompute visibility",
        model.build_naive_recompute_workflow(),
        (
            "no_visibility_equivalence_mismatches",
            "visible_key_sequences_match",
        ),
    )


def run_scenario_review() -> object:
    scenarios = (
        Scenario(
            name="fresh_score_20_still_visible",
            description="A fresh score-20 recommendation remains visible after consolidation.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.DecisionCase("fresh_score_20", model.FRESH_FINAL_OUTPUT, score=20),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("equivalent_fresh_visible",),
                forbidden_trace_labels=("mismatch_fresh_final_output",),
                summary="fresh final-output behavior is unchanged",
            ),
        ),
        Scenario(
            name="pool_current_stamp_with_sparse_evidence_stays_visible",
            description="A durable pool row with a current output stamp is trusted like today.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.DecisionCase(
                    "pool_current_stamp_sparse_evidence",
                    model.POOL_READBACK,
                    score=82,
                    final_output_ok=False,
                    link_recheck_ok=False,
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("equivalent_pool_visible",),
                forbidden_trace_labels=("mismatch_pool_readback",),
                summary="current materialized pool stamps remain authoritative",
            ),
        ),
        Scenario(
            name="stale_pool_policy_stays_hidden",
            description="A durable pass row with an old policy stamp is still hidden.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.DecisionCase(
                    "pool_stale_policy",
                    model.POOL_READBACK,
                    score=82,
                    current_policy_stamp=False,
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("equivalent_stale_policy_hidden",),
                forbidden_trace_labels=("equivalent_pool_visible", "mismatch_pool_readback"),
                summary="status pass is still insufficient without a current policy stamp",
            ),
        ),
        Scenario(
            name="historical_retention_stays_visible",
            description="Append-mode historical retention is preserved as a distinct behavior.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.DecisionCase(
                    "historical_retained_sparse_evidence",
                    model.HISTORICAL_APPEND,
                    score=72,
                    final_output_ok=False,
                    link_recheck_ok=False,
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("equivalent_historical_visible",),
                forbidden_trace_labels=("mismatch_historical_append",),
                summary="historical retention is not accidentally re-evaluated as fresh output",
            ),
        ),
        Scenario(
            name="duplicate_final_key_still_collapses",
            description="Two visible rows with one final key still produce one visible key.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.DecisionCase(
                    "fresh_duplicate_a",
                    model.FRESH_FINAL_OUTPUT,
                    score=82,
                    final_key="same-final-job",
                ),
                model.DecisionCase(
                    "fresh_duplicate_b",
                    model.FRESH_FINAL_OUTPUT,
                    score=86,
                    final_key="same-final-job",
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("equivalent_duplicate_hidden",),
                forbidden_trace_labels=("mismatch_fresh_final_output",),
                summary="dedupe behavior is unchanged",
            ),
        ),
    )
    report = review_scenarios(
        scenarios,
        default_workflow=model.build_workflow(),
        default_invariants=model.INVARIANTS,
    )
    print("=== scenario review ===")
    print(report.format_text())
    print()
    return report


def main() -> int:
    print("flowguard import source:", os.environ.get("PYTHONPATH") or "(import path)")
    print()
    correct_report = run_correct_model()
    broken_failed = run_broken_model_checks()
    scenario_report = run_scenario_review()
    if not correct_report.ok:
        return 1
    if not broken_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
