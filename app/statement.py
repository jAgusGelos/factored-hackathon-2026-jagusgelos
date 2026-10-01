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

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
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
from app.policy import (
    FOLLOWUP_FACTS,
    MIN_EXPLANATION_WORDS,
    StatementAssessment,
    StatementField,
    card_possession_matters,
    known_fact,
)
from app.transactions import TransactionCandidate


class _Unavailable(StrEnum):
    """Why the handoff carries no summary (`failure_class` of the
    `handoff_statement_unavailable` event).
    """

    DEADLINE = "deadline"
    UNAVAILABLE = "unavailable"
    INVALID_OUTPUT = "invalid_output"
    EMPTY_SUMMARY = "empty_summary"


# The model's summary is the only free text this step adds to a handoff, so
# code checks it too, with a margin over what the prompt asks: one that does
# not hold is dropped.
_SUMMARY_MAX_WORDS = llm.STATEMENT_SUMMARY_MAX_WORDS + 15
_QUOTED_RUN_WORDS = 6
# Dates and amounts are not personal data; a document, phone or card number
# is a long run of digits however it is separated.
_DATE = re.compile(
    r"(?<![\d./-])(?:\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])[./-](?:\d{4}|\d{2}))(?![./\s-]?\d)"
)
_CURRENCY = r"(?:\$|\b(?:cop|ars|usd|mxn|brl|pesos?|d[oó]lares|reales?)\b)"
# Up to 999.999.999 with its currency next to it; anything longer is no amount.
_GROUPED = r"(?<![\d.,])\d{1,3}(?:[.,]\d{3}){1,2}(?:,\d{1,2})?(?![.,]?\d)"
_AMOUNT = re.compile(rf"{_CURRENCY}\s*{_GROUPED}|{_GROUPED}\s*{_CURRENCY}", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"\d(?:[\s.,/\-–—]?\d){5,}")
_CONTACT_OR_QUOTE = re.compile(r"@|https?://|[\"“”«»‘]")
# An identifier named next to a long number, however that number is written.
_IDENTIFIER = re.compile(
    r"\b(?:documento|c[eé]dula|dni|cpf|rg|pasaporte|tel[eé]fono|celular|whatsapp|n[uú]mero|tarjeta|cuenta)\b"
    r"\W{0,20}\$?\s*\d(?:[\s.,/\-–—]?\d){5,}",
    re.IGNORECASE,
)
# A copied run only counts when it carries the customer's own content, not
# just the charge's facts in the words anyone would use for them.
_MIN_CONTENT_WORDS = 2
_COMMON_WORDS = frozenset({
    "cliente", "cargo", "cargos", "compra", "tarjeta", "banco", "comercio", "este", "esta", "ese", "esa",
    "para", "pero", "como", "porque", "desde", "hasta", "sobre", "entre", "cuando", "donde", "pesos",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
    "noviembre", "diciembre",
})


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _content_words(run: tuple[str, ...], charge_words: frozenset[str]) -> int:
    return sum(
        1 for word in run
        if len(word) >= 4 and not word.isdigit() and word not in _COMMON_WORDS and word not in charge_words
    )


def _quotes_the_customer(summary: str, customer_text: str, charge_words: frozenset[str]) -> bool:
    """The whole summary copied, or a run of the customer's own words."""
    written = _words(summary)
    said = _words(customer_text)
    if not written:
        return False
    if len(written) < _QUOTED_RUN_WORDS:
        return any(said[i:i + len(written)] == written for i in range(len(said) - len(written) + 1))
    runs = {tuple(said[i:i + _QUOTED_RUN_WORDS]) for i in range(len(said) - _QUOTED_RUN_WORDS + 1)}
    copied = (
        tuple(written[i:i + _QUOTED_RUN_WORDS]) for i in range(len(written) - _QUOTED_RUN_WORDS + 1)
    )
    return any(run in runs and _content_words(run, charge_words) >= _MIN_CONTENT_WORDS for run in copied)


def _charge_amount_digits(charge: TransactionCandidate | None) -> frozenset[str]:
    """The charge's own amount as the model may write it (with or without cents)."""
    if charge is None:
        return frozenset()
    return frozenset({str(int(charge.amount)), re.sub(r"\D", "", f"{charge.amount:.2f}")})


