"""Dispute conversation state machine (AD-3).

Guard functions here are thin wrappers around `app/policy.py`'s AD-11 rules —
this module owns the STATE TRANSITIONS, `app/policy.py` owns the THRESHOLDS.
Every guard function that reads customer data takes only a `Session`
(`app/auth.py`) via `app/transactions.py` — never a bare `customer_id`.

The agent tries first: a request to talk to a person is honored once the
customer has given details (amount, date or merchant) that the agent still
could not resolve (`cases.Case.handoff_unlocked`, set by `_offer` /
`_ask_for_details`), or once the agent has used up its clarification rounds.
Before that it answers with a charge list or asks again, and each deferral
spends a round, so insisting always reaches a person eventually. Policy
escalations (fraud score, amount, status...) are not affected.

States (stored per case; every transition is a compare-and-set):
  awaiting_report -> confirming | selecting | escalated   (first report, AD-11)
  selecting       -> resolved_auto | escalated  (customer picks a listed charge; policy decides)
  selecting       -> escalated  ("not in the list", or still ambiguous after
                                 MAX_CLARIFICATION_ROUNDS turns with nothing new)
  clarifying      -> ...        (only when the customer has no charges to list)
  selecting / confirming -> awaiting_explanation  (charge identified and policy-eligible)
  awaiting_explanation   -> resolved_auto  (convincing explanation + policy re-check)
  awaiting_explanation   -> escalated      (not convincing, or a reason a person must handle)
  confirming      -> selecting      (customer says it is not that charge)
  confirming      -> escalated      (customer asks for a human)

`selecting` (Milestone 8) replaces the free-text "tell me the amount and date"
question: the customer is shown their OWN charges (`app/charge_search.py`) and
picks one. A pick is an explicit identification by the customer, so it counts
as the AD-12 confirmation; the picked id must be one of the charges actually
offered AND belong to the session (`get_own_transaction`), and AD-11 still
decides resolve vs escalate in code. A transaction is never credited twice.

`handle_message()` orchestrates one turn: quick-reply actions and taps are
handled directly; free text goes through NLU entity extraction
(`app/llm.py`) -> `evaluate_case()` -> grounded NLG. An exhausted LLM retry
budget (`llm.LLMUnavailable`) or a failed fixture lookup (`duckdb.Error`)
forces escalation with the NFR's deterministic fallback message, never a
crash or a hallucinated answer.

Two databases are in play and must never be conflated: `db_path` below is the
APP db (SQLite sessions/cases, `config.APP_DB_PATH`) and is only ever passed to
`app/cases.py`; every fixture read in `app/transactions.py` uses its own
`config.FIXTURE_DB_PATH` default.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TypedDict

import duckdb

from app import cases, classifier, config, handoffs, llm, replies
from app.auth import Session
from app.case_model import (
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    CaseEvaluation,
    CaseState,
    CustomerAction,
    ReportedCharge,
)
from app.charge_search import (
    ChargeOption,
    ChargeSearch,
    ListFilter,
    charge_option,
    find_charges,
    iso_day,
    matching_charges,
    offered_charges,
    recent_charges,
    txn_day,
)
from app.llm import Language, PromptScene
from app.policy import (
    ABUSE_GUARD_WINDOW_DAYS,
    CREDIT_WINDOW_DAYS,
    DISPUTE_COMPLAINT_CATEGORY,
    DUPLICATE_WINDOW_DAYS,
    MATCH_DATE_TOLERANCE_DAYS,
    MAX_AUTO_CREDIT_TOTAL_USD,
    MAX_CASE_TURNS,
    MAX_CLARIFICATION_ROUNDS,
    MAX_EXPLANATION_ATTEMPTS,
    MAX_UNRECOGNIZED_AUTO_CREDITS,
    MIN_EXPLANATION_WORDS,
    DisputeContext,
    DisputeReason,
    ExplanationAssessment,
    ExplanationVerdict,
    MatchOutcome,
    ResolutionDecision,
    credit_key,
    effective_amount_usd,
    evaluate_explanation,
    evaluate_match,
    evaluate_resolution,
    match_amount_tolerance,
)
from app.transactions import (
    CustomerProfile,
    TransactionCandidate,
    count_own_charges_at_merchant,
    count_prior_complaints,
    find_own_duplicate_twins,
    get_case_history,
    get_customer_profile,
    get_own_transaction,
    search_own_transactions,
)

__all__ = [
    "CaseEvaluation", "CaseState", "ChatReply", "CustomerAction", "evaluate_case",
    "evaluate_transaction", "handle_message",
]

_COUNTRY_CURRENCY = {"México": "MXN", "Colombia": "COP", "Argentina": "ARS"}
_DEFAULT_CURRENCY = "USD"

IDENTIFIED_BY_REPORT = "Se localizó una transacción que coincide con el monto y la fecha reportados."
IDENTIFIED_BY_PICK = "El cliente eligió este cargo de la lista de sus movimientos."
IDENTIFIED_BY_MERCHANT = "El cliente nombró el comercio y es su único cargo que coincide."
IDENTIFIED_BY_CONFIRMATION = "El cliente confirmó el cargo propuesto."
IDENTIFIED_AND_EXPLAINED = "El cliente identificó el cargo y explicó qué pasó."


class ChatReply(TypedDict):
    case_id: str
    state: CaseState
    customer_id: str
    reply: str
    options: list[ChargeOption]
    human_available: bool


# -- Policy verdicts (no side effects) ----------------------------------------


def evaluate_case(
    session: Session,
    *,
    reported_amount: float,
    reported_date: date,
    currency: str,
    customer_requested_human: bool = False,
    db_path: Path | None = None,
) -> CaseEvaluation:
    """AD-11 Rows 1-5 for a report with an amount and a date: a single
    confident match gets the screening verdict (the customer has not explained
    yet), anything else is `SELECTING` (the customer has to pick; round
    accounting is the caller's job).
    """
    report = ReportedCharge(reported_amount, reported_date, currency)
    if customer_requested_human:
        return handoffs.human_request(report)
    candidates = search_own_transactions(
        session, reported_amount, reported_date,
        amount_tolerance=match_amount_tolerance(reported_amount),
        date_tolerance_days=MATCH_DATE_TOLERANCE_DAYS, currency=currency,
    )
    if evaluate_match(candidates) == MatchOutcome.AMBIGUOUS:
        return CaseEvaluation(state=CaseState.SELECTING, candidates=tuple(candidates))
    return evaluate_transaction(
        session, candidates[0], report=report, how_identified=IDENTIFIED_BY_REPORT, db_path=db_path,
    )


def _dispute_context(
    session: Session, matched: TransactionCandidate, reason: DisputeReason | None, db_path: Path | None,
) -> DisputeContext:
    """Every policy input for ONE of this session's transactions: its fixture
    history (session-scoped reads) and the credits this system already granted
    the customer (the app db).
    """
    day = txn_day(matched)
    profile = get_customer_profile(session)
    predicted_priority = classifier.predict_priority(
        classifier.build_live_features(
            profile,
            claimed_amount=matched.amount,
            currency=matched.currency,
            prior_complaint_count=count_prior_complaints(session, day),
        )
    )
    twins = find_own_duplicate_twins(session, matched, window_days=DUPLICATE_WINDOW_DAYS)
    pair_credited = bool(twins) and (
        any(cases.credited_case_for_transaction(session.customer_id, t, db_path=db_path) for t in twins)
        or cases.credited_case_for_key(
            session.customer_id, credit_key(matched, DisputeReason.DUPLICATE, twins), db_path=db_path,
        ) is not None
    )
    credits = cases.credit_history(session.customer_id, window_days=CREDIT_WINDOW_DAYS, db_path=db_path)
    return DisputeContext(
        reason=reason,
        as_of=date.fromisoformat(config.DATA_AS_OF),
        customer_status=profile.customer_status if profile is not None else None,
        prior_disputes_in_window=get_case_history(
            session, DISPUTE_COMPLAINT_CATEGORY, day, window_days=ABUSE_GUARD_WINDOW_DAYS
        ),
        classifier_priority=predicted_priority,
        other_charges_at_merchant=(
            count_own_charges_at_merchant(
                session, matched.merchant_name, exclude_transaction_id=matched.transaction_id,
            ) if matched.merchant_name else None
        ),
        duplicate_twins=twins,
        duplicate_pair_credited=pair_credited,
        recent_unrecognized_credits=credits.unrecognized_count,
        recent_credited_usd=credits.total_usd,
    )


def evaluate_transaction(
    session: Session, matched: TransactionCandidate, *, report: ReportedCharge, how_identified: str,
    reason: DisputeReason | None = None, db_path: Path | None = None,
) -> CaseEvaluation:
    """AD-11 Rows 4-5 for ONE identified transaction, which must already be
    known to belong to `session`. The policy inputs are the transaction's own
    amount and date, whichever way the customer identified it.

    Without a `reason` this is the SCREENING verdict: `RESOLVED_AUTO` then
    only means "ask the customer what happened", never "credit it".
    """
    ctx = _dispute_context(session, matched, reason, db_path)
    resolution = evaluate_resolution(matched, ctx)
    if resolution.decision == ResolutionDecision.AUTO_RESOLVE:
        return CaseEvaluation(
            state=CaseState.RESOLVED_AUTO, matched_transaction=matched, candidates=(matched,),
            duplicate_twins=ctx.duplicate_twins,
        )
    return handoffs.ineligible_match(report, matched, resolution.reasons, how_identified=how_identified)


def _infer_currency(profile: CustomerProfile | None) -> str:
    if profile is None or profile.country is None:
        return _DEFAULT_CURRENCY
    return _COUNTRY_CURRENCY.get(profile.country, _DEFAULT_CURRENCY)


# -- One turn ------------------------------------------------------------------


@dataclass(frozen=True)
class _Turn:
    session: Session
    case: cases.Case
    language: Language
    correlation_id: str
    db_path: Path | None

    @property
    def report(self) -> ReportedCharge:
        return ReportedCharge.from_case(self.case, _DEFAULT_CURRENCY)

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
        return current is not None and _human_handoff_available(current)

    def generate_reply(self, context: llm.PromptContext, *, fallback: str) -> str:
        try:
            return llm.generate_response(context, language=self.language)
        except llm.LLMUnavailable:
            self.log_event("llm_unavailable", {"call": "generate_response"})
            return fallback


def _load_or_create_case(
    session: Session, case_id: str | None, language: Language, db_path: Path | None
) -> cases.Case:
    if case_id is not None:
        case = cases.get_case_for_session(case_id, session.customer_id, db_path=db_path)
        if case is not None:
            return case
    return cases.create_case(session.customer_id, language, db_path=db_path)


def _reply_for_lost_race(turn: _Turn, attempted_state: CaseState) -> ChatReply:
    """Another request moved this case first; report where it is now instead
    of overwriting it.
    """
    current = cases.get_case(turn.case.case_id, db_path=turn.db_path)
    turn.log_event(
        "case_transition_lost_race", {"current_state": current.state, "attempted_state": attempted_state}
    )
    state = CaseState(current.state)
    if state in TERMINAL_STATES:
        return turn.reply(state, replies.terminal_case(state, current.resolution_reference, turn.language))
    fresh = replace(turn, case=current)
    return turn.reply(state, replies.CASE_MOVED_ON[turn.language], _current_options(fresh))


def _transition(
    turn: _Turn, state: CaseState, *, expected_states: tuple[str, ...] = NON_TERMINAL_STATES, **fields,
) -> ChatReply | None:
    """Claims the transition (compare-and-set). Returns None when it was
    claimed, or the reply to send when another request got there first.
    """
    claimed = cases.update_case(
        turn.case.case_id, state=state, expected_states=expected_states, db_path=turn.db_path, **fields
    )
    return None if claimed else _reply_for_lost_race(turn, state)


def _current_options(turn: _Turn) -> list[ChargeOption]:
    """The list the customer can still pick from, re-sent with any reply that
    stays in `selecting` without a new list.
    """
    if turn.case.state != CaseState.SELECTING or not turn.case.offered_transaction_ids:
        return []
    return [charge_option(c) for c in offered_charges(turn.session, turn.case.offered_transaction_ids)]


def _unless_already_credited(turn: _Turn, evaluation: CaseEvaluation, report: ReportedCharge) -> CaseEvaluation:
    if evaluation.state != CaseState.RESOLVED_AUTO:
        return evaluation
    matched = evaluation.matched_transaction
    credited_in = cases.credited_case_for_transaction(
        turn.session.customer_id, matched.transaction_id, db_path=turn.db_path
    )
    return evaluation if credited_in is None else handoffs.already_credited(report, matched, credited_in)


def _policy_verdict(
    turn: _Turn, matched: TransactionCandidate, report: ReportedCharge, how_identified: str,
    *, reason: DisputeReason | None = None,
) -> CaseEvaluation:
    evaluation = evaluate_transaction(
        turn.session, matched, report=report, how_identified=how_identified, reason=reason, db_path=turn.db_path,
    )
    return _unless_already_credited(turn, evaluation, report)


# -- Terminal and intermediate outcomes ----------------------------------------


def _force_escalation(turn: _Turn, *, event_type: str, failed_call: str, action_taken: str) -> ChatReply:
    turn.log_event(event_type, {"call": failed_call})
    handoff = handoffs.service_failure(action_taken).to_dict()
    lost = _transition(turn, CaseState.ESCALATED, handoff=handoff)
    if lost:
        return lost
    turn.log_event("case_escalated", handoff)
    return turn.reply(CaseState.ESCALATED, llm.DETERMINISTIC_FALLBACK_MESSAGE[turn.language])


def _finish_escalated(
    turn: _Turn, evaluation: CaseEvaluation, report: ReportedCharge,
    *, expected_offered: tuple[str, ...] | None = None, drop_proposed_match: bool = False,
) -> ChatReply:
    """`drop_proposed_match`: the customer rejected the proposed charge, so it
    stays in the handoff evidence but is no longer the case's match.
    """
    if evaluation.handoff is None:
        raise ValueError(f"Escalation without a handoff record (case {turn.case.case_id})")
    handoff = evaluation.handoff.to_dict()
    matched = evaluation.matched_transaction
    lost = _transition(
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


def _escalate(turn: _Turn, evaluation: CaseEvaluation) -> ChatReply:
    return _finish_escalated(turn, evaluation, turn.report)


def _simulate_provisional_credit(
    turn: _Turn, matched: TransactionCandidate, reference: str, reason: DisputeReason,
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
    return cases.CreditGrant(
        key=credit_key(matched, reason, twins), reason=reason,
        amount_usd=effective_amount_usd(matched), window_days=CREDIT_WINDOW_DAYS,
        max_unrecognized=MAX_UNRECOGNIZED_AUTO_CREDITS, max_total_usd=MAX_AUTO_CREDIT_TOTAL_USD,
        unrecognized_reason=DisputeReason.UNRECOGNIZED,
    )


def _finish_resolved(
    turn: _Turn, matched: TransactionCandidate, *, reason: DisputeReason, twins: tuple[str, ...],
    expected_states: tuple[CaseState, ...], expected_match: str | None = None,
) -> ChatReply:
    """Only ever called after a verdict WITH a reason. The credit limits are
    checked again inside the claiming UPDATE (`cases.CreditGrant`), so a second
    case of the same customer running at the same time cannot slip past them.
    """
    reference = f"REF-{uuid.uuid4().hex[:10].upper()}"
    # The transition is claimed BEFORE the credit is logged: of two concurrent
    # requests exactly one flips the case to resolved_auto and issues a credit.
    try:
        claimed = cases.update_case(
            turn.case.case_id, state=CaseState.RESOLVED_AUTO, expected_states=expected_states,
            expected_matched_transaction_id=expected_match, matched_transaction_id=matched.transaction_id,
            resolution_reference=reference, dispute_reason=reason,
            credit=_credit_grant(matched, reason, twins), db_path=turn.db_path,
        )
    except cases.DuplicateCreditError:
        credited_in = cases.credited_case_for_transaction(
            turn.session.customer_id, matched.transaction_id, db_path=turn.db_path
        )
        return _escalate(turn, handoffs.already_credited(turn.report, matched, credited_in or "desconocido"))
    if not claimed:
        current = cases.get_case(turn.case.case_id, db_path=turn.db_path)
        if current.state == turn.case.state and current.matched_transaction_id == turn.case.matched_transaction_id:
            # Nothing else moved the case: the credit limits refused it.
            turn.log_event("credit_limit_reached", {"matched_transaction_id": matched.transaction_id})
            return _escalate(turn, handoffs.credit_limit_reached(turn.report, matched))
        return _reply_for_lost_race(turn, CaseState.RESOLVED_AUTO)
    _simulate_provisional_credit(turn, matched, reference, reason)
    turn.log_event(
        "case_resolved",
        {
            "matched_transaction_id": matched.transaction_id, "amount": matched.amount,
            "currency": matched.currency, "resolution_reference": reference, "reason": reason,
        },
    )
    context = llm.build_prompt_context(
        case_state=CaseState.RESOLVED_AUTO, language=turn.language,
        candidate_amount=matched.amount, candidate_currency=matched.currency,
        candidate_date=iso_day(matched), candidate_merchant_name=matched.merchant_name,
        resolution_reference=reference, dispute_reason=reason,
    )
    fallback = replies.resolved(reference, reason, turn.language)
    reply = turn.generate_reply(context, fallback=fallback)
    if reference not in reply:
        # A resolution message without the case reference is useless to the
        # customer; never send one, whatever the model wrote.
        turn.log_event("resolution_reply_replaced", {"reason": "reference_missing"})
        reply = fallback
    elif reason == DisputeReason.UNRECOGNIZED and "bloque" not in reply.lower():
        # The customer must learn their card was blocked and the credit is
        # provisional; a reply that skips it is replaced.
        turn.log_event("resolution_reply_replaced", {"reason": "card_block_missing"})
        reply = fallback
    return turn.reply(CaseState.RESOLVED_AUTO, reply)


def _finish_confirming(turn: _Turn, matched: TransactionCandidate, report: ReportedCharge) -> ChatReply:
    lost = _transition(
        turn, CaseState.CONFIRMING, matched_transaction_id=matched.transaction_id, **report.update_fields()
    )
    if lost:
        return lost
    turn.log_event(
        "case_confirming",
        {"matched_transaction_id": matched.transaction_id, "amount": matched.amount, "currency": matched.currency},
    )
    context = llm.build_prompt_context(
        case_state=CaseState.CONFIRMING, language=turn.language,
        candidate_amount=matched.amount, candidate_currency=matched.currency,
        candidate_date=iso_day(matched), candidate_merchant_name=matched.merchant_name,
    )
    fallback = replies.confirmation_question(matched, turn.language)
    reply = turn.generate_reply(context, fallback=fallback)
    if not replies.names_the_facts(reply, matched, turn.language):
        turn.log_event("confirmation_reply_replaced", {"reason": "facts_missing"})
        reply = fallback
    return turn.reply(CaseState.CONFIRMING, reply)


def _propose_or_escalate(
    turn: _Turn, matched: TransactionCandidate, report: ReportedCharge, how_identified: str,
) -> ChatReply:
    """An identified charge: ask the customer to confirm it if policy would
    resolve it (AD-12), otherwise hand it off.
    """
    evaluation = _policy_verdict(turn, matched, report, how_identified)
    if evaluation.state == CaseState.RESOLVED_AUTO:
        return _finish_confirming(turn, matched, report)
    return _finish_escalated(turn, evaluation, report)


def _human_handoff_available(case: cases.Case) -> bool:
    return case.handoff_unlocked or case.clarification_rounds >= MAX_CLARIFICATION_ROUNDS


def _unlocks_handoff(report: ReportedCharge, search: ChargeSearch, *, spend_round: bool) -> bool:
    """The agent has shown it cannot find the charge from what the customer
    said: they gave details and either nothing matched them or the turn went
    nowhere (a spent round, e.g. after rejecting the proposed charge).
    """
    return report.has_details and (search.list_filter == ListFilter.FALLBACK_RECENT or spend_round)


def _offer(
    turn: _Turn, search: ChargeSearch, report: ReportedCharge, *, spend_round: bool,
    human_deferred: bool = False,
) -> ChatReply:
    """Shows the customer their own charges to pick from (AD-11 Row 3's
    clarification, as a list instead of a free-text question). A round is
    only spent when the turn brought nothing new. Entering `selecting` drops
    any previously proposed match: nothing is matched until they pick.
    """
    rounds = turn.case.clarification_rounds + (1 if spend_round else 0)
    if not search.charges:
        return _ask_for_details(turn, report, spend_round=spend_round)
    offered = tuple(c.transaction_id for c in search.charges)
    lost = _transition(
        turn, CaseState.SELECTING, offered_transaction_ids=offered, add_clarification_round=spend_round,
        unlock_handoff=_unlocks_handoff(report, search, spend_round=spend_round),
        clear_fields=("matched_transaction_id",), **report.update_fields(),
    )
    if lost:
        return lost
    turn.log_event(
        "charges_offered",
        {"list_filter": search.list_filter, "offered_transaction_ids": list(offered), "clarification_rounds": rounds},
    )
    context = llm.build_prompt_context(
        case_state=PromptScene.HUMAN_DEFERRED if human_deferred else CaseState.SELECTING,
        language=turn.language, candidate_count=len(search.charges), list_filter=search.list_filter,
    )
    fallback = (
        replies.HUMAN_DEFERRED[turn.language] if human_deferred
        else replies.CHARGE_LIST[turn.language][search.list_filter]
    )
    text = turn.generate_reply(context, fallback=fallback)
    return turn.reply(CaseState.SELECTING, text, [charge_option(c) for c in search.charges])


def _ask_for_details(turn: _Turn, report: ReportedCharge, *, spend_round: bool) -> ChatReply:
    """Only for a customer with no outgoing charges to list at all."""
    lost = _transition(
        turn, CaseState.CLARIFYING, add_clarification_round=spend_round, unlock_handoff=report.has_details,
        clear_fields=("matched_transaction_id",), **report.update_fields(),
    )
    if lost:
        return lost
    turn.log_event("case_clarifying", {"reason": "no_charges_to_list"})
    context = llm.build_prompt_context(
        case_state=CaseState.CLARIFYING, language=turn.language, reported_amount=report.amount,
        reported_currency=report.currency,
        reported_date=report.date.isoformat() if report.date is not None else None,
    )
    return turn.reply(CaseState.CLARIFYING, turn.generate_reply(context, fallback=replies.ASK_FOR_DETAILS[turn.language]))


def _introduce(turn: _Turn, scene: PromptScene) -> ChatReply:
    """A greeting gets an introduction and an out-of-scope request (balance,
    loans...) a plain statement of what this channel can do (the challenge's
    "unsupported request: abstain" case). Neither changes the case state or
    spends a round.
    """
    turn.log_event("introduction" if scene == PromptScene.GREETING else "out_of_scope_request", {})
    fallback = replies.WELCOME if scene == PromptScene.GREETING else replies.OUT_OF_SCOPE
    context = llm.build_prompt_context(case_state=scene, language=turn.language)
    reply = turn.generate_reply(context, fallback=fallback[turn.language])
    return turn.reply(CaseState(turn.case.state), reply, _current_options(turn))


# -- Handlers ------------------------------------------------------------------


def _handle_human_request(turn: _Turn) -> ChatReply:
    """Honored once the agent has shown it cannot resolve the case; before
    that the agent keeps trying (the charge list, or the pending
    confirmation) and says it will hand off if it cannot find the charge.
    """
    case = turn.case
    if _human_handoff_available(case):
        if case.state == CaseState.CONFIRMING:
            return _escalate(turn, handoffs.confirmation_outcome(
                turn.report, case, customer_confirmation=str(llm.ConfirmationAnswer.HUMAN),
                action="Cliente solicitó explícitamente hablar con un agente humano.",
                open_question="El cliente prefirió hablar con una persona antes de confirmar el cargo propuesto.",
            ))
        return _escalate(turn, handoffs.human_request(turn.report))
    turn.log_event("human_request_deferred", {"state": case.state})
    if case.state == CaseState.AWAITING_EXPLANATION:
        lost = _transition(
            turn, CaseState.AWAITING_EXPLANATION, expected_states=(CaseState.AWAITING_EXPLANATION,),
            add_clarification_round=True,
        )
        return lost or turn.reply(CaseState.AWAITING_EXPLANATION, replies.HUMAN_DEFERRED_WHILE_EXPLAINING[turn.language])
    if case.state == CaseState.CONFIRMING:
        lost = _transition(turn, CaseState.CONFIRMING, expected_states=(CaseState.CONFIRMING,), add_clarification_round=True)
        if lost:
            return lost
        context = llm.build_prompt_context(case_state=PromptScene.HUMAN_DEFERRED, language=turn.language)
        fallback = replies.HUMAN_DEFERRED_WHILE_CONFIRMING[turn.language]
        return turn.reply(CaseState.CONFIRMING, turn.generate_reply(context, fallback=fallback))
    report = turn.report
    search = find_charges(turn.session, report) if report.has_details else recent_charges(turn.session)
    return _offer(turn, search, report, spend_round=True, human_deferred=True)


_ACTION_ANSWERS = {
    CustomerAction.CONFIRM_YES: llm.ConfirmationAnswer.YES,
    CustomerAction.CONFIRM_NO: llm.ConfirmationAnswer.NO,
}


def _confirmation_answer(turn: _Turn, text: str, action: CustomerAction | None) -> llm.ConfirmationAnswer:
    """Raises `llm.LLMUnavailable` when typed text cannot be classified."""
    if action in _ACTION_ANSWERS:
        return _ACTION_ANSWERS[action]
    return llm.classify_confirmation(text, language=turn.language)


def _handle_confirmation(turn: _Turn, text: str, action: CustomerAction | None = None) -> ChatReply:
    """AD-12: the one bounded confirmation round. Only an explicit "yes" can
    resolve, and even then AD-11 is re-run on the stored transaction, looked
    up again through the session. "Not that one" (or an unclear answer) shows
    the customer their charges around that date instead of giving up.
    """
    case = turn.case
    try:
        answer = _confirmation_answer(turn, text, action)
    except llm.LLMUnavailable:
        return _force_escalation(
            turn, event_type="llm_unavailable", failed_call="classify_confirmation",
            action_taken="El servicio de NLU no respondió al pedir la confirmación del cliente.",
        )
    turn.log_event("confirmation_received", {"answer": str(answer), "via": "button" if action else "text"})
    report = turn.report

    if answer == llm.ConfirmationAnswer.YES:
        return _confirm_proposed_charge(turn, report)
    if answer == llm.ConfirmationAnswer.HUMAN:
        return _handle_human_request(turn)
    if case.clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
        return _finish_escalated(turn, handoffs.confirmation_outcome(
            report, case, customer_confirmation=str(answer),
            action=f"Se propuso al cliente la transacción coincidente y no la confirmó (respuesta: {answer}); "
                   "no quedan rondas de aclaración.",
            open_question="¿Cuál es la transacción que el cliente no reconoce?",
        ), report, drop_proposed_match=True)
    return _offer(turn, _charges_other_than_proposed(turn, report), report, spend_round=True)


def _confirm_proposed_charge(turn: _Turn, report: ReportedCharge) -> ChatReply:
    case = turn.case
    matched = get_own_transaction(turn.session, case.matched_transaction_id) if case.matched_transaction_id else None
    evaluation = (
        _policy_verdict(turn, matched, report, IDENTIFIED_BY_CONFIRMATION)
        if matched is not None else CaseEvaluation(state=CaseState.ESCALATED)
    )
    if evaluation.state == CaseState.RESOLVED_AUTO:
        return _ask_for_explanation(
            turn, matched, expected_states=(CaseState.CONFIRMING,), expected_match=matched.transaction_id,
        )
    turn.log_event("confirmation_reverification_failed", {"state": evaluation.state})
    reasons = evaluation.resolution_reasons or (evaluation.handoff.open_questions if evaluation.handoff else ())
    return _escalate(turn, handoffs.confirmation_outcome(
        report, case, customer_confirmation=str(llm.ConfirmationAnswer.YES),
        action="El cliente confirmó el cargo propuesto, pero la política no permitió auto-resolverlo al re-verificar.",
        open_question="; ".join(reasons) or "No se pudo volver a verificar la transacción propuesta.",
    ))


def _charges_other_than_proposed(turn: _Turn, report: ReportedCharge) -> ChargeSearch:
    proposed = turn.case.matched_transaction_id
    around_date = ReportedCharge(amount=None, date=report.date, currency=report.currency)
    for search in (find_charges(turn.session, around_date), recent_charges(turn.session)):
        others = tuple(c for c in search.charges if c.transaction_id != proposed)
        if others:
            return ChargeSearch(others, search.list_filter)
    return ChargeSearch((), ListFilter.RECENT)


def _handle_selection(turn: _Turn, transaction_id: str) -> ChatReply:
    """The customer tapped one of the charges they were shown. Accepted only if
    the case is still offering that exact list, the id is on it, and it is
    this session's own transaction; anything else changes nothing.
    """
    case = turn.case
    matched = None
    if case.state != CaseState.SELECTING:
        rejection = "not_selecting"
    elif transaction_id not in case.offered_transaction_ids:
        rejection = "not_offered"
    else:
        matched = get_own_transaction(turn.session, transaction_id)
        rejection = None if matched is not None else "not_owned"
    if rejection is not None:
        turn.log_event(
            "selection_rejected", {"transaction_id": transaction_id, "reason": rejection, "state": case.state}
        )
        return turn.reply(CaseState(case.state), replies.SELECTION_UNAVAILABLE[turn.language], _current_options(turn))

    turn.log_event("charge_selected", {"transaction_id": transaction_id})
    evaluation = _policy_verdict(turn, matched, turn.report, IDENTIFIED_BY_PICK)
    if evaluation.state == CaseState.RESOLVED_AUTO:
        # Picking the charge is the customer's identification of it (AD-12);
        # what happened with it comes next.
        return _ask_for_explanation(
            turn, matched, expected_states=(CaseState.SELECTING,), expected_offered=case.offered_transaction_ids,
        )
    return _finish_escalated(turn, evaluation, turn.report, expected_offered=case.offered_transaction_ids)


def _charge_context(turn: _Turn, state: str, matched: TransactionCandidate) -> llm.PromptContext:
    return llm.build_prompt_context(
        case_state=state, language=turn.language,
        candidate_amount=matched.amount, candidate_currency=matched.currency,
        candidate_date=iso_day(matched), candidate_merchant_name=matched.merchant_name,
        candidate_merchant_category=matched.merchant_category, candidate_channel=matched.channel,
    )


def _ask_for_explanation(
    turn: _Turn, matched: TransactionCandidate, *, expected_states: tuple[CaseState, ...],
    expected_offered: tuple[str, ...] | None = None, expected_match: str | None = None,
) -> ChatReply:
    """The charge is identified and policy-eligible: before any credit the
    customer explains, in their own words, what happened with it.
    """
    lost = _transition(
        turn, CaseState.AWAITING_EXPLANATION, expected_states=expected_states,
        expected_offered_transaction_ids=expected_offered, expected_matched_transaction_id=expected_match,
        matched_transaction_id=matched.transaction_id,
    )
    if lost:
        return lost
    turn.log_event("explanation_requested", {"matched_transaction_id": matched.transaction_id})
    fallback = replies.ask_for_explanation(matched, turn.language)
    reply = turn.generate_reply(_charge_context(turn, CaseState.AWAITING_EXPLANATION, matched), fallback=fallback)
    if not replies.names_the_facts(reply, matched, turn.language):
        turn.log_event("explanation_request_replaced", {"reason": "facts_missing"})
        reply = fallback
    return turn.reply(CaseState.AWAITING_EXPLANATION, reply)


_TOO_SHORT = ExplanationAssessment(
    reason=DisputeReason.UNCLEAR, specific=False, consistent=True, contradictions=(),
    summary="Explicación demasiado breve para evaluar.",
)


def _assess(turn: _Turn, explanation: str, matched: TransactionCandidate) -> ExplanationAssessment | None:
    """Raises `llm.LLMUnavailable`. None when the model's answer is unusable."""
    if len(explanation.split()) < MIN_EXPLANATION_WORDS:
        return _TOO_SHORT
    return llm.assess_explanation(explanation, charge=_charge_context(turn, CaseState.AWAITING_EXPLANATION, matched))


def _handle_explanation(turn: _Turn, text: str) -> ChatReply:
    """The customer's account of what happened. The model only assesses it;
    `policy.evaluate_explanation` may ask for one more detail or escalate, and
    otherwise the charge still has to pass the evidence check for the reason
    the explanation names (AD-13) before any credit.
    """
    case = turn.case
    matched = get_own_transaction(turn.session, case.matched_transaction_id) if case.matched_transaction_id else None
    if matched is None:
        return _escalate(turn, handoffs.unidentified_charge(turn.report, case))
    explanation = f"{case.explanation_text}\n{text}" if case.explanation_text else text
    try:
        assessment = _assess(turn, explanation, matched)
    except llm.LLMUnavailable:
        return _force_escalation(
            turn, event_type="llm_unavailable", failed_call="assess_explanation",
            action_taken="El servicio de NLU no respondió al evaluar la explicación del cliente.",
        )
    attempts_left = case.explanation_attempts + 1 < MAX_EXPLANATION_ATTEMPTS
    verdict, why = evaluate_explanation(assessment or _TOO_SHORT, attempts_left=attempts_left)
    turn.log_event(
        "explanation_assessed",
        {"verdict": verdict, "reason": assessment.reason if assessment else None,
         "specific": assessment.specific if assessment else None,
         "consistent": assessment.consistent if assessment else None},
    )
    report = replace(turn.report, reason=assessment.reason if assessment else None)

    if verdict == ExplanationVerdict.NEEDS_DETAIL:
        return _ask_for_more_detail(turn, explanation)
    if verdict == ExplanationVerdict.ESCALATE:
        return _finish_escalated(turn, handoffs.explanation_not_accepted(report, matched, why, assessment), report)
    # The explanation raised no red flag; the evidence check for the reason it
    # names decides (a persuasive story alone never credits anything).
    evaluation = _policy_verdict(turn, matched, report, IDENTIFIED_AND_EXPLAINED, reason=assessment.reason)
    if evaluation.state == CaseState.RESOLVED_AUTO:
        return _finish_resolved(
            turn, matched, reason=assessment.reason, twins=evaluation.duplicate_twins,
            expected_states=(CaseState.AWAITING_EXPLANATION,), expected_match=matched.transaction_id,
        )
    handoff = replace(evaluation.handoff, facts={**evaluation.handoff.facts, **handoffs.explanation_facts(assessment)})
    return _finish_escalated(turn, replace(evaluation, handoff=handoff), report)


def _ask_for_more_detail(turn: _Turn, explanation: str) -> ChatReply:
    lost = _transition(
        turn, CaseState.AWAITING_EXPLANATION, expected_states=(CaseState.AWAITING_EXPLANATION,),
        explanation_text=explanation, add_explanation_attempt=True,
    )
    if lost:
        return lost
    context = llm.build_prompt_context(case_state=PromptScene.EXPLANATION_FOLLOWUP, language=turn.language)
    reply = turn.generate_reply(context, fallback=replies.EXPLANATION_FOLLOWUP[turn.language])
    return turn.reply(CaseState.AWAITING_EXPLANATION, reply)


def _handle_none_of_these(turn: _Turn) -> ChatReply:
    """Not in the list: with no details from the customer yet, the agent asks
    for one and keeps looking; after details, it hands off.
    """
    case = turn.case
    if case.state != CaseState.SELECTING:
        turn.log_event("selection_rejected", {"reason": "none_of_these_outside_selecting", "state": case.state})
        return turn.reply(CaseState(case.state), replies.SELECTION_UNAVAILABLE[turn.language])
    if not turn.report.has_details and case.clarification_rounds < MAX_CLARIFICATION_ROUNDS:
        lost = _transition(turn, CaseState.SELECTING, expected_states=(CaseState.SELECTING,), add_clarification_round=True)
        if lost:
            return lost
        turn.log_event("details_requested", {"reason": "not_in_unfiltered_list"})
        context = llm.build_prompt_context(case_state=PromptScene.ASK_FOR_DETAILS, language=turn.language)
        return turn.reply(
            CaseState.SELECTING, turn.generate_reply(context, fallback=replies.ASK_FOR_ONE_DETAIL[turn.language]),
        )
    return _finish_escalated(
        turn, handoffs.not_in_list(turn.report, case), turn.report, expected_offered=case.offered_transaction_ids,
    )


def handle_message(
    session: Session,
    case_id: str | None,
    text: str,
    *,
    language: Language | str = Language.ES,
    db_path: Path | None = None,
    selected_transaction_id: str | None = None,
    action: CustomerAction | str | None = None,
) -> ChatReply:
    """`case_id=None` starts a new case. An existing `case_id` is only ever
    resumed if it belongs to `session.customer_id` (AD-3) — `cases.CaseOwnershipError`
    propagates to the caller (app/main.py maps it to 403), never silently
    reassigned or ignored. An unrecognized `case_id` (no matching row at all —
    distinct from an ownership conflict) starts a fresh case rather than
    erroring, on the theory that a client only ever gets a `case_id` from a
    prior reply of this same endpoint; the substitution is still logged so
    it's visible in the audit trail, not silently invisible.

    `selected_transaction_id` is a tap on one of the listed charges and
    `action` a quick-reply button; `text` is always what the customer sees
    in their bubble (the button label, for a tap).
    """
    language = Language(language)
    action = CustomerAction(action) if action is not None else None
    case = _load_or_create_case(session, case_id, language, db_path)
    turn = _Turn(session, case, language, uuid.uuid4().hex, db_path)
    if case_id is not None and case.case_id != case_id:
        turn.log_event("unknown_case_id_new_case_started", {"requested_case_id": case_id})
    cases.log_message(case.case_id, "customer", text, db_path=db_path)

    if case.state in TERMINAL_STATES:
        return turn.reply(
            CaseState(case.state), replies.terminal_case(CaseState(case.state), case.resolution_reference, language)
        )
    try:
        if action == CustomerAction.HUMAN:
            return _handle_human_request(turn)
        if selected_transaction_id is not None:
            return _handle_selection(turn, selected_transaction_id)
        if action == CustomerAction.NONE_OF_THESE:
            return _handle_none_of_these(turn)
        if case.state == CaseState.CONFIRMING:
            return _handle_confirmation(turn, text, action)
        if case.state == CaseState.AWAITING_EXPLANATION:
            return _handle_explanation(turn, text)
        return _handle_report(turn, text)
    except duckdb.Error:
        return _force_escalation(
            turn, event_type="fixture_unavailable", failed_call="fixture_lookup",
            action_taken="La consulta a los datos del cliente falló.",
        )


# -- Free-text reports -----------------------------------------------------------


def _merged_report(turn: _Turn, extraction: llm.ExtractedEntities) -> ReportedCharge:
    """This turn's details on top of what the case already has: a follow-up
    that does not restate the amount, date, currency or merchant keeps the
    earlier value.
    """
    case = turn.case
    reported_date = extraction.date or case.reported_date
    return ReportedCharge(
        amount=extraction.amount if extraction.amount is not None else case.reported_amount,
        date=date.fromisoformat(reported_date) if reported_date else None,
        currency=extraction.currency or case.reported_currency or _infer_currency(get_customer_profile(turn.session)),
        merchant=extraction.merchant_hint or case.reported_merchant,
    )


def _brings_new_info(extraction: llm.ExtractedEntities, case: cases.Case) -> bool:
    new_amount = extraction.amount is not None and extraction.amount != case.reported_amount
    new_date = extraction.date is not None and extraction.date != case.reported_date
    new_merchant = bool(extraction.merchant_hint) and (
        extraction.merchant_hint.casefold() != (case.reported_merchant or "").casefold()
    )
    return new_amount or new_date or new_merchant


def _handle_report(turn: _Turn, text: str) -> ChatReply:
    """A free-text turn: extract what the customer said, then either match it
    (AD-11), show them their charges to pick from, or escalate.
    """
    try:
        extraction = llm.extract_entities(text, language=turn.language, today=config.DATA_AS_OF)
    except llm.LLMUnavailable:
        return _force_escalation(
            turn, event_type="llm_unavailable", failed_call="extract_entities",
            action_taken="El servicio de NLU no respondió tras agotar los reintentos.",
        )
    if extraction.parse_failed:
        turn.log_event("extraction_parse_failed", {"call": "extract_entities"})
    has_details = extraction.has_details
    if extraction.wants_human and (_human_handoff_available(turn.case) or not has_details):
        return _handle_human_request(turn)
    if extraction.wants_human:
        # Asked for a person but also gave details: try them first.
        turn.log_event("human_request_deferred", {"state": turn.case.state, "reason": "details_to_try"})
    if not has_details and extraction.intent == llm.ExtractionIntent.GREETING:
        return _introduce(turn, PromptScene.GREETING)
    if not has_details and extraction.intent == llm.ExtractionIntent.OTHER:
        return _introduce(turn, PromptScene.OUT_OF_SCOPE)
    if cases.increment_turn_count(turn.case.case_id, db_path=turn.db_path) > MAX_CASE_TURNS:
        return _escalate(turn, handoffs.turn_limit(turn.report, turn.case))

    report = _merged_report(turn, extraction)
    if not has_details and extraction.intent == llm.ExtractionIntent.SHOW_CHARGES:
        return _offer(turn, recent_charges(turn.session), report, spend_round=False)
    brought_new_info = _brings_new_info(extraction, turn.case)
    can_ask_again = brought_new_info or turn.case.clarification_rounds < MAX_CLARIFICATION_ROUNDS
    if report.is_complete:
        return _handle_full_report(turn, report, spend_round=not brought_new_info, can_ask_again=can_ask_again)
    return _handle_partial_report(
        turn, report, merchant_named_now=bool(extraction.merchant_hint),
        spend_round=not brought_new_info, can_ask_again=can_ask_again,
    )


def _handle_full_report(turn: _Turn, report: ReportedCharge, *, spend_round: bool, can_ask_again: bool) -> ChatReply:
    evaluation = _unless_already_credited(
        turn,
        evaluate_case(
            turn.session, reported_amount=report.amount, reported_date=report.date, currency=report.currency,
            db_path=turn.db_path,
        ),
        report,
    )
    turn.log_event("case_evaluated", {"state": evaluation.state, "candidate_count": len(evaluation.candidates)})
    if evaluation.state == CaseState.RESOLVED_AUTO:
        # Policy-eligible, but never resolved in the same turn as the first
        # report: the customer confirms the matched charge first (AD-12).
        return _finish_confirming(turn, evaluation.matched_transaction, report)
    if evaluation.state == CaseState.ESCALATED:
        return _finish_escalated(turn, evaluation, report)
    if not can_ask_again:
        return _finish_escalated(
            turn, handoffs.ambiguous_match(report, evaluation.candidates, turn.case.clarification_rounds), report,
        )
    matches = matching_charges(turn.session, report)
    search = ChargeSearch(matches, ListFilter.FILTERED) if len(matches) >= 2 else find_charges(turn.session, report)
    return _offer(turn, search, report, spend_round=spend_round)


def _handle_partial_report(
    turn: _Turn, report: ReportedCharge, *, merchant_named_now: bool, spend_round: bool, can_ask_again: bool,
) -> ChatReply:
    if not can_ask_again:
        return _escalate(turn, handoffs.unidentified_charge(report, turn.case))
    search = find_charges(turn.session, report)
    if merchant_named_now and search.matched_on_merchant and len(search.charges) == 1:
        # "El de Uber": exactly one of their charges is at that merchant, so
        # propose it instead of making them pick from a list of one.
        return _propose_or_escalate(turn, search.charges[0], report, IDENTIFIED_BY_MERCHANT)
    return _offer(turn, search, report, spend_round=spend_round)
