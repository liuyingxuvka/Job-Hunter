"""FlowGuard model for final recommendation detail-page verification.

The model covers one product rule:

    A new job may enter the final recommendation table only after the current
    detail page is verified as a valid job page. Historical rows that were
    already visible are preserved without being rechecked on every refresh.

It does not call production code, databases, the network, or an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


@dataclass(frozen=True)
class JobSignal:
    name: str
    source: str = "new"  # new | historical
    analysis: str = "recommend"  # recommend | reject
    detail_page: str = "valid"  # valid | expired | generic | unreachable
    apply_link: str = "none"  # none | present


@dataclass(frozen=True)
class PreparedJob:
    signal: JobSignal
    should_recommend: bool
    already_visible: bool


@dataclass(frozen=True)
class VerifiedJob:
    signal: JobSignal
    should_recommend: bool
    already_visible: bool
    verification: str = "unchecked"
    detail_stamp: bool = False


@dataclass(frozen=True)
class OutputDecision:
    status: str  # visible_new | visible_history | rejected
    click_target: str = ""  # detail | apply | none


@dataclass(frozen=True)
class State:
    jobs_seen: int = 0
    new_verifications: int = 0
    historical_rechecks: int = 0
    historical_skips: int = 0
    visible_new: int = 0
    visible_history: int = 0
    rejected_new: int = 0
    invalid_new_visible: int = 0
    missing_stamp_visible: int = 0
    apply_click_visible: int = 0


class CandidateRecommendationGate:
    name = "CandidateRecommendationGate"
    reads = ()
    writes = ("jobs_seen",)
    accepted_input_type = JobSignal
    input_description = "abstract scored job candidate or already visible historical row"
    output_description = "prepared recommendation candidate"
    idempotency = "Each abstract input represents one output refresh consideration."

    def apply(self, input_obj: JobSignal, state: State) -> Iterable[FunctionResult]:
        is_historical = input_obj.source == "historical"
        should_recommend = is_historical or input_obj.analysis == "recommend"
        label = "historical_row_loaded" if is_historical else "new_candidate_loaded"
        if not should_recommend:
            label = "new_candidate_rejected_by_analysis"
        yield FunctionResult(
            output=PreparedJob(
                signal=input_obj,
                should_recommend=should_recommend,
                already_visible=is_historical,
            ),
            new_state=replace(state, jobs_seen=state.jobs_seen + 1),
            label=label,
        )


class DetailPagePostVerify:
    name = "DetailPagePostVerify"
    reads = ("historical_skips", "new_verifications")
    writes = ("historical_skips", "new_verifications")
    accepted_input_type = PreparedJob
    input_description = "prepared recommendation candidate"
    output_description = "detail-page verification result"
    idempotency = "New recommended jobs are checked once before final output; already visible historical rows are not rechecked."

    def apply(self, input_obj: PreparedJob, state: State) -> Iterable[FunctionResult]:
        if input_obj.already_visible:
            yield FunctionResult(
                output=VerifiedJob(
                    signal=input_obj.signal,
                    should_recommend=True,
                    already_visible=True,
                    verification="historical_skip",
                    detail_stamp=False,
                ),
                new_state=replace(state, historical_skips=state.historical_skips + 1),
                label="historical_recheck_skipped",
            )
            return
        if not input_obj.should_recommend:
            yield FunctionResult(
                output=VerifiedJob(
                    signal=input_obj.signal,
                    should_recommend=False,
                    already_visible=False,
                    verification="analysis_reject",
                    detail_stamp=False,
                ),
                new_state=state,
                label="analysis_reject_not_verified",
            )
            return
        if input_obj.signal.detail_page == "valid":
            yield FunctionResult(
                output=VerifiedJob(
                    signal=input_obj.signal,
                    should_recommend=True,
                    already_visible=False,
                    verification="valid_detail_page",
                    detail_stamp=True,
                ),
                new_state=replace(state, new_verifications=state.new_verifications + 1),
                label="detail_verified",
            )
            return
        yield FunctionResult(
            output=VerifiedJob(
                signal=input_obj.signal,
                should_recommend=True,
                already_visible=False,
                verification=f"invalid_detail_page:{input_obj.signal.detail_page}",
                detail_stamp=False,
            ),
            new_state=replace(state, new_verifications=state.new_verifications + 1),
            label="detail_verification_failed",
        )


class RecheckHistoricalDetailPagePostVerify(DetailPagePostVerify):
    name = "RecheckHistoricalDetailPagePostVerify"

    def apply(self, input_obj: PreparedJob, state: State) -> Iterable[FunctionResult]:
        if input_obj.already_visible:
            yield FunctionResult(
                output=VerifiedJob(
                    signal=input_obj.signal,
                    should_recommend=True,
                    already_visible=True,
                    verification="historical_rechecked",
                    detail_stamp=input_obj.signal.detail_page == "valid",
                ),
                new_state=replace(state, historical_rechecks=state.historical_rechecks + 1),
                label="broken_historical_rechecked",
            )
            return
        yield from super().apply(input_obj, state)


class FinalRecommendationOutput:
    name = "FinalRecommendationOutput"
    reads = ("visible_new", "visible_history", "rejected_new")
    writes = (
        "visible_new",
        "visible_history",
        "rejected_new",
        "invalid_new_visible",
        "missing_stamp_visible",
        "apply_click_visible",
    )
    accepted_input_type = VerifiedJob
    input_description = "detail-page verification result"
    output_description = "final recommendation visibility decision"
    idempotency = "The same final-output pass may be rerun; visibility depends on the latest abstract evidence."

    def apply(self, input_obj: VerifiedJob, state: State) -> Iterable[FunctionResult]:
        if input_obj.already_visible:
            yield FunctionResult(
                output=OutputDecision("visible_history", click_target="detail"),
                new_state=replace(state, visible_history=state.visible_history + 1),
                label="historical_kept_without_recheck",
            )
            return
        if input_obj.should_recommend and input_obj.detail_stamp:
            yield FunctionResult(
                output=OutputDecision("visible_new", click_target="detail"),
                new_state=replace(state, visible_new=state.visible_new + 1),
                label="new_visible_after_detail_verify",
            )
            return
        yield FunctionResult(
            output=OutputDecision("rejected", click_target="none"),
            new_state=replace(state, rejected_new=state.rejected_new + 1),
            label="new_rejected_after_failed_detail_verify",
        )


class NoVerifyFinalRecommendationOutput(FinalRecommendationOutput):
    name = "NoVerifyFinalRecommendationOutput"

    def apply(self, input_obj: VerifiedJob, state: State) -> Iterable[FunctionResult]:
        if input_obj.already_visible:
            yield from super().apply(input_obj, state)
            return
        if input_obj.should_recommend:
            invalid_visible = 0 if input_obj.signal.detail_page == "valid" else 1
            missing_stamp = 0 if input_obj.detail_stamp else 1
            yield FunctionResult(
                output=OutputDecision("visible_new", click_target="detail"),
                new_state=replace(
                    state,
                    visible_new=state.visible_new + 1,
                    invalid_new_visible=state.invalid_new_visible + invalid_visible,
                    missing_stamp_visible=state.missing_stamp_visible + missing_stamp,
                ),
                label="broken_new_visible_without_required_detail_verify",
            )
            return
        yield from super().apply(input_obj, state)


class ApplyLinkFinalRecommendationOutput(FinalRecommendationOutput):
    name = "ApplyLinkFinalRecommendationOutput"

    def apply(self, input_obj: VerifiedJob, state: State) -> Iterable[FunctionResult]:
        if input_obj.already_visible:
            yield from super().apply(input_obj, state)
            return
        if input_obj.should_recommend and input_obj.signal.apply_link == "present":
            invalid_visible = 0 if input_obj.signal.detail_page == "valid" else 1
            missing_stamp = 0 if input_obj.detail_stamp else 1
            yield FunctionResult(
                output=OutputDecision("visible_new", click_target="apply"),
                new_state=replace(
                    state,
                    visible_new=state.visible_new + 1,
                    invalid_new_visible=state.invalid_new_visible + invalid_visible,
                    missing_stamp_visible=state.missing_stamp_visible + missing_stamp,
                    apply_click_visible=state.apply_click_visible + 1,
                ),
                label="broken_new_visible_via_apply_link",
            )
            return
        yield from super().apply(input_obj, state)


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, state, trace
    return False


def new_visible_requires_current_detail_verification(state: State, trace) -> InvariantResult:
    del trace
    if state.invalid_new_visible:
        return InvariantResult.fail(
            "new final recommendation became visible without a valid detail page",
            {"invalid_new_visible": state.invalid_new_visible},
        )
    if state.missing_stamp_visible:
        return InvariantResult.fail(
            "new final recommendation became visible without a detail verification stamp",
            {"missing_stamp_visible": state.missing_stamp_visible},
        )
    return InvariantResult.pass_()


def visible_new_uses_detail_page_not_apply(state: State, trace) -> InvariantResult:
    del trace
    if state.apply_click_visible:
        return InvariantResult.fail(
            "new final recommendation used apply link as the primary click target",
            {"apply_click_visible": state.apply_click_visible},
        )
    return InvariantResult.pass_()


def historical_rows_not_rechecked(state: State, trace) -> InvariantResult:
    del trace
    if state.historical_rechecks:
        return InvariantResult.fail(
            "already visible historical recommendation was rechecked during output refresh",
            {"historical_rechecks": state.historical_rechecks},
        )
    return InvariantResult.pass_()


INVARIANTS = (
    Invariant(
        name="new_visible_requires_current_detail_verification",
        description="A new final recommendation requires a current valid detail-page verification stamp.",
        predicate=new_visible_requires_current_detail_verification,
    ),
    Invariant(
        name="visible_new_uses_detail_page_not_apply",
        description="The primary click target for a new final recommendation remains the detail page, not the apply form.",
        predicate=visible_new_uses_detail_page_not_apply,
    ),
    Invariant(
        name="historical_rows_not_rechecked",
        description="Already visible historical recommendations are preserved without routine rechecking.",
        predicate=historical_rows_not_rechecked,
    ),
)


EXTERNAL_INPUTS = (
    JobSignal("new_valid_recommended", detail_page="valid", apply_link="present"),
    JobSignal("new_expired_with_apply", detail_page="expired", apply_link="present"),
    JobSignal("new_generic_with_apply", detail_page="generic", apply_link="present"),
    JobSignal("new_unreachable_recommended", detail_page="unreachable"),
    JobSignal("new_analysis_reject", analysis="reject", detail_page="valid", apply_link="present"),
    JobSignal("historical_visible_expired", source="historical", detail_page="expired", apply_link="present"),
)

MAX_SEQUENCE_LENGTH = 2


def initial_state() -> State:
    return State()


def build_workflow() -> Workflow:
    return Workflow(
        (
            CandidateRecommendationGate(),
            DetailPagePostVerify(),
            FinalRecommendationOutput(),
        ),
        name="final_output_detail_verification",
    )


def build_no_verify_workflow() -> Workflow:
    return Workflow(
        (
            CandidateRecommendationGate(),
            DetailPagePostVerify(),
            NoVerifyFinalRecommendationOutput(),
        ),
        name="broken_no_verify_final_output",
    )


def build_apply_link_workflow() -> Workflow:
    return Workflow(
        (
            CandidateRecommendationGate(),
            DetailPagePostVerify(),
            ApplyLinkFinalRecommendationOutput(),
        ),
        name="broken_apply_link_final_output",
    )


def build_recheck_history_workflow() -> Workflow:
    return Workflow(
        (
            CandidateRecommendationGate(),
            RecheckHistoricalDetailPagePostVerify(),
            FinalRecommendationOutput(),
        ),
        name="broken_recheck_historical_output",
    )


__all__ = [
    "EXTERNAL_INPUTS",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "JobSignal",
    "State",
    "build_apply_link_workflow",
    "build_no_verify_workflow",
    "build_recheck_history_workflow",
    "build_workflow",
    "initial_state",
    "terminal_predicate",
]
