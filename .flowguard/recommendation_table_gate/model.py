"""FlowGuard model for final recommendation quality.

Risk Intent Brief
-----------------
Failure modes modeled:
- A job that does not satisfy final recommendation standards reaches the user
  table.
- A job that does satisfy the standards is lost before the user table.
- Two variants of the same real job both reach the user table.
- A stale durable output stamp is trusted after the recommendation policy
  changed.

Protected harm:
- The user sees noisy or invalid recommendations, misses a valid opportunity,
  or sees repeated copies of the same opportunity.

Critical state and side effects:
- Expected final recommendation keys, selected final output keys, table rows,
  duplicate groups, durable pool status, and output-policy stamp freshness.

Adversarial inputs:
- Boundary score 19/20, AI reject, prefilter reject, post-verify failure,
  invalid link recheck, repeated inputs, URL variants for one real job, and
  stale pool rows.

Hard invariants:
- The final table only shows jobs that satisfy the output policy.
- Every distinct policy-eligible job key appears once.
- Duplicate final keys collapse to one visible row.
- Stale policy stamps do not reach the final user table.

Blindspots:
- This model abstracts LLM scoring and web fetching into finite signals. It
  validates the recommendation/output state machine, not live web correctness.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


FINAL_SCORE_FLOOR = 20
STRICT_OLD_SCORE_FLOOR = 50


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    if not value or value in values:
        return values
    return (*values, value)


def _append(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return (*values, value) if value else values


def _count(values: tuple[str, ...], value: str) -> int:
    return sum(1 for item in values if item == value)


@dataclass(frozen=True)
class JobSignal:
    name: str
    score: int
    recommend: bool = True
    prefilter_reject: bool = False
    post_verify_ok: bool = True
    final_output_ok: bool = True
    link_recheck_ok: bool = True
    final_key: str = ""


@dataclass(frozen=True)
class EligibilityDecision:
    name: str
    expected: bool
    eligible: bool
    reason: str
    final_key: str
    score: int
    recommend: bool
    prefilter_reject: bool


@dataclass(frozen=True)
class FinalDecision:
    name: str
    expected: bool
    visible: bool
    reason: str
    final_key: str
    score: int
    recommend: bool
    prefilter_reject: bool


@dataclass(frozen=True)
class TableRow:
    name: str
    visible: bool
    reason: str
    final_key: str
    score: int
    stale_policy_stamp: bool = False


@dataclass(frozen=True)
class PoolRow:
    name: str
    score: int
    recommend: bool = True
    recommendation_status: str = "pass"
    output_status: str = "pass"
    eligible_stamp: bool = True
    current_policy_stamp: bool = True
    hidden: bool = False
    review_rejected: bool = False
    final_key: str = ""


@dataclass(frozen=True)
class State:
    expected_final_keys: tuple[str, ...] = ()
    selected_final_keys: tuple[str, ...] = ()
    table_final_keys: tuple[str, ...] = ()
    below_floor_visible: int = 0
    prefilter_reject_visible: int = 0
    non_final_visible: int = 0
    stale_policy_visible: int = 0


def final_key_for_signal(input_obj: JobSignal) -> str:
    return input_obj.final_key or input_obj.name


def final_key_for_pool_row(input_obj: PoolRow) -> str:
    return input_obj.final_key or input_obj.name


def intended_recommendation_standard(input_obj: JobSignal) -> bool:
    return (
        not input_obj.prefilter_reject
        and input_obj.recommend
        and input_obj.score >= FINAL_SCORE_FLOOR
        and input_obj.post_verify_ok
        and input_obj.final_output_ok
        and input_obj.link_recheck_ok
    )


def pool_row_satisfies_current_policy(input_obj: PoolRow) -> bool:
    return (
        input_obj.recommendation_status == "pass"
        and input_obj.output_status == "pass"
        and input_obj.recommend
        and input_obj.score >= FINAL_SCORE_FLOOR
        and input_obj.eligible_stamp
        and input_obj.current_policy_stamp
        and not input_obj.hidden
        and not input_obj.review_rejected
    )


class EligibilityGate:
    name = "EligibilityGate"
    reads = ()
    writes = ()
    accepted_input_type = JobSignal
    input_description = "analyzed candidate job"
    output_description = "output eligibility decision"
    idempotency = "Equivalent analyzed job inputs produce the same eligibility decision."

    def __init__(self, *, score_floor: int = FINAL_SCORE_FLOOR) -> None:
        self.score_floor = int(score_floor)

    def apply(self, input_obj: JobSignal, state: State) -> Iterable[FunctionResult]:
        expected = intended_recommendation_standard(input_obj)
        final_key = final_key_for_signal(input_obj)
        eligible = False
        reason = "eligible"
        label = "eligible_for_output"
        if input_obj.prefilter_reject:
            reason = "prefilter_rejected"
            label = "prefilter_reject_blocked"
        elif not input_obj.recommend:
            reason = "not_recommended"
            label = "ai_reject_blocked"
        elif input_obj.score < self.score_floor:
            reason = "below_score_floor"
            label = "below_floor_blocked"
        elif not input_obj.post_verify_ok:
            reason = "post_verify_failed"
            label = "post_verify_blocked"
        elif not input_obj.final_output_ok:
            reason = "final_output_check_failed"
            label = "final_output_check_blocked"
        elif not input_obj.link_recheck_ok:
            reason = "link_recheck_failed"
            label = "link_recheck_failed"
        else:
            eligible = True
        yield FunctionResult(
            output=EligibilityDecision(
                input_obj.name,
                expected,
                eligible,
                reason,
                final_key,
                input_obj.score,
                input_obj.recommend,
                input_obj.prefilter_reject,
            ),
            new_state=state,
            label=label,
        )


class FinalOutputRebuilder:
    name = "FinalOutputRebuilder"
    reads = ("selected_final_keys",)
    writes = ("selected_final_keys",)
    accepted_input_type = EligibilityDecision
    input_description = "output eligibility decision"
    output_description = "deduped final output decision"
    idempotency = "Repeated equivalent final keys collapse to one selected output row."

    def apply(self, input_obj: EligibilityDecision, state: State) -> Iterable[FunctionResult]:
        if not input_obj.eligible:
            yield FunctionResult(
                output=FinalDecision(
                    input_obj.name,
                    input_obj.expected,
                    False,
                    input_obj.reason,
                    input_obj.final_key,
                    input_obj.score,
                    input_obj.recommend,
                    input_obj.prefilter_reject,
                ),
                new_state=state,
                label=f"{input_obj.reason}_hidden",
            )
            return
        if input_obj.final_key in state.selected_final_keys:
            yield FunctionResult(
                output=FinalDecision(
                    input_obj.name,
                    input_obj.expected,
                    False,
                    "duplicate_merged",
                    input_obj.final_key,
                    input_obj.score,
                    input_obj.recommend,
                    input_obj.prefilter_reject,
                ),
                new_state=state,
                label="duplicate_merged",
            )
            return
        yield FunctionResult(
            output=FinalDecision(
                input_obj.name,
                input_obj.expected,
                True,
                "visible_final_recommendation",
                input_obj.final_key,
                input_obj.score,
                input_obj.recommend,
                input_obj.prefilter_reject,
            ),
            new_state=replace(
                state,
                selected_final_keys=_append_unique(
                    state.selected_final_keys,
                    input_obj.final_key,
                ),
            ),
            label="final_recommendation_visible",
        )


class BrokenNoDedupeFinalOutputRebuilder(FinalOutputRebuilder):
    name = "BrokenNoDedupeFinalOutputRebuilder"

    def apply(self, input_obj: EligibilityDecision, state: State) -> Iterable[FunctionResult]:
        if not input_obj.eligible:
            yield from super().apply(input_obj, state)
            return
        yield FunctionResult(
            output=FinalDecision(
                input_obj.name,
                input_obj.expected,
                True,
                "visible_final_recommendation",
                input_obj.final_key,
                input_obj.score,
                input_obj.recommend,
                input_obj.prefilter_reject,
            ),
            new_state=replace(
                state,
                selected_final_keys=_append(state.selected_final_keys, input_obj.final_key),
            ),
            label="broken_duplicate_selected",
        )


class FinalTableProjection:
    name = "FinalTableProjection"
    reads = (
        "expected_final_keys",
        "table_final_keys",
        "below_floor_visible",
        "prefilter_reject_visible",
        "non_final_visible",
    )
    writes = (
        "expected_final_keys",
        "table_final_keys",
        "below_floor_visible",
        "prefilter_reject_visible",
        "non_final_visible",
    )
    accepted_input_type = FinalDecision
    input_description = "deduped final output decision"
    output_description = "user-facing final table row"
    idempotency = "Only visible final decisions are projected into the final table."

    def apply(self, input_obj: FinalDecision, state: State) -> Iterable[FunctionResult]:
        expected_keys = (
            _append_unique(state.expected_final_keys, input_obj.final_key)
            if input_obj.expected
            else state.expected_final_keys
        )
        state_with_expected = replace(state, expected_final_keys=expected_keys)
        if not input_obj.visible:
            yield FunctionResult(
                output=TableRow(
                    input_obj.name,
                    False,
                    input_obj.reason,
                    input_obj.final_key,
                    input_obj.score,
                ),
                new_state=state_with_expected,
                label="non_final_row_hidden",
            )
            return
        yield FunctionResult(
            output=TableRow(
                input_obj.name,
                True,
                input_obj.reason,
                input_obj.final_key,
                input_obj.score,
            ),
            new_state=replace(
                state_with_expected,
                table_final_keys=_append(state.table_final_keys, input_obj.final_key),
                below_floor_visible=state.below_floor_visible
                + (1 if input_obj.score < FINAL_SCORE_FLOOR else 0),
                prefilter_reject_visible=state.prefilter_reject_visible
                + (1 if input_obj.prefilter_reject else 0),
                non_final_visible=state.non_final_visible
                + (1 if not input_obj.expected else 0),
            ),
            label="final_table_row_rendered",
        )


class BrokenLivePoolProjection(FinalTableProjection):
    name = "BrokenLivePoolProjection"

    def apply(self, input_obj: FinalDecision, state: State) -> Iterable[FunctionResult]:
        if input_obj.visible:
            yield from super().apply(input_obj, state)
            return
        expected_keys = (
            _append_unique(state.expected_final_keys, input_obj.final_key)
            if input_obj.expected
            else state.expected_final_keys
        )
        yield FunctionResult(
            output=TableRow(
                input_obj.name,
                True,
                input_obj.reason,
                input_obj.final_key,
                input_obj.score,
            ),
            new_state=replace(
                state,
                expected_final_keys=expected_keys,
                table_final_keys=_append(state.table_final_keys, input_obj.final_key),
                below_floor_visible=state.below_floor_visible
                + (1 if input_obj.score < FINAL_SCORE_FLOOR else 0),
                prefilter_reject_visible=state.prefilter_reject_visible
                + (1 if input_obj.prefilter_reject else 0),
                non_final_visible=state.non_final_visible + 1,
            ),
            label="broken_live_pool_row_rendered",
        )


class PoolRecommendedLoader:
    name = "PoolRecommendedLoader"
    reads = ("table_final_keys", "stale_policy_visible", "below_floor_visible", "non_final_visible")
    writes = ("table_final_keys", "stale_policy_visible", "below_floor_visible", "non_final_visible")
    accepted_input_type = PoolRow
    input_description = "durable candidate job pool row"
    output_description = "user-facing final table row loaded from the durable pool"
    idempotency = "A durable row is visible only when its output stamp matches the current policy."

    def row_is_visible(self, input_obj: PoolRow) -> bool:
        return pool_row_satisfies_current_policy(input_obj)

    def apply(self, input_obj: PoolRow, state: State) -> Iterable[FunctionResult]:
        final_key = final_key_for_pool_row(input_obj)
        expected = pool_row_satisfies_current_policy(input_obj)
        state_with_expected = replace(
            state,
            expected_final_keys=_append_unique(state.expected_final_keys, final_key)
            if expected
            else state.expected_final_keys,
        )
        if not self.row_is_visible(input_obj):
            yield FunctionResult(
                output=TableRow(
                    input_obj.name,
                    False,
                    "pool_row_hidden",
                    final_key,
                    input_obj.score,
                    stale_policy_stamp=not input_obj.current_policy_stamp,
                ),
                new_state=state_with_expected,
                label="stale_pool_row_hidden"
                if not input_obj.current_policy_stamp
                else "pool_row_hidden",
            )
            return
        yield FunctionResult(
            output=TableRow(
                input_obj.name,
                True,
                "pool_row_visible",
                final_key,
                input_obj.score,
                stale_policy_stamp=not input_obj.current_policy_stamp,
            ),
            new_state=replace(
                state_with_expected,
                table_final_keys=_append(state_with_expected.table_final_keys, final_key),
                stale_policy_visible=state_with_expected.stale_policy_visible
                + (1 if not input_obj.current_policy_stamp else 0),
                below_floor_visible=state_with_expected.below_floor_visible
                + (1 if input_obj.score < FINAL_SCORE_FLOOR else 0),
                non_final_visible=state_with_expected.non_final_visible
                + (1 if not pool_row_satisfies_current_policy(input_obj) else 0),
            ),
            label="current_pool_row_rendered",
        )


class BrokenStatusOnlyPoolRecommendedLoader(PoolRecommendedLoader):
    name = "BrokenStatusOnlyPoolRecommendedLoader"

    def row_is_visible(self, input_obj: PoolRow) -> bool:
        return (
            input_obj.recommendation_status == "pass"
            and input_obj.output_status == "pass"
            and not input_obj.hidden
            and not input_obj.review_rejected
        )


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, state, trace
    return False


def final_table_only_shows_expected_jobs(state: State, trace) -> InvariantResult:
    del trace
    unexpected = [
        key for key in state.table_final_keys if key not in state.expected_final_keys
    ]
    if unexpected:
        return InvariantResult.fail(
            "final table showed a job outside the intended recommendation standard",
            {"unexpected_keys": tuple(unexpected)},
        )
    return InvariantResult.pass_()


def no_expected_recommendation_missing(state: State, trace) -> InvariantResult:
    del trace
    missing = [
        key for key in state.expected_final_keys if key not in state.table_final_keys
    ]
    if missing:
        return InvariantResult.fail(
            "a policy-eligible recommendation was not shown in the final table",
            {"missing_keys": tuple(missing)},
        )
    return InvariantResult.pass_()


def no_duplicate_final_rows(state: State, trace) -> InvariantResult:
    del trace
    duplicates = tuple(
        key for key in state.table_final_keys if _count(state.table_final_keys, key) > 1
    )
    if duplicates:
        return InvariantResult.fail(
            "final table showed duplicate rows for the same final recommendation key",
            {"duplicate_keys": duplicates},
        )
    return InvariantResult.pass_()


def final_table_never_shows_below_floor(state: State, trace) -> InvariantResult:
    del trace
    if state.below_floor_visible:
        return InvariantResult.fail(
            "final table showed a job below the score floor",
            {"below_floor_visible": state.below_floor_visible},
        )
    return InvariantResult.pass_()


def final_table_never_shows_prefilter_reject(state: State, trace) -> InvariantResult:
    del trace
    if state.prefilter_reject_visible:
        return InvariantResult.fail(
            "final table showed a prefilter-rejected job",
            {"prefilter_reject_visible": state.prefilter_reject_visible},
        )
    return InvariantResult.pass_()


def final_table_never_shows_non_final_rows(state: State, trace) -> InvariantResult:
    del trace
    if state.non_final_visible:
        return InvariantResult.fail(
            "final table showed a non-final row",
            {"non_final_visible": state.non_final_visible},
        )
    return InvariantResult.pass_()


def final_table_never_shows_stale_policy(state: State, trace) -> InvariantResult:
    del trace
    if state.stale_policy_visible:
        return InvariantResult.fail(
            "final table showed a stale output-policy stamp",
            {"stale_policy_visible": state.stale_policy_visible},
        )
    return InvariantResult.pass_()


INVARIANTS = (
    Invariant(
        name="final_table_only_shows_expected_jobs",
        description="Final rows must satisfy the intended recommendation standard.",
        predicate=final_table_only_shows_expected_jobs,
    ),
    Invariant(
        name="no_expected_recommendation_missing",
        description="Every distinct eligible recommendation must appear once.",
        predicate=no_expected_recommendation_missing,
    ),
    Invariant(
        name="no_duplicate_final_rows",
        description="Equivalent final recommendation keys must not appear twice.",
        predicate=no_duplicate_final_rows,
    ),
    Invariant(
        name="final_table_never_shows_below_floor",
        description="Final user table rows must have score >= 20.",
        predicate=final_table_never_shows_below_floor,
    ),
    Invariant(
        name="final_table_never_shows_prefilter_reject",
        description="Prefilter-rejected rows must not appear in the final user table.",
        predicate=final_table_never_shows_prefilter_reject,
    ),
    Invariant(
        name="final_table_never_shows_non_final_rows",
        description="The final user table must not project live-pool reject rows.",
        predicate=final_table_never_shows_non_final_rows,
    ),
    Invariant(
        name="final_table_never_shows_stale_policy",
        description="Rows stamped for an old output policy must not be shown.",
        predicate=final_table_never_shows_stale_policy,
    ),
)


EXTERNAL_INPUTS = (
    JobSignal("score_19_recommend", score=19),
    JobSignal("score_20_recommend", score=20),
    JobSignal("score_42_ai_reject", score=42, recommend=False),
    JobSignal("prefilter_zero", score=0, recommend=False, prefilter_reject=True),
    JobSignal("post_verify_failed", score=88, post_verify_ok=False),
    JobSignal("missing_output_link", score=88, final_output_ok=False),
    JobSignal("link_recheck_failed", score=88, link_recheck_ok=False),
    JobSignal("duplicate_a", score=82, final_key="same-job"),
    JobSignal("duplicate_b", score=86, final_key="same-job"),
)


POOL_INPUTS = (
    PoolRow("current_pool_pass", score=82),
    PoolRow("stale_policy_pass", score=82, current_policy_stamp=False),
    PoolRow("old_low_score_pass", score=19),
    PoolRow("status_pass_without_stamp", score=82, eligible_stamp=False),
    PoolRow("pool_ai_reject", score=82, recommend=False),
    PoolRow("pool_hidden", score=82, hidden=True),
)


MAX_SEQUENCE_LENGTH = 2
POOL_MAX_SEQUENCE_LENGTH = 1


def initial_state() -> State:
    return State()


def build_workflow() -> Workflow:
    return Workflow(
        (EligibilityGate(), FinalOutputRebuilder(), FinalTableProjection()),
        name="recommendation_quality_gate",
    )


def build_broken_strict_threshold_workflow() -> Workflow:
    return Workflow(
        (
            EligibilityGate(score_floor=STRICT_OLD_SCORE_FLOOR),
            FinalOutputRebuilder(),
            FinalTableProjection(),
        ),
        name="broken_strict_threshold_gate",
    )


def build_broken_no_dedupe_workflow() -> Workflow:
    return Workflow(
        (
            EligibilityGate(),
            BrokenNoDedupeFinalOutputRebuilder(),
            FinalTableProjection(),
        ),
        name="broken_no_dedupe_gate",
    )


def build_broken_live_pool_workflow() -> Workflow:
    return Workflow(
        (EligibilityGate(), FinalOutputRebuilder(), BrokenLivePoolProjection()),
        name="broken_live_pool_projection",
    )


def build_pool_loader_workflow() -> Workflow:
    return Workflow((PoolRecommendedLoader(),), name="pool_loader_policy_gate")


def build_broken_status_only_pool_workflow() -> Workflow:
    return Workflow(
        (BrokenStatusOnlyPoolRecommendedLoader(),),
        name="broken_status_only_pool_loader",
    )


__all__ = [
    "EXTERNAL_INPUTS",
    "FINAL_SCORE_FLOOR",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "POOL_INPUTS",
    "POOL_MAX_SEQUENCE_LENGTH",
    "JobSignal",
    "PoolRow",
    "State",
    "build_broken_live_pool_workflow",
    "build_broken_no_dedupe_workflow",
    "build_broken_status_only_pool_workflow",
    "build_broken_strict_threshold_workflow",
    "build_pool_loader_workflow",
    "build_workflow",
    "initial_state",
    "terminal_predicate",
]
