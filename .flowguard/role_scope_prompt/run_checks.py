"""Run FlowGuard checks for role-scope prompt semantics."""

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
            "saved_scope_core",
            "saved_scope_adjacent",
            "saved_scope_exploratory",
            "bucket_conflict_return_fewer",
            "unsupported_idea_skipped",
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
            name="mainline_technical_shift_stays_core",
            description="A mainline technical idea remains core even when the practical work setting changes.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.RoleIdea("mainline_technical_shift", "mainline", "technical_shift", "core"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("natural_scope_core", "saved_scope_core"),
                forbidden_trace_labels=("broken_function_shift_overrode_search_radius",),
                summary="mainline evidence is not demoted by function-shift wording",
            ),
        ),
        Scenario(
            name="conflicting_bucket_returns_fewer",
            description="A role-mix request cannot relabel a mainline idea as adjacent.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.RoleIdea("mainline_requested_adjacent", "mainline", "technical_shift", "adjacent"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("natural_scope_core", "bucket_conflict_return_fewer"),
                forbidden_trace_labels=("broken_mix_target_forced_scope",),
                summary="bucket conflicts skip rather than force labels",
            ),
        ),
        Scenario(
            name="nearby_transfer_domain_can_be_adjacent",
            description="A transferable nearby technical domain remains available as adjacent.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.RoleIdea("nearby_transfer", "nearby_transfer", "technical_shift", "adjacent"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("natural_scope_adjacent", "saved_scope_adjacent"),
                forbidden_trace_labels=("broken_nearby_transfer_domain_blocked",),
                summary="nearby transferable domains are not banned by prompt wording",
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
    correct = run_explorer("role scope prompt policy", model.build_workflow())
    function_shift_failed = run_expected_broken(
        "broken function-shift scope policy",
        model.build_function_shift_workflow(),
        "mainline_evidence_stays_core",
    )
    mix_forced_failed = run_expected_broken(
        "broken mix-forced scope policy",
        model.build_mix_forced_workflow(),
        "mix_targets_do_not_force_labels",
    )
    restrictive_failed = run_expected_broken(
        "broken restrictive nearby scope policy",
        model.build_restrictive_nearby_workflow(),
        "nearby_transfer_domains_remain_available",
    )
    scenario_report = run_scenario_review()
    if not correct.ok:
        return 1
    if not function_shift_failed or not mix_forced_failed or not restrictive_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
