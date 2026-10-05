"""Dispute conversation state machine (AD-3).

Guard functions here are thin wrappers around `app/policy.py`'s AD-11 rules —
this module owns the STATE TRANSITIONS, `app/policy.py` owns the THRESHOLDS.
Every guard function that reads customer data takes only a `Session`
(`app/auth.py`) via `app/transactions.py` — never a bare `customer_id`.

The agent tries first, once per request (plan.md AD-8): the first request
to talk to a person keeps the case where it is (charge list, pending
confirmation, explanation), spends no round, unlocks the handoff in the same
compare-and-set and ends the reply with an offer; the second request (typed
or the button) hands the case to a person, after the statement step unless
the customer already explained the charge. The handoff is also unlocked silently when the
customer gave details the agent could not resolve (`_offer` /
`_ask_for_details`) or the clarification rounds are used up, and then the
first request escalates at once. Policy escalations (fraud score, amount,
status...) are not affected and win over a request that came with details.

States (stored per case; every transition is a compare-and-set):
  awaiting_report -> confirming | selecting | escalated   (first report, AD-11)
  selecting       -> awaiting_explanation | escalated  (customer picks a listed charge; screening decides)
  selecting       -> escalated  ("not in the list", or still ambiguous after
                                 MAX_CLARIFICATION_ROUNDS turns with nothing new)
  clarifying      -> ...        (only when the customer has no charges to list)
  selecting / confirming -> awaiting_explanation  (charge identified and policy-eligible)
  awaiting_explanation   -> resolved_auto  (convincing explanation + policy re-check)
  awaiting_explanation   -> escalated      (not convincing, or a reason a person must handle)
  confirming      -> selecting      (customer says it is not that charge)
  confirming      -> escalated      (customer asks for a human)
  any of the above -> awaiting_statement -> escalated
                  (every escalation except a technical failure first asks the
                   customer what happened and why they want the refund, unless
                   they already explained the charge: `app/statement.py`)

`selecting` (Milestone 8) replaces the free-text "tell me the amount and date"
question: the customer is shown their OWN charges (`app/charge_search.py`) and
picks one. A pick is an explicit identification by the customer, so it counts
as the AD-12 confirmation; the picked id must be one of the charges actually
offered AND belong to the session (`get_own_transaction`), and AD-11 still
decides resolve vs escalate in code. A transaction is never credited twice.

`handle_message()` orchestrates one turn: quick-reply actions and taps are
handled directly and answered with templates, never calling the model (AD-9);
free text goes through NLU entity extraction (`app/llm.py`) ->
`evaluate_case()` -> grounded NLG. An exhausted LLM retry
budget (`llm.LLMUnavailable`, also raised once the turn's shared model budget
`llm.turn_deadline()` is used up) or a failed fixture lookup (`duckdb.Error`)
forces escalation with the deterministic escalation notice (the NFR), never a
crash or a hallucinated answer. A quick-reply tapped outside the state it
belongs to (an old button still on screen) changes nothing, and a turn sent
with a `turn_id` is applied at most once (`app/turns.py`).

The explanation step lives in `app/explanation.py` (it receives this
module's `_policy_verdict`), the credit it may grant in `app/credit.py`, and
the customer's statement before any handoff in `app/statement.py`;
what every step shares (`Turn`, the compare-and-set transition, escalation)
is in `app/case_turn.py`.

Two databases are in play and must never be conflated: `db_path` below is the
APP db (SQLite sessions/cases, `config.APP_DB_PATH`) and is only ever passed to
`app/cases.py`; every fixture read in `app/transactions.py` uses its own
`config.FIXTURE_DB_PATH` default.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb

from app import cases, classifier, config, handoffs, llm, replies, turns
from app.auth import Session
from app.case_model import (
    OPEN_STATES,
    TERMINAL_STATES,
    CaseEvaluation,
    CaseState,
    CustomerAction,
    EscalationReason,
    ReportedCharge,
)
from app.case_turn import (
    ChatReply,
    Turn,
    current_options,
    escalate,
    escalation_of,
    finish_escalated,
    force_escalation,
    human_handoff_available,
    transition,
    where_the_case_is,
)
from app.charge_search import (
    ChargeSearch,
    ListFilter,
    charge_option,
    find_charges,
    iso_day,
    matching_charges,
    recent_charges,
    txn_day,
)
from app.explanation import GivenAccount, ask_for_explanation, handle_explanation
from app.llm import Language, PromptScene
from app.policy import (
    ABUSE_GUARD_WINDOW_DAYS,
    AUTO_RESOLVE_REQUIRED_STATUS,
    DISPUTE_COMPLAINT_CATEGORY,
    DUPLICATE_WINDOW_MINUTES,
    MATCH_DATE_TOLERANCE_DAYS,
    MAX_CASE_TURNS,
    MAX_CLARIFICATION_ROUNDS,
    DisputeContext,
    DisputeReason,
    MatchOutcome,
    ResolutionDecision,
    credit_key,
    evaluate_match,
    evaluate_resolution,
    match_amount_tolerance,
)
from app.statement import handle_statement
from app.transactions import (
    CustomerProfile,
    TransactionCandidate,
    count_own_charges_at_merchant,
    count_prior_complaints,
    find_own_duplicate_evidence,
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

# -- Policy verdicts (no side effects) ----------------------------------------


def evaluate_case(
    session: Session,
    *,
    reported_amount: float,
    reported_date: date,
    currency: str | None,
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
        date_tolerance_days=MATCH_DATE_TOLERANCE_DAYS,
        currency=currency or _infer_currency(get_customer_profile(session)),
    )
    if evaluate_match(candidates) == MatchOutcome.AMBIGUOUS:
        return CaseEvaluation(state=CaseState.SELECTING, candidates=tuple(candidates))
    return evaluate_transaction(
        session, candidates[0], report=report, how_identified=handoffs.ChargeIdentification.REPORT, db_path=db_path,
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
    duplicates = find_own_duplicate_evidence(
        session, matched, window_minutes=DUPLICATE_WINDOW_MINUTES, required_status=AUTO_RESOLVE_REQUIRED_STATUS,
    )
    twins = duplicates.twins
    pair_credited = bool(twins) and (
        any(cases.credited_case_for_transaction(session.customer_id, t, db_path=db_path) for t in twins)
        or cases.credited_case_for_key(
            session.customer_id, credit_key(matched, DisputeReason.DUPLICATE, twins), db_path=db_path,
        ) is not None
    )
    credits = cases.credit_history(session.customer_id, db_path=db_path)
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
        repeat_charges=duplicates.repeats,
        duplicate_pair_credited=pair_credited,
        recent_unrecognized_credits=credits.unrecognized_count,
        recent_credited_usd=credits.total_usd,
    )


def evaluate_transaction(
    session: Session, matched: TransactionCandidate, *, report: ReportedCharge,
    how_identified: handoffs.ChargeIdentification,
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


def _load_or_create_case(
    session: Session, case_id: str | None, language: Language, db_path: Path | None
) -> cases.Case:
    if case_id is not None:
        case = cases.get_case_for_session(case_id, session.customer_id, db_path=db_path)
        if case is not None:
            return case
    return cases.create_case(session.customer_id, language, db_path=db_path)


def _unless_already_handled(
    turn: Turn, evaluation: CaseEvaluation, report: ReportedCharge, how_identified: handoffs.ChargeIdentification,
) -> CaseEvaluation:
    """An eligible charge still goes to a person when another case of this
    customer already credited it, or already assessed the customer's
    explanation of it (handed to a person, or still open asking for more
    detail): a fresh case must not become a way to retry the same charge with
    a different story and fresh attempts (AD-13).
    """
    if evaluation.state != CaseState.RESOLVED_AUTO:
        return evaluation
    matched = evaluation.matched_transaction
    customer_id = turn.session.customer_id
    credited_in = cases.credited_case_for_transaction(customer_id, matched.transaction_id, db_path=turn.db_path)
    if credited_in is not None:
        return handoffs.already_credited(report, matched, credited_in, how_identified=how_identified)
    explained_in = cases.explained_case_for_transaction(
        customer_id, matched.transaction_id, exclude_case_id=turn.case.case_id, db_path=turn.db_path,
    )
    if explained_in is None:
        return evaluation
    turn.log_event(
        "prior_escalation_same_charge",
        {"matched_transaction_id": matched.transaction_id, "prior_case_id": explained_in},
    )
    return handoffs.prior_escalation_same_charge(report, matched, explained_in, how_identified=how_identified)


def _policy_verdict(
    turn: Turn, matched: TransactionCandidate, report: ReportedCharge, how_identified: handoffs.ChargeIdentification,
    *, reason: DisputeReason | None = None,
) -> CaseEvaluation:
    evaluation = evaluate_transaction(
        turn.session, matched, report=report, how_identified=how_identified, reason=reason, db_path=turn.db_path,
    )
    return _unless_already_handled(turn, evaluation, report, how_identified)


# -- Terminal and intermediate outcomes ----------------------------------------


def _finish_confirming(turn: Turn, matched: TransactionCandidate, report: ReportedCharge) -> ChatReply:
    lost = transition(
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
    turn: Turn, matched: TransactionCandidate, report: ReportedCharge, how_identified: handoffs.ChargeIdentification,
) -> ChatReply:
    """An identified charge: ask the customer to confirm it if policy would
    resolve it (AD-12), otherwise hand it off.
    """
    evaluation = _policy_verdict(turn, matched, report, how_identified)
    if evaluation.state == CaseState.RESOLVED_AUTO:
        return _finish_confirming(turn, matched, report)
    return finish_escalated(turn, evaluation, report)


def _unlocks_handoff(report: ReportedCharge, search: ChargeSearch, *, spend_round: bool) -> bool:
    """The agent has shown it cannot find the charge from what the customer
    said: they gave details and either nothing matched them or the turn went
    nowhere (a spent round, e.g. after rejecting the proposed charge).
    """
    return report.has_details and (search.list_filter == ListFilter.FALLBACK_RECENT or spend_round)


def _offer(
    turn: Turn, search: ChargeSearch, report: ReportedCharge, *, spend_round: bool,
    human_deferred: bool = False, expected_states: tuple[str, ...] = OPEN_STATES,
) -> ChatReply:
    """Shows the customer their own charges to pick from (AD-11 Row 3's
    clarification, as a list instead of a free-text question). A round is
    only spent when the turn brought nothing new. Entering `selecting` drops
    any previously proposed match: nothing is matched until they pick.

    `expected_states`: the states this turn may move the case from (a button
    that belongs to one state must lose to a request that moved it first).
    `human_deferred`: the list answers a request for a person, with a fixed
    text (the offer follows it, see `case_turn.Turn.reply`).
    """
    rounds = turn.case.clarification_rounds + (1 if spend_round else 0)
    if not search.charges:
        return _ask_for_details(
            turn, report, spend_round=spend_round, expected_states=expected_states, human_deferred=human_deferred,
        )
    offered = tuple(c.transaction_id for c in search.charges)
    lost = transition(
        turn, CaseState.SELECTING, expected_states=expected_states,
        offered_transaction_ids=offered, add_clarification_round=spend_round,
        unlock_handoff=_unlocks_handoff(report, search, spend_round=spend_round),
        clear_fields=("matched_transaction_id",), **report.update_fields(),
    )
    if lost:
        return lost
    turn.log_event(
        "charges_offered",
        {"list_filter": search.list_filter, "offered_transaction_ids": list(offered), "clarification_rounds": rounds},
    )
    options = [charge_option(c) for c in search.charges]
    if human_deferred:
        return turn.reply(CaseState.SELECTING, replies.HUMAN_DEFERRED[turn.language], options)
    context = llm.build_prompt_context(
        case_state=CaseState.SELECTING, language=turn.language,
        candidate_count=len(search.charges), list_filter=search.list_filter,
    )
    fallback = replies.CHARGE_LIST[turn.language][search.list_filter]
    return turn.reply(CaseState.SELECTING, turn.generate_reply(context, fallback=fallback), options)


def _offer_recent_charges(
    turn: Turn, report: ReportedCharge, *, expected_states: tuple[str, ...] = OPEN_STATES,
) -> ChatReply:
    """The customer asked to see their charges: a request, not a failed
    attempt, so no round is spent.
    """
    return _offer(turn, recent_charges(turn.session), report, spend_round=False, expected_states=expected_states)


def _ask_for_details(
    turn: Turn, report: ReportedCharge, *, spend_round: bool,
    expected_states: tuple[str, ...] = OPEN_STATES, human_deferred: bool = False,
) -> ChatReply:
    """Only for a customer with no outgoing charges to list at all."""
    lost = transition(
        turn, CaseState.CLARIFYING, expected_states=expected_states,
        add_clarification_round=spend_round, unlock_handoff=report.has_details,
        clear_fields=("matched_transaction_id",), **report.update_fields(),
    )
    if lost:
        return lost
    turn.log_event("case_clarifying", {"reason": "no_charges_to_list"})
    if human_deferred:
        return turn.reply(CaseState.CLARIFYING, replies.ASK_FOR_DETAILS[turn.language])
    context = llm.build_prompt_context(
        case_state=CaseState.CLARIFYING, language=turn.language, reported_amount=report.amount,
        reported_currency=report.currency,
        reported_date=report.date.isoformat() if report.date is not None else None,
    )
    return turn.reply(CaseState.CLARIFYING, turn.generate_reply(context, fallback=replies.ASK_FOR_DETAILS[turn.language]))


def _introduce(turn: Turn, scene: PromptScene) -> ChatReply:
    """A greeting gets an introduction and an out-of-scope request (balance,
    loans...) a plain statement of what this channel can do (the challenge's
    "unsupported request: abstain" case). Neither changes the case state or
    spends a round.
    """
    turn.log_event("introduction" if scene == PromptScene.GREETING else "out_of_scope_request", {})
    fallback = replies.WELCOME if scene == PromptScene.GREETING else replies.OUT_OF_SCOPE
    context = llm.build_prompt_context(case_state=scene, language=turn.language)
    reply = turn.generate_reply(context, fallback=fallback[turn.language])
    return turn.reply(CaseState(turn.case.state), reply, current_options(turn))


# -- Handlers ------------------------------------------------------------------


def _handle_human_request(turn: Turn, *, account: GivenAccount | None = None) -> ChatReply:
    """One "let me try first" per request (plan.md AD-8): the first request
    keeps the case where it is (the charge list, the pending confirmation or
    the explanation) with a fixed text, spends no round and unlocks the
    handoff, so the reply ends with the offer; once unlocked (by that first
    request, or silently when the agent could not find the charge or used its
    rounds) a request escalates at once.
    """
    case = turn.case
    if human_handoff_available(case):
        if case.state == CaseState.CONFIRMING:
            return escalate(turn, handoffs.confirmation_outcome(
                turn.report, case, customer_confirmation=llm.ConfirmationAnswer.HUMAN,
                action="Cliente solicitó explícitamente hablar con un agente humano.",
                open_question="El cliente prefirió hablar con una persona antes de confirmar el cargo propuesto.",
                customer_reason=EscalationReason.HUMAN_REQUESTED, charge=_proposed_charge(turn),
            ))
        charge = _charge_being_explained(turn)
        account_given = _account_given(case, account)
        evaluation = handoffs.human_request(turn.report, charge, explained=account_given)
        if account is not None:
            evaluation = handoffs.with_reported(evaluation, handoffs.explanation_reported(account.assessment))
        claimed_reason = account.assessment.reason if account is not None else None
        return escalate(turn, evaluation, charge=charge, account_given=account_given, claimed_reason=claimed_reason)
    turn = replace(turn, human_requested=True)
    state = CaseState(case.state)
    if state in (CaseState.AWAITING_EXPLANATION, CaseState.CONFIRMING):
        # Never stored as the explanation, even with an account: the text asks
        # for a person, and the next assessment would read that again.
        lost = transition(turn, state, expected_states=(state,))
        deferred = (
            replies.HUMAN_DEFERRED_WHILE_EXPLAINING if state == CaseState.AWAITING_EXPLANATION
            else replies.HUMAN_DEFERRED_WHILE_CONFIRMING
        )
        return lost or turn.reply(state, deferred[turn.language])
    report = turn.report
    search = find_charges(turn.session, report) if report.has_details else recent_charges(turn.session)
    return _offer(turn, search, report, spend_round=False, human_deferred=True)


def _proposed_charge(turn: Turn) -> TransactionCandidate | None:
    matched_id = turn.case.matched_transaction_id
    return get_own_transaction(turn.session, matched_id) if matched_id else None


def _charge_being_explained(turn: Turn) -> TransactionCandidate | None:
    """In `awaiting_explanation` the case's match is a charge the customer
    picked or confirmed, so an escalation there may name it (plan.md AD-5).
    Anywhere else the stored match may be an unconfirmed proposal: None.
    """
    if turn.case.state != CaseState.AWAITING_EXPLANATION:
        return None
    return _proposed_charge(turn)


def _account_given(case: cases.Case, account: GivenAccount | None) -> bool:
    return case.state == CaseState.AWAITING_EXPLANATION and (
        account is not None or bool(case.explanation_text)
    )


_ACTION_ANSWERS = {
    CustomerAction.CONFIRM_YES: llm.ConfirmationAnswer.YES,
    CustomerAction.CONFIRM_NO: llm.ConfirmationAnswer.NO,
}


def _confirmation_answer(turn: Turn, text: str, action: CustomerAction | None) -> llm.ConfirmationAnswer:
    """Raises `llm.LLMUnavailable` when typed text cannot be classified."""
    if action in _ACTION_ANSWERS:
        return _ACTION_ANSWERS[action]
    return llm.classify_confirmation(text, language=turn.language)


def _handle_confirmation(turn: Turn, text: str, action: CustomerAction | None = None) -> ChatReply:
    """AD-12: the one bounded confirmation round. Only an explicit "yes" can
    resolve, and even then AD-11 is re-run on the stored transaction, looked
    up again through the session. "Not that one" (or an unclear answer) shows
    the customer their charges around that date instead of giving up.
    """
    case = turn.case
    try:
        answer = _confirmation_answer(turn, text, action)
    except llm.LLMUnavailable as exc:
        return force_escalation(
            turn, event_type="llm_unavailable", failed_call="classify_confirmation",
            action_taken="El servicio de NLU no respondió al pedir la confirmación del cliente.", error=exc,
        )
    turn.log_event("confirmation_received", {"answer": str(answer), "via": "button" if action else "text"})
    report = turn.report

    if answer == llm.ConfirmationAnswer.YES:
        return _confirm_proposed_charge(turn, report)
    if answer == llm.ConfirmationAnswer.HUMAN:
        return _handle_human_request(turn)
    if case.clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
        return finish_escalated(turn, handoffs.confirmation_outcome(
            report, case, customer_confirmation=answer,
            action=f"Se propuso al cliente la transacción coincidente y no la confirmó (respuesta: {answer}); "
                   "no quedan rondas de aclaración.",
            open_question="¿Cuál es la transacción que el cliente no reconoce?",
            customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
        ), report, drop_proposed_match=True)
    return _offer(
        turn, _charges_other_than_proposed(turn, report), report, spend_round=True,
        expected_states=(CaseState.CONFIRMING,),
    )


def _confirm_proposed_charge(turn: Turn, report: ReportedCharge) -> ChatReply:
    case = turn.case
    matched = _proposed_charge(turn)
    evaluation = (
        _policy_verdict(turn, matched, report, handoffs.ChargeIdentification.CONFIRMATION)
        if matched is not None else CaseEvaluation(state=CaseState.ESCALATED)
    )
    if evaluation.state == CaseState.RESOLVED_AUTO:
        return ask_for_explanation(
            turn, matched, expected_states=(CaseState.CONFIRMING,), expected_match=matched.transaction_id,
        )
    turn.log_event("confirmation_reverification_failed", {"state": evaluation.state})
    return escalate(turn, handoffs.reverification_failed(report, case, evaluation, matched), charge=matched)


def _charges_other_than_proposed(turn: Turn, report: ReportedCharge) -> ChargeSearch:
    proposed = turn.case.matched_transaction_id
    around_date = ReportedCharge(amount=None, date=report.date, currency=report.currency)
    for search in (find_charges(turn.session, around_date), recent_charges(turn.session)):
        others = tuple(c for c in search.charges if c.transaction_id != proposed)
        if others:
            return ChargeSearch(others, search.list_filter)
    return ChargeSearch((), ListFilter.RECENT)


def _handle_selection(turn: Turn, transaction_id: str) -> ChatReply:
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
        return turn.reply(CaseState(case.state), replies.SELECTION_UNAVAILABLE[turn.language], current_options(turn))

    turn.log_event("charge_selected", {"transaction_id": transaction_id})
    evaluation = _policy_verdict(turn, matched, turn.report, handoffs.ChargeIdentification.PICK)
    if evaluation.state == CaseState.RESOLVED_AUTO:
        # Picking the charge is the customer's identification of it (AD-12);
        # what happened with it comes next.
        return ask_for_explanation(
            turn, matched, expected_states=(CaseState.SELECTING,), expected_offered=case.offered_transaction_ids,
        )
    return finish_escalated(turn, evaluation, turn.report, expected_offered=case.offered_transaction_ids)


def _handle_none_of_these(turn: Turn) -> ChatReply:
    """Not in the list: with no details from the customer yet, the agent asks
    for one and keeps looking; after details, it hands off. Only reached in
    `selecting` (a stale tap is refused earlier, see `_ACTION_STATES`).
    """
    case = turn.case
    if not turn.report.has_details and case.clarification_rounds < MAX_CLARIFICATION_ROUNDS:
        lost = transition(turn, CaseState.SELECTING, expected_states=(CaseState.SELECTING,), add_clarification_round=True)
        if lost:
            return lost
        turn.log_event("details_requested", {"reason": "not_in_unfiltered_list"})
        context = llm.build_prompt_context(case_state=PromptScene.ASK_FOR_DETAILS, language=turn.language)
        return turn.reply(
            CaseState.SELECTING, turn.generate_reply(context, fallback=replies.ASK_FOR_ONE_DETAIL[turn.language]),
        )
    return finish_escalated(
        turn, handoffs.not_in_list(turn.report, case), turn.report, expected_offered=case.offered_transaction_ids,
    )


# The state a quick-reply button belongs to: tapped in any other state (an old
# button still on screen), it changes nothing.
_ACTION_STATES = {
    CustomerAction.CONFIRM_YES: CaseState.CONFIRMING,
    CustomerAction.CONFIRM_NO: CaseState.CONFIRMING,
    CustomerAction.NONE_OF_THESE: CaseState.SELECTING,
    CustomerAction.SHOW_CHARGES: CaseState.AWAITING_REPORT,
}


def handle_message(
    session: Session,
    case_id: str | None,
    text: str,
    *,
    language: Language | str = Language.ES,
    db_path: Path | None = None,
    selected_transaction_id: str | None = None,
    action: CustomerAction | str | None = None,
    turn_id: str | None = None,
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

    `turn_id` makes the turn idempotent (AD-4, `app/turns.py`): a retry with
    the same id gets the stored reply and is never processed twice. Raises
    `turns.TurnInProgress` while the first request is still running. A turn
    that fails with an unexpected error is marked abandoned before the error
    propagates, so its retry gets the case's current state at once.
    """
    language = Language(language)
    action = CustomerAction(action) if action is not None else None
    if turn_id is None:
        return _run_turn(
            session, case_id, text, language,
            db_path=db_path, selected_transaction_id=selected_transaction_id, action=action,
        )

    customer_id = session.customer_id
    claim = turns.claim(customer_id, turn_id, db_path=db_path)
    if claim.status == turns.TurnStatus.COMPLETE:
        assert claim.reply is not None  # a complete turn always stores its reply
        cases.log_event(uuid.uuid4().hex, claim.case_id, "turn_replayed", {"turn_id": turn_id}, db_path=db_path)
        return claim.reply
    if claim.status == turns.TurnStatus.IN_FLIGHT:
        cases.log_event(uuid.uuid4().hex, claim.case_id, "turn_in_flight", {"turn_id": turn_id}, db_path=db_path)
        raise turns.TurnInProgress(turn_id)
    if claim.status in _UNRUNNABLE_TURN_EVENTS:
        return _abandoned_turn_reply(
            session, claim.case_id or case_id, turn_id, language, db_path,
            event_type=_UNRUNNABLE_TURN_EVENTS[claim.status],
        )
    try:
        reply = _run_turn(
            session, case_id, text, language,
            db_path=db_path, selected_transaction_id=selected_transaction_id, action=action, turn_id=turn_id,
        )
    except cases.CaseOwnershipError:
        turns.release(customer_id, turn_id, db_path=db_path)
        raise
    except Exception:
        # It may already have moved the case: never run it again, but do not
        # leave the retry stuck on 409 until the pending timeout either.
        failed_case_id = turns.abandon(customer_id, turn_id, db_path=db_path)
        cases.log_event(uuid.uuid4().hex, failed_case_id, "turn_failed", {"turn_id": turn_id}, db_path=db_path)
        raise
    turns.complete(customer_id, turn_id, reply, db_path=db_path)
    return reply


