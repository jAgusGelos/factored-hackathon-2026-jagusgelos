"""Builders for the structured handoff a human agent receives when a case
escalates: facts (only what the customer actually said, plus identifiers),
actions taken, evidence (transaction ids) and open questions. Every escalation
path goes through one of these, so the shape is always the same and never a
raw transcript. The text is Spanish on purpose: it is an internal artifact.

Each builder also sets the reason the customer is told (`EscalationReason`,
plan.md AD-4). The builders that cover several causes (`confirmation_outcome`,
`explanation_not_accepted`) take it from the caller, which knows the cause.
"""

from __future__ import annotations

from app import cases
from app.case_model import (
    CaseEvaluation,
    CaseState,
    EscalationReason,
    HandoffRecord,
    ReportedCharge,
)
from app.policy import MATCH_DATE_TOLERANCE_DAYS, MAX_CASE_TURNS, ExplanationAssessment
from app.transactions import TransactionCandidate

CUSTOMER_MESSAGE_OMITTED = "[omitido, ver mensajes del caso]"


def _facts(report: ReportedCharge, **extra: str) -> dict[str, str]:
    # Unknown values are omitted, never filled with placeholders.
    facts: dict[str, str] = {}
    if report.amount is not None:
        facts["reported_amount"] = str(report.amount)
    if report.currency is not None:
        facts["currency"] = report.currency
    if report.date is not None:
        facts["reported_date"] = report.date.isoformat()
    if report.merchant:
        facts["reported_merchant"] = report.merchant
    if report.reason:
        facts["dispute_reason"] = report.reason
    return {**facts, **extra}


def _case_evidence(case: cases.Case) -> tuple[str, ...]:
    if case.offered_transaction_ids:
        return case.offered_transaction_ids
    return (case.matched_transaction_id,) if case.matched_transaction_id else ()


def _escalation(
    facts: dict[str, str], action: str, *, customer_reason: EscalationReason, evidence: tuple[str, ...] = (),
    open_questions: tuple[str, ...] = (), matched: TransactionCandidate | None = None,
    candidates: tuple[TransactionCandidate, ...] = (), reasons: tuple[str, ...] = (),
) -> CaseEvaluation:
    return CaseEvaluation(
        state=CaseState.ESCALATED, matched_transaction=matched, candidates=candidates,
        resolution_reasons=reasons, customer_reason=customer_reason,
        handoff=HandoffRecord(facts=facts, actions_taken=(action,), evidence=evidence, open_questions=open_questions),
    )


def human_request(report: ReportedCharge) -> CaseEvaluation:
    return _escalation(
        _facts(report), "Cliente solicitó explícitamente hablar con un agente humano.",
        customer_reason=EscalationReason.HUMAN_REQUESTED,
    )