def _charge_words(charge: TransactionCandidate | None) -> frozenset[str]:
    return frozenset(_words(charge.merchant_name or "")) if charge is not None else frozenset()


def _names_personal_data(summary: str, charge: TransactionCandidate | None) -> bool:
    allowed = _charge_amount_digits(charge)
    without_dates_or_amounts = _AMOUNT.sub(" ", _DATE.sub(" ", summary))
    numbers = (re.sub(r"\D", "", match) for match in _LONG_NUMBER.findall(without_dates_or_amounts))
    return (
        any(digits not in allowed for digits in numbers)
        or bool(_CONTACT_OR_QUOTE.search(summary)) or bool(_IDENTIFIER.search(summary))
    )


def _summary_is_safe(summary: str, customer_text: str, charge: TransactionCandidate | None) -> bool:
    return (
        len(summary.split()) <= _SUMMARY_MAX_WORDS and not _names_personal_data(summary, charge)
        and not _quotes_the_customer(summary, customer_text, _charge_words(charge))
    )


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

    def merged(self, assessment: StatementAssessment) -> _Statement:
        """A fact the customer already gave is never lost to a later turn that
        does not repeat it.
        """
        known = {str(fact): str(value) for fact, value in assessment.facts().items() if known_fact(value)}
        return _Statement(summary=assessment.summary or self.summary, facts={**self.facts, **known})

    def has_account(self) -> bool:
        """Something the customer told, even if its summary was dropped."""
        return bool(self.summary) or any(known_fact(value) for value in self.facts.values())

    def missing_fact(self) -> StatementField | None:
        """The one follow-up, in fixed priority (`policy.FOLLOWUP_FACTS`)."""
        for fact in FOLLOWUP_FACTS:
            if fact == StatementField.CARD_POSSESSION and not card_possession_matters(self.facts):
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
    if case.pending_escalation is None:
        raise ValueError(f"A case waiting for the statement without a pending escalation (case {case.case_id})")
    pending = PendingEscalation.from_dict(case.pending_escalation)
    known = _Statement.from_case(case.statement_facts)
    if action == CustomerAction.HUMAN:
        return _on_refusal(turn, pending, known, text=None, via="button", had_account=known.has_account())
    if not text.strip():
        return _ask_more_or_finish(turn, pending, known, text=None, followup=None, needs_more=True)
    try:
        assessment = llm.assess_statement(
            text, earlier=case.statement_text, charge=charge_prompt_context(turn, CaseState.AWAITING_STATEMENT, pending.charge),
        )
    except llm.LLMUnavailable as exc:
        failure = _Unavailable.DEADLINE if isinstance(exc, llm.LLMDeadlineExceeded) else _Unavailable.UNAVAILABLE
        return _finish_without_assessment(turn, pending, known, text, failure=failure)
    if assessment is None:
        return _finish_without_assessment(turn, pending, known, text, failure=_Unavailable.INVALID_OUTPUT)
    noted: list[tuple[str, dict]] = []
    customer_text = f"{case.statement_text or ''}\n{text}"
    if assessment.summary and not _summary_is_safe(assessment.summary, customer_text, pending.charge):
        noted.append(_outcome(pending, "handoff_statement_summary_dropped"))
        assessment = replace(assessment, summary="")
    statement = known.merged(assessment)
    if assessment.declines or assessment.wants_human:
        return _on_refusal(
            turn, pending, statement, text=text, via="text", had_account=known.has_account(), noted=noted,
        )
    # Only this turn: an earlier turn here was a refusal, never part of an account.
    too_short = len(text.split()) < MIN_EXPLANATION_WORDS
    followup = statement.missing_fact()
    return _ask_more_or_finish(
        turn, pending, statement, text=text, followup=followup, needs_more=too_short or followup is not None,
        noted=noted,
    )


def _outcome(pending: PendingEscalation, event_type: str, **payload) -> tuple[str, dict]:
    # Closed values only: never the customer's words or the model's summary.
    return event_type, {"pending_escalation_reason": pending.reason, **payload}


