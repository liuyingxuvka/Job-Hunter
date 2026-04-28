"""Flowguard model for target-role replacement and stale job analysis bindings.

The model covers one narrow boundary:

    search_profiles replacement/deletion -> candidate_jobs JSON cleanup -> runtime analysis write

The production risk is that candidate job payloads can keep an old
boundTargetRole.profileId after the corresponding search profile has been
deleted. A later runtime write then treats that stale JSON as live state and
attempts to write job_analyses with a missing foreign key.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


OLD_PROFILE_ID = 1
NEW_PROFILE_ID = 2


@dataclass(frozen=True)
class RoleEvent:
    kind: str  # bootstrap | replace_via_app | unsafe_external_delete | runtime_write


@dataclass(frozen=True)
class State:
    valid_profiles: frozenset[int]
    analysis_refs: frozenset[int]
    review_refs: frozenset[int]
    json_bound_profile_id: int | None
    json_target_score_profile_ids: tuple[int, ...]
    scoring_status: str
    recommendation_status: str
    output_status: str
    manual_fields_preserved: bool = True
    fk_failure: bool = False
    runtime_writes: int = 0
    stale_bindings_cleared: int = 0
    stale_runtime_writes_skipped: int = 0


def clean_old_profile_state() -> State:
    return State(
        valid_profiles=frozenset({OLD_PROFILE_ID}),
        analysis_refs=frozenset({OLD_PROFILE_ID}),
        review_refs=frozenset({OLD_PROFILE_ID}),
        json_bound_profile_id=OLD_PROFILE_ID,
        json_target_score_profile_ids=(OLD_PROFILE_ID,),
        scoring_status="scored",
        recommendation_status="pass",
        output_status="pass",
    )


def dirty_replaced_state() -> State:
    return State(
        valid_profiles=frozenset({NEW_PROFILE_ID}),
        analysis_refs=frozenset({OLD_PROFILE_ID}),
        review_refs=frozenset({OLD_PROFILE_ID}),
        json_bound_profile_id=OLD_PROFILE_ID,
        json_target_score_profile_ids=(OLD_PROFILE_ID,),
        scoring_status="scored",
        recommendation_status="pass",
        output_status="pass",
    )


class ProfileMutation:
    name = "ProfileMutation"
    reads = ("valid_profiles",)
    writes = ("valid_profiles", "analysis_refs", "review_refs")
    accepted_input_type = RoleEvent
    input_description = "profile lifecycle event"
    output_description = "same event after profile table mutation"
    idempotency = "Repeated replacement keeps the same current profile set."

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        if input_obj.kind == "replace_via_app":
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    valid_profiles=frozenset({NEW_PROFILE_ID}),
                    analysis_refs=frozenset(
                        profile_id
                        for profile_id in state.analysis_refs
                        if profile_id == NEW_PROFILE_ID
                    ),
                    review_refs=frozenset(
                        profile_id
                        for profile_id in state.review_refs
                        if profile_id == NEW_PROFILE_ID
                    ),
                ),
                label="profiles_replaced_with_fk_cascade",
            )
            return
        if input_obj.kind == "unsafe_external_delete":
            yield FunctionResult(
                output=input_obj,
                new_state=replace(state, valid_profiles=frozenset({NEW_PROFILE_ID})),
                label="profiles_replaced_without_fk_cascade",
            )
            return
        yield FunctionResult(output=input_obj, new_state=state, label="profiles_unchanged")


class RelationalOrphanCleanup:
    name = "RelationalOrphanCleanup"
    reads = ("valid_profiles", "analysis_refs", "review_refs")
    writes = ("analysis_refs", "review_refs")
    accepted_input_type = RoleEvent
    input_description = "profile lifecycle event after mutation"
    output_description = "same event after relational repair"
    idempotency = "Repeated cleanup is stable after all orphan references are removed."

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        analysis_refs = frozenset(
            profile_id for profile_id in state.analysis_refs if profile_id in state.valid_profiles
        )
        review_refs = frozenset(
            profile_id for profile_id in state.review_refs if profile_id in state.valid_profiles
        )
        if analysis_refs != state.analysis_refs or review_refs != state.review_refs:
            yield FunctionResult(
                output=input_obj,
                new_state=replace(state, analysis_refs=analysis_refs, review_refs=review_refs),
                label="relational_orphans_repaired",
            )
            return
        yield FunctionResult(output=input_obj, new_state=state, label="relational_state_clean")


class CandidateJsonBindingCleanup:
    name = "CandidateJsonBindingCleanup"
    reads = ("valid_profiles", "json_bound_profile_id", "json_target_score_profile_ids")
    writes = (
        "json_bound_profile_id",
        "json_target_score_profile_ids",
        "scoring_status",
        "recommendation_status",
        "output_status",
        "stale_bindings_cleared",
    )
    accepted_input_type = RoleEvent
    input_description = "profile lifecycle event after relational repair"
    output_description = "same event after JSON binding cleanup"
    idempotency = "Repeated cleanup leaves already-unbound jobs pending for rescoring."

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        bound_profile_id = (
            state.json_bound_profile_id
            if state.json_bound_profile_id in state.valid_profiles
            else None
        )
        score_profile_ids = tuple(
            profile_id
            for profile_id in state.json_target_score_profile_ids
            if profile_id in state.valid_profiles
        )
        stale_removed = (
            bound_profile_id != state.json_bound_profile_id
            or score_profile_ids != state.json_target_score_profile_ids
        )
        if stale_removed:
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    json_bound_profile_id=bound_profile_id,
                    json_target_score_profile_ids=score_profile_ids,
                    scoring_status="pending",
                    recommendation_status="pending",
                    output_status="pending",
                    stale_bindings_cleared=state.stale_bindings_cleared + 1,
                ),
                label="stale_json_binding_cleared",
            )
            return
        yield FunctionResult(output=input_obj, new_state=state, label="json_binding_clean")


class NoJsonBindingCleanup(CandidateJsonBindingCleanup):
    name = "NoJsonBindingCleanup"

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        yield FunctionResult(output=input_obj, new_state=state, label="broken_json_cleanup_skipped")


class RuntimeAnalysisWrite:
    name = "RuntimeAnalysisWrite"
    reads = ("valid_profiles", "json_bound_profile_id")
    writes = ("analysis_refs", "runtime_writes", "stale_runtime_writes_skipped")
    accepted_input_type = RoleEvent
    input_description = "profile lifecycle event after cleanup"
    output_description = "runtime write result"
    idempotency = "Repeated writes upsert the same valid analysis binding and skip stale bindings."

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        if input_obj.kind != "runtime_write":
            yield FunctionResult(output=input_obj, new_state=state, label="runtime_write_not_requested")
            return
        if state.json_bound_profile_id is None:
            yield FunctionResult(output=input_obj, new_state=state, label="runtime_no_bound_profile")
            return
        if state.json_bound_profile_id in state.valid_profiles:
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    analysis_refs=frozenset({*state.analysis_refs, state.json_bound_profile_id}),
                    runtime_writes=state.runtime_writes + 1,
                ),
                label="runtime_analysis_written",
            )
            return
        yield FunctionResult(
            output=input_obj,
            new_state=replace(
                state,
                stale_runtime_writes_skipped=state.stale_runtime_writes_skipped + 1,
            ),
            label="runtime_stale_binding_skipped",
        )


class RuntimeAnalysisWriteWithoutGuard(RuntimeAnalysisWrite):
    name = "RuntimeAnalysisWriteWithoutGuard"

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        if input_obj.kind != "runtime_write":
            yield FunctionResult(output=input_obj, new_state=state, label="runtime_write_not_requested")
            return
        if state.json_bound_profile_id is None:
            yield FunctionResult(output=input_obj, new_state=state, label="runtime_no_bound_profile")
            return
        if state.json_bound_profile_id in state.valid_profiles:
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    analysis_refs=frozenset({*state.analysis_refs, state.json_bound_profile_id}),
                    runtime_writes=state.runtime_writes + 1,
                ),
                label="runtime_analysis_written",
            )
            return
        yield FunctionResult(
            output=input_obj,
            new_state=replace(state, fk_failure=True),
            label="broken_runtime_fk_failure",
        )


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, state, trace
    return False


def no_fk_failures(state: State, trace) -> InvariantResult:
    del trace
    if state.fk_failure:
        return InvariantResult.fail("runtime write attempted a missing search_profile_id")
    return InvariantResult.pass_()


def no_relational_orphans(state: State, trace) -> InvariantResult:
    if not trace.steps:
        return InvariantResult.pass_()
    orphaned = tuple(
        sorted(
            {
                *state.analysis_refs.difference(state.valid_profiles),
                *state.review_refs.difference(state.valid_profiles),
            }
        )
    )
    if orphaned:
        return InvariantResult.fail(
            "relational tables still reference deleted search profiles",
            {"orphaned_profile_ids": orphaned},
        )
    return InvariantResult.pass_()


def no_stale_json_bindings(state: State, trace) -> InvariantResult:
    if not trace.steps:
        return InvariantResult.pass_()
    stale_ids: set[int] = set()
    if state.json_bound_profile_id is not None and state.json_bound_profile_id not in state.valid_profiles:
        stale_ids.add(state.json_bound_profile_id)
    stale_ids.update(
        profile_id
        for profile_id in state.json_target_score_profile_ids
        if profile_id not in state.valid_profiles
    )
    if stale_ids:
        return InvariantResult.fail(
            "candidate job JSON still references deleted search profiles",
            {"stale_profile_ids": tuple(sorted(stale_ids))},
        )
    return InvariantResult.pass_()


def stale_clears_reset_scoring_outputs(state: State, trace) -> InvariantResult:
    del trace
    if state.stale_bindings_cleared <= 0:
        return InvariantResult.pass_()
    if (
        state.scoring_status,
        state.recommendation_status,
        state.output_status,
    ) != ("pending", "pending", "pending"):
        return InvariantResult.fail(
            "stale role-bound jobs were not reset for rescoring",
            {
                "scoring_status": state.scoring_status,
                "recommendation_status": state.recommendation_status,
                "output_status": state.output_status,
            },
        )
    return InvariantResult.pass_()


def manual_fields_are_not_consumed(state: State, trace) -> InvariantResult:
    del trace
    if not state.manual_fields_preserved:
        return InvariantResult.fail("manual review/application fields were lost")
    return InvariantResult.pass_()


INVARIANTS = (
    Invariant(
        name="no_fk_failures",
        description="Runtime analysis writes must never target a missing search profile.",
        predicate=no_fk_failures,
    ),
    Invariant(
        name="no_relational_orphans",
        description="Relational profile references must be a subset of live search profiles.",
        predicate=no_relational_orphans,
    ),
    Invariant(
        name="no_stale_json_bindings",
        description="Candidate job JSON must not retain stale profile-bound target-role data.",
        predicate=no_stale_json_bindings,
    ),
    Invariant(
        name="stale_clears_reset_scoring_outputs",
        description="A stale role-bound analysis is invalidated and queued for rescoring.",
        predicate=stale_clears_reset_scoring_outputs,
    ),
    Invariant(
        name="manual_fields_are_not_consumed",
        description="Cleanup must not consume user review/application fields.",
        predicate=manual_fields_are_not_consumed,
    ),
)


EXTERNAL_INPUTS = (
    RoleEvent("bootstrap"),
    RoleEvent("replace_via_app"),
    RoleEvent("unsafe_external_delete"),
    RoleEvent("runtime_write"),
)

MAX_SEQUENCE_LENGTH = 3


def initial_states() -> tuple[State, ...]:
    return (clean_old_profile_state(), dirty_replaced_state())


def build_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            CandidateJsonBindingCleanup(),
            RuntimeAnalysisWrite(),
        ),
        name="target_role_reset",
    )


def build_no_json_cleanup_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            NoJsonBindingCleanup(),
            RuntimeAnalysisWrite(),
        ),
        name="broken_no_json_cleanup",
    )


def build_no_json_cleanup_no_write_guard_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            NoJsonBindingCleanup(),
            RuntimeAnalysisWriteWithoutGuard(),
        ),
        name="broken_no_json_cleanup_no_write_guard",
    )


__all__ = [
    "EXTERNAL_INPUTS",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "RoleEvent",
    "State",
    "build_no_json_cleanup_no_write_guard_workflow",
    "build_no_json_cleanup_workflow",
    "build_workflow",
    "clean_old_profile_state",
    "dirty_replaced_state",
    "initial_states",
    "terminal_predicate",
]
