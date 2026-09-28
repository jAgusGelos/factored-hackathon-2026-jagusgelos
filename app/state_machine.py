"""Dispute conversation state machine (AD-3).

Guard functions here are thin wrappers around `app/policy.py`'s AD-11 rules —
this module owns the STATE TRANSITIONS, `app/policy.py` owns the THRESHOLDS.
Every guard function that reads customer data takes only a `Session`
(`app/auth.py`) via `app/transactions.py` — never a bare `customer_id`.

States:
  awaiting_report -> matching   (customer reports an amount/date)
  matching        -> resolved_auto | escalated | clarifying   (AD-11 evaluation)
  clarifying      -> matching   (customer answers a clarifying question)
  clarifying      -> escalated  (still ambiguous after MAX_CLARIFICATION_ROUNDS)

`evaluate_case()` is the single guard-function entrypoint for the
matching/resolution transition — it runs Rows 1-5 of AD-11 in one pass since
none of it requires an async wait once the report's structured amount/date
are known (the NLU extraction that produces those is a separate concern in
`app/llm.py`).

`handle_message()` orchestrates one turn: NLU entity extraction
(`app/llm.py`) -> `evaluate_case()` -> grounded NLG response generation
(`app/llm.py`), with case/message persistence in `app/cases.py`. An
exhausted LLM retry budget (`llm.LLMUnavailable`) or a failed fixture lookup
(`duckdb.Error`) forces escalation with the NFR's deterministic fallback
message, never a crash or a hallucinated answer.

Two databases are in play and must never be conflated: `db_path` below is the
APP db (SQLite sessions/cases, `config.APP_DB_PATH`) and is only ever passed to
`app/cases.py`; every fixture read in `app/transactions.py` uses its own
`config.FIXTURE_DB_PATH` default.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TypedDict

import duckdb

from app import cases, classifier, llm
from app.auth import Session
from app.llm import Language
from app.policy import (
    ABUSE_GUARD_WINDOW_DAYS,
    DISPUTE_COMPLAINT_CATEGORY,
    MATCH_DATE_TOLERANCE_DAYS,
    MAX_CLARIFICATION_ROUNDS,
    MatchOutcome,
    ResolutionDecision,
    evaluate_match,
    evaluate_resolution,
    match_amount_tolerance,
)
from app.transactions import (
    CustomerProfile,
    TransactionCandidate,
    count_prior_complaints,
    get_case_history,
    get_customer_profile,
    search_own_transactions,
)


class CaseState(StrEnum):
    AWAITING_REPORT = "awaiting_report"
    CLARIFYING = "clarifying"
    MATCHING = "matching"
    RESOLVED_AUTO = "resolved_auto"
    ESCALATED = "escalated"


TERMINAL_STATES = frozenset({CaseState.RESOLVED_AUTO, CaseState.ESCALATED})


@dataclass(frozen=True)
class HandoffRecord:
    """The structured artifact a case that escalates produces — facts,
    actions taken, evidence, open questions. Never a raw transcript dump
    (plan.md's Always-rule).
    """

    facts: dict[str, str]
    actions_taken: tuple[str, ...]
    evidence: tuple[str, ...]
    open_questions: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CaseEvaluation:
    state: CaseState
    matched_transaction: TransactionCandidate | None = None
    candidates: tuple[TransactionCandidate, ...] = field(default_factory=tuple)
    resolution_reasons: tuple[str, ...] = field(default_factory=tuple)
    handoff: HandoffRecord | None = None


_CUSTOMER_MESSAGE_OMITTED = "[omitido — ver mensajes del caso]"


def _report_facts(
    reported_amount: float | None, reported_date: date | None, currency: str, **extra: str
) -> dict[str, str]:
    # Unknown values are omitted, never filled with placeholders: a handoff
    # fact is read by a human agent as something the customer actually said.
    facts: dict[str, str] = {}
    if reported_amount is not None:
        facts["reported_amount"] = str(reported_amount)
    facts["currency"] = currency
    if reported_date is not None:
        facts["reported_date"] = reported_date.isoformat()
    return {**facts, **extra}


def _human_request_evaluation(
    *, reported_amount: float | None, reported_date: date | None, currency: str
) -> CaseEvaluation:
    return CaseEvaluation(
        state=CaseState.ESCALATED,
        handoff=HandoffRecord(
            facts=_report_facts(reported_amount, reported_date, currency),
            actions_taken=("Cliente solicitó explícitamente hablar con un agente humano.",),
            evidence=(),
            open_questions=(),
        ),
    )


def _ambiguous_match_escalation(
    candidates: list[TransactionCandidate],
    *,
    reported_amount: float,
    reported_date: date,
    currency: str,
    clarification_rounds: int,
) -> CaseEvaluation:
    return CaseEvaluation(
        state=CaseState.ESCALATED,
        candidates=tuple(candidates),
        handoff=HandoffRecord(
            facts=_report_facts(
                reported_amount, reported_date, currency, candidate_count=str(len(candidates))
            ),
            actions_taken=(
                f"Se buscaron transacciones del cliente en un rango de "
                f"{MATCH_DATE_TOLERANCE_DAYS} días; {len(candidates)} candidata(s) "
                f"encontrada(s) tras {clarification_rounds} ronda(s) de aclaración.",
            ),
            evidence=tuple(c.transaction_id for c in candidates),
            open_questions=("¿Cuál de las transacciones candidatas corresponde al reclamo?",)
            if candidates
            else ("No se encontró ninguna transacción candidata para este reclamo.",),
        ),
    )


def _ineligible_match_escalation(
    matched: TransactionCandidate,
    reasons: tuple[str, ...],
    *,
    reported_amount: float,
    reported_date: date,
    currency: str,
) -> CaseEvaluation:
    return CaseEvaluation(
        state=CaseState.ESCALATED,
        matched_transaction=matched,
        candidates=(matched,),
        resolution_reasons=reasons,
        handoff=HandoffRecord(
            facts=_report_facts(
                reported_amount, reported_date, currency,
                matched_transaction_id=matched.transaction_id,
            ),
            actions_taken=(
                "Se localizó una transacción coincidente, pero no cumple las condiciones "
                "de auto-resolución.",
            ),
            evidence=(matched.transaction_id,),
            open_questions=reasons,
        ),
    )


def evaluate_case(
    session: Session,
    *,
    reported_amount: float,
    reported_date: date,
    currency: str,
    clarification_rounds: int,
    customer_requested_human: bool = False,
) -> CaseEvaluation:
    """Guard function for the matching/resolution transition — AD-11 Rows
    1-5. Returns the resulting state plus enough structured context to build
    the reply or the handoff record (escalation).
    """
    if customer_requested_human:
        return _human_request_evaluation(
            reported_amount=reported_amount, reported_date=reported_date, currency=currency
        )

    candidates = search_own_transactions(
        session,
        reported_amount,
        reported_date,
        amount_tolerance=match_amount_tolerance(reported_amount),
        date_tolerance_days=MATCH_DATE_TOLERANCE_DAYS,
        currency=currency,
    )

    if evaluate_match(candidates) == MatchOutcome.AMBIGUOUS:
        if clarification_rounds < MAX_CLARIFICATION_ROUNDS:
            return CaseEvaluation(state=CaseState.CLARIFYING, candidates=tuple(candidates))
        return _ambiguous_match_escalation(
            candidates,
            reported_amount=reported_amount,
            reported_date=reported_date,
            currency=currency,
            clarification_rounds=clarification_rounds,
        )

    matched = candidates[0]
    prior_disputes = get_case_history(
        session, DISPUTE_COMPLAINT_CATEGORY, reported_date, window_days=ABUSE_GUARD_WINDOW_DAYS
    )
    predicted_priority = classifier.predict_priority(
        classifier.build_live_features(
            get_customer_profile(session),
            claimed_amount=reported_amount,
            currency=currency,
            prior_complaint_count=count_prior_complaints(session, reported_date),
        )
    )
    resolution = evaluate_resolution(
        matched, prior_disputes_in_window=prior_disputes, classifier_priority=predicted_priority
    )

    if resolution.decision == ResolutionDecision.AUTO_RESOLVE:
        return CaseEvaluation(
            state=CaseState.RESOLVED_AUTO, matched_transaction=matched, candidates=(matched,)
        )
    return _ineligible_match_escalation(
        matched,
        resolution.reasons,
        reported_amount=reported_amount,
        reported_date=reported_date,
        currency=currency,
    )


class ChatReply(TypedDict):
    case_id: str
    state: CaseState
    customer_id: str
    reply: str


_COUNTRY_CURRENCY = {"México": "MXN", "Colombia": "COP", "Argentina": "ARS"}
_DEFAULT_CURRENCY = "USD"

_MISSING_ENTITIES_REPLY = {
    Language.ES: (
        "Para ayudarte necesito el monto exacto y la fecha aproximada del cargo que "
        "no reconocés. ¿Me los podés compartir?"
    ),
    Language.PT: (
        "Para te ajudar preciso do valor exato e da data aproximada da cobrança que "
        "você não reconhece. Pode me informar?"
    ),
}


def _infer_currency(profile: CustomerProfile | None) -> str:
    if profile is None or profile.country is None:
        return _DEFAULT_CURRENCY
    return _COUNTRY_CURRENCY.get(profile.country, _DEFAULT_CURRENCY)


def _reply_for_terminal_case(case: cases.Case, language: Language) -> str:
    # Uses the CURRENT request's language, not the stored case.language: a
    # customer may switch the PT/ES toggle mid-conversation, and every other
    # reply in this module already replies in the requesting turn's language
    # — a terminal-case reply staying frozen in whatever language the case
    # was created in would be the one inconsistent path.
    return {
        Language.ES: {
            CaseState.RESOLVED_AUTO: f"Tu caso ya fue resuelto (referencia {case.resolution_reference}).",
            CaseState.ESCALATED: "Tu caso ya fue derivado a un agente humano; te van a contactar a la brevedad.",
        },
        Language.PT: {
            CaseState.RESOLVED_AUTO: f"Seu caso já foi resolvido (referência {case.resolution_reference}).",
            CaseState.ESCALATED: "Seu caso já foi encaminhado a um agente humano; você será contatado em breve.",
        },
    }[language][case.state]


@dataclass(frozen=True)
class _Turn:
    session: Session
    case: cases.Case
    language: Language
    correlation_id: str
    db_path: Path | None

    def log_event(self, event_type: str, payload: dict) -> None:
        cases.log_event(
            self.correlation_id, self.case.case_id, event_type, payload, db_path=self.db_path
        )

    def reply(self, state: CaseState, text: str) -> ChatReply:
        cases.log_message(self.case.case_id, "agent", text, db_path=self.db_path)
        return {
            "case_id": self.case.case_id,
            "state": state,
            "customer_id": self.session.customer_id,
            "reply": text,
        }

    def generate_reply(self, context: llm.PromptContext) -> str:
        try:
            return llm.generate_response(context, language=self.language)
        except llm.LLMUnavailable:
            self.log_event("llm_unavailable", {"call": "generate_response"})
            return llm.DETERMINISTIC_FALLBACK_MESSAGE[self.language]


def _load_or_create_case(
    session: Session, case_id: str | None, language: Language, db_path: Path | None
) -> cases.Case:
    if case_id is not None:
        case = cases.get_case_for_session(case_id, session.customer_id, db_path=db_path)
        if case is not None:
            return case
    return cases.create_case(session.customer_id, language, db_path=db_path)


def _force_escalation(turn: _Turn, *, event_type: str, failed_call: str, action_taken: str) -> ChatReply:
    turn.log_event(event_type, {"call": failed_call})
    handoff = HandoffRecord(
        facts={"customer_message": _CUSTOMER_MESSAGE_OMITTED},
        actions_taken=(action_taken,),
        evidence=(),
        open_questions=("Requiere revisión manual del mensaje original del cliente.",),
    )
    cases.update_case(
        turn.case.case_id, state=CaseState.ESCALATED, handoff=handoff.to_dict(), db_path=turn.db_path
    )
    return turn.reply(CaseState.ESCALATED, llm.DETERMINISTIC_FALLBACK_MESSAGE[turn.language])


def _ask_for_missing_entities(turn: _Turn) -> ChatReply:
    cases.update_case(
        turn.case.case_id, state=CaseState.CLARIFYING,
        clarification_rounds=turn.case.clarification_rounds + 1, db_path=turn.db_path,
    )
    return turn.reply(CaseState.CLARIFYING, _MISSING_ENTITIES_REPLY[turn.language])


def _missing_entities_escalation() -> CaseEvaluation:
    return CaseEvaluation(
        state=CaseState.ESCALATED,
        handoff=HandoffRecord(
            facts={"customer_message": _CUSTOMER_MESSAGE_OMITTED},
            actions_taken=("No fue posible extraer monto y fecha tras varios intentos.",),
            evidence=(),
            open_questions=("Requiere que un agente humano recabe los datos del reclamo.",),
        ),
    )


def _evaluate_turn(
    turn: _Turn,
    extraction: llm.ExtractedEntities,
    *,
    amount: float | None,
    reported_date: date | None,
    currency: str,
) -> CaseEvaluation:
    if extraction.wants_human:
        return _human_request_evaluation(
            reported_amount=amount, reported_date=reported_date, currency=currency
        )
    if amount is None or reported_date is None:
        return _missing_entities_escalation()
    return evaluate_case(
        turn.session, reported_amount=amount, reported_date=reported_date,
        currency=currency, clarification_rounds=turn.case.clarification_rounds,
    )


def _finish_clarifying(
    turn: _Turn, evaluation: CaseEvaluation, *, amount: float, reported_date: date, currency: str
) -> ChatReply:
    rounds = turn.case.clarification_rounds + 1
    cases.update_case(
        turn.case.case_id, state=CaseState.CLARIFYING, reported_amount=amount,
        reported_date=reported_date.isoformat(), clarification_rounds=rounds, db_path=turn.db_path,
    )
    context = llm.build_prompt_context(
        case_state=CaseState.CLARIFYING, language=turn.language, reported_amount=amount,
        reported_currency=currency, reported_date=reported_date.isoformat(),
        candidate_count=len(evaluation.candidates), clarification_rounds=rounds,
    )
    return turn.reply(CaseState.CLARIFYING, turn.generate_reply(context))


def _simulate_provisional_credit(turn: _Turn, matched: TransactionCandidate) -> str:
    """AD-11 Row 6's auto-resolution action: a SIMULATED provisional credit,
    logged as such — never a real transfer, never a call to any payment
    provider (there is no such integration in this codebase).
    """
    reference = f"REF-{uuid.uuid4().hex[:10].upper()}"
    turn.log_event(
        "simulated_credit",
        {
            "reference": reference,
            "matched_transaction_id": matched.transaction_id,
            "amount": matched.amount,
            "currency": matched.currency,
            "simulated": True,
        },
    )
    return reference


def _finish_resolved(
    turn: _Turn, matched: TransactionCandidate, *, amount: float, reported_date: date
) -> ChatReply:
    reference = _simulate_provisional_credit(turn, matched)
    cases.update_case(
        turn.case.case_id, state=CaseState.RESOLVED_AUTO, reported_amount=amount,
        reported_date=reported_date.isoformat(), matched_transaction_id=matched.transaction_id,
        resolution_reference=reference, db_path=turn.db_path,
    )
    context = llm.build_prompt_context(
        case_state=CaseState.RESOLVED_AUTO, language=turn.language,
        candidate_amount=matched.amount, candidate_currency=matched.currency,
        candidate_date=matched.transaction_date.isoformat(),
        candidate_merchant_name=matched.merchant_name, resolution_reference=reference,
    )
    return turn.reply(CaseState.RESOLVED_AUTO, turn.generate_reply(context))


def _finish_escalated(
    turn: _Turn, handoff: HandoffRecord, *, amount: float | None, reported_date: date | None
) -> ChatReply:
    cases.update_case(
        turn.case.case_id, state=CaseState.ESCALATED, reported_amount=amount,
        reported_date=reported_date.isoformat() if reported_date is not None else None,
        handoff=handoff.to_dict(), db_path=turn.db_path,
    )
    context = llm.build_prompt_context(case_state=CaseState.ESCALATED, language=turn.language)
    return turn.reply(CaseState.ESCALATED, turn.generate_reply(context))


def handle_message(
    session: Session,
    case_id: str | None,
    text: str,
    *,
    language: Language | str = Language.ES,
    db_path: Path | None = None,
) -> ChatReply:
    """`case_id=None` starts a new case. An existing `case_id` is only ever
    resumed if it belongs to `session.customer_id` (AD-3) — `cases.CaseOwnershipError`
    propagates to the caller (app/main.py maps it to 403), never silently
    reassigned or ignored. An unrecognized `case_id` (no matching row at all —
    distinct from an ownership conflict) starts a fresh case rather than
    erroring, on the theory that a client only ever gets a `case_id` from a
    prior reply of this same endpoint; the substitution is still logged so
    it's visible in the audit trail, not silently invisible.
    """
    language = Language(language)
    case = _load_or_create_case(session, case_id, language, db_path)
    turn = _Turn(session, case, language, uuid.uuid4().hex, db_path)
    if case_id is not None and case.case_id != case_id:
        turn.log_event("unknown_case_id_new_case_started", {"requested_case_id": case_id})
    cases.log_message(case.case_id, "customer", text, db_path=db_path)

    if case.state in TERMINAL_STATES:
        return turn.reply(CaseState(case.state), _reply_for_terminal_case(case, language))

    try:
        extraction = llm.extract_entities(text, language=language, today=date.today().isoformat())
    except llm.LLMUnavailable:
        return _force_escalation(
            turn, event_type="llm_unavailable", failed_call="extract_entities",
            action_taken="El servicio de NLU no respondió tras agotar los reintentos.",
        )
    if extraction.parse_failed:
        turn.log_event("extraction_parse_failed", {"call": "extract_entities"})

    amount = extraction.amount if extraction.amount is not None else case.reported_amount
    reported_date_iso = extraction.date if extraction.date is not None else case.reported_date
    reported_date = date.fromisoformat(reported_date_iso) if reported_date_iso is not None else None

    entities_missing = amount is None or reported_date is None
    rounds_left = case.clarification_rounds < MAX_CLARIFICATION_ROUNDS
    if entities_missing and not extraction.wants_human and rounds_left:
        return _ask_for_missing_entities(turn)

    try:
        currency = extraction.currency or _infer_currency(get_customer_profile(session))
        evaluation = _evaluate_turn(
            turn, extraction, amount=amount, reported_date=reported_date, currency=currency
        )
    except duckdb.Error:
        return _force_escalation(
            turn, event_type="fixture_unavailable", failed_call="fixture_lookup",
            action_taken="La consulta a los datos del cliente falló.",
        )

    turn.log_event(
        "case_evaluated", {"state": evaluation.state, "candidate_count": len(evaluation.candidates)}
    )

    if evaluation.state == CaseState.CLARIFYING:
        return _finish_clarifying(
            turn, evaluation, amount=amount, reported_date=reported_date, currency=currency
        )
    if evaluation.state == CaseState.RESOLVED_AUTO:
        return _finish_resolved(
            turn, evaluation.matched_transaction, amount=amount, reported_date=reported_date
        )
    return _finish_escalated(turn, evaluation.handoff, amount=amount, reported_date=reported_date)