def _stay(
    turn: Turn, statement: _Statement, text: str | None, *, events: Sequence[tuple[str, dict]], **counter,
) -> ChatReply | None:
    """Keeps waiting for the statement, appending this turn's text, from the
    exact counts this turn read (a concurrent statement turn loses). `events`
    are logged only once the move is claimed.
    """
    case = turn.case
    lost = transition(
        turn, CaseState.AWAITING_STATEMENT, expected_states=(CaseState.AWAITING_STATEMENT,),
        expected_statement_counts=(case.statement_followups, case.statement_declines),
        append_statement=text, statement_facts=statement.to_dict(), **counter,
    )
    if lost:
        return lost
    for event_type, payload in events:
        turn.log_event(event_type, payload)
    return None


def _on_refusal(
    turn: Turn, pending: PendingEscalation, statement: _Statement, *, text: str | None, via: str,
    had_account: bool, noted: Sequence[tuple[str, dict]] = (),
) -> ChatReply:
    """The first refusal gets one insistence; the second is handed off as
    declined. A refusal after a follow-up to an account the customer gave
    before this turn hands that account off instead.
    """
    case = turn.case
    if case.statement_followups > 0 and had_account:
        return _finish(turn, pending, statement, text, noted=noted)
    declined = _outcome(pending, "handoff_statement_declined", decline_number=case.statement_declines + 1, via=via)
    if case.statement_declines > 0:
        return _finish(
            turn, pending, statement, text, status=handoffs.StatementStatus.DECLINED, events=[declined], noted=noted,
        )
    lost = _stay(
        turn, statement, text, events=[*noted, declined, _outcome(pending, "handoff_statement_insisted")],
        add_statement_decline=True,
    )
    return lost or turn.reply(CaseState.AWAITING_STATEMENT, replies.STATEMENT_INSIST[turn.language])


def _ask_more_or_finish(
    turn: Turn, pending: PendingEscalation, statement: _Statement, *, text: str | None,
    followup: StatementField | None, needs_more: bool, noted: Sequence[tuple[str, dict]] = (),
) -> ChatReply:
    if not needs_more or turn.case.statement_followups > 0:
        return _finish(turn, pending, statement, text, noted=noted)
    asked = _outcome(pending, "handoff_statement_followup_requested", fact=followup)
    lost = _stay(turn, statement, text, events=[*noted, asked], add_statement_followup=True)
    return lost or turn.reply(CaseState.AWAITING_STATEMENT, replies.statement_followup(followup, turn.language))


def _finish_without_assessment(
    turn: Turn, pending: PendingEscalation, statement: _Statement, text: str, *, failure: _Unavailable,
) -> ChatReply:
    """A summary from an earlier turn still stands; only this turn's text went unread."""
    if statement.summary:
        outcome = _outcome(pending, "handoff_statement_available", failure_class=failure)
    else:
        outcome = _outcome(pending, "handoff_statement_unavailable", failure_class=failure)
    return _finish(turn, pending, statement, text, events=[outcome])


def _finish(
    turn: Turn, pending: PendingEscalation, statement: _Statement, text: str | None,
    *, status: handoffs.StatementStatus | None = None, events: Sequence[tuple[str, dict]] | None = None,
    noted: Sequence[tuple[str, dict]] = (),
) -> ChatReply:
    """`events`: the outcome to log once the hand-off is claimed (after
    `noted`); by default the one its status implies.
    """
    if status is None:
        status = handoffs.StatementStatus.GIVEN if statement.summary else handoffs.StatementStatus.SUMMARY_UNAVAILABLE
    if events is None:
        events = [
            _outcome(pending, "handoff_statement_available") if status == handoffs.StatementStatus.GIVEN
            else _outcome(pending, "handoff_statement_unavailable", failure_class=_Unavailable.EMPTY_SUMMARY)
        ]
    handoff = handoffs.with_statement(pending.handoff, status=status, summary=statement.summary, facts=statement.facts)
    case = turn.case
    return finish_pending_escalation(
        turn, pending, handoff, claimed_events=[*noted, *events], append_statement=text,
        statement_facts=statement.to_dict(), expected_statement_counts=(case.statement_followups, case.statement_declines),
    )
