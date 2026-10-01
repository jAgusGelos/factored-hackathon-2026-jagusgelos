"""The explanation step (Milestone 9): once the charge is identified and
passes screening, the customer says in their own words what happened. The
model only assesses that account; `policy.evaluate_explanation` decides
whether to ask for one more detail or escalate, and otherwise the evidence
check for the reason it names (`policy_verdict`, supplied by
`app/state_machine.py`) decides the credit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from app import config, handoffs, llm, replies
from app.case_model import CaseEvaluation, CaseState, EscalationReason, ReportedCharge
from app.case_turn import (
    ChatReply,
    Turn,
    charge_prompt_context,
    escalate,
    finish_escalated,
    force_escalation,
    transition,
)
from app.credit import finish_resolved
from app.llm import PromptScene
from app.policy import (
    MAX_EXPLANATION_ATTEMPTS,
    MIN_EXPLANATION_WORDS,
    REASONS_REQUIRING_A_PERSON,
    DisputeReason,
    ExplanationAssessment,
    ExplanationDecision,
    ExplanationVerdict,
    MissingDetail,
    evaluate_explanation,
)
from app.transactions import TransactionCandidate, get_own_transaction


class PolicyVerdict(Protocol):
    def __call__(
        self, turn: Turn, matched: TransactionCandidate, report: ReportedCharge,
        how_identified: handoffs.ChargeIdentification,
        *, reason: DisputeReason | None = None,
    ) -> CaseEvaluation: ...


def ask_for_explanation(
    turn: Turn, matched: TransactionCandidate, *, expected_states: tuple[CaseState, ...],
    expected_offered: tuple[str, ...] | None = None, expected_match: str | None = None,
) -> ChatReply:
    """The charge is identified and policy-eligible: before any credit the
    customer explains, in their own words, what happened with it.
    """
    lost = transition(
        turn, CaseState.AWAITING_EXPLANATION, expected_states=expected_states,
        expected_offered_transaction_ids=expected_offered, expected_matched_transaction_id=expected_match,
        matched_transaction_id=matched.transaction_id,
    )
    if lost:
        return lost
    turn.log_event("explanation_requested", {"matched_transaction_id": matched.transaction_id})
    fallback = replies.ask_for_explanation(matched, turn.language)
    reply = turn.generate_reply(
        charge_prompt_context(turn, CaseState.AWAITING_EXPLANATION, matched), fallback=fallback,
    )
    if not replies.names_the_facts(reply, matched, turn.language):
        turn.log_event("explanation_request_replaced", {"reason": "facts_missing"})
        reply = fallback
    return turn.reply(CaseState.AWAITING_EXPLANATION, reply)


_TOO_SHORT = ExplanationAssessment(
    reason=DisputeReason.UNCLEAR, specific=False, consistent=True, contradictions=(),
    summary="Explicación demasiado breve para evaluar.",
)


def _assess(turn: Turn, explanation: str, matched: TransactionCandidate) -> ExplanationAssessment | None:
    """Raises `llm.LLMUnavailable`. None when the model's answer is unusable."""
    if _too_short(explanation):
        return _TOO_SHORT
    assessment = llm.assess_explanation(
        explanation, charge=charge_prompt_context(turn, CaseState.AWAITING_EXPLANATION, matched),
    )
    if assessment is None:
        turn.log_event("explanation_parse_failed", {"call": "assess_explanation"})
    return assessment


def _too_short(explanation: str) -> bool:
    return len(explanation.split()) < MIN_EXPLANATION_WORDS


def _explanation_verdict(
    assessment: ExplanationAssessment | None, *, attempts_left: bool,
) -> ExplanationDecision:
    """An unusable model answer is the model's failure, not the customer's:
    ask once more, then escalate saying so.
    """
    if assessment is not None:
        return evaluate_explanation(assessment, attempts_left=attempts_left)
    if attempts_left:
        return ExplanationDecision.needs_detail()
    return ExplanationDecision.escalate(handoffs.ASSESSMENT_FAILED)


def _asks_for_a_person(turn: Turn, text: str) -> bool:
    """An explanation still too short to be assessed (so no model reads it)
    checked for a request for a person with the extraction call; only its
    `wants_human` is used. If the model is unavailable the text counts as not asking (logged), so a
    detection failure never escalates by itself.
    """
    try:
        extraction = llm.extract_entities(text, language=turn.language, today=config.DATA_AS_OF)
    except llm.LLMUnavailable as exc:
        turn.log_event("human_request_detection_failed", llm.failure_payload("extract_entities", exc))
        return False
    if extraction.wants_human:
        turn.log_event("human_request_detected", {"via": "extract"})
    return extraction.wants_human


