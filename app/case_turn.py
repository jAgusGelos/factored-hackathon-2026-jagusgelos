"""One turn of a dispute conversation: the case being handled, the reply to
send, and the moves every conversation step shares (a compare-and-set
transition, the answer when another request moved the case first, and
escalation).

Escalation has two phases (statement-before-handoff AD-2): unless the
customer already explained the charge, `finish_escalated` only holds the
decided escalation and asks what happened; `finish_pending_escalation`
hands it off once the statement step (`app/statement.py`) is done.

Below `app/credit.py`, `app/explanation.py`, `app/statement.py` and
`app/state_machine.py`: this module never imports any of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypedDict

from app import cases, handoffs, llm, register, replies
from app.auth import Session
from app.case_model import (
    OPEN_STATES,
    TERMINAL_STATES,
    CaseEvaluation,
    CaseState,
    EscalationReason,
    ReportedCharge,
)
from app.charge_search import ChargeOption, charge_option, iso_day, offered_charges
from app.llm import Language
from app.policy import (
    MAX_CLARIFICATION_ROUNDS,
    DisputeReason,
    ProtectiveAction,
    ProtectiveDecision,
    fraud_score_flagged,
    protective_action,
)
from app.transactions import TransactionCandidate


class ChatReply(TypedDict):
    # None only when a retried first message died before its case existed
    # (state_machine._abandoned_turn_reply).
    case_id: str | None
    state: CaseState
    customer_id: str
    reply: str
    options: list[ChargeOption]
    human_available: bool
    # On every reply about an escalated case (the escalating turn, and any
    # later, lost-race or abandoned-turn reply); None otherwise.
    escalation: replies.EscalationNotice | None


@dataclass(frozen=True)
class Turn:
    session: Session
    case: cases.Case
    language: Language
    correlation_id: str
    db_path: Path | None
    # A menu or button turn (a tapped charge or quick-reply): its replies are
    # the validated templates, with no model call (AD-9). Typed text is not.
    from_menu: bool = False
    # The customer asked for a person this turn and the agent tries once more
    # (plan.md AD-8): every non-terminal move unlocks the handoff in its own
    # compare-and-set, and the reply ends with the offer.
    human_requested: bool = False

    @property
    def report(self) -> ReportedCharge:
        return ReportedCharge.from_case(self.case)

    def log_event(self, event_type: str, payload: dict) -> None:
        cases.log_event(self.correlation_id, self.case.case_id, event_type, payload, db_path=self.db_path)

    def reply(
        self, state: CaseState, text: str, options: list[ChargeOption] | None = None,
        *, escalation: replies.EscalationNotice | None = None,
    ) -> ChatReply:
        """`escalation`: the escalating turn's own notice; any other reply in
        `escalated` gets the one built from the stored reason. After a request
        for a person that this turn deferred, the text ends with the offer,
        only when the case really is unlocked (a lost race may have left it
        locked).
        """
        current = cases.get_case(self.case.case_id, db_path=self.db_path)
        human_available = state not in TERMINAL_STATES and current is not None and human_handoff_available(current)
        if self.human_requested and human_available:
            text = f"{text} {replies.HUMAN_OFFER[self.language]}"
            self.log_event("human_request_deferred", {"state": state, "offer": True})
        cases.log_message(self.case.case_id, "agent", text, db_path=self.db_path)
        if escalation is None and state == CaseState.ESCALATED and current is not None:
            escalation = escalation_of(current, self.language)
        return {
            "case_id": self.case.case_id,
            "state": state,
            "customer_id": self.session.customer_id,
            "reply": text,
            "options": options or [],
            "human_available": human_available,
            "escalation": escalation,
        }

    def generate_reply(self, context: llm.PromptContext, *, fallback: str) -> str:
        if self.from_menu:
            self.log_event("nlg_skipped_for_menu", {"scene": str(context["case_state"])})
            return fallback
        try:
            reply = llm.generate_response(context, language=self.language)
        except llm.LLMUnavailable as exc:
            self.log_event("llm_unavailable", llm.failure_payload("generate_response", exc))
            return fallback
        forms = register.runtime_findings(reply, self.language)
        if forms:
            # Closed-list words, never the customer's text: which forms to tune.
            self.log_event(
                "nlg_reply_replaced",
                {"reason": "register", "scene": str(context["case_state"]), "forms": sorted(set(forms))[:5]},
            )
            return fallback
        return reply


def human_handoff_available(case: cases.Case) -> bool:
    return case.handoff_unlocked or case.clarification_rounds >= MAX_CLARIFICATION_ROUNDS


def escalation_of(case: cases.Case, language: Language) -> replies.EscalationNotice | None:
    """The escalation object of any reply about an already escalated case
    (a later message, a lost race, an abandoned turn), from the stored reason.
    It names no charge: only the escalating turn knows the customer identified
    one (plan.md AD-5).
    """
    if case.state != CaseState.ESCALATED:
        return None
    # An unknown stored code (a renamed reason) is answered like a legacy
    # NULL row instead of failing every later message on the case.
    reason = (
        EscalationReason(case.escalation_reason)
        if case.escalation_reason in EscalationReason else None
    )
    return replies.escalation_summary(case.case_id, reason, charge=None, language=language)


def reply_for_lost_race(turn: Turn, attempted_state: CaseState) -> ChatReply:
    """Another request moved this case first; report where it is now instead
    of overwriting it.
    """
    current = cases.get_case(turn.case.case_id, db_path=turn.db_path)
    turn.log_event(
        "case_transition_lost_race", {"current_state": current.state, "attempted_state": attempted_state}
    )
    state, text = where_the_case_is(current, turn.language)
    if state in TERMINAL_STATES:
        return turn.reply(state, text)
    fresh = replace(turn, case=current, human_requested=False)
    return fresh.reply(state, text, current_options(fresh))


def where_the_case_is(case: cases.Case, language: Language) -> tuple[CaseState, str]:
    """The case's current state and the message that reports it, for a turn
    that must not move the case (a lost race, an abandoned retried turn).
    """
    state = CaseState(case.state)
    if state in TERMINAL_STATES:
        return state, replies.terminal_case(
            state, case_number=case.case_id, reference=case.resolution_reference, language=language,
        )
    return state, replies.CASE_MOVED_ON[language]


def transition(
    turn: Turn, state: CaseState, *, expected_states: tuple[str, ...] = OPEN_STATES, **fields,
) -> ChatReply | None:
    """Claims the transition (compare-and-set). Returns None when it was
    claimed, or the reply to send when another request got there first.
    By default never from `awaiting_statement`: only the statement step moves
    a case on from there, passing that state explicitly.
    A turn that deferred a request for a person unlocks the handoff in this
    same update, so the next request escalates (plan.md AD-8).
    """
    if turn.human_requested and state not in TERMINAL_STATES:
        fields["unlock_handoff"] = True
    claimed = cases.update_case(
        turn.case.case_id, state=state, expected_states=expected_states, db_path=turn.db_path, **fields
    )
    return None if claimed else reply_for_lost_race(turn, state)


def current_options(turn: Turn) -> list[ChargeOption]:
    """The list the customer can still pick from, re-sent with any reply that
    stays in `selecting` without a new list.
    """
    if turn.case.state != CaseState.SELECTING or not turn.case.offered_transaction_ids:
        return []
    return [charge_option(c) for c in offered_charges(turn.session, turn.case.offered_transaction_ids)]


def _escalation_reply(
    turn: Turn, reason: EscalationReason, *, charge: TransactionCandidate | None, card_blocked: bool = False,
) -> ChatReply:
    # A fixed template, not a model call: the case number, reason, deadline
    # and what the chat can still do are promises, so they come from code.
    text, notice = replies.escalation_notice(
        turn.case.case_id, reason, charge=charge, language=turn.language, card_blocked=card_blocked,
    )
    return turn.reply(CaseState.ESCALATED, text, escalation=notice)


def _protection(
    reason: DisputeReason | None, charge: TransactionCandidate | None, *, high_fraud_score: bool,
    facts: Mapping[str, object],
) -> ProtectiveDecision:
    """AD-14's protective card block for an escalation about `charge`."""
    return protective_action(
        reason=reason, facts=facts, high_fraud_score=high_fraud_score,
        channel=charge.channel if charge is not None else None,
    )


