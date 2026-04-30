from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


DISCOVER_COST = 1
EARLY_VERIFY_COST = 2
SCORE_COST = 8
ROLE_BIND_COST = 3
POST_VERIFY_COST = 4


@dataclass(frozen=True)
class JobLead:
    name: str
    valid: bool
    clearly_invalid_early: bool
    good_fit: bool
    early_evidence_strong: bool


@dataclass(frozen=True)
class Outcome:
    strategy: str
    scored: int
    post_verified: int
    final_visible: int
    bad_visible: int
    missed_good: int
    cost: int
    notes: tuple[str, ...]


SAMPLE_LEADS: tuple[JobLead, ...] = (
    JobLead(
        "direct-good-strong-evidence",
        valid=True,
        clearly_invalid_early=False,
        good_fit=True,
        early_evidence_strong=True,
    ),
    JobLead(
        "direct-good-weak-dynamic",
        valid=True,
        clearly_invalid_early=False,
        good_fit=True,
        early_evidence_strong=False,
    ),
    JobLead(
        "direct-404",
        valid=False,
        clearly_invalid_early=True,
        good_fit=True,
        early_evidence_strong=False,
    ),
    JobLead(
        "company-good-not-fit",
        valid=True,
        clearly_invalid_early=False,
        good_fit=False,
        early_evidence_strong=True,
    ),
    JobLead(
        "company-generic-careers",
        valid=False,
        clearly_invalid_early=True,
        good_fit=False,
        early_evidence_strong=False,
    ),
)


def current_strict_skipped_postverify(leads: Iterable[JobLead]) -> Outcome:
    cost = 0
    scored = 0
    post_verified = 0
    visible: list[JobLead] = []
    notes: list[str] = []
    for lead in leads:
        cost += DISCOVER_COST + EARLY_VERIFY_COST
        if lead.clearly_invalid_early:
            notes.append(f"{lead.name}: early dropped")
            continue
        cost += SCORE_COST + ROLE_BIND_COST
        scored += 1
        if lead.good_fit:
            notes.append(f"{lead.name}: recommended but postVerify skipped, final blocked")
            continue
        notes.append(f"{lead.name}: scored reject")
    return _outcome("current_strict_skipped_postverify", leads, scored, post_verified, visible, cost, notes)


def final_only_postverify(leads: Iterable[JobLead]) -> Outcome:
    cost = 0
    scored = 0
    post_verified = 0
    visible: list[JobLead] = []
    notes: list[str] = []
    for lead in leads:
        cost += DISCOVER_COST
        cost += SCORE_COST + ROLE_BIND_COST
        scored += 1
        if not lead.good_fit:
            notes.append(f"{lead.name}: scored reject")
            continue
        cost += POST_VERIFY_COST
        post_verified += 1
        if lead.valid:
            visible.append(lead)
            notes.append(f"{lead.name}: final pass")
        else:
            notes.append(f"{lead.name}: bad link found late after scoring")
    return _outcome("final_only_postverify", leads, scored, post_verified, visible, cost, notes)


def early_invalid_drop_then_final_evidence_gate(leads: Iterable[JobLead]) -> Outcome:
    cost = 0
    scored = 0
    post_verified = 0
    visible: list[JobLead] = []
    notes: list[str] = []
    for lead in leads:
        cost += DISCOVER_COST + EARLY_VERIFY_COST
        if lead.clearly_invalid_early:
            notes.append(f"{lead.name}: early hard-invalid drop")
            continue
        cost += SCORE_COST + ROLE_BIND_COST
        scored += 1
        if not lead.good_fit:
            notes.append(f"{lead.name}: scored reject")
            continue
        if lead.early_evidence_strong:
            visible.append(lead)
            notes.append(f"{lead.name}: final pass from strong early evidence")
            continue
        cost += POST_VERIFY_COST
        post_verified += 1
        if lead.valid:
            visible.append(lead)
            notes.append(f"{lead.name}: final pass after postVerify")
        else:
            notes.append(f"{lead.name}: postVerify reject")
    return _outcome("early_invalid_drop_then_final_evidence_gate", leads, scored, post_verified, visible, cost, notes)


def _outcome(
    strategy: str,
    leads: Iterable[JobLead],
    scored: int,
    post_verified: int,
    visible: list[JobLead],
    cost: int,
    notes: list[str],
) -> Outcome:
    lead_list = list(leads)
    bad_visible = sum(1 for lead in visible if not lead.valid)
    missed_good = sum(1 for lead in lead_list if lead.valid and lead.good_fit and lead not in visible)
    return Outcome(
        strategy=strategy,
        scored=scored,
        post_verified=post_verified,
        final_visible=len(visible),
        bad_visible=bad_visible,
        missed_good=missed_good,
        cost=cost,
        notes=tuple(notes),
    )


def run_all_strategies(leads: Iterable[JobLead] = SAMPLE_LEADS) -> tuple[Outcome, ...]:
    lead_tuple = tuple(leads)
    return (
        current_strict_skipped_postverify(lead_tuple),
        final_only_postverify(lead_tuple),
        early_invalid_drop_then_final_evidence_gate(lead_tuple),
    )
