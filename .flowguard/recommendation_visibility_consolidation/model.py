"""FlowGuard equivalence model for recommendation visibility consolidation.

Risk Intent Brief
-----------------
Failure modes modeled:
- A future single visibility helper changes what the user sees compared with
  today's scattered final-output, pool, runner, and historical-retention gates.
- A row that is visible today becomes hidden after migration, or a hidden row
  becomes visible.
- Duplicate final keys stop collapsing in the same way.
- Durable pool rows or historical rows are accidentally re-evaluated as if they
  were fresh search results.

Protected harm:
- The simplification pass silently changes recommendation behavior while trying
  to reduce patch-on-patch complexity.

Critical state and side effects:
- Legacy visible keys, unified-helper visible keys, hidden reasons, duplicate
  collapse state, durable output-policy stamps, and historical retention.

Adversarial inputs:
- Fresh jobs at score 19/20, AI rejects, prefilter rejects, post-verify and
  link failures, repeated final keys, current and stale pool stamps, no-config
  fallback stamps, user-hidden rows, and historical rows with sparse evidence.

Hard invariants:
- Corrected unified decisions must match legacy decisions for visibility.
- Corrected unified decisions must match legacy public reasons.
- Corrected unified visible keys must equal legacy visible keys after dedupe.
- Corrected unified output must not duplicate final keys.

Blindspots:
- This model abstracts web fetching, LLM scoring, and SQLite mechanics into
  finite signals. It checks migration equivalence for the recommendation
  visibility policy, not live web correctness.
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
class DecisionCase:
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


@dataclass(frozen=True)
class VisibilityDecision:
    visible: bool
    key: str
    reason: str


@dataclass(frozen=True)
class ComparisonResult:
    case_name: str
    source: str
    legacy: VisibilityDecision
    unified: VisibilityDecision
    equivalent: bool


@dataclass(frozen=True)
class State:
    legacy_visible_keys: tuple[str, ...] = ()
    unified_visible_keys: tuple[str, ...] = ()
    mismatches: tuple[str, ...] = ()
    reason_mismatches: tuple[str, ...] = ()
    duplicate_mismatches: tuple[str, ...] = ()


def initial_state() -> State:
    return State()


def terminal_predicate(current_output, state: State, trace) -> bool:
    del current_output, state, trace
    return False


def final_key_for_case(case: DecisionCase) -> str:
    return case.final_key or case.name


def fresh_final_output_decision(case: DecisionCase) -> VisibilityDecision:
    key = final_key_for_case(case)
    if case.prefilter_reject:
        return VisibilityDecision(False, key, "prefilter_rejected")
    if not case.recommend:
        return VisibilityDecision(False, key, "not_recommended")
    if case.score < FINAL_SCORE_FLOOR:
        return VisibilityDecision(False, key, "below_score_floor")
    if not case.post_verify_ok:
        return VisibilityDecision(False, key, "post_verify_failed")
    if not case.final_output_ok:
        return VisibilityDecision(False, key, "final_output_check_failed")
    if not case.link_recheck_ok:
        return VisibilityDecision(False, key, "link_recheck_failed")
    if not case.has_output_url:
        return VisibilityDecision(False, key, "missing_output_url")
    if not case.has_title:
        return VisibilityDecision(False, key, "missing_title")
    if case.bad_output_url:
        return VisibilityDecision(False, key, "invalid_output_url")
    if case.unavailable:
        return VisibilityDecision(False, key, "unavailable")
    return VisibilityDecision(True, key, "visible_final_recommendation")


def pool_readback_decision(case: DecisionCase) -> VisibilityDecision:
    key = final_key_for_case(case)
    if not case.pool_active:
        return VisibilityDecision(False, key, "pool_inactive")
    if case.trashed:
        return VisibilityDecision(False, key, "trashed")
    if case.hidden:
        return VisibilityDecision(False, key, "hidden")
    if case.not_interested:
        return VisibilityDecision(False, key, "not_interested")
    if case.review_rejected:
        return VisibilityDecision(False, key, "review_rejected")
    if case.recommendation_status != "pass":
        return VisibilityDecision(False, key, "recommendation_status_not_pass")
    if case.output_status != "pass":
        return VisibilityDecision(False, key, "output_status_not_pass")
    if not case.recommend:
        return VisibilityDecision(False, key, "not_recommended")
    if not case.eligible_stamp:
        return VisibilityDecision(False, key, "missing_eligible_stamp")
    if case.has_runtime_config and not case.current_policy_stamp:
        return VisibilityDecision(False, key, "stale_policy_stamp")
    if not case.has_runtime_config and not case.any_current_policy_stamp:
        return VisibilityDecision(False, key, "missing_policy_stamp")
    return VisibilityDecision(True, key, "visible_materialized_pool")


def historical_append_decision(case: DecisionCase) -> VisibilityDecision:
    key = final_key_for_case(case)
    if not case.recommend:
        return VisibilityDecision(False, key, "not_recommended")
    if case.score < FINAL_SCORE_FLOOR:
        return VisibilityDecision(False, key, "below_score_floor")
    if not case.has_output_url:
        return VisibilityDecision(False, key, "missing_output_url")
    if not case.has_title:
        return VisibilityDecision(False, key, "missing_title")
    if case.bad_output_url:
        return VisibilityDecision(False, key, "invalid_output_url")
    if case.unavailable:
        return VisibilityDecision(False, key, "unavailable")
    return VisibilityDecision(True, key, "visible_historical_retained")


def legacy_visibility_decision(case: DecisionCase) -> VisibilityDecision:
    if case.source == FRESH_FINAL_OUTPUT:
        return fresh_final_output_decision(case)
    if case.source == POOL_READBACK:
        return pool_readback_decision(case)
    if case.source == HISTORICAL_APPEND:
        return historical_append_decision(case)
    return VisibilityDecision(False, final_key_for_case(case), "unknown_source")


def unified_visibility_decision(case: DecisionCase) -> VisibilityDecision:
    """Planned helper behavior after consolidation.

    The important design choice is that the helper is one public decision
    surface but not one context-free formula. Fresh jobs, durable pool rows, and
    historical retained rows have different source-of-truth contracts today.
    Preserving those contracts is what makes the migration equivalent.
    """
    if case.source == FRESH_FINAL_OUTPUT:
        return fresh_final_output_decision(case)
    if case.source == POOL_READBACK:
        return pool_readback_decision(case)
    if case.source == HISTORICAL_APPEND:
        return historical_append_decision(case)
    return VisibilityDecision(False, final_key_for_case(case), "unknown_source")


def naive_recompute_everything_decision(case: DecisionCase) -> VisibilityDecision:
    """A tempting but non-equivalent simplification.

    It treats pool and historical rows as fresh rows. FlowGuard should reject
    this plan because current durable stamps and historical retention are real
    behavior, not redundant branches.
    """
    return fresh_final_output_decision(case)


def apply_dedupe(decision: VisibilityDecision, visible_keys: tuple[str, ...]) -> VisibilityDecision:
    if not decision.visible:
        return decision
    if decision.key in visible_keys:
        return VisibilityDecision(False, decision.key, "duplicate_merged")
    return decision


def decisions_match(legacy: VisibilityDecision, unified: VisibilityDecision) -> bool:
    if legacy.visible != unified.visible:
        return False
    if legacy.reason != unified.reason:
        return False
    if legacy.visible and legacy.key != unified.key:
        return False
    return True


class EquivalenceComparator:
    name = "EquivalenceComparator"
    reads = ("legacy_visible_keys", "unified_visible_keys")
    writes = (
        "legacy_visible_keys",
        "unified_visible_keys",
        "mismatches",
        "reason_mismatches",
        "duplicate_mismatches",
    )
    accepted_input_type = DecisionCase
    input_description = "candidate recommendation visibility case"
    output_description = "legacy versus unified visibility comparison"
    idempotency = "Equivalent final keys collapse independently in both legacy and unified projections."

    def __init__(self, *, strategy: str = "corrected") -> None:
        self.strategy = strategy

    def _unified_decision(self, case: DecisionCase) -> VisibilityDecision:
        if self.strategy == "naive_recompute":
            return naive_recompute_everything_decision(case)
        return unified_visibility_decision(case)

    def apply(self, input_obj: DecisionCase, state: State) -> Iterable[FunctionResult]:
        legacy = apply_dedupe(legacy_visibility_decision(input_obj), state.legacy_visible_keys)
        unified = apply_dedupe(self._unified_decision(input_obj), state.unified_visible_keys)
        equivalent = decisions_match(legacy, unified)
        legacy_keys = (
            _append(state.legacy_visible_keys, legacy.key)
            if legacy.visible
            else state.legacy_visible_keys
        )
        unified_keys = (
            _append(state.unified_visible_keys, unified.key)
            if unified.visible
            else state.unified_visible_keys
        )
        mismatch_key = f"{input_obj.name}:{legacy.reason}->{unified.reason}"
        mismatches = state.mismatches
        reason_mismatches = state.reason_mismatches
        duplicate_mismatches = state.duplicate_mismatches
        if not equivalent:
            mismatches = _append(mismatches, mismatch_key)
            if legacy.visible == unified.visible and legacy.reason != unified.reason:
                reason_mismatches = _append(reason_mismatches, mismatch_key)
            if legacy.reason == "duplicate_merged" or unified.reason == "duplicate_merged":
                duplicate_mismatches = _append(duplicate_mismatches, mismatch_key)

        label = label_for_result(input_obj, legacy, unified, equivalent)
        yield FunctionResult(
            output=ComparisonResult(
                input_obj.name,
                input_obj.source,
                legacy,
                unified,
                equivalent,
            ),
            new_state=replace(
                state,
                legacy_visible_keys=legacy_keys,
                unified_visible_keys=unified_keys,
                mismatches=mismatches,
                reason_mismatches=reason_mismatches,
                duplicate_mismatches=duplicate_mismatches,
            ),
            label=label,
        )


def label_for_result(
    case: DecisionCase,
    legacy: VisibilityDecision,
    unified: VisibilityDecision,
    equivalent: bool,
) -> str:
    if not equivalent:
        return f"mismatch_{case.source}"
    if legacy.reason == "duplicate_merged":
        return "equivalent_duplicate_hidden"
    if legacy.visible and case.source == FRESH_FINAL_OUTPUT:
        return "equivalent_fresh_visible"
    if legacy.visible and case.source == POOL_READBACK:
        if not case.has_runtime_config:
            return "equivalent_no_config_pool_visible"
        return "equivalent_pool_visible"
    if legacy.visible and case.source == HISTORICAL_APPEND:
        return "equivalent_historical_visible"
    if legacy.reason == "stale_policy_stamp":
        return "equivalent_stale_policy_hidden"
    if case.source == FRESH_FINAL_OUTPUT:
        return "equivalent_fresh_hidden"
    if case.source == POOL_READBACK:
        return "equivalent_pool_hidden"
    if case.source == HISTORICAL_APPEND:
        return "equivalent_historical_hidden"
    return "equivalent_unknown_source_hidden"


def no_visibility_equivalence_mismatches(state: State, trace) -> InvariantResult:
    del trace
    if state.mismatches:
        return InvariantResult.fail(
            "unified visibility changed legacy behavior",
            {"mismatches": state.mismatches},
        )
    return InvariantResult.pass_()


def visible_key_sequences_match(state: State, trace) -> InvariantResult:
    del trace
    if state.legacy_visible_keys != state.unified_visible_keys:
        return InvariantResult.fail(
            "legacy and unified visible key sequences diverged",
            {
                "legacy_visible_keys": state.legacy_visible_keys,
                "unified_visible_keys": state.unified_visible_keys,
            },
        )
    return InvariantResult.pass_()


def no_reason_equivalence_mismatches(state: State, trace) -> InvariantResult:
    del trace
    if state.reason_mismatches:
        return InvariantResult.fail(
            "legacy and unified hide/show reasons diverged",
            {"reason_mismatches": state.reason_mismatches},
        )
    return InvariantResult.pass_()


def no_unified_duplicate_visible_keys(state: State, trace) -> InvariantResult:
    del trace
    duplicates = tuple(
        key for key in state.unified_visible_keys if _count(state.unified_visible_keys, key) > 1
    )
    if duplicates:
        return InvariantResult.fail(
            "unified helper produced duplicate visible final keys",
            {"duplicate_keys": duplicates},
        )
    return InvariantResult.pass_()


INVARIANTS = (
    Invariant(
        name="no_visibility_equivalence_mismatches",
        description="Unified visibility must not change legacy show/hide behavior.",
        predicate=no_visibility_equivalence_mismatches,
    ),
    Invariant(
        name="visible_key_sequences_match",
        description="Legacy and unified visible final keys must remain identical.",
        predicate=visible_key_sequences_match,
    ),
    Invariant(
        name="no_reason_equivalence_mismatches",
        description="Unified visibility should preserve public reasons.",
        predicate=no_reason_equivalence_mismatches,
    ),
    Invariant(
        name="no_unified_duplicate_visible_keys",
        description="Unified visibility must preserve final-key dedupe.",
        predicate=no_unified_duplicate_visible_keys,
    ),
)


EXTERNAL_INPUTS = (
    DecisionCase("fresh_score_20", FRESH_FINAL_OUTPUT, score=20),
    DecisionCase("fresh_score_19", FRESH_FINAL_OUTPUT, score=19),
    DecisionCase("fresh_ai_reject", FRESH_FINAL_OUTPUT, score=82, recommend=False),
    DecisionCase("fresh_prefilter_reject", FRESH_FINAL_OUTPUT, score=82, prefilter_reject=True),
    DecisionCase("fresh_post_verify_failed", FRESH_FINAL_OUTPUT, score=82, post_verify_ok=False),
    DecisionCase("fresh_final_output_failed", FRESH_FINAL_OUTPUT, score=82, final_output_ok=False),
    DecisionCase("fresh_link_recheck_failed", FRESH_FINAL_OUTPUT, score=82, link_recheck_ok=False),
    DecisionCase("fresh_duplicate_a", FRESH_FINAL_OUTPUT, score=82, final_key="same-final-job"),
    DecisionCase("fresh_duplicate_b", FRESH_FINAL_OUTPUT, score=86, final_key="same-final-job"),
    DecisionCase(
        "pool_current_stamp_sparse_evidence",
        POOL_READBACK,
        score=82,
        final_output_ok=False,
        link_recheck_ok=False,
    ),
    DecisionCase("pool_stale_policy", POOL_READBACK, score=82, current_policy_stamp=False),
    DecisionCase("pool_output_status_reject", POOL_READBACK, score=82, output_status="reject"),
    DecisionCase("pool_missing_eligible_stamp", POOL_READBACK, score=82, eligible_stamp=False),
    DecisionCase("pool_hidden", POOL_READBACK, score=82, hidden=True),
    DecisionCase("pool_review_rejected", POOL_READBACK, score=82, review_rejected=True),
    DecisionCase(
        "pool_no_config_with_any_current_stamp",
        POOL_READBACK,
        score=82,
        has_runtime_config=False,
        any_current_policy_stamp=True,
        current_policy_stamp=False,
    ),
    DecisionCase(
        "pool_no_config_without_stamp",
        POOL_READBACK,
        score=82,
        has_runtime_config=False,
        any_current_policy_stamp=False,
    ),
    DecisionCase(
        "historical_retained_sparse_evidence",
        HISTORICAL_APPEND,
        score=72,
        final_output_ok=False,
        link_recheck_ok=False,
    ),
    DecisionCase("historical_score_19", HISTORICAL_APPEND, score=19),
    DecisionCase("historical_bad_url", HISTORICAL_APPEND, score=72, bad_output_url=True),
)

MAX_SEQUENCE_LENGTH = 2


def build_workflow() -> Workflow:
    return Workflow((EquivalenceComparator(strategy="corrected"),), name="visibility_equivalence")


def build_naive_recompute_workflow() -> Workflow:
    return Workflow(
        (EquivalenceComparator(strategy="naive_recompute"),),
        name="broken_naive_recompute_visibility",
    )


__all__ = [
    "EXTERNAL_INPUTS",
    "FRESH_FINAL_OUTPUT",
    "HISTORICAL_APPEND",
    "INVARIANTS",
    "MAX_SEQUENCE_LENGTH",
    "POOL_READBACK",
    "DecisionCase",
    "State",
    "build_naive_recompute_workflow",
    "build_workflow",
    "initial_state",
    "terminal_predicate",
]
