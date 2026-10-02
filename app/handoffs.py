"""Builders for the structured handoff (`HandoffRecord`) a human agent
receives when a case escalates. Every escalation path goes through one of
these, so the shape is always the same and never a raw transcript. The text is Spanish on purpose: it is an
internal artifact.

Each builder also sets the reason the customer is told (`EscalationReason`,
plan.md AD-4). The builders that cover several causes (`confirmation_outcome`,
`explanation_not_accepted`) take it from the caller, which knows the cause.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum

from app import cases
from app.case_model import (
    CaseEvaluation,
    CaseState,
    EscalationReason,
    HandoffRecord,
    ReportedCharge,
)
from app.charge_search import iso_day
from app.llm import ConfirmationAnswer
from app.policy import (
    MATCH_DATE_TOLERANCE_DAYS,
    MAX_CASE_TURNS,
    ExplanationAssessment,
    StatementField,
    Tristate,
    known_fact,
    open_facts,
)
from app.replies import format_amount
from app.transactions import TransactionCandidate

CUSTOMER_MESSAGE_OMITTED = "[omitido, ver mensajes del caso]"


class ChargeIdentification(StrEnum):
    REPORT = "Se localizó una transacción que coincide con el monto y la fecha reportados."
    PICK = "El cliente eligió este cargo de la lista de sus movimientos."
    MERCHANT = "El cliente nombró el comercio y es su único cargo que coincide."
    CONFIRMATION = "El cliente confirmó el cargo propuesto."
    EXPLANATION = "El cliente identificó el cargo y explicó qué pasó."


_IDENTIFIED_WITHOUT_THE_CUSTOMER = frozenset({ChargeIdentification.REPORT, ChargeIdentification.MERCHANT})


def _confirmed_by_customer(how_identified: ChargeIdentification) -> bool:
    return how_identified not in _IDENTIFIED_WITHOUT_THE_CUSTOMER


POLICY_REVIEW_QUESTION = "¿Corresponde un reintegro después de revisar los motivos de política?"
EXPLANATION_REVIEW_QUESTION = (
    "¿Qué pasó con este cargo? Leer la explicación del cliente en los mensajes del caso y decidir "
    "si corresponde un reintegro."
)

_REQUEST_SUMMARY = {
    EscalationReason.HUMAN_REQUESTED: "El cliente pidió hablar con una persona sobre un cargo que no reconoce.",
    EscalationReason.CHARGE_NOT_IDENTIFIED: (
        "El cliente reporta un cargo que no reconoce y no se pudo identificar entre sus movimientos."
    ),
    EscalationReason.NEEDS_REVIEW: (
        "El cliente disputa un cargo que necesita la revisión de una persona antes de cualquier reintegro."
    ),
    EscalationReason.NOT_RECEIVED: "El cliente reconoce la compra pero dice que no recibió el producto o servicio.",
    EscalationReason.WRONG_AMOUNT: "El cliente reconoce la compra pero discute el monto cobrado.",
    EscalationReason.CARD_LOST_STOLEN: "El cliente reporta un cargo hecho con una tarjeta perdida o robada.",
    EscalationReason.ALREADY_CREDITED: "El cliente vuelve a disputar un cargo que ya recibió un crédito provisional.",
    EscalationReason.ALREADY_IN_REVIEW: "El cliente abrió otro reclamo por un cargo que ya está en revisión.",
    EscalationReason.SERVICE_ISSUE: "No se pudo procesar el reclamo del cliente por una falla del servicio.",
}


def request_summary(reason: EscalationReason, charge: TransactionCandidate | None, *, confirmed: bool | None) -> str:
    summary = _REQUEST_SUMMARY[reason]
    if charge is None:
        return summary
    label = "Cargo identificado, sin confirmar por el cliente" if confirmed is False else "Cargo en disputa"
    merchant = charge.merchant_name or "comercio sin nombre"
    return f"{summary} {label}: {merchant}, {format_amount(charge.amount, charge.currency)}, {iso_day(charge)}."


def _yes_no(flag: bool) -> str:
    return "sí" if flag else "no"


def _verified_charge(charge: TransactionCandidate | None, *, confirmed: bool | None) -> dict[str, str]:
    if charge is None:
        return {}
    record = {
        "transaction_id": charge.transaction_id,
        "merchant": charge.merchant_name,
        "amount": str(charge.amount),
        "currency": charge.currency,
        "date": iso_day(charge),
        "channel": charge.channel,
        "status": charge.transaction_status,
        "category": charge.merchant_category,
    }
    verified = {k: v for k, v in record.items() if v is not None}
    if confirmed is None:
        return verified
    return {**verified, "charge_confirmed": _yes_no(confirmed)}


def _reported(report: ReportedCharge) -> dict[str, str]:
    # Unknown values are omitted, never filled with placeholders or defaults.
    reported = {
        "amount": str(report.amount) if report.amount is not None else None,
        "currency": report.currency,
        "date": report.date.isoformat() if report.date is not None else None,
        "merchant": report.merchant,
        "dispute_reason": report.reason,
    }
    return {k: str(v) for k, v in reported.items() if v}


def _charge_evidence(charge: TransactionCandidate | None) -> tuple[str, ...]:
    return (charge.transaction_id,) if charge is not None else ()


def _case_evidence(case: cases.Case) -> tuple[str, ...]:
    if case.offered_transaction_ids:
        return case.offered_transaction_ids
    return (case.matched_transaction_id,) if case.matched_transaction_id else ()


def _escalation(
    report: ReportedCharge, action: str, *, customer_reason: EscalationReason,
    charge: TransactionCandidate | None = None, charge_confirmed: bool | None = True,
    system_facts: dict[str, str] | None = None, reported_extra: dict[str, str] | None = None,
    policy_reasons: tuple[str, ...] = (), evidence: tuple[str, ...] = (), open_questions: tuple[str, ...] = (),
    matched: TransactionCandidate | None = None, candidates: tuple[TransactionCandidate, ...] = (),
) -> CaseEvaluation:
    return CaseEvaluation(
        state=CaseState.ESCALATED, matched_transaction=matched, candidates=candidates,
        resolution_reasons=policy_reasons, customer_reason=customer_reason,
        handoff=HandoffRecord(
            request_summary=request_summary(customer_reason, charge, confirmed=charge_confirmed),
            verified_facts={**_verified_charge(charge, confirmed=charge_confirmed), **(system_facts or {})},
            customer_reported={**_reported(report), **(reported_extra or {})},
            policy_reasons=policy_reasons, actions_taken=(action,), evidence=evidence,
            open_questions=open_questions,
        ),
    )


def human_request(
    report: ReportedCharge, charge: TransactionCandidate | None = None, *, explained: bool = False,
) -> CaseEvaluation:
    """`charge`: one the customer already confirmed or picked, if any.
    `explained`: they also explained it before asking for a person.
    """
    if charge is None:
        question = "¿Qué cargo quiere revisar el cliente y qué pasó con él?"
    elif explained:
        question = EXPLANATION_REVIEW_QUESTION
    else:
        question = "¿Qué pasó con este cargo? El cliente pidió una persona antes de explicarlo."
    return _escalation(
        report, "Cliente solicitó explícitamente hablar con un agente humano.",
        customer_reason=EscalationReason.HUMAN_REQUESTED, charge=charge,
        evidence=_charge_evidence(charge), open_questions=(question,),
    )


def ambiguous_match(
    report: ReportedCharge, candidates: tuple[TransactionCandidate, ...], clarification_rounds: int,
) -> CaseEvaluation:
    return _escalation(
        report,
        f"Se buscaron transacciones del cliente en un rango de {MATCH_DATE_TOLERANCE_DAYS} días; "
        f"{len(candidates)} candidata(s) encontrada(s) tras {clarification_rounds} ronda(s) de aclaración.",
        system_facts={"candidate_count": str(len(candidates))},
        evidence=tuple(c.transaction_id for c in candidates),
        open_questions=("¿Cuál de las transacciones candidatas corresponde al reclamo?",)
        if candidates else ("No se encontró ninguna transacción candidata para este reclamo.",),
        candidates=candidates, customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def ineligible_match(
    report: ReportedCharge, matched: TransactionCandidate, reasons: tuple[str, ...],
    *, how_identified: ChargeIdentification,
) -> CaseEvaluation:
    return _escalation(
        report, f"{how_identified} El cargo no cumple las condiciones de auto-resolución.",
        charge=matched, charge_confirmed=_confirmed_by_customer(how_identified),
        policy_reasons=reasons, evidence=(matched.transaction_id,),
        open_questions=(POLICY_REVIEW_QUESTION,), matched=matched, candidates=(matched,),
        customer_reason=EscalationReason.NEEDS_REVIEW,
    )


def already_credited(
    report: ReportedCharge, matched: TransactionCandidate, credited_case_id: str,
    *, how_identified: ChargeIdentification,
) -> CaseEvaluation:
    return _escalation(
        report, f"El cargo ya tuvo un crédito provisional en el caso {credited_case_id}; no se acredita dos veces.",
        charge=matched, charge_confirmed=_confirmed_by_customer(how_identified), system_facts={"credited_in_case": credited_case_id},
        evidence=(matched.transaction_id,),
        open_questions=("El cliente vuelve a disputar un cargo ya acreditado: revisar el caso anterior.",),
        matched=matched, customer_reason=EscalationReason.ALREADY_CREDITED,
    )


def prior_escalation_same_charge(
    report: ReportedCharge, matched: TransactionCandidate, prior_case_id: str,
    *, how_identified: ChargeIdentification,
) -> CaseEvaluation:
    """The customer already explained this charge in another case (one a
    person now has, or one still open asking for more detail): a new case on
    it goes to a person too, instead of a second explanation with fresh attempts.
    """
    return _escalation(
        report,
        f"La explicación del cliente sobre este cargo ya se evaluó en el caso {prior_case_id} (derivado a "
        "una persona o todavía abierto pidiendo más detalle); no se vuelve a pedir otra explicación.",
        charge=matched, charge_confirmed=_confirmed_by_customer(how_identified),
        system_facts={"prior_case": prior_case_id},
        evidence=(matched.transaction_id,),
        open_questions=(
            f"El cliente abrió otro reclamo por el mismo cargo: revisarlo junto con el caso {prior_case_id}.",
        ),
        matched=matched, customer_reason=EscalationReason.ALREADY_IN_REVIEW,
    )


def _model_summary(summary: str) -> str:
    return f"{summary} (resumen del modelo)"


ASSESSMENT_FAILED = (
    "No se pudo evaluar la explicación: la respuesta del modelo no respetó el formato esperado. "
    "Leer la explicación del cliente en los mensajes del caso."
)


def with_reported(evaluation: CaseEvaluation, extra: dict[str, str]) -> CaseEvaluation:
    """The same verdict with more of what the customer reported (or the model read from it)."""
    reported = {**evaluation.handoff.customer_reported, **extra}
    return replace(evaluation, handoff=replace(evaluation.handoff, customer_reported=reported))


def explanation_reported(assessment: ExplanationAssessment | None, *, too_short: bool = False) -> dict[str, str]:
    if too_short:
        return {"explanation_assessment": "explicación demasiado breve; no se evaluó con el modelo"}
    if assessment is None:
        return {"explanation_assessment": "no evaluable (respuesta del modelo inválida)"}
    summary = {"explanation_summary": _model_summary(assessment.summary)} if assessment.summary else {}
    return {
        **summary,
        "explanation_specific": _yes_no(assessment.specific),
        "explanation_consistent": _yes_no(assessment.consistent),
    }


def explanation_not_accepted(
    report: ReportedCharge, matched: TransactionCandidate, why: str, assessment: ExplanationAssessment | None,
    *, customer_reason: EscalationReason, too_short: bool = False,
) -> CaseEvaluation:
    return _escalation(
        report,
        "El cliente identificó el cargo y explicó qué pasó, pero la explicación no permite "
        "resolverlo automáticamente.",
        charge=matched, reported_extra=explanation_reported(assessment, too_short=too_short),
        policy_reasons=(why,), evidence=(matched.transaction_id,), open_questions=(EXPLANATION_REVIEW_QUESTION,),
        matched=matched, customer_reason=customer_reason,
    )


def credit_limit_reached(report: ReportedCharge, matched: TransactionCandidate) -> CaseEvaluation:
    return _escalation(
        report,
        "El cargo cumplía la política, pero al acreditarlo se superaba el límite de créditos "
        "automáticos del cliente (otro caso se acreditó al mismo tiempo).",
        charge=matched, evidence=(matched.transaction_id,),
        open_questions=("Revisar los créditos automáticos recientes del cliente antes de acreditar este.",),
        matched=matched, customer_reason=EscalationReason.NEEDS_REVIEW,
    )


def not_in_list(report: ReportedCharge, case: cases.Case) -> CaseEvaluation:
    return _escalation(
        report, "Se le mostraron al cliente sus movimientos y no encontró el cargo entre ellos.",
        system_facts={"charges_shown": str(len(case.offered_transaction_ids))},
        evidence=case.offered_transaction_ids,
        open_questions=(
            "El cargo no aparece entre los movimientos mostrados: ¿otro producto, un cargo "
            "todavía no registrado o una fecha distinta?",
        ),
        customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def unidentified_charge(report: ReportedCharge, case: cases.Case) -> CaseEvaluation:
    return _escalation(
        report, "No fue posible identificar el cargo con los datos que dio el cliente.",
        evidence=_case_evidence(case),
        open_questions=("Requiere que un agente humano recabe los datos del reclamo.",),
        customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def turn_limit(report: ReportedCharge, case: cases.Case) -> CaseEvaluation:
    return _escalation(
        report, f"Se alcanzó el máximo de {MAX_CASE_TURNS} mensajes sin resolver el caso.",
        evidence=_case_evidence(case),
        open_questions=("Requiere que un agente humano retome la conversación.",),
        customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def confirmation_outcome(
    report: ReportedCharge, case: cases.Case, *, customer_confirmation: ConfirmationAnswer, action: str,
    open_question: str,
    customer_reason: EscalationReason, charge: TransactionCandidate | None = None,
    policy_reasons: tuple[str, ...] = (),
) -> CaseEvaluation:
    return _escalation(
        report, action, charge=charge, charge_confirmed=customer_confirmation == ConfirmationAnswer.YES,
        reported_extra={"customer_confirmation": str(customer_confirmation)}, policy_reasons=policy_reasons,
        evidence=(case.matched_transaction_id,) if case.matched_transaction_id else (),
        open_questions=(open_question,), customer_reason=customer_reason,
    )


def reverification_failed(
    report: ReportedCharge, case: cases.Case, evaluation: CaseEvaluation, charge: TransactionCandidate | None,
) -> CaseEvaluation:
    if evaluation.resolution_reasons:
        question = POLICY_REVIEW_QUESTION
    elif evaluation.handoff is not None:
        question = "; ".join(evaluation.handoff.open_questions)
    else:
        question = "No se pudo volver a verificar la transacción propuesta."
    return confirmation_outcome(
        report, case, customer_confirmation=ConfirmationAnswer.YES,
        action="El cliente confirmó el cargo propuesto, pero la política no permitió auto-resolverlo al re-verificar.",
        open_question=question, customer_reason=EscalationReason.NEEDS_REVIEW,
        charge=charge, policy_reasons=evaluation.resolution_reasons,
    )


class StatementStatus(StrEnum):
    """How the statement step ended (`customer_reported["statement_status"]`)."""

    GIVEN = "given"
    DECLINED = "declined"
    SUMMARY_UNAVAILABLE = "summary_unavailable"


_STATEMENT_OPEN_QUESTIONS = {
    StatementField.DENIES_PURCHASE: "Confirmar con el cliente si hizo o autorizó esta compra.",
    StatementField.MERCHANT_KNOWN: "Confirmar con el cliente si conoce el comercio o lo usó alguna vez.",
    StatementField.CARD_POSSESSION: "Confirmar con el cliente si tiene la tarjeta consigo.",
    StatementField.HOW_NOTICED: "Confirmar con el cliente cómo y cuándo se dio cuenta del cargo.",
    StatementField.OTHER_SUSPICIOUS_ACTIVITY: (
        "Confirmar con el cliente si hay otros cargos o movimientos que no reconoce."
    ),
}
CARD_LOST_QUESTION = (
    "Confirmar con el cliente si perdió la tarjeta o se la robaron, y si corresponde bloquearla."
)


def _statement_open_questions(facts: dict[str, str | None]) -> tuple[str, ...]:
    questions = tuple(_STATEMENT_OPEN_QUESTIONS[fact] for fact in open_facts(facts, _STATEMENT_OPEN_QUESTIONS))
    return (*questions, CARD_LOST_QUESTION) if facts.get(StatementField.CARD_POSSESSION) == Tristate.NO else questions


def with_statement(
    handoff: dict, *, status: StatementStatus, summary: str = "", facts: dict[str, str | None] | None = None,
) -> dict:
    """A pending handoff (`case_turn.PendingEscalation.handoff`) with the
    statement step's outcome: its status, the model's summary (only when
    given) and every known key fact in `customer_reported`, and one advisor
    task per fact still unknown in `open_questions`. Every other field is the
    pending one, untouched: the statement never changes the decision.
    """
    facts = facts or {}
    reported = {"statement_status": str(status)}
    if status == StatementStatus.GIVEN:
        reported["statement_summary"] = _model_summary(summary)
    reported |= {fact: str(value) for fact, value in facts.items() if known_fact(value)}
    return {
        **handoff,
        "customer_reported": {**handoff["customer_reported"], **reported},
        "open_questions": [*handoff["open_questions"], *_statement_open_questions(facts)],
    }


def service_failure(
    action: str, report: ReportedCharge, charge: TransactionCandidate | None = None,
) -> HandoffRecord:
    """`charge`: one the customer already identified, if any."""
    return _escalation(
        report, action, customer_reason=EscalationReason.SERVICE_ISSUE, charge=charge,
        reported_extra={"customer_message": CUSTOMER_MESSAGE_OMITTED}, evidence=_charge_evidence(charge),
        open_questions=("Requiere revisión manual del mensaje original del cliente.",),
    ).handoff
