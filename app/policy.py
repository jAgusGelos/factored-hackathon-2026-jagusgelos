"""Dispute-resolution policy (AD-11) — explicit, falsifiable thresholds.

This module IS the policy table from plan.md's AD-11, implemented as data +
pure functions (no DB access, no I/O). `app/state_machine.py`'s guard
functions are thin wrappers that call into this module — this file is what
makes "eligible" / "ambiguous" / "high-risk" testable, not asserted.

AD-11's rows, in order:
  1. Transaction match: fuzzy-match candidates = this customer's own
     transactions where `abs(amount - reported_amount) <= max(reported_amount
     * 0.05, 2 USD-equivalent)` AND `abs(date diff) <= 3 days`.
  2. Confident match: exactly one candidate.
  3. Ambiguous match: 0 or 2+ candidates -> clarify (max 2 rounds), then
     escalate if still ambiguous.
  4. Auto-resolution eligible: confident match AND amount_usd <= 200 AND
     fraud_score < 30 AND status == "Approved" AND < 3 disputes in the same
     category in the trailing 90 days.
  5. Forced escalation: confident match but fails a Row-4 condition, OR the
     classifier (AD-6, Milestone 3) predicts Critical, OR the customer
     explicitly requests a human, OR any tool/LLM call fails after its retry
     budget is exhausted.
  6. Auto-resolution action: simulated provisional credit + case reference +
     expected-timeframe message — never a real transfer.

Simplification, disclosed: the "2 USD-equivalent" floor in Row 1 is applied
as a flat 2-unit floor in the complaint's OWN currency, not currency-converted
via exchange rates — the runtime fixture (AD-2) does not ship
`daily_exchange_rates` (no consumer needs it at request time). This matches
what `etl/build_fixture.py` already used when empirically classifying real
data during persona selection. In practice this floor is dominated by the 5%
relative term for any non-trivial claimed amount, so the currency-unit
mismatch has negligible effect on the match boundary — documented as a
hackathon-scope simplification, not a validated FX-aware threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.transactions import TransactionCandidate

MATCH_DATE_TOLERANCE_DAYS = 3
MATCH_AMOUNT_PCT_TOLERANCE = 0.05
MATCH_AMOUNT_MIN_TOLERANCE = 2.0

AUTO_RESOLVE_MAX_AMOUNT_USD = 200.0
AUTO_RESOLVE_MAX_FRAUD_SCORE = 30.0
AUTO_RESOLVE_REQUIRED_STATUS = "Approved"

ABUSE_GUARD_MAX_DISPUTES = 3
ABUSE_GUARD_WINDOW_DAYS = 90

MAX_CLARIFICATION_ROUNDS = 2

DISPUTE_COMPLAINT_CATEGORY = "Transactions"


class MatchOutcome(StrEnum):
    CONFIDENT = "confident"
    AMBIGUOUS = "ambiguous"


class ResolutionDecision(StrEnum):
    AUTO_RESOLVE = "auto_resolve"
    FORCED_ESCALATION = "forced_escalation"


@dataclass(frozen=True)
class ResolutionEvaluation:
    decision: ResolutionDecision
    reasons: tuple[str, ...]  # which condition(s) drove the decision — for the handoff record


def match_amount_tolerance(reported_amount: float) -> float:
    return max(reported_amount * MATCH_AMOUNT_PCT_TOLERANCE, MATCH_AMOUNT_MIN_TOLERANCE)


def evaluate_match(candidates: list[TransactionCandidate]) -> MatchOutcome:
    return MatchOutcome.CONFIDENT if len(candidates) == 1 else MatchOutcome.AMBIGUOUS


def effective_amount_usd(txn: TransactionCandidate) -> float | None:
    """`amount_usd` is NULL in the real dataset whenever `currency == 'USD'`
    (no conversion needed) — verified during Milestone 1's ETL development.
    """
    if txn.amount_usd is not None:
        return txn.amount_usd
    if txn.currency == "USD":
        return txn.amount
    return None


def evaluate_resolution(
    txn: TransactionCandidate, *, prior_disputes_in_window: int
) -> ResolutionEvaluation:
    """Row 4/5 of AD-11, given a single CONFIDENT match. Never called for an
    ambiguous match — that path is Row 3's clarification loop, not this.
    """
    amount_usd = effective_amount_usd(txn)
    reasons: list[str] = []

    amount_ok = amount_usd is not None and amount_usd <= AUTO_RESOLVE_MAX_AMOUNT_USD
    if not amount_ok:
        reasons.append(
            f"amount_usd={amount_usd} exceeds the {AUTO_RESOLVE_MAX_AMOUNT_USD} auto-resolve cap"
        )

    fraud_ok = txn.fraud_score is not None and txn.fraud_score < AUTO_RESOLVE_MAX_FRAUD_SCORE
    if not fraud_ok:
        reasons.append(
            f"fraud_score={txn.fraud_score} at/above the {AUTO_RESOLVE_MAX_FRAUD_SCORE} threshold"
        )

    status_ok = txn.transaction_status == AUTO_RESOLVE_REQUIRED_STATUS
    if not status_ok:
        reasons.append(f"transaction_status={txn.transaction_status!r}, not Approved")

    abuse_ok = prior_disputes_in_window < ABUSE_GUARD_MAX_DISPUTES
    if not abuse_ok:
        reasons.append(
            f"{prior_disputes_in_window} prior disputes in the trailing "
            f"{ABUSE_GUARD_WINDOW_DAYS} days (abuse guard)"
        )

    if amount_ok and fraud_ok and status_ok and abuse_ok:
        return ResolutionEvaluation(decision=ResolutionDecision.AUTO_RESOLVE, reasons=())
    return ResolutionEvaluation(decision=ResolutionDecision.FORCED_ESCALATION, reasons=tuple(reasons))
