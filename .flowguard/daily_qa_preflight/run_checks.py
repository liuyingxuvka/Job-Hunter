"""Run FlowGuard checks for the daily desktop QA local freshness gate."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from flowguard import Explorer, Scenario, ScenarioExpectation, review_scenarios
except ModuleNotFoundError as exc:  # pragma: no cover
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
            "fresh_package_use_current",
            "stable_changes_rebuild",
            "active_local_changes_stop",
            "local_package_rebuilt",
            "daily_qa_launch_current",
            "daily_qa_launch_rebuilt",
            "daily_qa_stopped_with_notice",
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
            name="fresh_package_launches_current",
            description="No local source changes and a fresh package should proceed with the current EXE.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.QaPreflightSignal("fresh_no_changes", package_state="fresh", local_change_state="none"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("fresh_package_use_current", "daily_qa_launch_current"),
                summary="current package is acceptable",
            ),
        ),
        Scenario(
            name="stable_local_changes_rebuild_then_launch",
            description="Stable completed local changes are rebuilt and the new package is launched.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.QaPreflightSignal("stable_changes", package_state="fresh", local_change_state="stable"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("stable_changes_rebuild", "local_package_rebuilt", "daily_qa_launch_rebuilt"),
                forbidden_trace_labels=("daily_qa_launch_current",),
                summary="local source changes become the tested package",
            ),
        ),
        Scenario(
            name="active_local_changes_stop",
            description="Active local changes stop the daily run before build or launch.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.QaPreflightSignal("active_changes", package_state="stale", local_change_state="active"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("active_local_changes_stop", "daily_qa_stopped_with_notice"),
                forbidden_trace_labels=("local_package_rebuilt", "daily_qa_launch_current", "daily_qa_launch_rebuilt"),
                summary="do not build from a moving worktree",
            ),
        ),
        Scenario(
            name="stable_build_failure_stops",
            description="A failed local rebuild stops instead of launching the stale current package.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.QaPreflightSignal(
                    "stable_changes_build_fail",
                    package_state="fresh",
                    local_change_state="stable",
                    build_result="fail",
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("stable_changes_rebuild", "local_package_build_failed_stop", "daily_qa_stopped_with_notice"),
                forbidden_trace_labels=("daily_qa_launch_current", "daily_qa_launch_rebuilt"),
                summary="failed rebuild blocks the QA run",
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
    correct = run_explorer("daily QA preflight", model.build_workflow())
    github_only_failed = run_expected_broken(
        "broken GitHub-only freshness gate",
        model.build_github_only_workflow(),
        "stable_local_changes_rebuilt_before_launch",
    )
    active_build_failed = run_expected_broken(
        "broken build during active changes",
        model.build_active_build_workflow(),
        "active_changes_stop_before_build_or_launch",
    )
    scenario_report = run_scenario_review()
    if not correct.ok:
        return 1
    if not github_only_failed or not active_build_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
