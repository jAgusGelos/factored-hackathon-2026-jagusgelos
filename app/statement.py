"""The statement step (statement-before-handoff): before a case goes to a
person, the customer says what happened and why they want the refund, so the
advisor does not have to call them back for it.

The escalation itself is already decided in code and held on the case
(`case_turn.PendingEscalation`); this step only adds the customer's account
to its handoff and then hands it off with the same reason. It never changes
the decision, the reason or any credit.
"""

from __future__ import annotations

from app import handoffs
from app.case_model import CustomerAction
from app.case_turn import ChatReply, PendingEscalation, Turn, finish_pending_escalation


def handle_statement(turn: Turn, text: str, action: CustomerAction | None) -> ChatReply:
    """A turn in `awaiting_statement`. The human button is a refusal to tell
    more (no model call); typed text is the customer's statement.
    """
    pending = PendingEscalation.from_dict(turn.case.pending_escalation)
    if action == CustomerAction.HUMAN:
        return _finish(turn, pending, handoffs.StatementStatus.DECLINED)
    return _finish(turn, pending, handoffs.StatementStatus.SUMMARY_UNAVAILABLE, text=text)


def _finish(
    turn: Turn, pending: PendingEscalation, status: handoffs.StatementStatus, *, text: str | None = None,
) -> ChatReply:
    handoff = handoffs.with_statement(pending.handoff, status=status)
    return finish_pending_escalation(turn, pending, handoff, append_statement=text)
