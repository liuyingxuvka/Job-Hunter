"""FlowGuard model for full-chain equivalence under fixed-oracle assumptions.

Risk Intent Brief
-----------------
Failure modes modeled:
- We claim the recommendation migration is equivalent, but an upstream input
  that affects the final table is not actually fixed: search results, prompt,
  AI output, URL/post-verify output, config, DB state, review state, clock, or
  ordering.
- A future unified final visibility helper changes the final recommendation
  table even when every upstream oracle is fixed.
- A tempting simplification re-evaluates durable or historical rows as fresh
  jobs and silently changes user-visible recommendations.

Protected harm:
- The user trusts an equivalence claim that was only true for a shallow model.

Critical state and side effects:
- Search job identity, prompt fingerprint, AI recommendation and score,
  verified output URL/final key, runtime policy key, durable pool stamp,
  review/user visibility state, clock/order metadata, visible final keys,
  public drop reasons, and duplicate collapse state.

Adversarial inputs:
- Fresh recommendations at score 19/20, AI rejects, prefilter rejects,
  post-verify failures, output URL failures, duplicate final keys, durable pool
  rows with current and stale stamps, no-config fallback rows, user-hidden rows,
  historical append rows, and deliberate oracle drifts.

Hard invariants:
- With every oracle fixed, old chain and planned new chain must have identical
  upstream fingerprints, final visible keys, public reasons, policy metadata,
  and dedupe behavior.
- If any fixed-oracle assumption is broken, the model must not allow a global
  equivalence claim to pass silently.

Blindspots:
- This model still abstracts the external world into finite oracle outputs. It
  proves conditional equivalence under frozen inputs, not that live web pages or
  hosted AI models are deterministic in reality.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from flowguard import FunctionResult, Invariant, InvariantResult, Workflow


FINAL_SCORE_FLOOR = 20
FRESH_FINAL_OUTPUT = "fresh_final_output"
POOL_READBACK = "pool_readback"
HISTORICAL_APPEND = "historical_append"


def _append(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return (*values, value) if value else values


def _count(values: tuple[str, ...], value: str) -> int:
    return sum(1 for item in values if item == value)


@dataclass(frozen=True)
class ChainCase:
    name: str
    source: str
    score: int
    recommend: bool = True
    prefilter_reject: bool = False
    post_verify_ok: bool = True
    final_output_ok: bool = True
    link_recheck_ok: bool = True
    has_output_url: bool = True
    has_title: bool = True
    bad_output_url: bool = False
    unavailable: bool = False
    final_key: str = ""
    recommendation_status: str = "pass"
    output_status: str = "pass"
    pool_active: bool = True
    eligible_stamp: bool = True
    current_policy_stamp: bool = True
    has_runtime_config: bool = True
    any_current_policy_stamp: bool = True
    trashed: bool = False
    hidden: bool = False
    not_interested: bool = False
    review_rejected: bool = False
    prompt_same: bool = True
    ai_same: bool = True
    search_same: bool = True
    url_same: bool = True
    config_same: bool = True
    db_same: bool = True
    review_same: bool = True
    clock_same: bool = True
    order_same: bool = True


@dataclass(frozen=True)
class ChainView:
    source: str
    job_key: str
    final_key: str
    prompt_fingerprint: str
    ai_recommend: bool
    ai_score: int
    prefilter_reject: bool
    post_verify_ok: bool
    final_output_ok: bool
    link_recheck_ok: bool
    has_output_url: bool
    has_title: bool
    bad_output_url: bool
    unavailable: bool
    recommendation_status: str
    output_status: str
    pool_active: bool
    eligible_stamp: bool
    current_policy_stamp: bool
    has_runtime_config: bool
    any_current_policy_stamp: bool
    trashed: bool
    hidden: bool
    not_interested: bool
    review_rejected: bool
    policy_key: str
    clock_token: str
    order_rank: int
    output_url: str
    prompt_same: bool
    ai_same: bool
    url_same: bool
    config_same: bool
    db_same: bool
    review_same: bool
    clock_same: bool


@dataclass(frozen=True)
class ChainPair:
    case_name: str
    legacy: ChainView
    proposed: ChainView


@dataclass(frozen=True)
class VisibilityDecision:
    visible: bool
    key: str
    reason: str
    policy_key: str
    output_url: str
    prompt_fingerprint: str
    clock_token: str
    order_rank: int


@dataclass(frozen=True)
class ComparisonResult:
    case_name: str
    legacy: VisibilityDecision
    proposed: VisibilityDecision
    equivalent: bool


@dataclass(frozen=True)
class State:
    legacy_visible_keys: tuple[str, ...] = ()
    proposed_visible_keys: tuple[str, ...] = ()
    upstream_mismatches: tuple[str, ...] = ()
    final_mismatches: tuple[str, ...] = ()
    reason_mismatches: tuple[str, ...] = ()
    duplicate_mismatches: tuple[str, ...] = ()
    metadata_mismatches: tuple[str, ...] = ()


def initial_state() -> State:
    return State()


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, state, trace
    return False


def final_key_for_case(case: ChainCase) -> str:
    return case.final_key or case.name


def view_from_case(case: ChainCase) -> ChainView:
    key = final_key_for_case(case)
    return ChainView(
        source=case.source,
        job_key=f"job:{case.name}",
        final_key=key,
        prompt_fingerprint=f"prompt:{case.name}",
        ai_recommend=case.recommend,
        ai_score=int(case.score),
        prefilter_reject=case.prefilter_reject,
        post_verify_ok=case.post_verify_ok,
        final_output_ok=case.final_output_ok,
        link_recheck_ok=case.link_recheck_ok,
        has_output_url=case.has_output_url,
        has_title=case.has_title,
        bad_output_url=case.bad_output_url,
        unavailable=case.unavailable,
        recommendation_status=case.recommendation_status,
        output_status=case.output_status,
        pool_active=case.pool_active,
        eligible_stamp=case.eligible_stamp,
        current_policy_stamp=case.current_policy_stamp,
        has_runtime_config=case.has_runtime_config,
        any_current_policy_stamp=case.any_current_policy_stamp,
        trashed=case.trashed,
        hidden=case.hidden,
        not_interested=case.not_interested,
        review_rejected=case.review_rejected,
        policy_key="policy:v1",
        clock_token="clock:fixed",
        order_rank=10,
        output_url=f"https://jobs.example/{key}",
        prompt_same=case.prompt_same,
        ai_same=case.ai_same,
        url_same=case.url_same,
        config_same=case.config_same,
        db_same=case.db_same,
        review_same=case.review_same,
        clock_same=case.clock_same,
    )


def _record_named_upstream_mismatches(
    state: State,
    case_name: str,
    labels: tuple[str, ...],
) -> State:
    if not labels:
        return state
    upstream_mismatches = state.upstream_mismatches
    for label in labels:
        upstream_mismatches = _append(upstream_mismatches, f"{case_name}:{label}")
    return replace(state, upstream_mismatches=upstream_mismatches)


def _oracle_label(prefix: str, mismatch_labels: tuple[str, ...]) -> str:
    if not mismatch_labels:
        return f"{prefix}_oracle_equal"
    if len(mismatch_labels) == 1:
        return f"{prefix}_oracle_{mismatch_labels[0]}_drift"
    return f"{prefix}_oracle_multi_drift"


class SearchOracleBlock:
    name = "SearchOracleBlock"
    reads = ()
    writes = ("upstream_mismatches",)
    accepted_input_type = ChainCase
    input_description = "fixed search-oracle case"
    output_description = "legacy/proposed chain views after search"
    idempotency = "With search_same=true, search identity and order are identical."

    def apply(self, input_obj: ChainCase, state: State) -> Iterable[FunctionResult]:
        legacy = view_from_case(input_obj)
        proposed = view_from_case(input_obj)
        mismatch_labels: tuple[str, ...] = ()
        if not input_obj.search_same:
            proposed = replace(proposed, job_key=f"{proposed.job_key}:search-drift", final_key=f"{proposed.final_key}:search-drift")
            mismatch_labels = _append(mismatch_labels, "search")
        if not input_obj.order_same:
            proposed = replace(proposed, order_rank=proposed.order_rank + 1)
            mismatch_labels = _append(mismatch_labels, "order")
        pair = ChainPair(input_obj.name, legacy, proposed)
        new_state = _record_named_upstream_mismatches(state, input_obj.name, mismatch_labels)
        yield FunctionResult(
            output=pair,
            new_state=new_state,
            label=_oracle_label("search", mismatch_labels),
        )


class PromptOracleBlock:
    name = "PromptOracleBlock"
    reads = ("upstream_mismatches",)
    writes = ("upstream_mismatches",)
    accepted_input_type = ChainPair
    input_description = "chain views after search"
    output_description = "chain views after prompt construction"
    idempotency = "With prompt_same=true, prompt fingerprint is identical."

    def apply(self, input_obj: ChainPair, state: State) -> Iterable[FunctionResult]:
        pair = input_obj
        mismatch_labels: tuple[str, ...] = ()
        if not pair.legacy.prompt_same:
            pair = replace(
                pair,
                proposed=replace(pair.proposed, prompt_fingerprint=f"{pair.proposed.prompt_fingerprint}:prompt-drift"),
            )
            mismatch_labels = _append(mismatch_labels, "prompt")
        new_state = _record_named_upstream_mismatches(state, pair.case_name, mismatch_labels)
        yield FunctionResult(
            output=pair,
            new_state=new_state,
            label=_oracle_label("prompt", mismatch_labels),
        )


class AIOracleBlock:
    name = "AIOracleBlock"
    reads = ("upstream_mismatches",)
    writes = ("upstream_mismatches",)
    accepted_input_type = ChainPair
    input_description = "chain views after prompt"
    output_description = "chain views after AI oracle"
    idempotency = "With ai_same=true, AI score and recommend fields are identical."

    def apply(self, input_obj: ChainPair, state: State) -> Iterable[FunctionResult]:
        pair = input_obj
        mismatch_labels: tuple[str, ...] = ()
        if not pair.legacy.ai_same:
            replacement_score = 19 if pair.proposed.ai_score >= FINAL_SCORE_FLOOR else FINAL_SCORE_FLOOR
            pair = replace(
                pair,
                proposed=replace(
                    pair.proposed,
                    ai_recommend=not pair.proposed.ai_recommend,
                    ai_score=replacement_score,
                ),
            )
            mismatch_labels = _append(mismatch_labels, "ai")
        new_state = _record_named_upstream_mismatches(state, pair.case_name, mismatch_labels)
        yield FunctionResult(
            output=pair,
            new_state=new_state,
            label=_oracle_label("ai", mismatch_labels),
        )


class URLOracleBlock:
    name = "URLOracleBlock"
    reads = ("upstream_mismatches",)
    writes = ("upstream_mismatches",)
    accepted_input_type = ChainPair
    input_description = "chain views after AI"
    output_description = "chain views after URL/post-verify oracle"
    idempotency = "With url_same=true, final URL and final key are identical."

    def apply(self, input_obj: ChainPair, state: State) -> Iterable[FunctionResult]:
        pair = input_obj
        mismatch_labels: tuple[str, ...] = ()
        if not pair.legacy.url_same:
            pair = replace(
                pair,
                proposed=replace(
                    pair.proposed,
                    final_key=f"{pair.proposed.final_key}:url-drift",
                    output_url=f"{pair.proposed.output_url}/url-drift",
                ),
            )
            mismatch_labels = _append(mismatch_labels, "url")
        new_state = _record_named_upstream_mismatches(state, pair.case_name, mismatch_labels)
        yield FunctionResult(
            output=pair,
            new_state=new_state,
            label=_oracle_label("url", mismatch_labels),
        )


class StateOracleBlock:
    name = "StateOracleBlock"
    reads = ("upstream_mismatches",)
    writes = ("upstream_mismatches",)
    accepted_input_type = ChainPair
    input_description = "chain views after URL"
    output_description = "chain views after config/DB/review/clock oracle"
    idempotency = "With state assumptions true, config, DB, review, and clock metadata are identical."

    def apply(self, input_obj: ChainPair, state: State) -> Iterable[FunctionResult]:
        pair = input_obj
        proposed = pair.proposed
        mismatch_labels: tuple[str, ...] = ()
        if not pair.legacy.config_same:
            proposed = replace(proposed, policy_key="policy:v2", ai_score=min(proposed.ai_score, 19))
            mismatch_labels = _append(mismatch_labels, "config")
        if not pair.legacy.db_same:
            proposed = replace(proposed, eligible_stamp=False, output_status="reject")
            mismatch_labels = _append(mismatch_labels, "db")
        if not pair.legacy.review_same:
            proposed = replace(proposed, hidden=True)
            mismatch_labels = _append(mismatch_labels, "review")
        if not pair.legacy.clock_same:
            proposed = replace(proposed, clock_token="clock:drift")
            mismatch_labels = _append(mismatch_labels, "clock")
        pair = replace(pair, proposed=proposed)
        new_state = _record_named_upstream_mismatches(state, pair.case_name, mismatch_labels)
        yield FunctionResult(
            output=pair,
            new_state=new_state,
            label=_oracle_label("state", mismatch_labels),
        )


def final_output_decision(view: ChainView) -> VisibilityDecision:
    key = view.final_key
    if view.prefilter_reject:
        return _decision(False, key, "prefilter_rejected", view)
    if not view.ai_recommend:
        return _decision(False, key, "not_recommended", view)
    if view.ai_score < FINAL_SCORE_FLOOR:
        return _decision(False, key, "below_score_floor", view)
    if not view.post_verify_ok:
        return _decision(False, key, "post_verify_failed", view)
    if not view.final_output_ok:
        return _decision(False, key, "final_output_check_failed", view)
    if not view.link_recheck_ok:
        return _decision(False, key, "link_recheck_failed", view)
    if not view.has_output_url:
        return _decision(False, key, "missing_output_url", view)
    if not view.has_title:
        return _decision(False, key, "missing_title", view)
    if view.bad_output_url:
        return _decision(False, key, "invalid_output_url", view)
    if view.unavailable:
        return _decision(False, key, "unavailable", view)
    return _decision(True, key, "visible_final_recommendation", view)


def pool_readback_decision(view: ChainView) -> VisibilityDecision:
    key = view.final_key
    if not view.pool_active:
        return _decision(False, key, "pool_inactive", view)
    if view.trashed:
        return _decision(False, key, "trashed", view)
    if view.hidden:
        return _decision(False, key, "hidden", view)
    if view.not_interested:
        return _decision(False, key, "not_interested", view)
    if view.review_rejected:
        return _decision(False, key, "review_rejected", view)
    if view.recommendation_status != "pass":
        return _decision(False, key, "recommendation_status_not_pass", view)
    if view.output_status != "pass":
        return _decision(False, key, "output_status_not_pass", view)
    if not view.ai_recommend:
        return _decision(False, key, "not_recommended", view)
    if not view.eligible_stamp:
        return _decision(False, key, "missing_eligible_stamp", view)
    if view.has_runtime_config and not view.current_policy_stamp:
        return _decision(False, key, "stale_policy_stamp", view)
    if not view.has_runtime_config and not view.any_current_policy_stamp:
        return _decision(False, key, "missing_policy_stamp", view)
    return _decision(True, key, "visible_materialized_pool", view)


def historical_append_decision(view: ChainView) -> VisibilityDecision:
    key = view.final_key
    if not view.ai_recommend:
        return _decision(False, key, "not_recommended", view)
    if view.ai_score < FINAL_SCORE_FLOOR:
        return _decision(False, key, "below_score_floor", view)
    if not view.has_output_url:
        return _decision(False, key, "missing_output_url", view)
    if not view.has_title:
        return _decision(False, key, "missing_title", view)
    if view.bad_output_url:
        return _decision(False, key, "invalid_output_url", view)
    if view.unavailable:
        return _decision(False, key, "unavailable", view)
    return _decision(True, key, "visible_historical_retained", view)


def _decision(visible: bool, key: str, reason: str, view: ChainView) -> VisibilityDecision:
    return VisibilityDecision(
        visible=visible,
        key=key,
        reason=reason,
        policy_key=view.policy_key,
        output_url=view.output_url,
        prompt_fingerprint=view.prompt_fingerprint,
        clock_token=view.clock_token,
        order_rank=view.order_rank,
    )


def legacy_visibility(view: ChainView) -> VisibilityDecision:
    if view.source == FRESH_FINAL_OUTPUT:
        return final_output_decision(view)
    if view.source == POOL_READBACK:
        return pool_readback_decision(view)
    if view.source == HISTORICAL_APPEND:
        return historical_append_decision(view)
    return _decision(False, view.final_key, "unknown_source", view)


def proposed_visibility(view: ChainView, *, strategy: str) -> VisibilityDecision:
    if strategy == "naive_recompute":
        return final_output_decision(view)
    return legacy_visibility(view)


def apply_dedupe(decision: VisibilityDecision, visible_keys: tuple[str, ...]) -> VisibilityDecision:
    if not decision.visible:
        return decision
    if decision.key in visible_keys:
        return replace(decision, visible=False, reason="duplicate_merged")
    return decision


def decisions_match(legacy: VisibilityDecision, proposed: VisibilityDecision) -> bool:
    return (
        legacy.visible == proposed.visible
        and legacy.reason == proposed.reason
        and legacy.key == proposed.key
        and legacy.policy_key == proposed.policy_key
        and legacy.output_url == proposed.output_url
        and legacy.prompt_fingerprint == proposed.prompt_fingerprint
        and legacy.clock_token == proposed.clock_token
        and legacy.order_rank == proposed.order_rank
    )


class ProjectionComparatorBlock:
    name = "ProjectionComparatorBlock"
    reads = ("legacy_visible_keys", "proposed_visible_keys", "final_mismatches")
    writes = (
        "legacy_visible_keys",
        "proposed_visible_keys",
        "final_mismatches",
        "reason_mismatches",
        "duplicate_mismatches",
        "metadata_mismatches",
    )
    accepted_input_type = ChainPair
    input_description = "fully materialized legacy/proposed chain views"
    output_description = "final recommendation projection comparison"
    idempotency = "Equivalent final keys collapse independently in both chains."

    def __init__(self, *, strategy: str = "source_aware") -> None:
        self.strategy = strategy

    def apply(self, input_obj: ChainPair, state: State) -> Iterable[FunctionResult]:
        legacy = apply_dedupe(legacy_visibility(input_obj.legacy), state.legacy_visible_keys)
        proposed = apply_dedupe(
            proposed_visibility(input_obj.proposed, strategy=self.strategy),
            state.proposed_visible_keys,
        )
        equivalent = decisions_match(legacy, proposed)
        legacy_keys = _append(state.legacy_visible_keys, legacy.key) if legacy.visible else state.legacy_visible_keys
        proposed_keys = (
            _append(state.proposed_visible_keys, proposed.key)
            if proposed.visible
            else state.proposed_visible_keys
        )
        final_mismatches = state.final_mismatches
        reason_mismatches = state.reason_mismatches
        duplicate_mismatches = state.duplicate_mismatches
        metadata_mismatches = state.metadata_mismatches
        if not equivalent:
            mismatch = f"{input_obj.case_name}:{legacy.reason}->{proposed.reason}"
            final_mismatches = _append(final_mismatches, mismatch)
            if legacy.visible == proposed.visible and legacy.reason != proposed.reason:
                reason_mismatches = _append(reason_mismatches, mismatch)
            if legacy.reason == "duplicate_merged" or proposed.reason == "duplicate_merged":
                duplicate_mismatches = _append(duplicate_mismatches, mismatch)
            if (
                legacy.policy_key != proposed.policy_key
                or legacy.output_url != proposed.output_url
                or legacy.prompt_fingerprint != proposed.prompt_fingerprint
                or legacy.clock_token != proposed.clock_token
                or legacy.order_rank != proposed.order_rank
            ):
                metadata_mismatches = _append(metadata_mismatches, mismatch)
        new_state = replace(
            state,
            legacy_visible_keys=legacy_keys,
            proposed_visible_keys=proposed_keys,
            final_mismatches=final_mismatches,
            reason_mismatches=reason_mismatches,
            duplicate_mismatches=duplicate_mismatches,
            metadata_mismatches=metadata_mismatches,
        )
        yield FunctionResult(
            output=ComparisonResult(input_obj.case_name, legacy, proposed, equivalent),
            new_state=new_state,
            label=label_for_projection(input_obj, legacy, proposed, equivalent),
        )


def label_for_projection(
    pair: ChainPair,
    legacy: VisibilityDecision,
    proposed: VisibilityDecision,
    equivalent: bool,
) -> str:
    if not equivalent:
        return f"chain_mismatch_{pair.legacy.source}"
    if legacy.reason == "duplicate_merged":
        return "chain_equivalent_duplicate_hidden"
    if legacy.visible and pair.legacy.source == FRESH_FINAL_OUTPUT:
        return "chain_equivalent_fresh_visible"
    if legacy.visible and pair.legacy.source == POOL_READBACK:
        return "chain_equivalent_pool_visible"
    if legacy.visible and pair.legacy.source == HISTORICAL_APPEND:
        return "chain_equivalent_historical_visible"
    if legacy.reason == "stale_policy_stamp":
        return "chain_equivalent_stale_policy_hidden"
    return f"chain_equivalent_{pair.legacy.source}_hidden"


def no_upstream_oracle_mismatches(state: State, trace) -> InvariantResult:
    del trace
    if state.upstream_mismatches:
        return InvariantResult.fail(
            "fixed-oracle assumptions were violated",
            {"upstream_mismatches": state.upstream_mismatches},
        )
    return InvariantResult.pass_()


def no_final_chain_mismatches(state: State, trace) -> InvariantResult:
    del trace
    if state.final_mismatches:
        return InvariantResult.fail(
            "old and proposed final recommendation outputs diverged",
            {"final_mismatches": state.final_mismatches},
        )
    return InvariantResult.pass_()


def visible_key_sequences_match(state: State, trace) -> InvariantResult:
    del trace
    if state.legacy_visible_keys != state.proposed_visible_keys:
        return InvariantResult.fail(
            "old and proposed visible key sequences diverged",
            {
                "legacy_visible_keys": state.legacy_visible_keys,
                "proposed_visible_keys": state.proposed_visible_keys,
            },
        )
    return InvariantResult.pass_()


def no_reason_mismatches(state: State, trace) -> InvariantResult:
    del trace
    if state.reason_mismatches:
        return InvariantResult.fail(
            "old and proposed reasons diverged",
            {"reason_mismatches": state.reason_mismatches},
        )
    return InvariantResult.pass_()


def no_metadata_mismatches(state: State, trace) -> InvariantResult:
    del trace
    if state.metadata_mismatches:
        return InvariantResult.fail(
            "old and proposed prompt/url/policy/clock/order metadata diverged",
            {"metadata_mismatches": state.metadata_mismatches},
        )
    return InvariantResult.pass_()


def no_proposed_duplicate_visible_keys(state: State, trace) -> InvariantResult:
    del trace
    duplicates = tuple(
        key for key in state.proposed_visible_keys if _count(state.proposed_visible_keys, key) > 1
    )
    if duplicates:
        return InvariantResult.fail(
            "proposed chain produced duplicate visible final keys",
            {"duplicate_keys": duplicates},
        )
    return InvariantResult.pass_()


INVARIANTS = (
    Invariant(
        name="no_upstream_oracle_mismatches",
        description="Frozen search/prompt/AI/URL/config/DB/review/clock/order assumptions must hold.",
        predicate=no_upstream_oracle_mismatches,
    ),
    Invariant(
        name="no_final_chain_mismatches",
        description="Old and proposed final recommendation outputs must match.",
        predicate=no_final_chain_mismatches,
    ),
    Invariant(
        name="visible_key_sequences_match",
        description="Old and proposed visible final keys must remain identical.",
        predicate=visible_key_sequences_match,
    ),
    Invariant(
        name="no_reason_mismatches",
        description="Old and proposed public reasons should match.",
        predicate=no_reason_mismatches,
    ),
    Invariant(
        name="no_metadata_mismatches",
        description="Old and proposed prompt/url/policy/clock/order metadata should match.",
        predicate=no_metadata_mismatches,
    ),
    Invariant(
        name="no_proposed_duplicate_visible_keys",
        description="The proposed chain must preserve final-key dedupe.",
        predicate=no_proposed_duplicate_visible_keys,
    ),
)


EQUIVALENT_INPUTS = (
    ChainCase("fresh_score_20", FRESH_FINAL_OUTPUT, score=20),
    ChainCase("fresh_score_19", FRESH_FINAL_OUTPUT, score=19),
    ChainCase("fresh_ai_reject", FRESH_FINAL_OUTPUT, score=82, recommend=False),
    ChainCase("fresh_prefilter_reject", FRESH_FINAL_OUTPUT, score=82, prefilter_reject=True),
    ChainCase("fresh_post_verify_failed", FRESH_FINAL_OUTPUT, score=82, post_verify_ok=False),
    ChainCase("fresh_final_output_failed", FRESH_FINAL_OUTPUT, score=82, final_output_ok=False),
    ChainCase("fresh_link_recheck_failed", FRESH_FINAL_OUTPUT, score=82, link_recheck_ok=False),
    ChainCase("fresh_duplicate_a", FRESH_FINAL_OUTPUT, score=82, final_key="same-final-job"),
    ChainCase("fresh_duplicate_b", FRESH_FINAL_OUTPUT, score=86, final_key="same-final-job"),
    ChainCase("pool_current_stamp_sparse_evidence", POOL_READBACK, score=82, final_output_ok=False),
    ChainCase("pool_stale_policy", POOL_READBACK, score=82, current_policy_stamp=False),
    ChainCase("pool_output_status_reject", POOL_READBACK, score=82, output_status="reject"),
    ChainCase("pool_hidden", POOL_READBACK, score=82, hidden=True),
    ChainCase(
        "pool_no_config_with_any_current_stamp",
        POOL_READBACK,
        score=82,
        has_runtime_config=False,
        any_current_policy_stamp=True,
        current_policy_stamp=False,
    ),
    ChainCase(
        "historical_retained_sparse_evidence",
        HISTORICAL_APPEND,
        score=72,
        final_output_ok=False,
        link_recheck_ok=False,
    ),
    ChainCase("historical_score_19", HISTORICAL_APPEND, score=19),
    ChainCase("historical_bad_url", HISTORICAL_APPEND, score=72, bad_output_url=True),
)


ASSUMPTION_BREAK_INPUTS = (
    ChainCase("fresh_score_20:prompt_drift", FRESH_FINAL_OUTPUT, score=20, prompt_same=False),
    ChainCase("fresh_score_20:ai_drift", FRESH_FINAL_OUTPUT, score=20, ai_same=False),
    ChainCase("fresh_score_20:url_drift", FRESH_FINAL_OUTPUT, score=20, url_same=False),
    ChainCase("fresh_score_20:config_drift", FRESH_FINAL_OUTPUT, score=20, config_same=False),
    ChainCase("pool_current:db_drift", POOL_READBACK, score=82, db_same=False),
    ChainCase("pool_current:review_drift", POOL_READBACK, score=82, review_same=False),
    ChainCase("fresh_score_20:clock_drift", FRESH_FINAL_OUTPUT, score=20, clock_same=False),
    ChainCase("fresh_score_20:search_drift", FRESH_FINAL_OUTPUT, score=20, search_same=False),
    ChainCase("fresh_score_20:order_drift", FRESH_FINAL_OUTPUT, score=20, order_same=False),
)

MAX_SEQUENCE_LENGTH = 2
ASSUMPTION_BREAK_MAX_SEQUENCE_LENGTH = 1


def build_workflow() -> Workflow:
    return Workflow(
        (
            SearchOracleBlock(),
            PromptOracleBlock(),
            AIOracleBlock(),
            URLOracleBlock(),
            StateOracleBlock(),
            ProjectionComparatorBlock(strategy="source_aware"),
        ),
        name="fixed_oracle_chain_equivalence",
    )


def build_naive_recompute_workflow() -> Workflow:
    return Workflow(
        (
            SearchOracleBlock(),
            PromptOracleBlock(),
            AIOracleBlock(),
            URLOracleBlock(),
            StateOracleBlock(),
            ProjectionComparatorBlock(strategy="naive_recompute"),
        ),
        name="broken_naive_recompute_chain",
    )


__all__ = [
    "ASSUMPTION_BREAK_INPUTS",
    "ASSUMPTION_BREAK_MAX_SEQUENCE_LENGTH",
    "EQUIVALENT_INPUTS",
    "FRESH_FINAL_OUTPUT",
    "HISTORICAL_APPEND",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "POOL_READBACK",
    "ChainCase",
    "State",
    "build_naive_recompute_workflow",
    "build_workflow",
    "initial_state",
    "terminal_predicate",
]