_UNRUNNABLE_TURN_EVENTS = {
    turns.TurnStatus.ABANDONED: "turn_abandoned",
    turns.TurnStatus.EXPIRED: "turn_expired",
}


def _abandoned_turn_reply(
    session: Session, case_id: str | None, turn_id: str, language: Language, db_path: Path | None,
    *, event_type: str,
) -> ChatReply:
    """The request that claimed this turn died or failed without storing its
    reply, or its reply expired, and it may already have moved the case: never
    run it again, just say where the case is now (as after a lost
    compare-and-set race).

    `case_id` is the one the turn attached, or else the one the request
    names; it is answered only if it belongs to this session.
    """
    try:
        case = cases.get_case_for_session(case_id, session.customer_id, db_path=db_path) if case_id else None
    except cases.CaseOwnershipError:
        case = None
    correlation_id = uuid.uuid4().hex
    cases.log_event(
        correlation_id, case.case_id if case else None, event_type, {"turn_id": turn_id}, db_path=db_path,
    )
    if case is None:
        # The first message of a conversation whose case was never created.
        return {
            "case_id": None, "state": CaseState.AWAITING_REPORT, "customer_id": session.customer_id,
            "reply": replies.CASE_MOVED_ON[language], "options": [], "human_available": False,
            "escalation": None,
        }
    state, text = where_the_case_is(case, language)
    turn = Turn(session, case, language, correlation_id, db_path)
    return {
        "case_id": case.case_id, "state": state, "customer_id": session.customer_id, "reply": text,
        "options": current_options(turn),
        "human_available": state not in TERMINAL_STATES and human_handoff_available(case),
        "escalation": escalation_of(case, language),
    }


