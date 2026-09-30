"""Friction #7: every handoff has the same seven parts (plan.md AD-2): a
summary of the request, facts verified against the charge record, what the
customer reported (never verified), the policy reasons in their own field,
actions, evidence and only real open questions.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import handoffs
from app.case_model import EscalationReason, ReportedCharge
from app.cases import Case
from app.llm import ConfirmationAnswer
from tests.support import HANDOFF_KEYS, clean_assessment, clean_txn

CHARGE = clean_txn(transaction_id="TRX-9", merchant_name="Uber", amount=38500.0, currency="COP", channel="App")
SAID = ReportedCharge(amount=38500.0, date=date(2026, 6, 14), currency=None, merchant="Uber")
POLICY_REASON = "fraud_score=91.0 at/above the 30.0 threshold"
CUSTOMER_TEXT = "No reconozco un cargo de 38.500 pesos del 14 de junio"


def _case(**overrides) -> Case:
    base = dict(
        case_id="CASE-1", customer_id="CLI-1", state="selecting", language="es", reported_amount=None,
        reported_currency=None, reported_date=None, matched_transaction_id=None, clarification_rounds=0,
        resolution_reference=None, handoff=None, offered_transaction_ids=("TRX-9", "TRX-8"),
    )
    return Case(**{**base, **overrides})


PRODUCERS = {
    "human_request": lambda: handoffs.human_request(SAID).handoff,
    "human_request_explaining": lambda: handoffs.human_request(SAID, CHARGE).handoff,
    "ambiguous_match": lambda: handoffs.ambiguous_match(SAID, (CHARGE,), 2).handoff,
    "ineligible_match": lambda: handoffs.ineligible_match(
        SAID, CHARGE, (POLICY_REASON,), how_identified=handoffs.ChargeIdentification.PICK,
    ).handoff,
    "already_credited": lambda: handoffs.already_credited(
        SAID, CHARGE, "CASE-0", how_identified=handoffs.ChargeIdentification.REPORT,
    ).handoff,
    "prior_escalation": lambda: handoffs.prior_escalation_same_charge(
        SAID, CHARGE, "CASE-0", how_identified=handoffs.ChargeIdentification.PICK,
    ).handoff,
    "explanation_not_accepted": lambda: handoffs.explanation_not_accepted(
        SAID, CHARGE, "why", clean_assessment(summary="s"), customer_reason=EscalationReason.NEEDS_REVIEW,
    ).handoff,
    "credit_limit_reached": lambda: handoffs.credit_limit_reached(SAID, CHARGE).handoff,
    "not_in_list": lambda: handoffs.not_in_list(SAID, _case()).handoff,
    "unidentified_charge": lambda: handoffs.unidentified_charge(SAID, _case()).handoff,
    "turn_limit": lambda: handoffs.turn_limit(SAID, _case()).handoff,
    "confirmation_outcome": lambda: handoffs.confirmation_outcome(
        SAID, _case(matched_transaction_id="TRX-9"), customer_confirmation=ConfirmationAnswer.HUMAN, action="a", open_question="q",
        customer_reason=EscalationReason.HUMAN_REQUESTED, charge=CHARGE,
    ).handoff,
    "service_failure": lambda: handoffs.service_failure("a", SAID),
    "service_failure_with_charge": lambda: handoffs.service_failure("a", SAID, CHARGE),
}


@pytest.mark.parametrize("producer", PRODUCERS)
def test_every_producer_has_the_seven_parts(producer):
    handoff = PRODUCERS[producer]().to_dict()
    assert set(handoff) == HANDOFF_KEYS
    assert handoff["request_summary"]
    assert handoff["actions_taken"] and handoff["open_questions"]
    assert CUSTOMER_TEXT not in str(handoff)


@pytest.mark.parametrize("producer", PRODUCERS)
def test_policy_reasons_never_become_open_questions(producer):
    handoff = PRODUCERS[producer]()
    assert not set(handoff.policy_reasons) & set(handoff.open_questions)
    assert not any("threshold" in q or "fraud_score" in q for q in handoff.open_questions)


def test_a_policy_escalation_carries_the_charge_record_and_its_reasons():
    handoff = PRODUCERS["ineligible_match"]()
    assert handoff.verified_facts == {
        "transaction_id": "TRX-9", "merchant": "Uber", "amount": "38500.0", "currency": "COP",
        "date": "2026-06-09", "channel": "App", "status": "Approved", "category": "Retail", "charge_confirmed": "sí",
    }
    assert handoff.policy_reasons == (POLICY_REASON,)
    assert handoff.open_questions == (handoffs.POLICY_REVIEW_QUESTION,)


def test_what_the_customer_said_stays_apart_from_the_record():
    handoff = PRODUCERS["ineligible_match"]()
    assert handoff.customer_reported == {"amount": "38500.0", "date": "2026-06-14", "merchant": "Uber"}


def test_a_request_without_details_reports_nothing_and_verifies_nothing():
    handoff = handoffs.human_request(ReportedCharge(amount=None, date=None, currency=None)).handoff
    assert handoff.customer_reported == {}
    assert handoff.verified_facts == {}
    assert handoff.request_summary == handoffs._REQUEST_SUMMARY[EscalationReason.HUMAN_REQUESTED]


def test_a_proposed_charge_is_verified_but_marked_unconfirmed():
    handoff = PRODUCERS["confirmation_outcome"]()
    assert handoff.verified_facts["charge_confirmed"] == "no"
    assert "sin confirmar" in handoff.request_summary
    assert handoff.customer_reported["customer_confirmation"] == "human"


@pytest.mark.parametrize(
    ("how_identified", "confirmed"),
    [
        (handoffs.ChargeIdentification.REPORT, "no"),
        (handoffs.ChargeIdentification.MERCHANT, "no"),
        (handoffs.ChargeIdentification.PICK, "sí"),
        (handoffs.ChargeIdentification.CONFIRMATION, "sí"),
        (handoffs.ChargeIdentification.EXPLANATION, "sí"),
    ],
)
def test_a_charge_is_confirmed_only_when_the_customer_picked_or_confirmed_it(how_identified, confirmed):
    handoff = handoffs.ineligible_match(SAID, CHARGE, (POLICY_REASON,), how_identified=how_identified).handoff
    assert handoff.verified_facts["charge_confirmed"] == confirmed
    assert ("sin confirmar" in handoff.request_summary) == (confirmed == "no")


@pytest.mark.parametrize(("producer", "confirmed"), [("already_credited", "no"), ("prior_escalation", "sí")])
def test_a_charge_already_handled_says_how_it_was_identified(producer, confirmed):
    assert PRODUCERS[producer]().verified_facts["charge_confirmed"] == confirmed


def test_a_service_failure_keeps_what_the_customer_reported():
    reported = PRODUCERS["service_failure"]().customer_reported
    assert reported["merchant"] == "Uber" and reported["amount"] == "38500.0"
    assert reported["customer_message"] == handoffs.CUSTOMER_MESSAGE_OMITTED


def test_the_confirmed_charge_of_a_request_while_explaining_is_verified():
    handoff = PRODUCERS["human_request_explaining"]()
    assert handoff.verified_facts["charge_confirmed"] == "sí"
    assert handoff.evidence == ("TRX-9",)


@pytest.mark.parametrize(
    ("producer", "key", "value"),
    [
        ("already_credited", "credited_in_case", "CASE-0"),
        ("prior_escalation", "prior_case", "CASE-0"),
        ("ambiguous_match", "candidate_count", "1"),
        ("not_in_list", "charges_shown", "2"),
    ],
)
def test_system_facts_are_verified_facts(producer, key, value):
    assert PRODUCERS[producer]().verified_facts[key] == value


def test_the_model_read_of_an_explanation_is_reported_not_verified():
    handoff = PRODUCERS["explanation_not_accepted"]()
    assert handoff.customer_reported["explanation_summary"] == "s (resumen del modelo)"
    assert "explanation_summary" not in handoff.verified_facts
    assert handoff.policy_reasons == ("why",)


@pytest.mark.parametrize("reason", list(EscalationReason))
def test_the_request_summary_is_the_template_for_its_reason(reason):
    assert handoffs.request_summary(reason, None, confirmed=True) == handoffs._REQUEST_SUMMARY[reason]
    with_charge = handoffs.request_summary(reason, CHARGE, confirmed=True)
    assert with_charge == f"{handoffs._REQUEST_SUMMARY[reason]} Cargo en disputa: Uber, COP 38.500, 2026-06-09."


def test_a_confirmed_charge_found_already_credited_keeps_that_open_question():
    credited = handoffs.already_credited(SAID, CHARGE, "CASE-0", how_identified=handoffs.ChargeIdentification.PICK)
    handoff = handoffs.reverification_failed(SAID, _case(), credited, CHARGE).handoff
    assert handoff.open_questions == credited.handoff.open_questions
    assert handoff.verified_facts["charge_confirmed"] == "sí"
