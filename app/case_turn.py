"""One turn of a dispute conversation: the case being handled, the reply to
send, and the moves every conversation step shares (a compare-and-set
transition, the answer when another request moved the case first, and
escalation).

Below `app/credit.py`, `app/explanation.py` and `app/state_machine.py`:
this module never imports any of them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypedDict

from app import cases, handoffs, llm, register, replies
from app.auth import Session
from app.case_model import (
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    CaseEvaluation,
    CaseState,
    ReportedCharge,
)
from app.charge_search import ChargeOption, charge_option, offered_charges
from app.llm import Language
from app.policy import MAX_CLARIFICATION_ROUNDS

DEFAULT_CURRENCY = "USD"


class ChatReply(TypedDict):
    # None only when a retried first message died before its case existed
    # (state_machine._abandoned_turn_reply).
    case_id: str | None
    state: CaseState
    customer_id: str
    reply: str
    options: list[ChargeOption]
    human_available: bool


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

    @property
    def report(self) -> ReportedCharge:
        return ReportedCharge.from_case(self.case, DEFAULT_CURRENCY)

    def log_event(self, event_type: str, payload: dict) -> None:
        cases.log_event(self.correlation_id, self.case.case_id, event_type, payload, db_path=self.db_path)

    def reply(self, state: CaseState, text: str, options: list[ChargeOption] | None = None) -> ChatReply:
        cases.log_message(self.case.case_id, "agent", text, db_path=self.db_path)
        return {
            "case_id": self.case.case_id,
            "state": state,
            "customer_id": self.session.customer_id,
            "reply": text,
            "options": options or [],
            "human_available": state not in TERMINAL_STATES and self._handoff_unlocked_now(),
        }

    def _handoff_unlocked_now(self) -> bool:
        current = cases.get_case(self.case.case_id, db_path=self.db_path)
        return current is not None and human_handoff_available(current)

    def generate_reply(self, context: llm.PromptContext, *, fallback: str) -> str:
        if self.from_menu:
            self.log_event("nlg_skipped_for_menu", {"scene": str(context["case_state"])})
            return fallback
        try:
            reply = llm.generate_response(context, language=self.language)
        except llm.LLMUnavailable as exc:
            self.log_event("llm_unavailable", llm.failure_payload("generate_response", exc))
            return fallback
        if register.runtime_findings(reply, self.language):
            self.log_event("nlg_reply_replaced", {"reason": "register", "scene": str(context["case_state"])})
            return fallback
        return reply


def human_handoff_available(case: cases.Case) -> bool:
    return case.handoff_unlocked or case.clarification_rounds >= MAX_CLARIFICATION_ROUNDS


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
    fresh = replace(turn, case=current)
    return turn.reply(state, text, current_options(fresh))


def where_the_case_is(case: cases.Case, language: Language) -> tuple[CaseState, str]:
    """The case's current state and the message that reports it, for a turn
    that must not move the case (a lost race, an abandoned retried turn).
    """
    state = CaseState(case.state)
    if state in TERMINAL_STATES:
        return state, replies.terminal_case(state, case.resolution_reference, language)
    return state, replies.CASE_MOVED_ON[language]


def transition(
    turn: Turn, state: CaseState, *, expected_states: tuple[str, ...] = NON_TERMINAL_STATES, **fields,
) -> ChatReply | None:
    """Claims the transition (compare-and-set). Returns None when it was
    claimed, or the reply to send when another request got there first.
    """
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


def force_escalation(
    turn: Turn, *, event_type: str, failed_call: str, action_taken: str, error: llm.LLMUnavailable | None = None,
) -> ChatReply:
    """`error`: the model failure that forced it, if any; one stopped by the
    turn's deadline is logged with `"cause": "deadline"`.
    """
    turn.log_event(event_type, llm.failure_payload(failed_call, error) if error else {"call": failed_call})
    handoff = handoffs.service_failure(action_taken).to_dict()
    lost = transition(turn, CaseState.ESCALATED, handoff=handoff)
    if lost:
        return lost
    turn.log_event("case_escalated", handoff)
    return turn.reply(CaseState.ESCALATED, llm.DETERMINISTIC_FALLBACK_MESSAGE[turn.language])


def finish_escalated(
    turn: Turn, evaluation: CaseEvaluation, report: ReportedCharge,
    *, expected_offered: tuple[str, ...] | None = None, drop_proposed_match: bool = False,
) -> ChatReply:
    """`drop_proposed_match`: the customer rejected the proposed charge, so it
    stays in the handoff evidence but is no longer the case's match.
    """
    if evaluation.handoff is None:
        raise ValueError(f"Escalation without a handoff record (case {turn.case.case_id})")
    handoff = evaluation.handoff.to_dict()
    matched = evaluation.matched_transaction
    lost = transition(
        turn, CaseState.ESCALATED, handoff=handoff,
        matched_transaction_id=matched.transaction_id if matched is not None else None,
        clear_fields=("matched_transaction_id",) if drop_proposed_match else (),
        expected_offered_transaction_ids=expected_offered, **report.update_fields(),
    )
    if lost:
        return lost
    turn.log_event("case_escalated", handoff)
    context = llm.build_prompt_context(case_state=CaseState.ESCALATED, language=turn.language)
    fallback = replies.ESCALATED[turn.language]
    reply = turn.generate_reply(context, fallback=fallback)
    if "?" in reply:
        # The chat is over once a case is handed off (every later message gets
        # the terminal reply), so a closing question would be left unanswered.
        turn.log_event("escalation_reply_replaced", {"reason": "asks_a_question"})
        reply = fallback
    return turn.reply(CaseState.ESCALATED, reply)


def escalate(turn: Turn, evaluation: CaseEvaluation) -> ChatReply:
    return finish_escalated(turn, evaluation, turn.report)
