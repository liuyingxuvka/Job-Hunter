from __future__ import annotations

import json
from dataclasses import asdict

from model import run_all_strategies


def main() -> int:
    outcomes = run_all_strategies()
    by_name = {outcome.strategy: outcome for outcome in outcomes}

    current = by_name["current_strict_skipped_postverify"]
    final_only = by_name["final_only_postverify"]
    proposed = by_name["early_invalid_drop_then_final_evidence_gate"]

    failures: list[str] = []
    if current.final_visible != 0:
        failures.append("current strict skipped-postVerify model should reproduce empty final list")
    if final_only.bad_visible != 0:
        failures.append("final-only postVerify should not show bad links")
    if final_only.cost <= proposed.cost:
        failures.append("final-only postVerify should cost more than proposed early invalid drop")
    if proposed.bad_visible != 0:
        failures.append("proposed flow must not show known bad links")
    if proposed.missed_good != 0:
        failures.append("proposed flow should keep all valid good-fit leads in this scenario")
    if proposed.final_visible <= current.final_visible:
        failures.append("proposed flow should fix empty-final-list behavior")

    payload = {
        "outcomes": [asdict(outcome) for outcome in outcomes],
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
