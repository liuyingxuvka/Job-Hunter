"""FlowGuard model for live search-result table refresh behavior.

The model covers the UI polling boundary while a search is running:

    a poll with the same visible job signature should update lightweight
    progress text only, not rebuild the table; a poll with changed visible jobs
    may rebuild the table but must preserve the user's scroll position.

It has no Qt dependency and does not inspect runtime data.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


@dataclass(frozen=True)
class PollInput:
    name: str
    visible_signature: str
    current_scroll: str = "top"  # top | down


@dataclass(frozen=True)
class PollDecision:
    action: str  # render | skip
    visible_signature: str
    scroll_after: str


@dataclass(frozen=True)
class State:
    stored_signature: str = ""
    renders: int = 0
    skips: int = 0
    redundant_renders: int = 0
    scroll_resets: int = 0
    signature_writes: int = 0


class LiveResultsRefresh:
    name = "LiveResultsRefresh"
    reads = ("stored_signature",)
    writes = ("stored_signature", "renders", "skips", "signature_writes")
    accepted_input_type = PollInput
    input_description = "visible job signature observed by a live-results poll"
    output_description = "table render decision"
    idempotency = "Repeated polls with the same visible job signature skip table rebuilds."

    def apply(self, input_obj: PollInput, state: State) -> Iterable[FunctionResult]:
        if not input_obj.visible_signature or input_obj.visible_signature == state.stored_signature:
            yield FunctionResult(
                output=PollDecision("skip", input_obj.visible_signature, input_obj.current_scroll),
                new_state=replace(state, skips=state.skips + 1),
                label="skip_unchanged_visible_jobs",
            )
            return

        yield FunctionResult(
            output=PollDecision("render", input_obj.visible_signature, input_obj.current_scroll),
            new_state=replace(
                state,
                stored_signature=input_obj.visible_signature,
                renders=state.renders + 1,
                signature_writes=state.signature_writes + 1,
            ),
            label="render_changed_visible_jobs",
        )


class CompactRenderWithoutSignatureWrite(LiveResultsRefresh):
    name = "CompactRenderWithoutSignatureWrite"

    def apply(self, input_obj: PollInput, state: State) -> Iterable[FunctionResult]:
        if not input_obj.visible_signature:
            yield FunctionResult(
                output=PollDecision("skip", input_obj.visible_signature, input_obj.current_scroll),
                new_state=replace(state, skips=state.skips + 1),
                label="broken_empty_skip",
            )
            return
        if input_obj.visible_signature == state.stored_signature:
            yield FunctionResult(
                output=PollDecision("skip", input_obj.visible_signature, input_obj.current_scroll),
                new_state=replace(state, skips=state.skips + 1),
                label="broken_skip_after_recorded_signature",
            )
            return
        scroll_after = "top"
        yield FunctionResult(
            output=PollDecision("render", input_obj.visible_signature, scroll_after),
            new_state=replace(
                state,
                renders=state.renders + 1,
                redundant_renders=state.redundant_renders + 1,
                scroll_resets=state.scroll_resets
                + (1 if input_obj.current_scroll != "top" and scroll_after == "top" else 0),
            ),
            label="broken_render_without_signature_write",
        )


class ChangedRenderResetsScroll(LiveResultsRefresh):
    name = "ChangedRenderResetsScroll"

    def apply(self, input_obj: PollInput, state: State) -> Iterable[FunctionResult]:
        if not input_obj.visible_signature or input_obj.visible_signature == state.stored_signature:
            yield FunctionResult(
                output=PollDecision("skip", input_obj.visible_signature, input_obj.current_scroll),
                new_state=replace(state, skips=state.skips + 1),
                label="skip_unchanged_visible_jobs",
            )
            return
        scroll_after = "top"
        yield FunctionResult(
            output=PollDecision("render", input_obj.visible_signature, scroll_after),
            new_state=replace(
                state,
                stored_signature=input_obj.visible_signature,
                renders=state.renders + 1,
                scroll_resets=state.scroll_resets
                + (1 if input_obj.current_scroll != "top" and scroll_after == "top" else 0),
                signature_writes=state.signature_writes + 1,
            ),
            label="broken_changed_render_resets_scroll",
        )


def invariant_no_redundant_renders(state: State, trace) -> InvariantResult:
    del trace
    if state.redundant_renders:
        return InvariantResult.fail(
            "unchanged visible jobs triggered another table render",
            {"redundant_renders": state.redundant_renders},
        )
    return InvariantResult.pass_()


def invariant_no_scroll_resets(state: State, trace) -> InvariantResult:
    del trace
    if state.scroll_resets:
        return InvariantResult.fail(
            "live-results poll reset a user-scrolled table to the top",
            {"scroll_resets": state.scroll_resets},
        )
    return InvariantResult.pass_()


def invariant_changed_renders_record_signature(state: State, trace) -> InvariantResult:
    del trace
    if state.renders != state.signature_writes:
        return InvariantResult.fail(
            "a table render did not record the visible job signature",
            {"renders": state.renders, "signature_writes": state.signature_writes},
        )
    return InvariantResult.pass_()


def initial_state() -> State:
    return State()


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, state
    return len(trace) >= MAX_SEQUENCE_LENGTH


def build_workflow() -> Workflow:
    return Workflow(name="search_results_scroll_refresh", blocks=(LiveResultsRefresh(),))


def build_no_signature_workflow() -> Workflow:
    return Workflow(
        name="broken_search_results_scroll_refresh_no_signature",
        blocks=(CompactRenderWithoutSignatureWrite(),),
    )


def build_scroll_reset_workflow() -> Workflow:
    return Workflow(
        name="broken_search_results_scroll_refresh_resets_scroll",
        blocks=(ChangedRenderResetsScroll(),),
    )


EXTERNAL_INPUTS = (
    PollInput("initial_jobs_top", "jobs:A", "top"),
    PollInput("same_jobs_scrolled", "jobs:A", "down"),
    PollInput("new_jobs_scrolled", "jobs:B", "down"),
    PollInput("no_jobs_scrolled", "", "down"),
)

INVARIANTS = (
    Invariant(
        name="unchanged_visible_jobs_do_not_rerender",
        description="Repeated live polls with the same visible jobs do not rebuild the table.",
        predicate=invariant_no_redundant_renders,
    ),
    Invariant(
        name="live_refresh_preserves_user_scroll",
        description="Live polling does not jump a user-scrolled result table back to the top.",
        predicate=invariant_no_scroll_resets,
    ),
    Invariant(
        name="changed_renders_record_signature",
        description="Every table render records the visible job signature used for future no-op polls.",
        predicate=invariant_changed_renders_record_signature,
    ),
)

MAX_SEQUENCE_LENGTH = 2
