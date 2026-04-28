"""Flowguard model for the Jobflow timed search-session loop.

The model intentionally covers one narrow boundary:

    direct job discovery -> existing company sources -> company discovery -> stop decision

It does not call production code, databases, the network, or an LLM. The goal is
to make the intended round-level policy executable before or alongside
production changes to the search orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


EMPTY_ROUND_LIMIT = 3


@dataclass(frozen=True)
class RoundSignal:
    name: str
    direct: str = "empty"  # empty | job | soft_fail
    sources: str = "empty"  # empty | job | ranking_unlock | pending_stuck
    company_discovery: str = "empty"  # empty | new_company


@dataclass(frozen=True)
class DirectObserved:
    signal: RoundSignal
    produced: bool


@dataclass(frozen=True)
class SourcesObserved:
    signal: RoundSignal
    produced: bool


@dataclass(frozen=True)
class RoundObserved:
    produced: bool


@dataclass(frozen=True)
class SearchDecision:
    action: str  # continue | stop
    reason: str = ""


@dataclass(frozen=True)
class State:
    consecutive_empty_rounds: int = 0
    rounds_completed: int = 0
    direct_attempts: int = 0
    source_attempts: int = 0
    company_discovery_attempts: int = 0
    jobs: int = 0
    companies: int = 0
    unlocked_work: int = 0
    stopped: bool = False
    stop_reason: str = ""


class DirectJobDiscovery:
    name = "DirectJobDiscovery"
    reads = ("stopped",)
    writes = ("direct_attempts", "jobs")
    accepted_input_type = RoundSignal
    input_description = "abstract round signal"
    output_description = "direct discovery result for this round"
    idempotency = "A repeated abstract round may retry direct discovery, but it does not itself stop the session."

    def apply(self, input_obj: RoundSignal, state: State) -> Iterable[FunctionResult]:
        if input_obj.direct == "job":
            yield FunctionResult(
                output=DirectObserved(input_obj, produced=True),
                new_state=replace(
                    state,
                    direct_attempts=state.direct_attempts + 1,
                    jobs=state.jobs + 1,
                ),
                label="direct_job_found",
            )
            return
        if input_obj.direct == "soft_fail":
            yield FunctionResult(
                output=DirectObserved(input_obj, produced=False),
                new_state=replace(state, direct_attempts=state.direct_attempts + 1),
                label="direct_soft_failed",
            )
            return
        yield FunctionResult(
            output=DirectObserved(input_obj, produced=False),
            new_state=replace(state, direct_attempts=state.direct_attempts + 1),
            label="direct_empty",
        )


class ExistingCompanySources:
    name = "ExistingCompanySources"
    reads = ("jobs", "unlocked_work")
    writes = ("source_attempts", "jobs", "unlocked_work")
    accepted_input_type = DirectObserved
    input_description = "direct discovery result"
    output_description = "existing company sources result for this round"
    idempotency = "A repeated abstract round may process the next ready batch, but a pending-stuck batch is not progress."

    def apply(self, input_obj: DirectObserved, state: State) -> Iterable[FunctionResult]:
        produced = input_obj.produced
        next_state = replace(state, source_attempts=state.source_attempts + 1)
        if input_obj.signal.sources == "job":
            produced = True
            next_state = replace(next_state, jobs=next_state.jobs + 1)
            label = "sources_job"
        elif input_obj.signal.sources == "ranking_unlock":
            produced = True
            next_state = replace(next_state, unlocked_work=next_state.unlocked_work + 1)
            label = "sources_unlocked_work"
        elif input_obj.signal.sources == "pending_stuck":
            label = "sources_pending_stuck"
        else:
            label = "sources_empty"
        yield FunctionResult(
            output=SourcesObserved(input_obj.signal, produced=produced),
            new_state=next_state,
            label=label,
        )


class CompanyDiscovery:
    name = "CompanyDiscovery"
    reads = ("companies",)
    writes = ("company_discovery_attempts", "companies")
    accepted_input_type = SourcesObserved
    input_description = "existing company sources result"
    output_description = "full round result after replenishing company space"
    idempotency = "Every completed round attempts discovery once; existing-company dedupe belongs inside the real discovery stage."

    def apply(self, input_obj: SourcesObserved, state: State) -> Iterable[FunctionResult]:
        next_state = replace(
            state,
            company_discovery_attempts=state.company_discovery_attempts + 1,
        )
        if input_obj.signal.company_discovery == "new_company":
            yield FunctionResult(
                output=RoundObserved(produced=True),
                new_state=replace(next_state, companies=next_state.companies + 1),
                label="company_discovery_new_company",
            )
            return
        yield FunctionResult(
            output=RoundObserved(produced=input_obj.produced),
            new_state=next_state,
            label="company_discovery_empty",
        )


class StopDecision:
    name = "StopDecision"
    reads = ("consecutive_empty_rounds", "stopped")
    writes = ("consecutive_empty_rounds", "rounds_completed", "stopped", "stop_reason")
    accepted_input_type = RoundObserved
    input_description = "full round result"
    output_description = "continue or stop decision"
    idempotency = "Only a completed empty round increments the empty-round counter."

    def apply(self, input_obj: RoundObserved, state: State) -> Iterable[FunctionResult]:
        rounds_completed = state.rounds_completed + 1
        if input_obj.produced:
            yield FunctionResult(
                output=SearchDecision("continue"),
                new_state=replace(
                    state,
                    rounds_completed=rounds_completed,
                    consecutive_empty_rounds=0,
                ),
                label="continue_with_progress",
            )
            return

        empty_rounds = state.consecutive_empty_rounds + 1
        if empty_rounds >= EMPTY_ROUND_LIMIT:
            yield FunctionResult(
                output=SearchDecision("stop", "consecutive_empty_rounds"),
                new_state=replace(
                    state,
                    rounds_completed=rounds_completed,
                    consecutive_empty_rounds=empty_rounds,
                    stopped=True,
                    stop_reason="consecutive_empty_rounds",
                ),
                label="stop_after_three_empty",
            )
            return
        yield FunctionResult(
            output=SearchDecision("continue"),
            new_state=replace(
                state,
                rounds_completed=rounds_completed,
                consecutive_empty_rounds=empty_rounds,
            ),
            label="continue_empty",
        )


class StopAfterOneEmpty(StopDecision):
    name = "StopAfterOneEmpty"

    def apply(self, input_obj: RoundObserved, state: State) -> Iterable[FunctionResult]:
        rounds_completed = state.rounds_completed + 1
        if input_obj.produced:
            yield FunctionResult(
                output=SearchDecision("continue"),
                new_state=replace(
                    state,
                    rounds_completed=rounds_completed,
                    consecutive_empty_rounds=0,
                ),
                label="continue_with_progress",
            )
            return
        yield FunctionResult(
            output=SearchDecision("stop", "consecutive_empty_rounds"),
            new_state=replace(
                state,
                rounds_completed=rounds_completed,
                consecutive_empty_rounds=1,
                stopped=True,
                stop_reason="consecutive_empty_rounds",
            ),
            label="broken_stop_after_one_empty",
        )


class SkipDiscoveryWhenSourcesProduce(CompanyDiscovery):
    name = "SkipDiscoveryWhenSourcesProduce"

    def apply(self, input_obj: SourcesObserved, state: State) -> Iterable[FunctionResult]:
        if input_obj.produced:
            yield FunctionResult(
                output=RoundObserved(produced=True),
                new_state=state,
                label="broken_company_discovery_skipped",
            )
            return
        yield from super().apply(input_obj, state)


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, trace
    return bool(state.stopped)


def no_stop_before_three_empty_rounds(state: State, trace) -> InvariantResult:
    del trace
    if state.stopped and state.stop_reason == "consecutive_empty_rounds":
        if state.consecutive_empty_rounds < EMPTY_ROUND_LIMIT:
            return InvariantResult.fail(
                "session stopped before three consecutive empty rounds",
                {"empty_rounds": state.consecutive_empty_rounds},
            )
    return InvariantResult.pass_()


def discovery_attempted_for_each_completed_round(state: State, trace) -> InvariantResult:
    del trace
    if state.company_discovery_attempts != state.rounds_completed:
        return InvariantResult.fail(
            "company discovery was not attempted exactly once for each completed round",
            {
                "rounds_completed": state.rounds_completed,
                "company_discovery_attempts": state.company_discovery_attempts,
            },
        )
    return InvariantResult.pass_()


def productive_round_resets_empty_counter(state: State, trace) -> InvariantResult:
    del state
    for step in trace.steps:
        if step.function_name == "StopDecision" and step.label == "continue_with_progress":
            if step.new_state.consecutive_empty_rounds != 0:
                return InvariantResult.fail("productive round did not reset empty counter")
    return InvariantResult.pass_()


def nonterminal_empty_count_stays_below_limit(state: State, trace) -> InvariantResult:
    del trace
    if not state.stopped and state.consecutive_empty_rounds >= EMPTY_ROUND_LIMIT:
        return InvariantResult.fail("non-terminal state reached empty-round stop threshold")
    return InvariantResult.pass_()


INVARIANTS = (
    Invariant(
        name="no_stop_before_three_empty_rounds",
        description="A semantic no-output stop requires three consecutive empty rounds.",
        predicate=no_stop_before_three_empty_rounds,
    ),
    Invariant(
        name="discovery_attempted_for_each_completed_round",
        description="Company discovery is a fixed step in every completed round.",
        predicate=discovery_attempted_for_each_completed_round,
    ),
    Invariant(
        name="productive_round_resets_empty_counter",
        description="Any meaningful output resets the empty-round counter.",
        predicate=productive_round_resets_empty_counter,
    ),
    Invariant(
        name="nonterminal_empty_count_stays_below_limit",
        description="Reaching the empty-round limit must be terminal.",
        predicate=nonterminal_empty_count_stays_below_limit,
    ),
)


EXTERNAL_INPUTS = (
    RoundSignal("empty"),
    RoundSignal("direct_job", direct="job"),
    RoundSignal("direct_soft_fail", direct="soft_fail"),
    RoundSignal("source_job", sources="job"),
    RoundSignal("source_ranking_unlock", sources="ranking_unlock"),
    RoundSignal("source_pending_stuck", sources="pending_stuck"),
    RoundSignal("new_company", company_discovery="new_company"),
)

MAX_SEQUENCE_LENGTH = 3


def initial_state() -> State:
    return State()


def build_workflow() -> Workflow:
    return Workflow(
        (DirectJobDiscovery(), ExistingCompanySources(), CompanyDiscovery(), StopDecision()),
        name="jobflow_search_loop",
    )


def build_stop_after_one_empty_workflow() -> Workflow:
    return Workflow(
        (
            DirectJobDiscovery(),
            ExistingCompanySources(),
            CompanyDiscovery(),
            StopAfterOneEmpty(),
        ),
        name="broken_stop_after_one_empty",
    )


def build_skip_discovery_workflow() -> Workflow:
    return Workflow(
        (
            DirectJobDiscovery(),
            ExistingCompanySources(),
            SkipDiscoveryWhenSourcesProduce(),
            StopDecision(),
        ),
        name="broken_skip_discovery_when_sources_produce",
    )


__all__ = [
    "EMPTY_ROUND_LIMIT",
    "EXTERNAL_INPUTS",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "RoundSignal",
    "State",
    "build_skip_discovery_workflow",
    "build_stop_after_one_empty_workflow",
    "build_workflow",
    "initial_state",
    "terminal_predicate",
]
