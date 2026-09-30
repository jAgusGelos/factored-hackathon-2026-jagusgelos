"""Friction #5: when a case goes to a person the customer is told, from code
and never from the model, which charge (only when they identified it), why,
their case number and the contact deadline. The same values travel in
`ChatReply.escalation` for the chat's card and client panel, on the
escalating reply and on every later reply about that case (plan.md AD-3..AD-7).
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import patch

import anthropic
import duckdb
import pytest

from app import case_turn, cases, db, explanation, handoffs, llm, replies, state_machine, turns
from app.case_model import (
    CaseEvaluation,
    CaseState,
    CustomerAction,
    EscalationReason,
    ReportedCharge,
)
from app.case_turn import Turn, transition
from app.llm import Language
from app.policy import ESCALATION_CONTACT_BUSINESS_DAYS, REASONS_REQUIRING_A_PERSON
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    CONTACT_DEADLINE,
    CONTRADICTED_ASSESSMENT,
    DUPLICATE_ASSESSMENT,
    EXPLANATION,
    FRAUD_SCORE_CHARGE,
    NOT_RECEIVED_ASSESSMENT,
    app_db_rows,
    assert_escalation_notice,
    charge_extraction,
    clean_txn,
    demo_session,
    mock_anthropic_client,
    mocked_turn,
    reach_confirming,
    reach_explaining,
    requires_real_fixture,
)

CASE_NUMBER = "CASE-ABCDEF123456"
COP_CHARGE = clean_txn(amount=38500.0, currency="COP", amount_usd=9.6, merchant_name="Uber")
_FORBIDDEN = ("fraud", "score", "umbral", "threshold", "USD", "classifier", "escalad")


# -- The notice text ------------------------------------------------------------


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("reason", list(EscalationReason))
@pytest.mark.parametrize("charge", [None, COP_CHARGE], ids=["no_charge", "charge"])
def test_every_reason_gets_a_complete_notice_without_internal_details(reason, language, charge):
    text, notice = replies.escalation_notice(CASE_NUMBER, reason, charge=charge, language=language)

    assert CASE_NUMBER in text
    assert CONTACT_DEADLINE[language] in text
    assert notice["reason"] and notice["reason"] in text
    assert notice == {
        "case_number": CASE_NUMBER, "charge": notice["charge"], "reason": notice["reason"],
        "contact_business_days": ESCALATION_CONTACT_BUSINESS_DAYS,
    }
    assert (notice["charge"] is not None) == (charge is not None)
    if charge is not None:
        assert "Uber" in text and "38.500" in text
    lowered = text.lower()
    for word in _FORBIDDEN:
        assert word.lower() not in lowered, word
    assert not re.search(r"(?<![\d.,])(200|30)(?![\d.,])", text)
    for internal in REASONS_REQUIRING_A_PERSON.values():
        assert internal not in text
    assert "?" not in text


def test_every_reason_has_a_text_in_both_languages_and_they_differ():
    for reason in EscalationReason:
        es = replies.escalation_summary(CASE_NUMBER, reason, charge=None, language=Language.ES)["reason"]
        pt = replies.escalation_summary(CASE_NUMBER, reason, charge=None, language=Language.PT)["reason"]
        assert es and pt and es != pt


def test_the_notice_says_the_chat_no_longer_adds_to_the_case():
    es, _ = replies.escalation_notice(CASE_NUMBER, EscalationReason.NEEDS_REVIEW, charge=None, language=Language.ES)
    pt, _ = replies.escalation_notice(CASE_NUMBER, EscalationReason.NEEDS_REVIEW, charge=None, language=Language.PT)
    assert "Este chat ya no agrega información al caso" in es
    assert "Este chat não adiciona mais informações ao caso" in pt


def test_a_legacy_case_without_a_reason_gets_no_reason_line():
    notice = replies.escalation_summary(CASE_NUMBER, None, charge=None, language=Language.ES)
    assert notice["reason"] is None and notice["charge"] is None


@pytest.mark.parametrize("language", list(Language))
def test_the_terminal_reply_of_an_escalated_case_names_the_case_and_the_deadline(language):
    text = replies.terminal_case(CaseState.ESCALATED, case_number=CASE_NUMBER, reference=None, language=language)
    assert CASE_NUMBER in text and str(ESCALATION_CONTACT_BUSINESS_DAYS) in text


# -- The reason each builder sets ---------------------------------------------------


REPORT = ReportedCharge(amount=38500.0, date=None, currency="COP")
PICKED = handoffs.ChargeIdentification.PICK


def _case(**overrides) -> cases.Case:
    base = dict(
        case_id=CASE_NUMBER, customer_id="C1", language="es", state="selecting", reported_amount=None,
        reported_currency=None, reported_date=None, matched_transaction_id=None, clarification_rounds=0,
        resolution_reference=None, handoff=None,
    )
    return cases.Case(**{**base, **overrides})


@pytest.mark.parametrize(
    ("evaluation", "reason"),
    [
        (handoffs.human_request(REPORT), EscalationReason.HUMAN_REQUESTED),
        (handoffs.ambiguous_match(REPORT, (), 2), EscalationReason.CHARGE_NOT_IDENTIFIED),
        (handoffs.not_in_list(REPORT, _case()), EscalationReason.CHARGE_NOT_IDENTIFIED),
        (handoffs.unidentified_charge(REPORT, _case()), EscalationReason.CHARGE_NOT_IDENTIFIED),
        (handoffs.turn_limit(REPORT, _case()), EscalationReason.CHARGE_NOT_IDENTIFIED),
        (handoffs.ineligible_match(REPORT, COP_CHARGE, ("x",), how_identified=handoffs.ChargeIdentification.PICK), EscalationReason.NEEDS_REVIEW),
        (handoffs.credit_limit_reached(REPORT, COP_CHARGE), EscalationReason.NEEDS_REVIEW),
        (handoffs.already_credited(REPORT, COP_CHARGE, "CASE-1", how_identified=PICKED), EscalationReason.ALREADY_CREDITED),
        (handoffs.prior_escalation_same_charge(REPORT, COP_CHARGE, "CASE-1", how_identified=PICKED), EscalationReason.ALREADY_IN_REVIEW),
    ],
    ids=lambda v: v.value if isinstance(v, EscalationReason) else "",
)
def test_each_single_cause_builder_sets_its_customer_reason(evaluation, reason):
    assert evaluation.state == CaseState.ESCALATED
    assert evaluation.customer_reason == reason


def test_every_reason_policy_sends_to_a_person_has_a_customer_reason():
    assert set(explanation._PERSON_REASONS) == set(REASONS_REQUIRING_A_PERSON)


def test_the_builders_that_name_a_charge_carry_it_as_the_match():
    for evaluation in (
        handoffs.ineligible_match(REPORT, COP_CHARGE, ("x",), how_identified=handoffs.ChargeIdentification.PICK),
        handoffs.credit_limit_reached(REPORT, COP_CHARGE),
        handoffs.already_credited(REPORT, COP_CHARGE, "CASE-1", how_identified=PICKED),
        handoffs.prior_escalation_same_charge(REPORT, COP_CHARGE, "CASE-1", how_identified=PICKED),
    ):
        assert evaluation.matched_transaction == COP_CHARGE


def test_an_escalation_without_a_customer_reason_is_refused(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case = cases.create_case(session.customer_id, "es", db_path=real_fixture_app_db)
    turn = Turn(session, case, Language.ES, uuid.uuid4().hex, real_fixture_app_db)
    evaluation = CaseEvaluation(state=CaseState.ESCALATED, handoff=handoffs.human_request(REPORT).handoff)

    with pytest.raises(ValueError, match="customer reason"):
        state_machine.finish_escalated(turn, evaluation, REPORT)
    assert cases.get_case(case.case_id, db_path=real_fixture_app_db).state == CaseState.AWAITING_REPORT


# -- One compare-and-set --------------------------------------------------------


def test_the_reason_is_written_in_the_same_update_that_escalates(real_fixture_app_db):
    case = cases.create_case("C1", "es", db_path=real_fixture_app_db)

    claimed = cases.update_case(
        case.case_id, state=CaseState.ESCALATED, escalation_reason=EscalationReason.NEEDS_REVIEW,
        expected_states=(CaseState.AWAITING_REPORT,), db_path=real_fixture_app_db,
    )

    assert claimed
    assert app_db_rows(
        real_fixture_app_db, "SELECT state, escalation_reason FROM cases WHERE case_id = ?", [case.case_id],
    ) == [("escalated", "needs_review")]


def test_a_lost_compare_and_set_writes_neither_the_state_nor_the_reason(real_fixture_app_db):
    case = cases.create_case("C1", "es", db_path=real_fixture_app_db)

    claimed = cases.update_case(
        case.case_id, state=CaseState.ESCALATED, escalation_reason=EscalationReason.NEEDS_REVIEW,
        expected_states=(CaseState.CONFIRMING,), db_path=real_fixture_app_db,
    )

    assert not claimed
    assert app_db_rows(
        real_fixture_app_db, "SELECT state, escalation_reason FROM cases WHERE case_id = ?", [case.case_id],
    ) == [("awaiting_report", None)]


# -- Every escalation path (plan.md AD-4 matrix), against the real fixture ---------


@pytest.fixture()
def session(real_fixture_app_db):
    return demo_session(real_fixture_app_db)


def _unlock(case_id, app_db, state):
    assert cases.update_case(case_id, state=state, unlock_handoff=True, db_path=app_db)


def _stored_reason(case_id, app_db):
    return cases.get_case(case_id, db_path=app_db).escalation_reason


@requires_real_fixture
def test_human_request_in_confirming_names_no_charge(session, real_fixture_app_db):
    case_id = reach_confirming(session, real_fixture_app_db)
    _unlock(case_id, real_fixture_app_db, CaseState.CONFIRMING)

    reply = mocked_turn(session, real_fixture_app_db, "Hablar con una persona", case_id, action=CustomerAction.HUMAN)

    assert_escalation_notice(reply, EscalationReason.HUMAN_REQUESTED, charge_named=False)
    assert _stored_reason(case_id, real_fixture_app_db) == EscalationReason.HUMAN_REQUESTED


@requires_real_fixture
def test_human_request_while_explaining_names_the_confirmed_charge(session, real_fixture_app_db):
    case_id = reach_explaining(session, real_fixture_app_db)
    _unlock(case_id, real_fixture_app_db, CaseState.AWAITING_EXPLANATION)

    reply = mocked_turn(session, real_fixture_app_db, "Hablar con una persona", case_id, action=CustomerAction.HUMAN)

    assert_escalation_notice(reply, EscalationReason.HUMAN_REQUESTED, charge_named=True)
    assert reply["escalation"]["charge"]["transaction_id"] == AUTO_RESOLVE_CHARGE


@requires_real_fixture
def test_human_request_while_selecting_names_no_charge(session, real_fixture_app_db):
    listed = mocked_turn(session, real_fixture_app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES)
    _unlock(listed["case_id"], real_fixture_app_db, CaseState.SELECTING)

    reply = mocked_turn(session, real_fixture_app_db, "Hablar con una persona", listed["case_id"], action=CustomerAction.HUMAN)

    assert_escalation_notice(reply, EscalationReason.HUMAN_REQUESTED, charge_named=False)


@requires_real_fixture
def test_classifier_down_while_confirming_is_a_service_issue(session, real_fixture_app_db):
    case_id = reach_confirming(session, real_fixture_app_db)
    client = mock_anthropic_client(charge_extraction())
    client.messages.create.side_effect = anthropic.APITimeoutError(request=None)

    reply = mocked_turn(session, real_fixture_app_db, "mmm no sé", case_id, client=client)

    assert_escalation_notice(reply, EscalationReason.SERVICE_ISSUE, charge_named=False)
    assert _stored_reason(case_id, real_fixture_app_db) == EscalationReason.SERVICE_ISSUE


@requires_real_fixture
def test_no_with_every_round_used_is_charge_not_identified(session, real_fixture_app_db):
    case_id = reach_confirming(session, real_fixture_app_db)
    cases.update_case(case_id, state=CaseState.CONFIRMING, clarification_rounds=2, db_path=real_fixture_app_db)

    reply = mocked_turn(session, real_fixture_app_db, "No es ese", case_id, action=CustomerAction.CONFIRM_NO)

    assert_escalation_notice(reply, EscalationReason.CHARGE_NOT_IDENTIFIED, charge_named=False)


@requires_real_fixture
def test_yes_with_a_failed_reverification_names_the_confirmed_charge(session, real_fixture_app_db):
    case_id = reach_confirming(session, real_fixture_app_db)

    def ineligible(turn, matched, report, how_identified, *, reason=None):
        return handoffs.ineligible_match(report, matched, ("motivo interno",), how_identified=how_identified)

    with patch.object(state_machine, "_policy_verdict", ineligible):
        reply = mocked_turn(session, real_fixture_app_db, "Sí, es ese", case_id, action=CustomerAction.CONFIRM_YES)

    assert_escalation_notice(reply, EscalationReason.NEEDS_REVIEW, charge_named=True)
    assert reply["escalation"]["charge"]["transaction_id"] == AUTO_RESOLVE_CHARGE
    assert "motivo interno" not in reply["reply"]


@requires_real_fixture
def test_picking_an_ineligible_charge_names_it(session, real_fixture_app_db):
    listed = mocked_turn(session, real_fixture_app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES)
    cases.update_case(
        listed["case_id"], state=CaseState.SELECTING, offered_transaction_ids=(FRAUD_SCORE_CHARGE,),
        db_path=real_fixture_app_db,
    )

    reply = mocked_turn(
        session, real_fixture_app_db, "Tienda Online Global", listed["case_id"], selected_transaction_id=FRAUD_SCORE_CHARGE,
    )

    assert_escalation_notice(reply, EscalationReason.NEEDS_REVIEW, charge_named=True)
    assert reply["escalation"]["charge"]["transaction_id"] == FRAUD_SCORE_CHARGE
    assert "91" not in reply["reply"].replace(reply["escalation"]["case_number"], "")


@requires_real_fixture
def test_not_in_the_list_after_a_detail_names_no_charge(session, real_fixture_app_db):
    listed = mocked_turn(session, real_fixture_app_db, "fue el 14 de junio", extraction=charge_extraction(date="2026-06-14"))

    reply = mocked_turn(
        session, real_fixture_app_db, "No está en la lista", listed["case_id"], action=CustomerAction.NONE_OF_THESE,
    )

    assert_escalation_notice(reply, EscalationReason.CHARGE_NOT_IDENTIFIED, charge_named=False)


@requires_real_fixture
def test_the_turn_cap_names_no_charge(session, real_fixture_app_db):
    listed = mocked_turn(session, real_fixture_app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES)
    with db.app_connection(real_fixture_app_db) as con:
        con.execute("UPDATE cases SET turn_count = 99 WHERE case_id = ?", [listed["case_id"]])
        con.commit()

    reply = mocked_turn(session, real_fixture_app_db, "fue el 14", listed["case_id"], extraction=charge_extraction(date="2026-06-14"))

    assert_escalation_notice(reply, EscalationReason.CHARGE_NOT_IDENTIFIED, charge_named=False)


@requires_real_fixture
def test_a_fixture_failure_is_a_service_issue_without_a_charge(session, real_fixture_app_db):
    case_id = reach_explaining(session, real_fixture_app_db)

    with patch.object(state_machine, "handle_explanation", side_effect=duckdb.Error("down")):
        reply = mocked_turn(session, real_fixture_app_db, EXPLANATION, case_id)

    assert_escalation_notice(reply, EscalationReason.SERVICE_ISSUE, charge_named=False)


def _assessment_down_client():
    client = mock_anthropic_client(charge_extraction())
    answer = client.messages.create.side_effect

    def create(**kwargs):
        if llm.ASSESSMENT_MARKER in kwargs["system"]:
            raise anthropic.APITimeoutError(request=None)
        return answer(**kwargs)

    client.messages.create.side_effect = create
    return client


@requires_real_fixture
def test_the_assessment_down_names_the_confirmed_charge(session, real_fixture_app_db):
    case_id = reach_explaining(session, real_fixture_app_db)

    reply = mocked_turn(session, real_fixture_app_db, EXPLANATION, case_id, client=_assessment_down_client())

    assert_escalation_notice(reply, EscalationReason.SERVICE_ISSUE, charge_named=True)


@requires_real_fixture
@pytest.mark.parametrize(
    ("assessment", "reason"),
    [
        (NOT_RECEIVED_ASSESSMENT, EscalationReason.NOT_RECEIVED),
        ({**NOT_RECEIVED_ASSESSMENT, "reason": "wrong_amount"}, EscalationReason.WRONG_AMOUNT),
        ({**NOT_RECEIVED_ASSESSMENT, "reason": "card_lost_stolen"}, EscalationReason.CARD_LOST_STOLEN),
        (CONTRADICTED_ASSESSMENT, EscalationReason.NEEDS_REVIEW),
        # Card present at a POS: the evidence check for the named reason fails.
        (DUPLICATE_ASSESSMENT, EscalationReason.NEEDS_REVIEW),
    ],
    ids=["not_received", "wrong_amount", "card_lost_stolen", "contradiction", "evidence_check"],
)
def test_an_explanation_that_escalates_names_the_charge_and_its_reason(
    session, real_fixture_app_db, assessment, reason,
):
    case_id = reach_explaining(session, real_fixture_app_db)

    reply = mocked_turn(session, real_fixture_app_db, EXPLANATION, case_id, mock={"assessment": assessment})

    assert_escalation_notice(reply, reason, charge_named=True)
    assert _stored_reason(case_id, real_fixture_app_db) == reason


@requires_real_fixture
def test_a_vague_explanation_twice_is_needs_review(session, real_fixture_app_db):
    case_id = reach_explaining(session, real_fixture_app_db)
    vague = {**NOT_RECEIVED_ASSESSMENT, "reason": "unclear", "specific": False}

    first = mocked_turn(session, real_fixture_app_db, EXPLANATION, case_id, mock={"assessment": vague})
    second = mocked_turn(session, real_fixture_app_db, EXPLANATION, case_id, mock={"assessment": vague})

    assert first["state"] == CaseState.AWAITING_EXPLANATION
    assert_escalation_notice(second, EscalationReason.NEEDS_REVIEW, charge_named=True)


# -- `ChatReply.escalation` on every reply about an escalated case ----------------


def _escalated_case(session, app_db):
    listed = mocked_turn(session, app_db, "fue el 14 de junio", extraction=charge_extraction(date="2026-06-14"))
    reply = mocked_turn(session, app_db, "No está en la lista", listed["case_id"], action=CustomerAction.NONE_OF_THESE)
    assert reply["state"] == CaseState.ESCALATED
    return reply


@requires_real_fixture
@pytest.mark.parametrize("language", list(Language))
def test_a_later_message_gets_the_escalation_in_the_current_language(session, real_fixture_app_db, language):
    escalated = _escalated_case(session, real_fixture_app_db)

    later = mocked_turn(session, real_fixture_app_db, "¿Y ahora?", escalated["case_id"], language=language)

    assert later["state"] == CaseState.ESCALATED
    assert later["escalation"] == replies.escalation_summary(
        escalated["case_id"], EscalationReason.CHARGE_NOT_IDENTIFIED, charge=None, language=language,
    )
    assert escalated["case_id"] in later["reply"]


@requires_real_fixture
def test_a_lost_race_into_an_escalated_case_gets_the_escalation(session, real_fixture_app_db):
    listed = mocked_turn(session, real_fixture_app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES)
    stale = cases.get_case(listed["case_id"], db_path=real_fixture_app_db)
    cases.update_case(
        stale.case_id, state=CaseState.ESCALATED, escalation_reason=EscalationReason.HUMAN_REQUESTED,
        db_path=real_fixture_app_db,
    )
    turn = Turn(session, stale, Language.PT, uuid.uuid4().hex, real_fixture_app_db)

    reply = transition(turn, CaseState.SELECTING)

    assert reply["state"] == CaseState.ESCALATED
    assert reply["escalation"]["case_number"] == stale.case_id
    assert reply["escalation"]["reason"] == replies.escalation_summary(
        stale.case_id, EscalationReason.HUMAN_REQUESTED, charge=None, language=Language.PT,
    )["reason"]


@requires_real_fixture
def test_an_abandoned_turn_on_an_escalated_case_gets_the_escalation(session, real_fixture_app_db):
    escalated = _escalated_case(session, real_fixture_app_db)
    turn_id = uuid.uuid4().hex
    turns.claim(session.customer_id, turn_id, db_path=real_fixture_app_db)
    turns.attach_case(session.customer_id, turn_id, escalated["case_id"], db_path=real_fixture_app_db)
    turns.abandon(session.customer_id, turn_id, db_path=real_fixture_app_db)

    reply = mocked_turn(session, real_fixture_app_db, "hola", escalated["case_id"], turn_id=turn_id)

    assert reply["state"] == CaseState.ESCALATED
    assert reply["escalation"]["case_number"] == escalated["case_id"]
    assert reply["escalation"]["reason"] == escalated["escalation"]["reason"]


@requires_real_fixture
def test_a_replayed_escalating_turn_returns_the_same_escalation(session, real_fixture_app_db):
    listed = mocked_turn(session, real_fixture_app_db, "fue el 14 de junio", extraction=charge_extraction(date="2026-06-14"))
    turn_id = uuid.uuid4().hex
    kwargs = dict(action=CustomerAction.NONE_OF_THESE, turn_id=turn_id)

    first = mocked_turn(session, real_fixture_app_db, "No está en la lista", listed["case_id"], **kwargs)
    again = mocked_turn(session, real_fixture_app_db, "No está en la lista", listed["case_id"], **kwargs)

    assert first["escalation"] is not None
    assert again["escalation"] == first["escalation"]


@requires_real_fixture
def test_a_legacy_escalated_case_gets_an_escalation_without_a_reason(session, real_fixture_app_db):
    case = cases.create_case(session.customer_id, "es", db_path=real_fixture_app_db)
    cases.update_case(case.case_id, state=CaseState.ESCALATED, db_path=real_fixture_app_db)

    reply = mocked_turn(session, real_fixture_app_db, "hola", case.case_id)

    assert reply["escalation"] == {
        "case_number": case.case_id, "charge": None, "reason": None,
        "contact_business_days": ESCALATION_CONTACT_BUSINESS_DAYS,
    }


@requires_real_fixture
def test_replies_about_open_cases_carry_no_escalation(session, real_fixture_app_db):
    reply = mocked_turn(session, real_fixture_app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES)
    assert reply["escalation"] is None


@requires_real_fixture
def test_the_escalating_turn_makes_no_response_model_call(session, real_fixture_app_db):
    listed = mocked_turn(session, real_fixture_app_db, "fue el 14 de junio", extraction=charge_extraction(date="2026-06-14"))
    client = mock_anthropic_client(charge_extraction())

    mocked_turn(session, real_fixture_app_db, "No está en la lista", listed["case_id"], client=client,
                action=CustomerAction.NONE_OF_THESE)

    assert client.messages.create.call_count == 0


def test_an_unknown_stored_reason_is_answered_like_a_legacy_row():
    notice = case_turn.escalation_of(
        _case(state=CaseState.ESCALATED, escalation_reason="renamed_reason"), Language.ES,
    )
    assert notice == replies.escalation_summary(CASE_NUMBER, None, charge=None, language=Language.ES)
