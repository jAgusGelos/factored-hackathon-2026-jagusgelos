"""The statement step (statement-before-handoff): before a case goes to a
person, the customer says what happened and why they want the refund, so the
advisor does not have to call them back for it.

The escalation itself is already decided in code and held on the case
(`case_turn.PendingEscalation`); this step only adds the customer's account
to its handoff and then hands it off with the same reason. It never changes
the decision, the reason or any credit.

The model only reads (`llm.assess_statement`, one call per typed turn); code
decides every branch, and the step is bounded: at most one follow-up for a
missing key fact and one insistence after a refusal, then the case is handed
off whatever the customer says. The human button is a refusal, with no model
call, never a new HUMAN_REQUESTED escalation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app import handoffs, llm, replies
from app.case_model import CaseState, CustomerAction
from app.case_turn import (
    ChatReply,
    PendingEscalation,
    Turn,
    charge_prompt_context,
    finish_pending_escalation,
    transition,
)
from app.llm import StatementFact, card_possession_matters, known_fact
from app.policy import MIN_EXPLANATION_WORDS


class _Unavailable(StrEnum):
    """Why the handoff carries no summary (`failure_class` of the
    `handoff_statement_unavailable` event).
    """

    DEADLINE = "deadline"
    UNAVAILABLE = "unavailable"
    INVALID_OUTPUT = "invalid_output"
    EMPTY_SUMMARY = "empty_summary"


@dataclass(frozen=True)
class _Statement:
    """What the statement step knows so far (`cases.Case.statement_facts`):
    the latest valid summary and every key fact merged across turns.
    """

    summary: str
    facts: dict[str, str | None]

    @classmethod
    def from_case(cls, stored: dict | None) -> _Statement:
        stored = stored or {}
        return cls(summary=stored.get("summary", ""), facts=stored.get("facts", {}))

    def to_dict(self) -> dict:
        return {"summary": self.summary, "facts": self.facts}

    def merged(self, assessment: llm.StatementAssessment) -> _Statement:
        """A fact the customer already gave is never lost to a later turn that
        does not repeat it.
        """
        known = {fact: str(value) for fact, value in assessment.facts().items() if known_fact(value)}
        return _Statement(summary=assessment.summary or self.summary, facts={**self.facts, **known})

    def missing_fact(self) -> StatementFact | None:
        """The one follow-up, in fixed priority (`llm.StatementFact`)."""
        for fact in StatementFact:
            if fact == StatementFact.CARD_POSSESSION and not card_possession_matters(self.facts):
                continue
            if not known_fact(self.facts.get(fact)):
                return fact
        return None


def handle_statement(turn: Turn, text: str, action: CustomerAction | None) -> ChatReply:
    """A turn in `awaiting_statement`. Typed text is assessed once; an
    unavailable or unusable assessment hands the case off with the pending
    reason (never as a SERVICE_ISSUE).
    """
    case = turn.case
    pending = PendingEscalation.from_dict(case.pending_escalation)
    known = _Statement.from_case(case.statement_facts)
    if action == CustomerAction.HUMAN:
        return _on_refusal(turn, pending, known, text=None, via="button")
    if not text.strip():
        return _ask_more_or_finish(turn, pending, known, text=None, needs_more=True)
    try:
        assessment = llm.assess_statement(
            text, earlier=case.statement_text, charge=charge_prompt_context(turn, CaseState.AWAITING_STATEMENT, pending.charge),
        )
    except llm.LLMUnavailable as exc:
        failure = _Unavailable.DEADLINE if isinstance(exc, llm.LLMDeadlineExceeded) else _Unavailable.UNAVAILABLE
        return _finish_without_assessment(turn, pending, known, text, failure=failure)
    if assessment is None:
        return _finish_without_assessment(turn, pending, known, text, failure=_Unavailable.INVALID_OUTPUT)
    statement = known.merged(assessment)
    if assessment.declines or assessment.wants_human:
        return _on_refusal(turn, pending, statement, text=text, via="text")
    words = len(f"{case.statement_text or ''} {text}".split())
    needs_more = words < MIN_EXPLANATION_WORDS or statement.missing_fact() is not None
    return _ask_more_or_finish(turn, pending, statement, text=text, needs_more=needs_more)


def _event(turn: Turn, pending: PendingEscalation, event_type: str, **payload) -> None:
    # Closed values only: never the customer's words or the model's summary.
    turn.log_event(event_type, {"pending_escalation_reason": pending.reason, **payload})


def _stay(turn: Turn, pending: PendingEscalation, statement: _Statement, text: str | None, **counter) -> ChatReply | None:
    """Keeps waiting for the statement, appending this turn's text, from the
    exact counts this turn read (a concurrent statement turn loses).
    """
    case = turn.case
    return transition(
        turn, CaseState.AWAITING_STATEMENT, expected_states=(CaseState.AWAITING_STATEMENT,),
        expected_statement_counts=(case.statement_followups, case.statement_declines),
        append_statement=text, statement_facts=statement.to_dict(), **counter,
    )


def _on_refusal(
    turn: Turn, pending: PendingEscalation, statement: _Statement, *, text: str | None, via: str,
) -> ChatReply:
    """The first refusal gets one insistence; the second is handed off as
    declined. A refusal after the follow-up is not one: the customer already
    gave their account.
    """
    case = turn.case
    if case.statement_followups > 0:
        return _finish(turn, pending, statement, text)
    _event(turn, pending, "handoff_statement_declined", decline_number=case.statement_declines + 1, via=via)
    if case.statement_declines > 0:
        return _finish(turn, pending, statement, text, status=handoffs.StatementStatus.DECLINED)
    lost = _stay(turn, pending, statement, text, add_statement_decline=True)
    if lost:
        return lost
    _event(turn, pending, "handoff_statement_insisted")
    return turn.reply(CaseState.AWAITING_STATEMENT, replies.STATEMENT_INSIST[turn.language])


def _ask_more_or_finish(
    turn: Turn, pending: PendingEscalation, statement: _Statement, *, text: str | None, needs_more: bool,
) -> ChatReply:
    if not needs_more or turn.case.statement_followups > 0:
        return _finish(turn, pending, statement, text)
    fact = statement.missing_fact()
    lost = _stay(turn, pending, statement, text, add_statement_followup=True)
    if lost:
        return lost
    _event(turn, pending, "handoff_statement_followup_requested", fact=fact)
    return turn.reply(CaseState.AWAITING_STATEMENT, replies.statement_followup(fact, turn.language))


def _finish_without_assessment(
    turn: Turn, pending: PendingEscalation, statement: _Statement, text: str, *, failure: _Unavailable,
) -> ChatReply:
    """A summary from an earlier turn still stands; only this turn's text went unread."""
    _event(turn, pending, "handoff_statement_unavailable", failure_class=failure)
    return _finish(turn, pending, statement, text, log_outcome=False)


def _finish(
    turn: Turn, pending: PendingEscalation, statement: _Statement, text: str | None,
    *, status: handoffs.StatementStatus | None = None, log_outcome: bool = True,
) -> ChatReply:
    if status is None:
        status = handoffs.StatementStatus.GIVEN if statement.summary else handoffs.StatementStatus.SUMMARY_UNAVAILABLE
    if log_outcome and status == handoffs.StatementStatus.GIVEN:
        _event(turn, pending, "handoff_statement_available")
    elif log_outcome and status == handoffs.StatementStatus.SUMMARY_UNAVAILABLE:
        _event(turn, pending, "handoff_statement_unavailable", failure_class=_Unavailable.EMPTY_SUMMARY)
    handoff = handoffs.with_statement(pending.handoff, status=status, summary=statement.summary, facts=statement.facts)
    case = turn.case
    return finish_pending_escalation(
        turn, pending, handoff, append_statement=text, statement_facts=statement.to_dict(),
        expected_statement_counts=(case.statement_followups, case.statement_declines),
    )
