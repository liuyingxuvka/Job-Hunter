"""Run flowguard checks for the Jobflow search-loop model."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from flowguard import (
        BoundedEventuallyProperty,
        Explorer,
        GraphEdge,
        LoopCheckConfig,
        ProgressCheckConfig,
        Scenario,
        ScenarioExpectation,
        check_loops,
        check_progress,
        review_scenarios,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - local adoption helper
    hint = (
        "flowguard is not importable. Install it or run with "
        "PYTHONPATH pointing at the local FlowGuard source checkout."
    )
    raise SystemExit(hint) from exc

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
            "direct_soft_failed",
            "sources_job",
            "company_discovery_new_company",
            "continue_empty",
            "stop_after_three_empty",
            "continue_with_progress",
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
            name="three_empty_rounds_stop",
            description="Three fully empty rounds stop the session.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.RoundSignal("empty"),
                model.RoundSignal("empty"),
                model.RoundSignal("empty"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("stop_after_three_empty",),
                summary="three empty rounds produce the bounded retry stop",
            ),
        ),
        Scenario(
            name="progress_resets_empty_counter",
            description="A productive middle round prevents two surrounding empty rounds from stopping.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.RoundSignal("empty"),
                model.RoundSignal("direct_job", direct="job"),
                model.RoundSignal("empty"),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("continue_with_progress", "continue_empty"),
                forbidden_trace_labels=("stop_after_three_empty",),
                summary="progress resets the empty-round counter",
            ),
        ),
        Scenario(
            name="sources_progress_still_discovers_company",
            description="Existing company source output does not skip company discovery.",
            initial_state=model.initial_state(),
            external_input_sequence=(model.RoundSignal("source_job", sources="job"),),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("sources_job", "company_discovery_empty"),
                summary="source-stage output still reaches company discovery",
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


@dataclass(frozen=True)
class LoopState:
    empty_rounds: int = 0
    stopped: bool = False
    timebox_expired: bool = False


def loop_edges(state: LoopState) -> Iterable[GraphEdge]:
    if state.stopped:
        return ()
    edges: list[GraphEdge] = [
        GraphEdge(
            state,
            replace(state, empty_rounds=0),
            "productive_round",
            "Any meaningful output resets the empty counter.",
        ),
        GraphEdge(
            state,
            replace(state, stopped=True, timebox_expired=True),
            "timebox_expired",
            "External timebox ends the session.",
        ),
    ]
    if state.empty_rounds + 1 >= model.EMPTY_ROUND_LIMIT:
        edges.append(
            GraphEdge(
                state,
                replace(state, empty_rounds=state.empty_rounds + 1, stopped=True),
                "empty_limit_stop",
                "Three consecutive empty rounds stop the session.",
            )
        )
    else:
        edges.append(
            GraphEdge(
                state,
                replace(state, empty_rounds=state.empty_rounds + 1),
                "empty_round",
                "An empty round increments the bounded empty counter.",
            )
        )
    return tuple(edges)


def run_loop_review() -> tuple[object, object]:
    loop_config = LoopCheckConfig(
        initial_states=(LoopState(),),
        transition_fn=loop_edges,
        is_terminal=lambda state: state.stopped,
        is_success=lambda state: state.stopped,
        max_depth=5,
        required_success=True,
    )
    loop_report = check_loops(loop_config)
    progress_report = check_progress(
        ProgressCheckConfig(
            initial_states=(LoopState(),),
            transition_fn=loop_edges,
            is_terminal=lambda state: state.stopped,
            is_success=lambda state: state.stopped,
            bounded_eventually=(
                BoundedEventuallyProperty(
                    name="stop_within_three_empty_rounds_if_only_empty_rounds",
                    trigger=lambda state: not state.stopped,
                    target=lambda state: state.stopped or state.empty_rounds == 0,
                    max_steps=model.EMPTY_ROUND_LIMIT,
                    description=(
                        "The model can stop after three empty rounds, but productivity and "
                        "timebox fairness are external inputs."
                    ),
                ),
            ),
            max_depth=5,
        )
    )
    print("=== loop review ===")
    print(loop_report.format_text())
    print()
    print("=== progress review ===")
    print(progress_report.format_text())
    print()
    return loop_report, progress_report


def main() -> int:
    print("flowguard import source:", os.environ.get("PYTHONPATH") or "(import path)")
    print()
    correct = run_explorer("correct search loop", model.build_workflow())
    old_stop_failed = run_expected_broken(
        "broken old one-empty stop",
        model.build_stop_after_one_empty_workflow(),
        "no_stop_before_three_empty_rounds",
    )
    skip_discovery_failed = run_expected_broken(
        "broken skip discovery when sources produce",
        model.build_skip_discovery_workflow(),
        "discovery_attempted_for_each_completed_round",
    )
    scenario_report = run_scenario_review()
    loop_report, progress_report = run_loop_review()
    if not correct.ok:
        return 1
    if not old_stop_failed or not skip_discovery_failed:
        return 1
    if not scenario_report.ok:
        return 1
    if not loop_report.ok:
        return 1
    if not progress_report.ok:
        print(
            "Progress review found an expected modeling limitation: "
            "the loop relies on external timebox/productivity fairness."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
