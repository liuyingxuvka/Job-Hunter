"""FlowGuard model for the Job-Hunter GitHub release gate.

The model covers the release-ordering boundary for a patch release:

    observe the old packaged updater, decide whether the smoke result permits
    continuing, prepare the release files, then publish only after privacy and
    packaging checks pass.

It does not perform network or git side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


@dataclass(frozen=True)
class ReleaseSignal:
    name: str
    upgrade_smoke: str = "ok"  # ok | partial | fail
    version_bumped: bool = True
    privacy_ok: bool = True
    build_ok: bool = True
    push_ok: bool = True
    release_ok: bool = True


@dataclass(frozen=True)
class ReleaseDecision:
    signal: ReleaseSignal
    action: str  # continue | stop
    issue_logged: bool = False


@dataclass(frozen=True)
class PreparedRelease:
    signal: ReleaseSignal
    ready: bool


@dataclass(frozen=True)
class PublishResult:
    status: str  # published | stopped
    issue_logged: bool


@dataclass(frozen=True)
class State:
    upgrade_fail_continues: int = 0
    partial_upgrade_unlogged: int = 0
    unbumped_publishes: int = 0
    privacy_fail_publishes: int = 0
    build_fail_publishes: int = 0
    pushes: int = 0
    releases: int = 0
    stops: int = 0


class UpgradeSmokeGate:
    name = "UpgradeSmokeGate"
    reads = ()
    writes = ("upgrade_fail_continues", "partial_upgrade_unlogged")
    accepted_input_type = ReleaseSignal
    input_description = "observed updater smoke result before preparing a new public release"
    output_description = "whether release preparation can continue"
    idempotency = "Each release run records one updater smoke gate before publishing."

    def apply(self, input_obj: ReleaseSignal, state: State) -> Iterable[FunctionResult]:
        if input_obj.upgrade_smoke == "fail":
            yield FunctionResult(
                output=ReleaseDecision(input_obj, "stop", issue_logged=True),
                new_state=state,
                label="hard_upgrade_failure_stops",
            )
            return
        if input_obj.upgrade_smoke == "partial":
            yield FunctionResult(
                output=ReleaseDecision(input_obj, "continue", issue_logged=True),
                new_state=state,
                label="partial_upgrade_issue_logged",
            )
            return
        yield FunctionResult(
            output=ReleaseDecision(input_obj, "continue", issue_logged=False),
            new_state=state,
            label="clean_upgrade_smoke_continues",
        )


class PrepareReleaseFiles:
    name = "PrepareReleaseFiles"
    reads = ()
    writes = ()
    accepted_input_type = ReleaseDecision
    input_description = "release continuation decision"
    output_description = "whether version and release files are ready for packaging"
    idempotency = "Release preparation produces one intended version boundary."

    def apply(self, input_obj: ReleaseDecision, state: State) -> Iterable[FunctionResult]:
        if input_obj.action == "stop":
            yield FunctionResult(
                output=PreparedRelease(input_obj.signal, ready=False),
                new_state=state,
                label="release_preparation_stopped",
            )
            return
        yield FunctionResult(
            output=PreparedRelease(input_obj.signal, ready=input_obj.signal.version_bumped),
            new_state=state,
            label="release_files_prepared",
        )


class PublishRelease:
    name = "PublishRelease"
    reads = ()
    writes = (
        "unbumped_publishes",
        "privacy_fail_publishes",
        "build_fail_publishes",
        "pushes",
        "releases",
        "stops",
    )
    accepted_input_type = PreparedRelease
    input_description = "prepared release files plus verification signals"
    output_description = "public GitHub publication result"
    idempotency = "Publish runs only after local release verification."

    def apply(self, input_obj: PreparedRelease, state: State) -> Iterable[FunctionResult]:
        signal = input_obj.signal
        if not input_obj.ready:
            yield FunctionResult(
                output=PublishResult("stopped", issue_logged=True),
                new_state=replace(state, stops=state.stops + 1),
                label="missing_version_bump_stops",
            )
            return
        if not signal.privacy_ok:
            yield FunctionResult(
                output=PublishResult("stopped", issue_logged=True),
                new_state=replace(state, stops=state.stops + 1),
                label="privacy_failure_stops",
            )
            return
        if not signal.build_ok:
            yield FunctionResult(
                output=PublishResult("stopped", issue_logged=True),
                new_state=replace(state, stops=state.stops + 1),
                label="build_failure_stops",
            )
            return
        if not signal.push_ok or not signal.release_ok:
            yield FunctionResult(
                output=PublishResult("stopped", issue_logged=True),
                new_state=replace(state, stops=state.stops + 1),
                label="remote_publish_failure_stops",
            )
            return
        yield FunctionResult(
            output=PublishResult("published", issue_logged=False),
            new_state=replace(state, pushes=state.pushes + 1, releases=state.releases + 1),
            label="release_published",
        )


class PublishWithoutGates(PublishRelease):
    name = "PublishWithoutGates"

    def apply(self, input_obj: PreparedRelease, state: State) -> Iterable[FunctionResult]:
        signal = input_obj.signal
        yield FunctionResult(
            output=PublishResult("published", issue_logged=False),
            new_state=replace(
                state,
                pushes=state.pushes + 1,
                releases=state.releases + 1,
                unbumped_publishes=state.unbumped_publishes + (0 if signal.version_bumped else 1),
                privacy_fail_publishes=state.privacy_fail_publishes + (0 if signal.privacy_ok else 1),
                build_fail_publishes=state.build_fail_publishes + (0 if signal.build_ok else 1),
            ),
            label="broken_published_without_gates",
        )


class UpgradeFailContinues(UpgradeSmokeGate):
    name = "UpgradeFailContinues"

    def apply(self, input_obj: ReleaseSignal, state: State) -> Iterable[FunctionResult]:
        yield FunctionResult(
            output=ReleaseDecision(input_obj, "continue", issue_logged=False),
            new_state=replace(
                state,
                upgrade_fail_continues=state.upgrade_fail_continues
                + (1 if input_obj.upgrade_smoke == "fail" else 0),
                partial_upgrade_unlogged=state.partial_upgrade_unlogged
                + (1 if input_obj.upgrade_smoke == "partial" else 0),
            ),
            label="broken_upgrade_smoke_ignored",
        )


def invariant_no_hard_upgrade_failure_continues(state: State, trace) -> InvariantResult:
    del trace
    if state.upgrade_fail_continues:
        return InvariantResult.fail(
            "hard updater smoke failures continued into release preparation",
            {"upgrade_fail_continues": state.upgrade_fail_continues},
        )
    return InvariantResult.pass_()


def invariant_partial_upgrade_issue_logged(state: State, trace) -> InvariantResult:
    del trace
    if state.partial_upgrade_unlogged:
        return InvariantResult.fail(
            "partial updater smoke result continued without an issue note",
            {"partial_upgrade_unlogged": state.partial_upgrade_unlogged},
        )
    return InvariantResult.pass_()


def invariant_no_unverified_publish(state: State, trace) -> InvariantResult:
    del trace
    failures = {
        "unbumped_publishes": state.unbumped_publishes,
        "privacy_fail_publishes": state.privacy_fail_publishes,
        "build_fail_publishes": state.build_fail_publishes,
    }
    if any(failures.values()):
        return InvariantResult.fail("release published without required local gates", failures)
    return InvariantResult.pass_()


def invariant_one_terminal_result(state: State, trace) -> InvariantResult:
    del trace
    if state.pushes + state.stops > 1:
        return InvariantResult.fail(
            "release workflow produced more than one terminal result",
            {"pushes": state.pushes, "stops": state.stops},
        )
    return InvariantResult.pass_()


def initial_state() -> State:
    return State()


def terminal_predicate(current_output, state: State, trace) -> bool:
    return isinstance(current_output, PublishResult)


def build_workflow() -> Workflow:
    return Workflow(
        name="github_release_publish",
        blocks=(UpgradeSmokeGate(), PrepareReleaseFiles(), PublishRelease()),
    )


def build_broken_publish_workflow() -> Workflow:
    return Workflow(
        name="broken_github_release_publish_without_gates",
        blocks=(UpgradeSmokeGate(), PrepareReleaseFiles(), PublishWithoutGates()),
    )


def build_broken_upgrade_workflow() -> Workflow:
    return Workflow(
        name="broken_github_release_publish_upgrade_ignored",
        blocks=(UpgradeFailContinues(), PrepareReleaseFiles(), PublishRelease()),
    )


EXTERNAL_INPUTS = (
    ReleaseSignal("all_gates_ok", upgrade_smoke="ok"),
    ReleaseSignal("partial_upgrade_logged", upgrade_smoke="partial"),
    ReleaseSignal("hard_upgrade_failure", upgrade_smoke="fail"),
    ReleaseSignal("missing_version_bump", version_bumped=False),
    ReleaseSignal("privacy_failure", privacy_ok=False),
    ReleaseSignal("build_failure", build_ok=False),
    ReleaseSignal("remote_push_failure", push_ok=False),
    ReleaseSignal("release_creation_failure", release_ok=False),
)

INVARIANTS = (
    Invariant(
        name="hard_upgrade_failure_stops_release",
        description="A hard updater smoke failure stops the release before publication.",
        predicate=invariant_no_hard_upgrade_failure_continues,
    ),
    Invariant(
        name="partial_upgrade_result_is_logged",
        description="A partial updater smoke result may continue only when recorded as a release note/risk.",
        predicate=invariant_partial_upgrade_issue_logged,
    ),
    Invariant(
        name="release_not_published_without_local_gates",
        description="Publishing requires a version bump, privacy pass, and package build pass.",
        predicate=invariant_no_unverified_publish,
    ),
    Invariant(
        name="release_reaches_one_terminal_result",
        description="The publish workflow stops or publishes exactly once.",
        predicate=invariant_one_terminal_result,
    ),
)

MAX_SEQUENCE_LENGTH = 1
