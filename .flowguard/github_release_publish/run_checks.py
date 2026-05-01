"""Run FlowGuard checks for the Job-Hunter GitHub release gate."""

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
            "clean_upgrade_smoke_continues",
            "partial_upgrade_issue_logged",
            "hard_upgrade_failure_stops",
            "release_published",
            "privacy_failure_stops",
            "build_failure_stops",
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
            name="partial_upgrade_issue_can_continue_when_logged",
            description="A partial updater smoke result can continue when the issue is recorded and local gates pass.",
            initial_state=model.initial_state(),
            external_input_sequence=(model.ReleaseSignal("partial_upgrade_logged", upgrade_smoke="partial"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("partial_upgrade_issue_logged", "release_published"),
                summary="partial updater issue is visible but not a hard release blocker",
            ),
        ),
        Scenario(
            name="privacy_failure_stops_release",
            description="A privacy audit failure blocks branch/tag/release publication.",
            initial_state=model.initial_state(),
            external_input_sequence=(model.ReleaseSignal("privacy_failure", privacy_ok=False),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("privacy_failure_stops",),
                forbidden_trace_labels=("release_published",),
                summary="privacy must pass before publish",
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
    correct = run_explorer("github release publish", model.build_workflow())
    broken_publish_failed = run_expected_broken(
        "broken publish without local gates",
        model.build_broken_publish_workflow(),
        "release_not_published_without_local_gates",
    )
    broken_upgrade_failed = run_expected_broken(
        "broken upgrade smoke ignored",
        model.build_broken_upgrade_workflow(),
        "hard_upgrade_failure_stops_release",
    )
    scenario_report = run_scenario_review()
    if not correct.ok:
        return 1
    if not broken_publish_failed or not broken_upgrade_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
