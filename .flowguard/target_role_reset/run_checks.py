"""Run FlowGuard checks for target-role recommendation persistence."""

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
            "visible_recommendation_marked_needs_rescore",
            "unshown_stale_json_binding_cleared",
            "current_reject_preserved_visible_history",
            "output_refresh_preserved_visible_history",
            "runtime_stale_binding_skipped",
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
            name="shown_role_deleted_is_marked_needs_rescore",
            description="A visible recommendation keeps its row when the bound role disappears.",
            initial_state=model.clean_shown_state(),
            external_input_sequence=(model.RoleEvent("replace_via_app"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=(
                    "profiles_replaced_with_fk_cascade",
                    "visible_recommendation_marked_needs_rescore",
                ),
                summary="visible recommendations are preserved and labeled needs-rescore",
            ),
        ),
        Scenario(
            name="unshown_role_deleted_is_reset",
            description="A stale row that never reached the recommendation table is reset.",
            initial_state=model.clean_unshown_state(),
            external_input_sequence=(model.RoleEvent("replace_via_app"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=(
                    "profiles_replaced_with_fk_cascade",
                    "unshown_stale_json_binding_cleared",
                ),
                summary="unshown stale rows are reset to pending",
            ),
        ),
        Scenario(
            name="shown_role_updated_is_marked_needs_rescore",
            description="Editing the same target-role profile requires current-fit re-evaluation.",
            initial_state=model.clean_shown_state(),
            external_input_sequence=(model.RoleEvent("target_role_update"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("visible_recommendation_marked_needs_rescore",),
                summary="profile content edits label visible recommendations for rescore",
            ),
        ),
        Scenario(
            name="shown_current_reject_keeps_history",
            description="A current-role reject updates fit status but does not erase historical visibility.",
            initial_state=model.clean_shown_state(),
            external_input_sequence=(model.RoleEvent("current_rescore_reject"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("current_reject_preserved_visible_history",),
                summary="current rescore reject keeps the historical recommendation row visible",
            ),
        ),
        Scenario(
            name="shown_output_refresh_exclusion_keeps_history",
            description="A recommended-output refresh does not remove a row that was already shown.",
            initial_state=model.clean_shown_state(),
            external_input_sequence=(model.RoleEvent("output_refresh_excludes"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("output_refresh_preserved_visible_history",),
                summary="output refresh keeps the historical recommendation row visible",
            ),
        ),
        Scenario(
            name="stale_runtime_write_is_guarded",
            description="Preserved stale historical JSON must not write a missing search_profile_id.",
            initial_state=model.dirty_replaced_shown_state(),
            external_input_sequence=(model.RoleEvent("runtime_write"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("runtime_stale_binding_skipped",),
                forbidden_trace_labels=("broken_runtime_fk_failure",),
                summary="runtime persistence skips stale preserved bindings",
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
    correct = run_explorer("target-role recommendation persistence", model.build_workflow())
    reset_failed = run_expected_broken(
        "broken reset visible recommendations",
        model.build_reset_visible_workflow(),
        "shown_recommendations_stay_visible",
    )
    unlabeled_failed = run_expected_broken(
        "broken keep visible unlabeled",
        model.build_unlabeled_visible_workflow(),
        "visible_stale_bindings_are_labeled",
    )
    overwrite_failed = run_expected_broken(
        "broken current rescore overwrite",
        model.build_rescore_overwrite_workflow(),
        "shown_recommendations_stay_visible",
    )
    output_overwrite_failed = run_expected_broken(
        "broken output refresh overwrite",
        model.build_output_refresh_overwrite_workflow(),
        "shown_recommendations_stay_visible",
    )
    no_guard_failed = run_expected_broken(
        "broken no runtime write guard",
        model.build_no_write_guard_workflow(),
        "no_fk_failures",
    )
    scenario_report = run_scenario_review()
    if not correct.ok:
        return 1
    if (
        not reset_failed
        or not unlabeled_failed
        or not overwrite_failed
        or not output_overwrite_failed
        or not no_guard_failed
    ):
        return 1
    if not scenario_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