def _run_turn(
    session: Session, case_id: str | None, text: str, language: Language, *, db_path: Path | None,
    selected_transaction_id: str | None, action: CustomerAction | None, turn_id: str | None = None,
) -> ChatReply:
    case = _load_or_create_case(session, case_id, language, db_path)
    if turn_id is not None:
        turns.attach_case(session.customer_id, turn_id, case.case_id, db_path=db_path)
    from_menu = action is not None or selected_transaction_id is not None
    turn = Turn(session, case, language, uuid.uuid4().hex, db_path, from_menu=from_menu)
    if case_id is not None and case.case_id != case_id:
        turn.log_event("unknown_case_id_new_case_started", {"requested_case_id": case_id})
    cases.log_message(case.case_id, "customer", text, db_path=db_path)

    if case.state in TERMINAL_STATES:
        return turn.reply(*where_the_case_is(case, language))
    if action in _ACTION_STATES and case.state != _ACTION_STATES[action]:
        turn.log_event("action_rejected", {"action": action, "state": case.state})
        return turn.reply(CaseState(case.state), replies.ACTION_UNAVAILABLE[language], current_options(turn))
    try:
        with llm.turn_deadline():
            return _route(turn, text, selected_transaction_id, action)
    except duckdb.Error:
        return force_escalation(
            turn, event_type="fixture_unavailable", failed_call="fixture_lookup",
            action_taken="La consulta a los datos del cliente falló.",
        )