def ambiguous_match(
    report: ReportedCharge, candidates: tuple[TransactionCandidate, ...], clarification_rounds: int,
) -> CaseEvaluation:
    return _escalation(
        _facts(report, candidate_count=str(len(candidates))),
        f"Se buscaron transacciones del cliente en un rango de {MATCH_DATE_TOLERANCE_DAYS} días; "
        f"{len(candidates)} candidata(s) encontrada(s) tras {clarification_rounds} ronda(s) de aclaración.",
        evidence=tuple(c.transaction_id for c in candidates),
        open_questions=("¿Cuál de las transacciones candidatas corresponde al reclamo?",)
        if candidates else ("No se encontró ninguna transacción candidata para este reclamo.",),
        candidates=candidates, customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def ineligible_match(
    report: ReportedCharge, matched: TransactionCandidate, reasons: tuple[str, ...], *, how_identified: str,
) -> CaseEvaluation:
    return _escalation(
        _facts(report, matched_transaction_id=matched.transaction_id),
        f"{how_identified} El cargo no cumple las condiciones de auto-resolución.",
        evidence=(matched.transaction_id,), open_questions=reasons,
        matched=matched, candidates=(matched,), reasons=reasons, customer_reason=EscalationReason.NEEDS_REVIEW,
    )


def already_credited(report: ReportedCharge, matched: TransactionCandidate, credited_case_id: str) -> CaseEvaluation:
    return _escalation(
        _facts(report, matched_transaction_id=matched.transaction_id, credited_in_case=credited_case_id),
        f"El cargo ya tuvo un crédito provisional en el caso {credited_case_id}; no se acredita dos veces.",
        evidence=(matched.transaction_id,),
        open_questions=("El cliente vuelve a disputar un cargo ya acreditado: revisar el caso anterior.",),
        matched=matched, customer_reason=EscalationReason.ALREADY_CREDITED,
    )


def prior_escalation_same_charge(
    report: ReportedCharge, matched: TransactionCandidate, prior_case_id: str,
) -> CaseEvaluation:
    """The customer already explained this charge in another case (one a
    person now has, or one still open asking for more detail): a new case on
    it goes to a person too, instead of a second explanation with fresh attempts.
    """
    return _escalation(
        _facts(report, matched_transaction_id=matched.transaction_id, prior_case=prior_case_id),
        f"La explicación del cliente sobre este cargo ya se evaluó en el caso {prior_case_id} (derivado a "
        "una persona o todavía abierto pidiendo más detalle); no se vuelve a pedir otra explicación.",
        evidence=(matched.transaction_id,),
        open_questions=(
            f"El cliente abrió otro reclamo por el mismo cargo: revisarlo junto con el caso {prior_case_id}.",
        ),
        matched=matched, customer_reason=EscalationReason.ALREADY_IN_REVIEW,
    )


ASSESSMENT_FAILED = (
    "No se pudo evaluar la explicación: la respuesta del modelo no respetó el formato esperado. "
    "Leer la explicación del cliente en los mensajes del caso."
)


def explanation_facts(assessment: ExplanationAssessment | None, *, too_short: bool = False) -> dict[str, str]:
    if too_short:
        return {"explanation_assessment": "explicación demasiado breve; no se evaluó con el modelo"}
    if assessment is None:
        return {"explanation_assessment": "no evaluable (respuesta del modelo inválida)"}
    return {
        "explanation_summary": f"{assessment.summary} (resumen del modelo)",
        "explanation_specific": "sí" if assessment.specific else "no",
        "explanation_consistent": "sí" if assessment.consistent else "no",
    }


def explanation_not_accepted(
    report: ReportedCharge, matched: TransactionCandidate, why: str, assessment: ExplanationAssessment | None,
    *, customer_reason: EscalationReason, too_short: bool = False,
) -> CaseEvaluation:
    return _escalation(
        _facts(
            report, matched_transaction_id=matched.transaction_id,
            **explanation_facts(assessment, too_short=too_short),
        ),
        "El cliente identificó el cargo y explicó qué pasó, pero la explicación no permite "
        "resolverlo automáticamente.",
        evidence=(matched.transaction_id,), open_questions=(why,), matched=matched,
        customer_reason=customer_reason,
    )


def credit_limit_reached(report: ReportedCharge, matched: TransactionCandidate) -> CaseEvaluation:
    return _escalation(
        _facts(report, matched_transaction_id=matched.transaction_id),
        "El cargo cumplía la política, pero al acreditarlo se superaba el límite de créditos "
        "automáticos del cliente (otro caso se acreditó al mismo tiempo).",
        evidence=(matched.transaction_id,),
        open_questions=("Revisar los créditos automáticos recientes del cliente antes de acreditar este.",),
        matched=matched, customer_reason=EscalationReason.NEEDS_REVIEW,
    )


def not_in_list(report: ReportedCharge, case: cases.Case) -> CaseEvaluation:
    return _escalation(
        _facts(report, charges_shown=str(len(case.offered_transaction_ids))),
        "Se le mostraron al cliente sus movimientos y no encontró el cargo entre ellos.",
        evidence=case.offered_transaction_ids,
        open_questions=(
            "El cargo no aparece entre los movimientos mostrados: ¿otro producto, un cargo "
            "todavía no registrado o una fecha distinta?",
        ),
        customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def unidentified_charge(report: ReportedCharge, case: cases.Case) -> CaseEvaluation:
    return _escalation(
        _facts(report), "No fue posible identificar el cargo con los datos que dio el cliente.",
        evidence=_case_evidence(case),
        open_questions=("Requiere que un agente humano recabe los datos del reclamo.",),
        customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def turn_limit(report: ReportedCharge, case: cases.Case) -> CaseEvaluation:
    return _escalation(
        _facts(report), f"Se alcanzó el máximo de {MAX_CASE_TURNS} mensajes sin resolver el caso.",
        evidence=_case_evidence(case),
        open_questions=("Requiere que un agente humano retome la conversación.",),
        customer_reason=EscalationReason.CHARGE_NOT_IDENTIFIED,
    )


def confirmation_outcome(
    report: ReportedCharge, case: cases.Case, *, customer_confirmation: str, action: str, open_question: str,
    customer_reason: EscalationReason,
) -> CaseEvaluation:
    """Escalation out of `confirming`: the answer the customer gave is a fact,
    and the proposed transaction stays in the evidence.
    """
    return _escalation(
        _facts(report, customer_confirmation=customer_confirmation), action,
        evidence=(case.matched_transaction_id,) if case.matched_transaction_id else (),
        open_questions=(open_question,), customer_reason=customer_reason,
    )


def service_failure(action: str) -> HandoffRecord:
    return HandoffRecord(
        facts={"customer_message": CUSTOMER_MESSAGE_OMITTED},
        actions_taken=(action,),
        evidence=(),
        open_questions=("Requiere revisión manual del mensaje original del cliente.",),
    )