def handle_explanation(
    turn: Turn, text: str, *, policy_verdict: PolicyVerdict, on_human_request: Callable[[Turn], ChatReply],
) -> ChatReply:
    """The customer's account of what happened. The model only assesses it;
    `policy.evaluate_explanation` may ask for one more detail or escalate, and
    otherwise the charge still has to pass the evidence check for the reason
    the explanation names (AD-13) before any credit.

    A text that asks for a person goes to `on_human_request` (supplied by
    `app/state_machine.py`) and is never counted as an explanation attempt
    (plan.md AD-8).
    """
    case = turn.case
    matched = get_own_transaction(turn.session, case.matched_transaction_id) if case.matched_transaction_id else None
    explanation = f"{case.explanation_text}\n{text}" if case.explanation_text else text
    if matched is None:
        return escalate(
            turn, handoffs.unidentified_charge(turn.report, case), account_given=bool(explanation.strip()),
        )
    too_short = _too_short(explanation)
    # Only when the assessment will not run (it reads `wants_human` itself):
    # at most one model call per turn, inside the shared turn budget.
    if too_short and _asks_for_a_person(turn, text):
        return on_human_request(turn)
    try:
        assessment = _assess(turn, explanation, matched)
    except llm.LLMUnavailable as exc:
        return force_escalation(
            turn, event_type="llm_unavailable", failed_call="assess_explanation",
            action_taken="El servicio de NLU no respondió al evaluar la explicación del cliente.", error=exc,
            charge=matched,
        )
    if assessment is not None and assessment.wants_human:
        turn.log_event("human_request_detected", {"via": "assessment"})
        return on_human_request(turn)
    attempts_left = case.explanation_attempts + 1 < MAX_EXPLANATION_ATTEMPTS
    decision = _explanation_verdict(assessment, attempts_left=attempts_left)
    turn.log_event(
        "explanation_assessed",
        {"verdict": decision.verdict, "reason": assessment.reason if assessment else None,
         "specific": assessment.specific if assessment else None,
         "consistent": assessment.consistent if assessment else None},
    )
    report = replace(turn.report, reason=assessment.reason if assessment else None)

    if decision.verdict == ExplanationVerdict.NEEDS_DETAIL:
        return _ask_for_more_detail(turn, text, assessment.missing_detail if assessment else None)
    if decision.verdict == ExplanationVerdict.ESCALATE:
        return finish_escalated(
            turn,
            handoffs.explanation_not_accepted(
                report, matched, decision.reason_to_escalate, assessment, too_short=too_short,
                customer_reason=_customer_reason(assessment, decision),
            ),
            report, account_given=True,
        )
    # The explanation raised no red flag; the evidence check for the reason it
    # names decides (a persuasive story alone never credits anything).
    evaluation = policy_verdict(
        turn, matched, report, handoffs.ChargeIdentification.EXPLANATION, reason=assessment.reason,
    )
    if evaluation.state == CaseState.RESOLVED_AUTO:
        return finish_resolved(
            turn, matched, reason=assessment.reason, twins=evaluation.duplicate_twins,
            expected_states=(CaseState.AWAITING_EXPLANATION,), expected_match=matched.transaction_id,
        )
    reported = {
        **evaluation.handoff.customer_reported, **handoffs.explanation_reported(assessment, too_short=too_short),
    }
    handoff = replace(evaluation.handoff, customer_reported=reported)
    return finish_escalated(turn, replace(evaluation, handoff=handoff), report, account_given=True)


# The dispute reasons a person handles, as the customer is told them.
_PERSON_REASONS = {
    DisputeReason.NOT_RECEIVED: EscalationReason.NOT_RECEIVED,
    DisputeReason.WRONG_AMOUNT: EscalationReason.WRONG_AMOUNT,
    DisputeReason.CARD_LOST_STOLEN: EscalationReason.CARD_LOST_STOLEN,
}


def _customer_reason(assessment: ExplanationAssessment | None, decision: ExplanationDecision) -> EscalationReason:
    """The reason the customer gave, when that reason alone sent the case to a
    person; a vague, contradictory or unassessable explanation is NEEDS_REVIEW.
    """
    if assessment is not None and decision.reason_to_escalate == REASONS_REQUIRING_A_PERSON.get(assessment.reason):
        return _PERSON_REASONS[assessment.reason]
    return EscalationReason.NEEDS_REVIEW


def _ask_for_more_detail(turn: Turn, text: str, missing_detail: MissingDetail | None) -> ChatReply:
    """Appends this turn's text in SQL, so two messages sent at the same time
    both end up in the explanation the next assessment reads. The question
    names only the detail the assessment says is missing (a closed enum, never
    the customer's words), so it does not ask again for what they already said.
    """
    lost = transition(
        turn, CaseState.AWAITING_EXPLANATION, expected_states=(CaseState.AWAITING_EXPLANATION,),
        append_explanation=text, add_explanation_attempt=True,
    )
    if lost:
        return lost
    context = llm.build_prompt_context(
        case_state=PromptScene.EXPLANATION_FOLLOWUP, language=turn.language, missing_detail=missing_detail,
    )
    reply = turn.generate_reply(context, fallback=replies.explanation_followup(missing_detail, turn.language))
    return turn.reply(CaseState.AWAITING_EXPLANATION, reply)
