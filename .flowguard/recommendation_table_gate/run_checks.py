"""Run FlowGuard checks for final recommendation quality."""

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
    external_inputs=None,
    max_sequence_length: int | None = None,
    required_labels: tuple[str, ...] = (),
) -> object:
    report = Explorer(
        workflow=workflow,
        initial_states=(model.initial_state(),),
        external_inputs=external_inputs or model.EXTERNAL_INPUTS,
        invariants=model.INVARIANTS,
        max_sequence_length=max_sequence_length or model.MAX_SEQUENCE_LENGTH,
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
    *,
    external_inputs=None,
    max_sequence_length: int | None = None,
) -> bool:
    report = run_explorer(
        name,
        workflow,
        external_inputs=external_inputs,
        max_sequence_length=max_sequence_length,
    )
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


def run_correct_models() -> tuple[object, object]:
    output_report = run_explorer(
        "recommendation quality gate",
        model.build_workflow(),
        required_labels=(
            "below_floor_blocked",
            "prefilter_reject_blocked",
            "post_verify_blocked",
            "final_output_check_blocked",
            "link_recheck_failed",
            "duplicate_merged",
            "final_recommendation_visible",
            "final_table_row_rendered",
            "non_final_row_hidden",
        ),
    )
    pool_report = run_explorer(
        "pool loader policy gate",
        model.build_pool_loader_workflow(),
        external_inputs=model.POOL_INPUTS,
        max_sequence_length=model.POOL_MAX_SEQUENCE_LENGTH,
        required_labels=(
            "current_pool_row_rendered",
            "stale_pool_row_hidden",
            "pool_row_hidden",
        ),
    )
    return output_report, pool_report


def run_broken_model_checks() -> bool:
    strict_failed = run_expected_broken(
        "broken strict threshold gate",
        model.build_broken_strict_threshold_workflow(),
        ("no_expected_recommendation_missing",),
    )
    no_dedupe_failed = run_expected_broken(
        "broken no-dedupe gate",
        model.build_broken_no_dedupe_workflow(),
        ("no_duplicate_final_rows",),
    )
    live_pool_failed = run_expected_broken(
        "broken live-pool projection",
        model.build_broken_live_pool_workflow(),
        ("final_table_only_shows_expected_jobs", "final_table_never_shows_non_final_rows"),
    )
    stale_pool_failed = run_expected_broken(
        "broken status-only pool loader",
        model.build_broken_status_only_pool_workflow(),
        ("final_table_never_shows_stale_policy",),
        external_inputs=model.POOL_INPUTS,
        max_sequence_length=model.POOL_MAX_SEQUENCE_LENGTH,
    )
    return strict_failed and no_dedupe_failed and live_pool_failed and stale_pool_failed


def run_scenario_review() -> object:
    scenarios = (
        Scenario(
            name="score_20_enters_final_table",
            description="A recommended job at the score floor reaches the final table.",
            initial_state=model.initial_state(),
            external_input_sequence=(model.JobSignal("score_20_recommend", score=20),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("final_recommendation_visible", "final_table_row_rendered"),
                summary="score 20 is enough when all output checks pass",
            ),
        ),
        Scenario(
            name="score_19_is_blocked",
            description="A recommended job below the floor is hidden from the final table.",
            initial_state=model.initial_state(),
            external_input_sequence=(model.JobSignal("score_19_recommend", score=19),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("below_floor_blocked", "non_final_row_hidden"),
                forbidden_trace_labels=("final_table_row_rendered",),
                summary="score 19 cannot enter the final table",
            ),
        ),
        Scenario(
            name="eligible_job_is_not_missed",
            description="A distinct eligible job is selected and projected.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.JobSignal("good_a", score=78, final_key="good-a"),
                model.JobSignal("good_b", score=81, final_key="good-b"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("final_recommendation_visible", "final_table_row_rendered"),
                summary="both distinct eligible keys are visible",
            ),
        ),
        Scenario(
            name="duplicate_group_collapses_to_one_row",
            description="Two URL variants for the same real job produce one final row.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.JobSignal("duplicate_a", score=82, final_key="same-job"),
                model.JobSignal("duplicate_b", score=86, final_key="same-job"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("final_table_row_rendered", "duplicate_merged", "non_final_row_hidden"),
                forbidden_trace_labels=("broken_duplicate_selected",),
                summary="duplicate output decisions stay out of the user-facing table",
            ),
        ),
        Scenario(
            name="link_recheck_failure_blocks_output",
            description="A job that fails the final link recheck is not projected.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.JobSignal("link_recheck_failed", score=88, link_recheck_ok=False),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("link_recheck_failed", "non_final_row_hidden"),
                forbidden_trace_labels=("final_table_row_rendered",),
                summary="invalid final links are dropped before the table",
            ),
        ),
        Scenario(
            name="stale_pool_policy_is_hidden",
            description="A durable pass row stamped for an old policy is hidden.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.PoolRow("stale_policy_pass", score=82, current_policy_stamp=False),
            ),
            workflow=model.build_pool_loader_workflow(),
            invariants=model.INVARIANTS,
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("stale_pool_row_hidden",),
                forbidden_trace_labels=("current_pool_row_rendered",),
                summary="status pass is insufficient without a current policy stamp",
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
    output_report, pool_report = run_correct_models()
    broken_failed = run_broken_model_checks()
    scenario_report = run_scenario_review()
    if not output_report.ok:
        return 1
    if not pool_report.ok:
        return 1
    if not broken_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