def _protected_handoff(handoff: dict, protection: ProtectiveDecision) -> dict:
    if protection.action == ProtectiveAction.CARD_BLOCK:
        return handoffs.with_card_block(handoff, protection)
    return handoff


def _log_protection(turn: Turn, protection: ProtectiveDecision) -> bool:
    """Logs the SIMULATED card block once the escalation is claimed (no real
    card system is called). True when the card was blocked.
    """
    if protection.action != ProtectiveAction.CARD_BLOCK:
        return False
    turn.log_event(
        "simulated_card_block",
        {"trigger": "escalation", "signals": [str(s) for s in protection.signals], "simulated": True},
    )
    return True


def charge_prompt_context(turn: Turn, state: str, charge: TransactionCandidate | None) -> llm.PromptContext:
    """The allowlisted facts of the charge a step asks the model about (AD-5)."""
    if charge is None:
        return llm.build_prompt_context(case_state=state, language=turn.language)
    return llm.build_prompt_context(
        case_state=state, language=turn.language,
        candidate_amount=charge.amount, candidate_currency=charge.currency,
        candidate_date=iso_day(charge), candidate_merchant_name=charge.merchant_name,
        candidate_merchant_category=charge.merchant_category, candidate_channel=charge.channel,
    )


def force_escalation(
    turn: Turn, *, event_type: str, failed_call: str, action_taken: str, error: llm.LLMUnavailable | None = None,
    charge: TransactionCandidate | None = None,
) -> ChatReply:
    """`error`: the model failure that forced it, if any; one stopped by the
    turn's deadline is logged with `"cause": "deadline"`. `charge`: one the
    customer already identified (and the caller already looked up), to name
    in the notice.
    """
    turn.log_event(event_type, llm.failure_payload(failed_call, error) if error else {"call": failed_call})
    handoff = handoffs.service_failure(action_taken, turn.report, charge).to_dict()
    reason = EscalationReason.SERVICE_ISSUE
    lost = transition(turn, CaseState.ESCALATED, handoff=handoff, escalation_reason=reason)
    if lost:
        return lost
    turn.log_event("case_escalated", handoff)
    return _escalation_reply(turn, reason, charge=charge)


