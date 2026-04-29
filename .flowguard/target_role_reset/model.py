"""FlowGuard model for target-role changes and recommendation persistence.

The model covers one narrow boundary:

    search_profiles deletion/update -> candidate_jobs recommendation state -> runtime analysis write

The intended product rule is cumulative: once a job has reached the visible
recommendation table, target-role edits must not erase it. Stale or changed
role bindings may remain as historical evidence, but visible rows must be
marked needs-rescore/not-current-fit instead of pretending to be current-fit.
Rows that never reached the visible recommendation table can still be reset.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


OLD_PROFILE_ID = 1
NEW_PROFILE_ID = 2


@dataclass(frozen=True)
class RoleEvent:
    kind: str


@dataclass(frozen=True)
class State:
    valid_profiles: frozenset[int]
    analysis_refs: frozenset[int]
    review_refs: frozenset[int]
    json_bound_profile_id: int | None
    json_target_score_profile_ids: tuple[int, ...]
    shown_once: bool
    scoring_status: str
    recommendation_status: str
    output_status: str
    current_fit_status: str
    manual_fields_preserved: bool = True
    fk_failure: bool = False
    runtime_writes: int = 0
    stale_unshown_cleared: int = 0
    visible_stale_marked: int = 0
    stale_runtime_writes_skipped: int = 0


def clean_shown_state() -> State:
    return State(
        valid_profiles=frozenset({OLD_PROFILE_ID}),
        analysis_refs=frozenset({OLD_PROFILE_ID}),
        review_refs=frozenset({OLD_PROFILE_ID}),
        json_bound_profile_id=OLD_PROFILE_ID,
        json_target_score_profile_ids=(OLD_PROFILE_ID,),
        shown_once=True,
        scoring_status="scored",
        recommendation_status="pass",
        output_status="pass",
        current_fit_status="current_fit",
    )


def clean_unshown_state() -> State:
    return replace(
        clean_shown_state(),
        shown_once=False,
        output_status="reject",
        current_fit_status="current_fit",
    )


def dirty_replaced_shown_state() -> State:
    return replace(
        clean_shown_state(),
        valid_profiles=frozenset({NEW_PROFILE_ID}),
    )


def dirty_replaced_unshown_state() -> State:
    return replace(
        clean_unshown_state(),
        valid_profiles=frozenset({NEW_PROFILE_ID}),
    )


def _json_profile_ids(state: State) -> frozenset[int]:
    ids = set(state.json_target_score_profile_ids)
    if state.json_bound_profile_id is not None:
        ids.add(state.json_bound_profile_id)
    return frozenset(ids)


def _has_stale_json_binding(state: State) -> bool:
    return any(profile_id not in state.valid_profiles for profile_id in _json_profile_ids(state))


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


class CandidateRecommendationBindingCleanup:
    name = "CandidateRecommendationBindingCleanup"
    reads = (
        "valid_profiles",
        "json_bound_profile_id",
        "json_target_score_profile_ids",
        "shown_once",
    )
    writes = (
        "json_bound_profile_id",
        "json_target_score_profile_ids",
        "scoring_status",
        "recommendation_status",
        "output_status",
        "current_fit_status",
        "stale_unshown_cleared",
        "visible_stale_marked",
    )
    accepted_input_type = RoleEvent
    input_description = "profile lifecycle event after relational repair"
    output_description = "same event after recommendation binding cleanup"
    idempotency = "Repeated cleanup keeps visible rows marked needs_rescore and leaves unshown rows reset."

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        role_changed = (
            input_obj.kind == "target_role_update"
            and state.json_bound_profile_id in state.valid_profiles
        )
        needs_recheck = _has_stale_json_binding(state) or role_changed
        if not needs_recheck:
            yield FunctionResult(output=input_obj, new_state=state, label="json_binding_clean")
            return

        if state.shown_once:
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    scoring_status="scored",
                    recommendation_status="pass",
                    output_status="pass",
                    current_fit_status="needs_rescore",
                    visible_stale_marked=state.visible_stale_marked + 1,
                ),
                label="visible_recommendation_marked_needs_rescore",
            )
            return

        yield FunctionResult(
            output=input_obj,
            new_state=replace(
                state,
                json_bound_profile_id=None,
                json_target_score_profile_ids=(),
                scoring_status="pending",
                recommendation_status="pending",
                output_status="pending",
                current_fit_status="none",
                stale_unshown_cleared=state.stale_unshown_cleared + 1,
            ),
            label="unshown_stale_json_binding_cleared",
        )


class ResetVisibleRecommendationCleanup(CandidateRecommendationBindingCleanup):
    name = "ResetVisibleRecommendationCleanup"

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        role_changed = (
            input_obj.kind == "target_role_update"
            and state.json_bound_profile_id in state.valid_profiles
        )
        if not (_has_stale_json_binding(state) or role_changed):
            yield FunctionResult(output=input_obj, new_state=state, label="json_binding_clean")
            return
        yield FunctionResult(
            output=input_obj,
            new_state=replace(
                state,
                json_bound_profile_id=None,
                json_target_score_profile_ids=(),
                scoring_status="pending",
                recommendation_status="pending",
                output_status="pending",
                current_fit_status="none",
                stale_unshown_cleared=state.stale_unshown_cleared + 1,
            ),
            label="broken_visible_recommendation_reset",
        )


class KeepVisibleUnlabeledCleanup(CandidateRecommendationBindingCleanup):
    name = "KeepVisibleUnlabeledCleanup"

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        role_changed = (
            input_obj.kind == "target_role_update"
            and state.json_bound_profile_id in state.valid_profiles
        )
        if not (_has_stale_json_binding(state) or role_changed):
            yield FunctionResult(output=input_obj, new_state=state, label="json_binding_clean")
            return
        if state.shown_once:
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    recommendation_status="pass",
                    output_status="pass",
                    current_fit_status="current_fit",
                ),
                label="broken_visible_stale_left_current_fit",
            )
            return
        yield from super().apply(input_obj, state)


class CurrentRescoreApplication:
    name = "CurrentRescoreApplication"
    reads = ("shown_once", "recommendation_status", "output_status")
    writes = ("recommendation_status", "output_status", "current_fit_status")
    accepted_input_type = RoleEvent
    input_description = "current-role rescore event"
    output_description = "same event after current-fit status update"
    idempotency = "Repeated current rejects keep historical visibility stable."

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        if input_obj.kind != "current_rescore_reject":
            yield FunctionResult(output=input_obj, new_state=state, label="current_rescore_not_requested")
            return
        if state.shown_once:
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    recommendation_status="pass",
                    output_status="pass",
                    current_fit_status="not_current_fit",
                ),
                label="current_reject_preserved_visible_history",
            )
            return
        yield FunctionResult(
            output=input_obj,
            new_state=replace(
                state,
                recommendation_status="reject",
                output_status="reject",
                current_fit_status="not_current_fit",
            ),
            label="current_reject_unshown_row",
        )


class CurrentRescoreOverwritesVisibility(CurrentRescoreApplication):
    name = "CurrentRescoreOverwritesVisibility"

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        if input_obj.kind != "current_rescore_reject":
            yield FunctionResult(output=input_obj, new_state=state, label="current_rescore_not_requested")
            return
        yield FunctionResult(
            output=input_obj,
            new_state=replace(
                state,
                recommendation_status="reject",
                output_status="reject",
                current_fit_status="not_current_fit",
            ),
            label="broken_current_reject_erased_visible_history",
        )


class RecommendedOutputSetRefresh:
    name = "RecommendedOutputSetRefresh"
    reads = ("shown_once", "recommendation_status", "output_status", "current_fit_status")
    writes = ("output_status", "current_fit_status")
    accepted_input_type = RoleEvent
    input_description = "recommended-output refresh event"
    output_description = "same event after output visibility reconciliation"
    idempotency = "Repeated output refreshes keep previously visible rows as historical recommendations."

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        if input_obj.kind != "output_refresh_excludes":
            yield FunctionResult(output=input_obj, new_state=state, label="output_refresh_not_requested")
            return
        if state.shown_once:
            current_fit_status = (
                state.current_fit_status
                if state.current_fit_status in {"needs_rescore", "not_current_fit"}
                else "historical_only"
            )
            yield FunctionResult(
                output=input_obj,
                new_state=replace(
                    state,
                    recommendation_status="pass",
                    output_status="pass",
                    current_fit_status=current_fit_status,
                ),
                label="output_refresh_preserved_visible_history",
            )
            return
        yield FunctionResult(
            output=input_obj,
            new_state=replace(
                state,
                output_status="reject",
                current_fit_status="historical_only",
            ),
            label="output_refresh_rejected_unshown_row",
        )


class OutputRefreshOverwritesVisibility(RecommendedOutputSetRefresh):
    name = "OutputRefreshOverwritesVisibility"

    def apply(self, input_obj: RoleEvent, state: State) -> Iterable[FunctionResult]:
        if input_obj.kind != "output_refresh_excludes":
            yield FunctionResult(output=input_obj, new_state=state, label="output_refresh_not_requested")
            return
        yield FunctionResult(
            output=input_obj,
            new_state=replace(
                state,
                output_status="reject",
                current_fit_status="historical_only",
            ),
            label="broken_output_refresh_erased_visible_history",
        )


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


def shown_recommendations_stay_visible(state: State, trace) -> InvariantResult:
    del trace
    if not state.shown_once:
        return InvariantResult.pass_()
    if state.recommendation_status != "pass" or state.output_status != "pass":
        return InvariantResult.fail(
            "previously visible recommendation was removed by role cleanup or rescore",
            {
                "recommendation_status": state.recommendation_status,
                "output_status": state.output_status,
            },
        )
    return InvariantResult.pass_()


def visible_stale_bindings_are_labeled(state: State, trace) -> InvariantResult:
    if not trace.steps:
        return InvariantResult.pass_()
    if not state.shown_once or state.recommendation_status != "pass" or state.output_status != "pass":
        return InvariantResult.pass_()
    if _has_stale_json_binding(state) and state.current_fit_status not in {
        "needs_rescore",
        "not_current_fit",
        "historical_only",
    }:
        return InvariantResult.fail(
            "visible stale target-role binding is not labeled historical/needs-rescore",
            {"current_fit_status": state.current_fit_status},
        )
    return InvariantResult.pass_()


def unshown_stale_bindings_are_reset(state: State, trace) -> InvariantResult:
    if not trace.steps or state.shown_once:
        return InvariantResult.pass_()
    if _has_stale_json_binding(state):
        return InvariantResult.fail("unshown stale target-role binding should be cleared")
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
        name="shown_recommendations_stay_visible",
        description="Rows already shown in recommendations remain visible unless user/link state removes them.",
        predicate=shown_recommendations_stay_visible,
    ),
    Invariant(
        name="visible_stale_bindings_are_labeled",
        description="Visible stale role bindings must be labeled historical/needs-rescore.",
        predicate=visible_stale_bindings_are_labeled,
    ),
    Invariant(
        name="unshown_stale_bindings_are_reset",
        description="Rows never shown in recommendations are reset instead of keeping stale bindings.",
        predicate=unshown_stale_bindings_are_reset,
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
    RoleEvent("target_role_update"),
    RoleEvent("current_rescore_reject"),
    RoleEvent("output_refresh_excludes"),
    RoleEvent("runtime_write"),
)

MAX_SEQUENCE_LENGTH = 3


def initial_states() -> tuple[State, ...]:
    return (
        clean_shown_state(),
        clean_unshown_state(),
        dirty_replaced_shown_state(),
        dirty_replaced_unshown_state(),
    )


def build_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            CandidateRecommendationBindingCleanup(),
            CurrentRescoreApplication(),
            RecommendedOutputSetRefresh(),
            RuntimeAnalysisWrite(),
        ),
        name="target_role_recommendation_persistence",
    )


def build_reset_visible_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            ResetVisibleRecommendationCleanup(),
            CurrentRescoreApplication(),
            RecommendedOutputSetRefresh(),
            RuntimeAnalysisWrite(),
        ),
        name="broken_reset_visible_recommendations",
    )


def build_unlabeled_visible_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            KeepVisibleUnlabeledCleanup(),
            CurrentRescoreApplication(),
            RecommendedOutputSetRefresh(),
            RuntimeAnalysisWrite(),
        ),
        name="broken_keep_visible_unlabeled",
    )


def build_rescore_overwrite_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            CandidateRecommendationBindingCleanup(),
            CurrentRescoreOverwritesVisibility(),
            RecommendedOutputSetRefresh(),
            RuntimeAnalysisWrite(),
        ),
        name="broken_current_rescore_overwrite",
    )


def build_output_refresh_overwrite_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            CandidateRecommendationBindingCleanup(),
            CurrentRescoreApplication(),
            OutputRefreshOverwritesVisibility(),
            RuntimeAnalysisWrite(),
        ),
        name="broken_output_refresh_overwrite",
    )


def build_no_write_guard_workflow() -> Workflow:
    return Workflow(
        (
            ProfileMutation(),
            RelationalOrphanCleanup(),
            CandidateRecommendationBindingCleanup(),
            CurrentRescoreApplication(),
            RecommendedOutputSetRefresh(),
            RuntimeAnalysisWriteWithoutGuard(),
        ),
        name="broken_no_runtime_write_guard",
    )


__all__ = [
    "EXTERNAL_INPUTS",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "RoleEvent",
    "State",
    "build_no_write_guard_workflow",
    "build_output_refresh_overwrite_workflow",
    "build_reset_visible_workflow",
    "build_rescore_overwrite_workflow",
    "build_unlabeled_visible_workflow",
    "build_workflow",
    "clean_shown_state",
    "clean_unshown_state",
    "dirty_replaced_shown_state",
    "dirty_replaced_unshown_state",
    "initial_states",
    "terminal_predicate",
]
