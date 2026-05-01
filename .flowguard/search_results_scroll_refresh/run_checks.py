"""Run FlowGuard checks for live search-results scroll preservation."""

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
            "render_changed_visible_jobs",
            "skip_unchanged_visible_jobs",
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
            name="same_jobs_noop_poll_preserves_scroll",
            description="After the first render records its signature, the next poll with the same jobs skips the table rebuild.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.PollInput("initial_jobs_top", "jobs:A", "top"),
                model.PollInput("same_jobs_scrolled", "jobs:A", "down"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("render_changed_visible_jobs", "skip_unchanged_visible_jobs"),
                forbidden_trace_labels=("broken_render_without_signature_write",),
                summary="unchanged live jobs are a no-op for the table",
            ),
        ),
        Scenario(
            name="changed_jobs_render_preserves_scroll",
            description="A real visible-job change can render the table but should keep the user's scroll position.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.PollInput("initial_jobs_top", "jobs:A", "top"),
                model.PollInput("new_jobs_scrolled", "jobs:B", "down"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("render_changed_visible_jobs",),
                forbidden_trace_labels=("broken_changed_render_resets_scroll",),
                summary="changed live jobs can redraw without jumping to top",
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
    correct = run_explorer("search-results scroll refresh", model.build_workflow())
    no_signature_failed = run_expected_broken(
        "broken compact render without signature write",
        model.build_no_signature_workflow(),
        "changed_renders_record_signature",
    )
    scroll_reset_failed = run_expected_broken(
        "broken changed render resets scroll",
        model.build_scroll_reset_workflow(),
        "live_refresh_preserves_user_scroll",
    )
    scenario_report = run_scenario_review()
    if not correct.ok:
        return 1
    if not no_signature_failed or not scroll_reset_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