@dataclass(frozen=True)
class PendingEscalation:
    """An escalation decided in code and held while the customer gives their
    statement (`cases.Case.pending_escalation`): the exact handoff and reason
    it will be handed off with, and the charge its notice names. The charge
    is a snapshot (every field but the fraud score), so finishing the
    escalation never reads the fixture (a failed read there must not become a SERVICE_ISSUE).
    """

    reason: EscalationReason
    handoff: dict
    charge: TransactionCandidate | None
    # The snapshot drops the fraud score; AD-14's block only needs this flag.
    high_fraud_score: bool = False

    def to_dict(self) -> dict:
        return {
            "reason": str(self.reason),
            "handoff": self.handoff,
            "charge": None if self.charge is None else self.charge.to_snapshot(),
            "high_fraud_score": self.high_fraud_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PendingEscalation:
        snapshot = data["charge"]
        charge = None if snapshot is None else TransactionCandidate.from_snapshot(snapshot)
        return cls(
            reason=EscalationReason(data["reason"]), handoff=data["handoff"], charge=charge,
            high_fraud_score=data.get("high_fraud_score", False),
        )


def finish_escalated(
    turn: Turn, evaluation: CaseEvaluation, report: ReportedCharge,
    *, expected_offered: tuple[str, ...] | None = None, drop_proposed_match: bool = False,
    charge: TransactionCandidate | None = None, account_given: bool = False,
    claimed_reason: DisputeReason | None = None,
) -> ChatReply:
    """Escalates at once only when `account_given`: the customer already
    explained the charge in this case. Otherwise the escalation is held as
    pending in the same compare-and-set that would have escalated, the case
    waits in `awaiting_statement` and the reply asks what happened (a fixed
    text, no model call); `app/statement.py` hands it off later with this
    same reason and handoff.

    `drop_proposed_match`: the customer rejected the proposed charge, so it
    stays in the handoff evidence but is no longer the case's match.
    `charge`: a charge the customer identified that the verdict does not carry
    (e.g. the one they confirmed, when its re-verification failed); otherwise
    the notice names the verdict's own match, if any.
    `claimed_reason`: the reason an account the case does not store names (a
    text that explained the charge and asked for a person): only AD-14's
    protective block reads it.
    """
    if evaluation.handoff is None:
        raise ValueError(f"Escalation without a handoff record (case {turn.case.case_id})")
    reason = evaluation.customer_reason
    if reason is None:
        raise ValueError(f"Escalation without a customer reason (case {turn.case.case_id})")
    handoff = evaluation.handoff.to_dict()
    matched = evaluation.matched_transaction
    notice_charge = charge if charge is not None else matched
    fields = dict(
        matched_transaction_id=matched.transaction_id if matched is not None else None,
        clear_fields=("matched_transaction_id",) if drop_proposed_match else (),
        expected_offered_transaction_ids=expected_offered, **report.update_fields(),
    )
    high_fraud_score = notice_charge is not None and fraud_score_flagged(notice_charge.fraud_score)
    if not account_given:
        pending = PendingEscalation(reason, handoff, notice_charge, high_fraud_score=high_fraud_score)
        return _ask_for_statement(turn, pending, fields)
    protection = _protection(
        claimed_reason or report.reason, notice_charge, high_fraud_score=high_fraud_score, facts={},
    )
    handoff = _protected_handoff(handoff, protection)
    lost = transition(turn, CaseState.ESCALATED, handoff=handoff, escalation_reason=reason, **fields)
    if lost:
        return lost
    # The one record of why no statement was asked (the eval reads it).
    turn.log_event("handoff_statement_skipped", {"reason": "account_given", "escalation_reason": reason})
    card_blocked = _log_protection(turn, protection)
    turn.log_event("case_escalated", handoff)
    return _escalation_reply(turn, reason, charge=notice_charge, card_blocked=card_blocked)


def _ask_for_statement(turn: Turn, pending: PendingEscalation, fields: dict) -> ChatReply:
    # The question is the whole reply: a request for a person this turn
    # deferred gets no "ask again" offer, the next step hands the case off.
    turn = replace(turn, human_requested=False)
    lost = transition(turn, CaseState.AWAITING_STATEMENT, pending_escalation=pending.to_dict(), **fields)
    if lost:
        return lost
    # Closed values only: never the customer's words.
    turn.log_event(
        "handoff_statement_requested",
        {"pending_escalation_reason": pending.reason, "source_state": turn.case.state},
    )
    return turn.reply(CaseState.AWAITING_STATEMENT, replies.ASK_FOR_STATEMENT[turn.language])


def finish_pending_escalation(
    turn: Turn, pending: PendingEscalation, handoff: dict, *, claimed_events: Sequence[tuple[str, dict]] = (),
    facts: Mapping[str, object] | None = None, **fields,
) -> ChatReply:
    """Hands off the escalation the statement step held, with its own reason
    and `handoff` (the pending one plus the statement fields), from
    `awaiting_statement` only: a stale or concurrent statement turn loses the
    compare-and-set instead of escalating twice. `claimed_events`: the
    statement step's outcome, logged only once the hand-off is claimed.
    `facts`: the statement's key facts, which can trigger AD-14's protective
    card block. `fields`: the statement step's own columns and guards
    (`cases.update_case`).
    """
    protection = _protection(
        turn.report.reason, pending.charge, high_fraud_score=pending.high_fraud_score, facts=facts or {},
    )
    handoff = _protected_handoff(handoff, protection)
    lost = transition(
        turn, CaseState.ESCALATED, expected_states=(CaseState.AWAITING_STATEMENT,), handoff=handoff,
        escalation_reason=pending.reason, **fields,
    )
    if lost:
        return lost
    for event_type, payload in claimed_events:
        turn.log_event(event_type, payload)
    card_blocked = _log_protection(turn, protection)
    turn.log_event("case_escalated", handoff)
    return _escalation_reply(turn, pending.reason, charge=pending.charge, card_blocked=card_blocked)


def escalate(
    turn: Turn, evaluation: CaseEvaluation, *, charge: TransactionCandidate | None = None,
    account_given: bool = False, claimed_reason: DisputeReason | None = None,
) -> ChatReply:
    return finish_escalated(
        turn, evaluation, turn.report, charge=charge, account_given=account_given, claimed_reason=claimed_reason,
    )
