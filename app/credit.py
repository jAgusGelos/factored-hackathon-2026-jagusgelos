"""AD-11 Row 6: issuing the automatic credit once the charge passed the
evidence check for the customer's reason. The case is claimed as resolved
(with the credit, inside one compare-and-set) before anything about the credit
is logged or said.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from app import cases, handoffs, replies
from app.case_model import CaseState
from app.case_turn import ChatReply, Turn, finish_escalated, reply_for_lost_race
from app.policy import DisputeReason, credit_key, effective_amount_usd
from app.transactions import TransactionCandidate


def _simulate_provisional_credit(
    turn: Turn, matched: TransactionCandidate, reference: str, reason: DisputeReason,
) -> None:
    """AD-11 Row 6's auto-resolution action: a SIMULATED provisional credit,
    logged as such — never a real transfer, never a call to any payment
    provider (there is no such integration in this codebase). An unrecognized
    charge also blocks the card (simulated) and queues the credit for
    back-office review, where it is reversed if the charge was the customer's.
    """
    turn.log_event(
        "simulated_credit",
        {
            "reference": reference, "matched_transaction_id": matched.transaction_id,
            "amount": matched.amount, "currency": matched.currency, "reason": reason, "simulated": True,
        },
    )
    if reason == DisputeReason.UNRECOGNIZED:
        turn.log_event("simulated_card_block", {"reference": reference, "simulated": True})
        turn.log_event("credit_review_queued", {"reference": reference, "reversible": True})


def _credit_grant(
    matched: TransactionCandidate, reason: DisputeReason, twins: tuple[str, ...],
) -> cases.CreditGrant:
    amount_usd = effective_amount_usd(matched)
    if amount_usd is None:
        raise ValueError(f"No USD amount for {matched.transaction_id}: screening must have escalated it")
    return cases.CreditGrant(key=credit_key(matched, reason, twins), reason=reason, amount_usd=amount_usd)


def finish_resolved(
    turn: Turn, matched: TransactionCandidate, *, reason: DisputeReason, twins: tuple[str, ...],
    expected_states: tuple[CaseState, ...], expected_match: str | None = None,
) -> ChatReply:
    """Only ever called after a verdict WITH a reason. The credit limits are
    checked again inside the claiming UPDATE (`cases.CreditGrant`), so a second
    case of the same customer running at the same time cannot slip past them.
    """
    reference = f"REF-{uuid.uuid4().hex[:10].upper()}"
    grant = _credit_grant(matched, reason, twins)
    report = replace(turn.report, reason=reason)
    # The transition is claimed BEFORE the credit is logged: of two concurrent
    # requests exactly one flips the case to resolved_auto and issues a credit.
    try:
        claimed = cases.update_case(
            turn.case.case_id, state=CaseState.RESOLVED_AUTO, expected_states=expected_states,
            expected_matched_transaction_id=expected_match, matched_transaction_id=matched.transaction_id,
            resolution_reference=reference, dispute_reason=reason, credit=grant, db_path=turn.db_path,
        )
    except cases.DuplicateCreditError:
        credited_in = _case_that_credited(turn, matched, grant)
        turn.log_event("credit_already_granted", {"credit_key": grant.key, "credited_in_case": credited_in})
        return finish_escalated(turn, handoffs.already_credited(report, matched, credited_in), report)
    if not claimed:
        current = cases.get_case(turn.case.case_id, db_path=turn.db_path)
        if current.state == turn.case.state and current.matched_transaction_id == turn.case.matched_transaction_id:
            # Nothing else moved the case: the credit limits refused it.
            turn.log_event("credit_limit_reached", {"matched_transaction_id": matched.transaction_id})
            return finish_escalated(turn, handoffs.credit_limit_reached(report, matched), report)
        return reply_for_lost_race(turn, CaseState.RESOLVED_AUTO)
    _simulate_provisional_credit(turn, matched, reference, reason)
    turn.log_event(
        "case_resolved",
        {
            "matched_transaction_id": matched.transaction_id, "amount": matched.amount,
            "currency": matched.currency, "resolution_reference": reference, "reason": reason,
        },
    )
    # A fixed template, not a model call: it must carry the reference and every
    # disclosure word for word, and it saves the turn a sequential LLM call.
    return turn.reply(CaseState.RESOLVED_AUTO, replies.resolved(reference, reason, turn.language))


def _case_that_credited(turn: Turn, matched: TransactionCandidate, grant: cases.CreditGrant) -> str:
    """The case whose credit collided with this one: on the transaction itself,
    or on its duplicate pair's shared credit key.
    """
    customer_id = turn.session.customer_id
    return (
        cases.credited_case_for_transaction(customer_id, matched.transaction_id, db_path=turn.db_path)
        or cases.credited_case_for_key(customer_id, grant.key, db_path=turn.db_path)
        or "desconocido"
    )