def _route(
    turn: Turn, text: str, selected_transaction_id: str | None, action: CustomerAction | None,
) -> ChatReply:
    # Before the human button: in this state a request for a person is part
    # of the statement step, never a new HUMAN_REQUESTED escalation. A tapped
    # charge falls through to `_handle_selection`, which refuses it.
    if turn.case.state == CaseState.AWAITING_STATEMENT and selected_transaction_id is None:
        return handle_statement(turn, text, action)
    if action == CustomerAction.HUMAN:
        return _handle_human_request(turn)
    if selected_transaction_id is not None:
        return _handle_selection(turn, selected_transaction_id)
    if action == CustomerAction.NONE_OF_THESE:
        return _handle_none_of_these(turn)
    if action == CustomerAction.SHOW_CHARGES:
        return _offer_recent_charges(
            turn, turn.report, expected_states=(_ACTION_STATES[CustomerAction.SHOW_CHARGES],),
        )
    if turn.case.state == CaseState.CONFIRMING:
        return _handle_confirmation(turn, text, action)
    if turn.case.state == CaseState.AWAITING_EXPLANATION:
        return handle_explanation(
            turn, text, policy_verdict=_policy_verdict, on_human_request=_handle_human_request,
        )
    return _handle_report(turn, text)


# -- Free-text reports -----------------------------------------------------------


