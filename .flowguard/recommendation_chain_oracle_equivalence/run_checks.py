"""Run FlowGuard checks for fixed-oracle recommendation chain equivalence."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from flowguard import Explorer, Scenario, ScenarioExpectation, review_scenarios
except ModuleNotFoundError as exc:  # pragma: no cover - local adoption helper
    raise SystemExit("flowguard is not importable in this Python environment.") from exc

import model


def run_explorer(
    name: str,
    workflow,
    *,
    external_inputs=None,
    max_sequence_length: int | None = None,
    required_labels: tuple[str, ...] = (),
) -> object:
    report = Explorer(
        workflow=workflow,
        initial_states=(model.initial_state(),),
        external_inputs=external_inputs or model.EQUIVALENT_INPUTS,
        invariants=model.INVARIANTS,
        max_sequence_length=max_sequence_length or model.MAX_SEQUENCE_LENGTH,
        terminal_predicate=model.terminal_predicate,
        required_labels=required_labels,
    ).explore()
    print(f"=== {name} ===")
    print(report.format_text())
    print()
    return report


def run_expected_broken(
    name: str,
    workflow,
    expected_invariants: tuple[str, ...],
    *,
    external_inputs=None,
    max_sequence_length: int | None = None,
) -> bool:
    report = run_explorer(
        name,
        workflow,
        external_inputs=external_inputs,
        max_sequence_length=max_sequence_length,
    )
    names = {violation.invariant_name for violation in report.violations}
    if report.ok:
        print(f"Expected {name} to fail, but it passed.")
        return False
    missing = [name for name in expected_invariants if name not in names]
    if missing:
        print(
            f"Expected {name} to fail {tuple(missing)!r}, "
            f"observed {tuple(sorted(names))!r}."
        )
        return False
    print(f"Expected violation observed for {name}: {', '.join(expected_invariants)}")
    print()
    return True


def run_correct_model() -> object:
    return run_explorer(
        "fixed-oracle full chain equivalence",
        model.build_workflow(),
        required_labels=(
            "search_oracle_equal",
            "prompt_oracle_equal",
            "ai_oracle_equal",
            "url_oracle_equal",
            "state_oracle_equal",
            "chain_equivalent_fresh_visible",
            "chain_equivalent_fresh_final_output_hidden",
            "chain_equivalent_pool_visible",
            "chain_equivalent_pool_readback_hidden",
            "chain_equivalent_stale_policy_hidden",
            "chain_equivalent_historical_visible",
            "chain_equivalent_historical_append_hidden",
            "chain_equivalent_duplicate_hidden",
        ),
    )


def run_broken_model_checks() -> bool:
    naive_failed = run_expected_broken(
        "broken naive recompute with fixed oracles",
        model.build_naive_recompute_workflow(),
        ("no_final_chain_mismatches", "visible_key_sequences_match"),
    )
    assumption_failed = run_expected_broken(
        "broken fixed-oracle assumptions",
        model.build_workflow(),
        ("no_upstream_oracle_mismatches",),
        external_inputs=model.ASSUMPTION_BREAK_INPUTS,
        max_sequence_length=model.ASSUMPTION_BREAK_MAX_SEQUENCE_LENGTH,
    )
    return naive_failed and assumption_failed


def run_scenario_review() -> object:
    scenarios = (
        Scenario(
            name="full_chain_fresh_score_20_visible",
            description="Frozen search/prompt/AI/URL state keeps a fresh score-20 job visible.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.ChainCase("fresh_score_20", model.FRESH_FINAL_OUTPUT, score=20),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=(
                    "search_oracle_equal",
                    "prompt_oracle_equal",
                    "ai_oracle_equal",
                    "url_oracle_equal",
                    "state_oracle_equal",
                    "chain_equivalent_fresh_visible",
                ),
                forbidden_trace_labels=("chain_mismatch_fresh_final_output",),
                summary="full fixed-oracle chain preserves a fresh recommendation",
            ),
        ),
        Scenario(
            name="full_chain_pool_stamp_visible",
            description="Frozen durable pool stamp state remains visible when current.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.ChainCase(
                    "pool_current_stamp_sparse_evidence",
                    model.POOL_READBACK,
                    score=82,
                    final_output_ok=False,
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("chain_equivalent_pool_visible",),
                forbidden_trace_labels=("chain_mismatch_pool_readback",),
                summary="pool readback is equivalent when DB and policy stamps are fixed",
            ),
        ),
        Scenario(
            name="full_chain_historical_retention_visible",
            description="Frozen historical append state stays visible under the source-aware helper.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.ChainCase(
                    "historical_retained_sparse_evidence",
                    model.HISTORICAL_APPEND,
                    score=72,
                    final_output_ok=False,
                    link_recheck_ok=False,
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("chain_equivalent_historical_visible",),
                forbidden_trace_labels=("chain_mismatch_historical_append",),
                summary="historical retention is preserved under fixed upstream outputs",
            ),
        ),
        Scenario(
            name="full_chain_duplicate_collapse",
            description="Frozen oracle outputs keep duplicate final keys collapsed.",
            initial_state=model.initial_state(),
            external_input_sequence=(
                model.ChainCase(
                    "fresh_duplicate_a",
                    model.FRESH_FINAL_OUTPUT,
                    score=82,
                    final_key="same-final-job",
                ),
                model.ChainCase(
                    "fresh_duplicate_b",
                    model.FRESH_FINAL_OUTPUT,
                    score=86,
                    final_key="same-final-job",
                ),
            ),
            expected=ScenarioExpectation(
                expected_status="ok",
                required_trace_labels=("chain_equivalent_duplicate_hidden",),
                forbidden_trace_labels=("chain_mismatch_fresh_final_output",),
                summary="dedupe is equivalent across the full fixed-oracle chain",
            ),
        ),
    )
    report = review_scenarios(
        scenarios,
        default_workflow=model.build_workflow(),
        default_invariants=model.INVARIANTS,
    )
    print("=== scenario review ===")
    print(report.format_text())
    print()
    return report


def run_assumption_break_review() -> object:
    drift_cases = (
        (
            "assumption_prompt_drift_rejected",
            model.ChainCase("fresh_score_20:prompt_drift", model.FRESH_FINAL_OUTPUT, score=20, prompt_same=False),
            "prompt_oracle_prompt_drift",
            "prompt fingerprint drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_ai_drift_rejected",
            model.ChainCase("fresh_score_20:ai_drift", model.FRESH_FINAL_OUTPUT, score=20, ai_same=False),
            "ai_oracle_ai_drift",
            "AI output drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_url_drift_rejected",
            model.ChainCase("fresh_score_20:url_drift", model.FRESH_FINAL_OUTPUT, score=20, url_same=False),
            "url_oracle_url_drift",
            "URL/final-key drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_config_drift_rejected",
            model.ChainCase("fresh_score_20:config_drift", model.FRESH_FINAL_OUTPUT, score=20, config_same=False),
            "state_oracle_config_drift",
            "runtime policy/config drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_db_drift_rejected",
            model.ChainCase("pool_current:db_drift", model.POOL_READBACK, score=82, db_same=False),
            "state_oracle_db_drift",
            "durable DB state drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_review_drift_rejected",
            model.ChainCase("pool_current:review_drift", model.POOL_READBACK, score=82, review_same=False),
            "state_oracle_review_drift",
            "review/user visibility drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_clock_drift_rejected",
            model.ChainCase("fresh_score_20:clock_drift", model.FRESH_FINAL_OUTPUT, score=20, clock_same=False),
            "state_oracle_clock_drift",
            "clock/order metadata drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_search_drift_rejected",
            model.ChainCase("fresh_score_20:search_drift", model.FRESH_FINAL_OUTPUT, score=20, search_same=False),
            "search_oracle_search_drift",
            "search identity drift blocks a full-chain equivalence claim",
        ),
        (
            "assumption_order_drift_rejected",
            model.ChainCase("fresh_score_20:order_drift", model.FRESH_FINAL_OUTPUT, score=20, order_same=False),
            "search_oracle_order_drift",
            "candidate ordering drift blocks a full-chain equivalence claim",
        ),
    )
    scenarios = tuple(
        Scenario(
            name=name,
            description=summary,
            initial_state=model.initial_state(),
            external_input_sequence=(case,),
            expected=ScenarioExpectation(
                expected_status="violation",
                expected_violation_names=("no_upstream_oracle_mismatches",),
                required_trace_labels=(label,),
                summary=summary,
            ),
        )
        for name, case, label, summary in drift_cases
    )
    report = review_scenarios(
        scenarios,
        default_workflow=model.build_workflow(),
        default_invariants=model.INVARIANTS,
    )
    print("=== assumption break review ===")
    print(report.format_text())
    print()
    return report


def main() -> int:
    print("flowguard import source:", os.environ.get("PYTHONPATH") or "(import path)")
    print()
    correct_report = run_correct_model()
    broken_failed = run_broken_model_checks()
    scenario_report = run_scenario_review()
    assumption_report = run_assumption_break_review()
    if not correct_report.ok:
        return 1
    if not broken_failed:
        return 1
    if not scenario_report.ok:
        return 1
    if not assumption_report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
