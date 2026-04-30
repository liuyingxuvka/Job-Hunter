"""Run FlowGuard checks for final recommendation detail-page verification."""

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


def run_explorer(name: str, workflow) -> object:
    report = Explorer(
        workflow=workflow,
        initial_states=(model.initial_state(),),
        external_inputs=model.EXTERNAL_INPUTS,
        invariants=model.INVARIANTS,
        max_sequence_length=model.MAX_SEQUENCE_LENGTH,
        terminal_predicate=model.terminal_predicate,
        required_labels=(
            "detail_verified",
            "detail_verification_failed",
            "historical_recheck_skipped",
            "new_visible_after_detail_verify",
            "new_rejected_after_failed_detail_verify",
            "historical_kept_without_recheck",
        ),
    ).explore()
    print(f"=== {name} ===")
    print(report.format_text())
    print()
    return report


def run_expected_broken(name: str, workflow, expected_invariant: str) -> bool:
    report = run_explorer(name, workflow)
    names = {violation.invariant_name for violation in report.violations}
    if report.ok:
        print(f"Expected {name} to fail, but it passed.")
        return False
    if expected_invariant not in names:
        print(
            f"Expected {name} to fail {expected_invariant!r}, "
            f"observed {tuple(sorted(names))!r}."
        )
        return False
    print(f"Expected violation observed for {name}: {expected_invariant}")
    print()
    return True


def run_scenario_review() -> object:
    scenarios = (
        Scenario(
            name="new_valid_detail_page_enters_final_recommendations",
            description="A new recommendation with a current detail-page verification stamp becomes visible.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.JobSignal("new_valid_recommended", detail_page="valid", apply_link="present"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("detail_verified", "new_visible_after_detail_verify"),
                summary="valid detail page is enough for a new final recommendation",
            ),
        ),
        Scenario(
            name="new_expired_detail_page_with_apply_link_is_rejected",
            description="An apply link cannot rescue an expired detail page for new final output.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.JobSignal("new_expired_with_apply", detail_page="expired", apply_link="present"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("detail_verification_failed", "new_rejected_after_failed_detail_verify"),
                forbidden_trace_labels=("new_visible_after_detail_verify",),
                summary="new jobs with expired detail pages do not enter the final table",
            ),
        ),
        Scenario(
            name="historical_visible_row_is_preserved_without_recheck",
            description="A previously visible recommendation remains visible without routine output refresh recheck.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.JobSignal(
                    "historical_visible_expired",
                    source="historical",
                    detail_page="expired",
                    apply_link="present",
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("historical_recheck_skipped", "historical_kept_without_recheck"),
                forbidden_trace_labels=("broken_historical_rechecked",),
                summary="historical recommendation rows are not reverified on every refresh",
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
    correct = run_explorer("final output detail verification", model.build_workflow())
    no_verify_failed = run_expected_broken(
        "broken no-verify final output",
        model.build_no_verify_workflow(),
        "new_visible_requires_current_detail_verification",
    )
    apply_link_failed = run_expected_broken(
        "broken apply-link primary output",
        model.build_apply_link_workflow(),
        "visible_new_uses_detail_page_not_apply",
    )
    history_recheck_failed = run_expected_broken(
        "broken historical recheck",
        model.build_recheck_history_workflow(),
        "historical_rows_not_rechecked",
    )
    scenario_report = run_scenario_review()
    if not correct.ok:
        return 1
    if not no_verify_failed or not apply_link_failed or not history_recheck_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