def _merged_report(turn: Turn, extraction: llm.ExtractedEntities) -> ReportedCharge:
    """This turn's details on top of what the case already has: a follow-up
    that does not restate the amount, date, currency or merchant keeps the
    earlier value. A currency the customer did not name stays unknown.
    """
    case = turn.case
    reported_date = extraction.date or case.reported_date
    return ReportedCharge(
        amount=extraction.amount if extraction.amount is not None else case.reported_amount,
        date=date.fromisoformat(reported_date) if reported_date else None,
        currency=extraction.currency or case.reported_currency,
        merchant=extraction.merchant_hint or case.reported_merchant,
    )


def _brings_new_info(extraction: llm.ExtractedEntities, case: cases.Case) -> bool:
    new_amount = extraction.amount is not None and extraction.amount != case.reported_amount
    new_date = extraction.date is not None and extraction.date != case.reported_date
    new_merchant = bool(extraction.merchant_hint) and (
        extraction.merchant_hint.casefold() != (case.reported_merchant or "").casefold()
    )
    return new_amount or new_date or new_merchant


def _handle_report(turn: Turn, text: str) -> ChatReply:
    """A free-text turn: extract what the customer said, then either match it
    (AD-11), show them their charges to pick from, or escalate.
    """
    try:
        extraction = llm.extract_entities(text, language=turn.language, today=config.DATA_AS_OF)
    except llm.LLMUnavailable as exc:
        return force_escalation(
            turn, event_type="llm_unavailable", failed_call="extract_entities",
            action_taken="El servicio de NLU no respondió tras agotar los reintentos.", error=exc,
        )
    if extraction.parse_failed:
        turn.log_event("extraction_parse_failed", {"call": "extract_entities"})
    has_details = extraction.has_details
    if extraction.wants_human and (human_handoff_available(turn.case) or not has_details):
        return _handle_human_request(turn)
    if extraction.wants_human:
        # Asked for a person but also gave details: try them first, and this
        # counts as the one deferral (a policy escalation still wins).
        turn.log_event("human_request_with_details", {"state": turn.case.state})
        turn = replace(turn, human_requested=True)
    if not has_details and extraction.intent == llm.ExtractionIntent.GREETING:
        return _introduce(turn, PromptScene.GREETING)
    if not has_details and extraction.intent == llm.ExtractionIntent.OTHER:
        return _introduce(turn, PromptScene.OUT_OF_SCOPE)
    if cases.increment_turn_count(turn.case.case_id, db_path=turn.db_path) > MAX_CASE_TURNS:
        return escalate(turn, handoffs.turn_limit(turn.report, turn.case))

    report = _merged_report(turn, extraction)
    if not has_details and extraction.intent == llm.ExtractionIntent.SHOW_CHARGES:
        return _offer_recent_charges(turn, report)
    brought_new_info = _brings_new_info(extraction, turn.case)
    can_ask_again = brought_new_info or turn.case.clarification_rounds < MAX_CLARIFICATION_ROUNDS
    if report.is_complete:
        return _handle_full_report(turn, report, spend_round=not brought_new_info, can_ask_again=can_ask_again)
    return _handle_partial_report(
        turn, report, merchant_named_now=bool(extraction.merchant_hint),
        spend_round=not brought_new_info, can_ask_again=can_ask_again,
    )


