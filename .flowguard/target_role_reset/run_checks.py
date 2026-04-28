"""Run flowguard checks for target-role reset consistency."""

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
        initial_states=model.initial_states(),
        external_inputs=model.EXTERNAL_INPUTS,
        invariants=model.INVARIANTS,
        max_sequence_length=model.MAX_SEQUENCE_LENGTH,
        terminal_predicate=model.terminal_predicate,
        required_labels=(
            "relational_orphans_repaired",
            "stale_json_binding_cleared",
            "runtime_no_bound_profile",
            "runtime_analysis_written",
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
            name="bootstrap_repairs_existing_dirty_db",
            description="Opening an already-dirty local DB clears orphan relational rows and stale JSON.",
            initial_state=model.dirty_replaced_state(),
            external_input_sequence=(model.RoleEvent("bootstrap"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=(
                    "relational_orphans_repaired",
                    "stale_json_binding_cleared",
                ),
                summary="bootstrap repair restores profile reference consistency",
            ),
        ),
        Scenario(
            name="replace_then_runtime_write_is_unbound",
            description="Replacing target roles clears old JSON before runtime persistence can reuse it.",
            initial_state=model.clean_old_profile_state(),
            external_input_sequence=(
                model.RoleEvent("replace_via_app"),
                model.RoleEvent("runtime_write"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=(
                    "profiles_replaced_with_fk_cascade",
                    "stale_json_binding_cleared",
                    "runtime_no_bound_profile",
                ),
                forbidden_trace_labels=("broken_runtime_fk_failure",),
                summary="old role-bound analyses are invalidated before later writes",
            ),
        ),
        Scenario(
            name="valid_runtime_write_before_replacement",
            description="A still-current bound profile remains writable until roles are replaced.",
            initial_state=model.clean_old_profile_state(),
            external_input_sequence=(model.RoleEvent("runtime_write"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("runtime_analysis_written",),
                summary="valid profile-bound analysis writes remain supported",
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
    correct = run_explorer("correct target-role reset", model.build_workflow())
    no_json_cleanup_failed = run_expected_broken(
        "broken no JSON cleanup",
        model.build_no_json_cleanup_workflow(),
        "no_stale_json_bindings",
    )
    no_guard_failed = run_expected_broken(
        "broken no JSON cleanup and no write guard",
        model.build_no_json_cleanup_no_write_guard_workflow(),
        "no_fk_failures",
    )
    scenario_report = run_scenario_review()
    if not correct.ok:
        return 1
    if not no_json_cleanup_failed or not no_guard_failed:
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
