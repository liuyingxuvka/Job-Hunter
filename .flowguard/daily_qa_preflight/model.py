"""FlowGuard model for daily desktop QA local freshness decisions.

The model covers the pre-run gate for the local packaged-app QA loop:

    before launching the packaged app, use the current package only when it is
    fresh enough; rebuild and switch when stable local source changes are newer
    than the tested package; stop and notify when local edits still appear
    active.

It has no production side effects and does not inspect the real repository.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


@dataclass(frozen=True)
class QaPreflightSignal:
    name: str
    package_state: str = "fresh"  # fresh | stale | missing
    local_change_state: str = "none"  # none | stable | active
    build_result: str = "ok"  # ok | fail


@dataclass(frozen=True)
class FreshnessDecision:
    signal: QaPreflightSignal
    action: str  # use_current | rebuild | stop
    reason: str


@dataclass(frozen=True)
class BuildDecision:
    signal: QaPreflightSignal
    launch_package: str  # current | rebuilt | none
    notified: bool


@dataclass(frozen=True)
class RunDecision:
    status: str  # launch | stop
    package: str  # current | rebuilt | none
    notified: bool


@dataclass(frozen=True)
class State:
    checks: int = 0
    builds: int = 0
    launches: int = 0
    stops: int = 0
    stale_old_launches: int = 0
    active_edit_builds: int = 0
    active_edit_launches: int = 0
    missing_package_launches: int = 0
    completed_change_unrebuilt_launches: int = 0


class LocalFreshnessGate:
    name = "LocalFreshnessGate"
    reads = ()
    writes = ("checks",)
    accepted_input_type = QaPreflightSignal
    input_description = "daily QA preflight signal from package and worktree inspection"
    output_description = "preflight action"
    idempotency = "Each daily QA run performs one pre-launch freshness gate."

    def apply(self, input_obj: QaPreflightSignal, state: State) -> Iterable[FunctionResult]:
        next_state = replace(state, checks=state.checks + 1)
        if input_obj.local_change_state == "active":
            yield FunctionResult(
                output=FreshnessDecision(input_obj, "stop", "local_changes_in_progress"),
                new_state=next_state,
                label="active_local_changes_stop",
            )
            return
        if input_obj.local_change_state == "stable" or input_obj.package_state in {"stale", "missing"}:
            yield FunctionResult(
                output=FreshnessDecision(input_obj, "rebuild", "stable_local_changes_or_stale_package"),
                new_state=next_state,
                label="stable_changes_rebuild",
            )
            return
        yield FunctionResult(
            output=FreshnessDecision(input_obj, "use_current", "current_package_is_fresh"),
            new_state=next_state,
            label="fresh_package_use_current",
        )


class GitHubOnlyFreshnessGate(LocalFreshnessGate):
    name = "GitHubOnlyFreshnessGate"

    def apply(self, input_obj: QaPreflightSignal, state: State) -> Iterable[FunctionResult]:
        next_state = replace(state, checks=state.checks + 1)
        if input_obj.package_state == "missing":
            yield FunctionResult(
                output=FreshnessDecision(input_obj, "stop", "missing_package"),
                new_state=next_state,
                label="broken_missing_package_stop",
            )
            return
        yield FunctionResult(
            output=FreshnessDecision(input_obj, "use_current", "github_release_says_current"),
            new_state=next_state,
            label="broken_github_only_use_current",
        )


class BuildOrSelectPackage:
    name = "BuildOrSelectPackage"
    reads = ("builds",)
    writes = (
        "builds",
        "stale_old_launches",
        "active_edit_builds",
        "missing_package_launches",
        "completed_change_unrebuilt_launches",
    )
    accepted_input_type = FreshnessDecision
    input_description = "preflight action"
    output_description = "package selected for launch or stop notification"
    idempotency = "A rebuild action creates at most one replacement package for the daily run."

    def apply(self, input_obj: FreshnessDecision, state: State) -> Iterable[FunctionResult]:
        signal = input_obj.signal
        if input_obj.action == "stop":
            yield FunctionResult(
                output=BuildDecision(signal, "none", notified=True),
                new_state=state,
                label="stop_before_build_or_launch",
            )
            return
        if input_obj.action == "use_current":
            stale_old = 1 if signal.package_state == "stale" else 0
            missing = 1 if signal.package_state == "missing" else 0
            unrebuilt = 1 if signal.local_change_state == "stable" else 0
            yield FunctionResult(
                output=BuildDecision(signal, "current", notified=False),
                new_state=replace(
                    state,
                    stale_old_launches=state.stale_old_launches + stale_old,
                    missing_package_launches=state.missing_package_launches + missing,
                    completed_change_unrebuilt_launches=state.completed_change_unrebuilt_launches + unrebuilt,
                ),
                label="current_package_selected",
            )
            return
        if signal.local_change_state == "active":
            yield FunctionResult(
                output=BuildDecision(signal, "none", notified=True),
                new_state=replace(state, active_edit_builds=state.active_edit_builds + 1),
                label="broken_build_started_during_active_changes",
            )
            return
        if signal.build_result == "ok":
            yield FunctionResult(
                output=BuildDecision(signal, "rebuilt", notified=False),
                new_state=replace(state, builds=state.builds + 1),
                label="local_package_rebuilt",
            )
            return
        yield FunctionResult(
            output=BuildDecision(signal, "none", notified=True),
            new_state=replace(state, builds=state.builds + 1),
            label="local_package_build_failed_stop",
        )


class BuildDuringActiveChanges(BuildOrSelectPackage):
    name = "BuildDuringActiveChanges"

    def apply(self, input_obj: FreshnessDecision, state: State) -> Iterable[FunctionResult]:
        if input_obj.signal.local_change_state == "active":
            yield FunctionResult(
                output=BuildDecision(input_obj.signal, "rebuilt", notified=False),
                new_state=replace(
                    state,
                    builds=state.builds + 1,
                    active_edit_builds=state.active_edit_builds + 1,
                ),
                label="broken_build_started_during_active_changes",
            )
            return
        yield from super().apply(input_obj, state)


class LaunchOrStopDailyQa:
    name = "LaunchOrStopDailyQa"
    reads = ("launches", "stops")
    writes = ("launches", "stops", "active_edit_launches")
    accepted_input_type = BuildDecision
    input_description = "selected package or stop notification"
    output_description = "daily QA launch decision"
    idempotency = "The daily QA task either launches one package or stops with a notification."

    def apply(self, input_obj: BuildDecision, state: State) -> Iterable[FunctionResult]:
        if input_obj.launch_package == "none":
            yield FunctionResult(
                output=RunDecision("stop", "none", notified=input_obj.notified),
                new_state=replace(state, stops=state.stops + 1),
                label="daily_qa_stopped_with_notice",
            )
            return
        active_launch = 1 if input_obj.signal.local_change_state == "active" else 0
        yield FunctionResult(
            output=RunDecision("launch", input_obj.launch_package, notified=False),
            new_state=replace(
                state,
                launches=state.launches + 1,
                active_edit_launches=state.active_edit_launches + active_launch,
            ),
            label=f"daily_qa_launch_{input_obj.launch_package}",
        )


def terminal_predicate(current_output, state: State, trace) -> bool:
    return isinstance(current_output, RunDecision)


def invariant_no_launch_while_active(state: State, trace) -> InvariantResult:
    del trace
    if state.active_edit_launches or state.active_edit_builds:
        return InvariantResult.fail(
            "active local edits reached build or launch",
            {
                "active_edit_builds": state.active_edit_builds,
                "active_edit_launches": state.active_edit_launches,
            },
        )
    return InvariantResult.pass_()


def invariant_no_stale_or_missing_current_launch(state: State, trace) -> InvariantResult:
    del trace
    if state.stale_old_launches or state.missing_package_launches:
        return InvariantResult.fail(
            "stale or missing current package was selected for launch",
            {
                "stale_old_launches": state.stale_old_launches,
                "missing_package_launches": state.missing_package_launches,
            },
        )
    return InvariantResult.pass_()


def invariant_completed_changes_are_rebuilt(state: State, trace) -> InvariantResult:
    del trace
    if state.completed_change_unrebuilt_launches:
        return InvariantResult.fail(
            "stable local changes were not rebuilt before launch",
            {"completed_change_unrebuilt_launches": state.completed_change_unrebuilt_launches},
        )
    return InvariantResult.pass_()


def invariant_terminal_progress(state: State, trace) -> InvariantResult:
    del trace
    if state.launches + state.stops > 1:
        return InvariantResult.fail(
            "daily preflight produced more than one terminal decision",
            {"launches": state.launches, "stops": state.stops},
        )
    return InvariantResult.pass_()


def initial_state() -> State:
    return State()


def build_workflow() -> Workflow:
    return Workflow(
        name="daily_qa_preflight",
        blocks=(LocalFreshnessGate(), BuildOrSelectPackage(), LaunchOrStopDailyQa()),
    )


def build_github_only_workflow() -> Workflow:
    return Workflow(
        name="broken_github_only_daily_qa_preflight",
        blocks=(GitHubOnlyFreshnessGate(), BuildOrSelectPackage(), LaunchOrStopDailyQa()),
    )


def build_active_build_workflow() -> Workflow:
    return Workflow(
        name="broken_active_build_daily_qa_preflight",
        blocks=(LocalFreshnessGate(), BuildDuringActiveChanges(), LaunchOrStopDailyQa()),
    )


EXTERNAL_INPUTS = (
    QaPreflightSignal("fresh_no_changes", package_state="fresh", local_change_state="none"),
    QaPreflightSignal("stable_changes_fresh_package", package_state="fresh", local_change_state="stable"),
    QaPreflightSignal("stable_changes_stale_package", package_state="stale", local_change_state="stable"),
    QaPreflightSignal("active_changes", package_state="fresh", local_change_state="active"),
    QaPreflightSignal("active_changes_stale_package", package_state="stale", local_change_state="active"),
    QaPreflightSignal("missing_package", package_state="missing", local_change_state="none"),
    QaPreflightSignal("stable_changes_build_fail", package_state="fresh", local_change_state="stable", build_result="fail"),
)


INVARIANTS = (
    Invariant(
        name="active_changes_stop_before_build_or_launch",
        description="Active local edits stop the daily QA before build or launch.",
        predicate=invariant_no_launch_while_active,
    ),
    Invariant(
        name="stale_or_missing_package_not_launched_as_current",
        description="A stale or missing current package is not launched as-is.",
        predicate=invariant_no_stale_or_missing_current_launch,
    ),
    Invariant(
        name="stable_local_changes_rebuilt_before_launch",
        description="Stable completed local changes are rebuilt before launch.",
        predicate=invariant_completed_changes_are_rebuilt,
    ),
    Invariant(
        name="daily_preflight_reaches_one_terminal_decision",
        description="Each preflight reaches exactly one terminal launch-or-stop decision.",
        predicate=invariant_terminal_progress,
    ),
)


MAX_SEQUENCE_LENGTH = 1