def _handle_full_report(turn: Turn, report: ReportedCharge, *, spend_round: bool, can_ask_again: bool) -> ChatReply:
    evaluation = _unless_already_handled(
        turn,
        evaluate_case(
            turn.session, reported_amount=report.amount, reported_date=report.date, currency=report.currency,
            db_path=turn.db_path,
        ),
        report,
        handoffs.ChargeIdentification.REPORT,
    )
    turn.log_event("case_evaluated", {"state": evaluation.state, "candidate_count": len(evaluation.candidates)})
    if evaluation.state == CaseState.RESOLVED_AUTO:
        # Policy-eligible, but never resolved in the same turn as the first
        # report: the customer confirms the matched charge first (AD-12).
        return _finish_confirming(turn, evaluation.matched_transaction, report)
    if evaluation.state == CaseState.ESCALATED:
        return finish_escalated(turn, evaluation, report)
    if not can_ask_again:
        return finish_escalated(
            turn, handoffs.ambiguous_match(report, evaluation.candidates, turn.case.clarification_rounds), report,
        )
    matches = matching_charges(turn.session, report)
    search = ChargeSearch(matches, ListFilter.FILTERED) if len(matches) >= 2 else find_charges(turn.session, report)
    return _offer(turn, search, report, spend_round=spend_round)


def _handle_partial_report(
    turn: Turn, report: ReportedCharge, *, merchant_named_now: bool, spend_round: bool, can_ask_again: bool,
) -> ChatReply:
    if not can_ask_again:
        return escalate(turn, handoffs.unidentified_charge(report, turn.case))
    search = find_charges(turn.session, report)
    if merchant_named_now and search.matched_on_merchant and len(search.charges) == 1:
        # "El de Uber": exactly one of their charges is at that merchant, so
        # propose it instead of making them pick from a list of one.
        return _propose_or_escalate(turn, search.charges[0], report, handoffs.ChargeIdentification.MERCHANT)
    return _offer(turn, search, report, spend_round=spend_round)
