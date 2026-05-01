"""FlowGuard model for role-scope prompt semantics.

The model covers one narrow prompt policy:

    target role idea + candidate evidence + mix request -> saved scope label

The intended rule is that scope labels describe search radius and career
distance. Role-mix targets may ask for core/adjacent/exploratory ideas, but
they must not relabel a near-core idea merely to fill a bucket.

This model does not call production code, databases, the network, or an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


@dataclass(frozen=True)
class RoleIdea:
    name: str
    evidence_distance: str  # mainline | nearby_transfer | far_reposition | unsupported
    function_change: str = "same"  # same | technical_shift | business_pivot
    requested_bucket: str = "any"  # any | core | adjacent | exploratory


@dataclass(frozen=True)
class PreparedIdea:
    idea: RoleIdea
    natural_scope: str  # core | adjacent | exploratory | skip


@dataclass(frozen=True)
class ScopedIdea:
    idea: RoleIdea
    natural_scope: str
    saved_scope: str  # core | adjacent | exploratory | skip


@dataclass(frozen=True)
class State:
    ideas_seen: int = 0
    labeled_count: int = 0
    skipped_count: int = 0
    mainline_demotions: int = 0
    unsupported_labeled: int = 0
    mix_forced_relabels: int = 0
    nearby_domains_blocked: int = 0


def _natural_scope(idea: RoleIdea) -> str:
    if idea.evidence_distance == "mainline":
        return "core"
    if idea.evidence_distance == "nearby_transfer":
        return "adjacent"
    if idea.evidence_distance == "far_reposition":
        return "exploratory"
    return "skip"


class EvidenceDistanceClassifier:
    name = "EvidenceDistanceClassifier"
    reads = ()
    writes = ("ideas_seen",)
    accepted_input_type = RoleIdea
    input_description = "abstract target-role idea with candidate-evidence distance"
    output_description = "role idea with its natural search-radius scope"
    idempotency = "The same abstract idea maps to the same natural scope."

    def apply(self, input_obj: RoleIdea, state: State) -> Iterable[FunctionResult]:
        yield FunctionResult(
            output=PreparedIdea(input_obj, _natural_scope(input_obj)),
            new_state=replace(state, ideas_seen=state.ideas_seen + 1),
            label=f"natural_scope_{_natural_scope(input_obj)}",
        )


class PromptScopePolicy:
    name = "PromptScopePolicy"
    reads = ("labeled_count", "skipped_count")
    writes = ("labeled_count", "skipped_count")
    accepted_input_type = PreparedIdea
    input_description = "role idea with natural search-radius scope"
    output_description = "saved role scope or skip decision"
    idempotency = "Repeated prompt review returns the natural scope or skips when a requested bucket conflicts."

    def apply(self, input_obj: PreparedIdea, state: State) -> Iterable[FunctionResult]:
        requested = input_obj.idea.requested_bucket
        natural = input_obj.natural_scope
        if natural == "skip":
            yield FunctionResult(
                output=ScopedIdea(input_obj.idea, natural, "skip"),
                new_state=replace(state, skipped_count=state.skipped_count + 1),
                label="unsupported_idea_skipped",
            )
            return
        if requested != "any" and requested != natural:
            yield FunctionResult(
                output=ScopedIdea(input_obj.idea, natural, "skip"),
                new_state=replace(state, skipped_count=state.skipped_count + 1),
                label="bucket_conflict_return_fewer",
            )
            return
        yield FunctionResult(
            output=ScopedIdea(input_obj.idea, natural, natural),
            new_state=replace(state, labeled_count=state.labeled_count + 1),
            label=f"saved_scope_{natural}",
        )


class FunctionShiftScopePolicy(PromptScopePolicy):
    name = "FunctionShiftScopePolicy"

    def apply(self, input_obj: PreparedIdea, state: State) -> Iterable[FunctionResult]:
        natural = input_obj.natural_scope
        saved = natural
        mainline_demotions = state.mainline_demotions
        if input_obj.idea.evidence_distance == "mainline" and input_obj.idea.function_change == "technical_shift":
            saved = "adjacent"
            mainline_demotions += 1
        if input_obj.idea.evidence_distance == "mainline" and input_obj.idea.function_change == "business_pivot":
            saved = "exploratory"
            mainline_demotions += 1
        yield FunctionResult(
            output=ScopedIdea(input_obj.idea, natural, saved),
            new_state=replace(
                state,
                labeled_count=state.labeled_count + (0 if saved == "skip" else 1),
                skipped_count=state.skipped_count + (1 if saved == "skip" else 0),
                mainline_demotions=mainline_demotions,
            ),
            label="broken_function_shift_overrode_search_radius",
        )


class MixForcedScopePolicy(PromptScopePolicy):
    name = "MixForcedScopePolicy"

    def apply(self, input_obj: PreparedIdea, state: State) -> Iterable[FunctionResult]:
        requested = input_obj.idea.requested_bucket
        natural = input_obj.natural_scope
        if natural == "skip":
            yield FunctionResult(
                output=ScopedIdea(input_obj.idea, natural, "skip"),
                new_state=replace(state, skipped_count=state.skipped_count + 1),
                label="unsupported_idea_skipped",
            )
            return
        saved = requested if requested != "any" else natural
        mix_forced = 1 if saved != natural else 0
        yield FunctionResult(
            output=ScopedIdea(input_obj.idea, natural, saved),
            new_state=replace(
                state,
                labeled_count=state.labeled_count + 1,
                mix_forced_relabels=state.mix_forced_relabels + mix_forced,
            ),
            label="broken_mix_target_forced_scope",
        )


class RestrictiveNearbyDomainPolicy(PromptScopePolicy):
    name = "RestrictiveNearbyDomainPolicy"

    def apply(self, input_obj: PreparedIdea, state: State) -> Iterable[FunctionResult]:
        if input_obj.natural_scope == "adjacent":
            yield FunctionResult(
                output=ScopedIdea(input_obj.idea, input_obj.natural_scope, "skip"),
                new_state=replace(
                    state,
                    skipped_count=state.skipped_count + 1,
                    nearby_domains_blocked=state.nearby_domains_blocked + 1,
                ),
                label="broken_nearby_transfer_domain_blocked",
            )
            return
        yield from super().apply(input_obj, state)


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, state, trace
    return False


def mainline_evidence_stays_core(state: State, trace) -> InvariantResult:
    del trace
    if state.mainline_demotions:
        return InvariantResult.fail(
            "mainline evidence was demoted because the daily function changed",
            {"mainline_demotions": state.mainline_demotions},
        )
    return InvariantResult.pass_()


def mix_targets_do_not_force_labels(state: State, trace) -> InvariantResult:
    del trace
    if state.mix_forced_relabels:
        return InvariantResult.fail(
            "role-mix target relabeled an idea instead of returning fewer roles",
            {"mix_forced_relabels": state.mix_forced_relabels},
        )
    return InvariantResult.pass_()


def unsupported_ideas_are_not_labeled(state: State, trace) -> InvariantResult:
    del trace
    if state.unsupported_labeled:
        return InvariantResult.fail(
            "unsupported idea received a saved scope label",
            {"unsupported_labeled": state.unsupported_labeled},
        )
    return InvariantResult.pass_()


def nearby_transfer_domains_remain_available(state: State, trace) -> InvariantResult:
    del trace
    if state.nearby_domains_blocked:
        return InvariantResult.fail(
            "nearby transferable technical domains were blocked by prompt wording",
            {"nearby_domains_blocked": state.nearby_domains_blocked},
        )
    return InvariantResult.pass_()


INVARIANTS = (
    Invariant(
        name="mainline_evidence_stays_core",
        description="Mainline candidate evidence remains core even if the practical work setting changes.",
        predicate=mainline_evidence_stays_core,
    ),
    Invariant(
        name="mix_targets_do_not_force_labels",
        description="Role-mix targets can skip weak buckets but cannot force incorrect labels.",
        predicate=mix_targets_do_not_force_labels,
    ),
    Invariant(
        name="unsupported_ideas_are_not_labeled",
        description="Unsupported distant ideas are skipped rather than labeled.",
        predicate=unsupported_ideas_are_not_labeled,
    ),
    Invariant(
        name="nearby_transfer_domains_remain_available",
        description="Nearby transferable technical domains remain valid adjacent ideas.",
        predicate=nearby_transfer_domains_remain_available,
    ),
)


EXTERNAL_INPUTS = (
    RoleIdea("mainline_same", "mainline", "same", "any"),
    RoleIdea("mainline_technical_shift", "mainline", "technical_shift", "any"),
    RoleIdea("mainline_requested_adjacent", "mainline", "technical_shift", "adjacent"),
    RoleIdea("nearby_transfer", "nearby_transfer", "technical_shift", "adjacent"),
    RoleIdea("nearby_requested_exploratory", "nearby_transfer", "technical_shift", "exploratory"),
    RoleIdea("far_reposition", "far_reposition", "business_pivot", "exploratory"),
    RoleIdea("far_requested_core", "far_reposition", "business_pivot", "core"),
    RoleIdea("unsupported", "unsupported", "business_pivot", "exploratory"),
)

MAX_SEQUENCE_LENGTH = 2


def initial_state() -> State:
    return State()


def build_workflow() -> Workflow:
    return Workflow(
        (
            EvidenceDistanceClassifier(),
            PromptScopePolicy(),
        ),
        name="role_scope_prompt_policy",
    )


def build_function_shift_workflow() -> Workflow:
    return Workflow(
        (
            EvidenceDistanceClassifier(),
            FunctionShiftScopePolicy(),
        ),
        name="broken_function_shift_scope_policy",
    )


def build_mix_forced_workflow() -> Workflow:
    return Workflow(
        (
            EvidenceDistanceClassifier(),
            MixForcedScopePolicy(),
        ),
        name="broken_mix_forced_scope_policy",
    )


def build_restrictive_nearby_workflow() -> Workflow:
    return Workflow(
        (
            EvidenceDistanceClassifier(),
            RestrictiveNearbyDomainPolicy(),
        ),
        name="broken_restrictive_nearby_scope_policy",
    )


__all__ = [
    "EXTERNAL_INPUTS",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "RoleIdea",
    "State",
    "build_function_shift_workflow",
    "build_mix_forced_workflow",
    "build_restrictive_nearby_workflow",
    "build_workflow",
    "initial_state",
    "terminal_predicate",
]
