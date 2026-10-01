"""AD-9: a tap on the menu or a button is deterministic and never calls the
model; typed text still does (extraction, the explanation's assessment, NLG).

Each turn gets its own mocked Anthropic client, so `messages.create`'s call
count is exactly that turn's model calls. Runs against the real fixture.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from unittest.mock import patch

import pytest

from app import cases, replies, state_machine
from app.case_model import CaseState, CustomerAction, EscalationReason
from app.case_turn import Turn
from app.charge_search import ListFilter
from app.state_machine import handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    EXPLANATION,
    FRAUD_SCORE_CHARGE,
    OPENING,
    STATEMENT,
    assert_asks_for_statement,
    assert_escalation_notice,
    charge_extraction,
    demo_session,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
)

pytestmark = requires_real_fixture

MODEL_TEXT = "Respuesta generada."


def _turn(session, app_db, text, case_id=None, *, extraction=None, **kwargs):
    """One turn; returns its reply and how many model calls it made."""
    client = mock_anthropic_client(extraction or charge_extraction(), MODEL_TEXT)
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        reply = handle_message(session, case_id, text, db_path=app_db, **kwargs)
    return reply, client.messages.create.call_count


def _hand_off(session, app_db, asked):
    """A tap that escalates now asks for the customer's statement (no model
    call); the typed statement that follows is its own turn, with its own client.
    """
    assert_asks_for_statement(asked)
    reply, _ = _turn(session, app_db, STATEMENT, asked["case_id"])
    return reply


def _show_charges(session, app_db):
    return _turn(session, app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES)


def _confirming(session, app_db):
    reply, calls = _turn(session, app_db, OPENING, extraction=charge_extraction(AUTO_RESOLVE_CHARGE))
    assert reply["state"] == CaseState.CONFIRMING
    assert calls >= 1
    return reply


@pytest.fixture()
def session(real_fixture_app_db):
    return demo_session(real_fixture_app_db)


def test_the_show_charges_button_lists_the_charges_without_the_model(session, real_fixture_app_db):
    reply, calls = _show_charges(session, real_fixture_app_db)

    assert calls == 0
    assert reply["state"] == CaseState.SELECTING
    assert reply["options"]
    assert reply["reply"] == replies.CHARGE_LIST["es"][ListFilter.RECENT]
    assert logged_events(real_fixture_app_db, "nlg_skipped_for_menu")
    assert logged_events(real_fixture_app_db, "llm_unavailable") == []


def test_the_show_charges_button_does_not_spend_a_round(session, real_fixture_app_db):
    reply, _ = _show_charges(session, real_fixture_app_db)

    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.clarification_rounds == 0
    assert not case.handoff_unlocked


def test_the_show_charges_button_outside_the_start_changes_nothing(session, real_fixture_app_db):
    listed, _ = _show_charges(session, real_fixture_app_db)

    reply, calls = _turn(
        session, real_fixture_app_db, "Ver mis últimos cargos", listed["case_id"],
        action=CustomerAction.SHOW_CHARGES,
    )

    assert calls == 0
    assert reply["state"] == CaseState.SELECTING
    assert reply["reply"] == replies.ACTION_UNAVAILABLE["es"]
    assert logged_events(real_fixture_app_db, "action_rejected")


def test_tapping_a_charge_asks_for_the_explanation_without_the_model(session, real_fixture_app_db):
    listed, _ = _show_charges(session, real_fixture_app_db)
    assert AUTO_RESOLVE_CHARGE in [o["transaction_id"] for o in listed["options"]]

    reply, calls = _turn(
        session, real_fixture_app_db, "Uber", listed["case_id"], selected_transaction_id=AUTO_RESOLVE_CHARGE,
    )

    assert calls == 0
    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    assert reply["reply"] != MODEL_TEXT
    assert "Uber" in reply["reply"]


def test_confirm_yes_asks_for_the_explanation_without_the_model(session, real_fixture_app_db):
    proposed = _confirming(session, real_fixture_app_db)

    reply, calls = _turn(
        session, real_fixture_app_db, "Sí, es ese", proposed["case_id"], action=CustomerAction.CONFIRM_YES,
    )

    assert calls == 0
    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    assert "Uber" in reply["reply"]


def test_confirm_no_lists_the_other_charges_without_the_model(session, real_fixture_app_db):
    proposed = _confirming(session, real_fixture_app_db)

    reply, calls = _turn(
        session, real_fixture_app_db, "No es ese", proposed["case_id"], action=CustomerAction.CONFIRM_NO,
    )

    assert calls == 0
    assert reply["state"] == CaseState.SELECTING
    assert reply["reply"] != MODEL_TEXT
    assert AUTO_RESOLVE_CHARGE not in [o["transaction_id"] for o in reply["options"]]


def test_not_in_the_list_asks_for_a_detail_without_the_model(session, real_fixture_app_db):
    listed, _ = _show_charges(session, real_fixture_app_db)

    reply, calls = _turn(
        session, real_fixture_app_db, "No está en la lista", listed["case_id"],
        action=CustomerAction.NONE_OF_THESE,
    )

    assert calls == 0
    assert reply["state"] == CaseState.SELECTING
    assert reply["reply"] == replies.ASK_FOR_ONE_DETAIL["es"]


def test_the_human_button_is_answered_without_the_model(session, real_fixture_app_db):
    listed, _ = _show_charges(session, real_fixture_app_db)

    deferred, deferred_calls = _turn(
        session, real_fixture_app_db, "Hablar con una persona", listed["case_id"], action=CustomerAction.HUMAN,
    )
    asked, asked_calls = _turn(
        session, real_fixture_app_db, "Hablar con una persona", listed["case_id"], action=CustomerAction.HUMAN,
    )

    assert (deferred_calls, asked_calls) == (0, 0)
    assert deferred["state"] == CaseState.SELECTING
    assert deferred["reply"] == f"{replies.HUMAN_DEFERRED['es']} {replies.HUMAN_OFFER['es']}"
    assert deferred["human_available"] is True
    escalated = _hand_off(session, real_fixture_app_db, asked)
    assert escalated["state"] == CaseState.ESCALATED
    assert_escalation_notice(escalated, EscalationReason.HUMAN_REQUESTED, charge_named=False)


def test_the_human_button_while_confirming_is_answered_without_the_model(session, real_fixture_app_db):
    proposed = _confirming(session, real_fixture_app_db)

    reply, calls = _turn(
        session, real_fixture_app_db, "Hablar con una persona", proposed["case_id"], action=CustomerAction.HUMAN,
    )

    assert calls == 0
    assert reply["state"] == CaseState.CONFIRMING
    assert reply["reply"] == f"{replies.HUMAN_DEFERRED_WHILE_CONFIRMING['es']} {replies.HUMAN_OFFER['es']}"


def test_typed_text_still_goes_through_the_model(session, real_fixture_app_db):
    listed, calls_to_list = _turn(
        session, real_fixture_app_db, "Mostrame mis últimos cargos",
        extraction=charge_extraction(intent="show_charges"),
    )
    picked, _ = _turn(
        session, real_fixture_app_db, "Uber", listed["case_id"], selected_transaction_id=AUTO_RESOLVE_CHARGE,
    )
    explained, calls_to_explain = _turn(session, real_fixture_app_db, EXPLANATION, picked["case_id"])

    assert listed["state"] == CaseState.SELECTING
    assert calls_to_list >= 1
    assert explained["state"] == CaseState.RESOLVED_AUTO
    assert calls_to_explain >= 1
    assert logged_events(real_fixture_app_db, "llm_unavailable") == []


def test_menu_replies_in_portuguese_use_the_portuguese_templates(session, real_fixture_app_db):
    reply, calls = _turn(
        session, real_fixture_app_db, "Ver minhas últimas cobranças", language="pt",
        action=CustomerAction.SHOW_CHARGES,
    )

    assert calls == 0
    assert reply["reply"] == replies.CHARGE_LIST["pt"][ListFilter.RECENT]


def _escalated_by_a_tap(session, app_db):
    listed, _ = _show_charges(session, app_db)
    cases.update_case(
        listed["case_id"], state=CaseState.SELECTING, offered_transaction_ids=(FRAUD_SCORE_CHARGE,), db_path=app_db,
    )
    return _turn(session, app_db, "Tienda Online Global", listed["case_id"], selected_transaction_id=FRAUD_SCORE_CHARGE)


def test_tapping_a_charge_that_fails_the_policy_escalates_without_the_model(session, real_fixture_app_db):
    asked, calls = _escalated_by_a_tap(session, real_fixture_app_db)

    assert calls == 0
    reply = _hand_off(session, real_fixture_app_db, asked)
    assert reply["state"] == CaseState.ESCALATED
    assert_escalation_notice(reply, EscalationReason.NEEDS_REVIEW, charge_named=True)


def test_not_in_the_list_after_a_detail_escalates_without_the_model(session, real_fixture_app_db):
    listed, _ = _turn(session, real_fixture_app_db, "fue el 14 de junio", extraction=charge_extraction(date="2026-06-14"))

    asked, calls = _turn(
        session, real_fixture_app_db, "No está en la lista", listed["case_id"],
        action=CustomerAction.NONE_OF_THESE,
    )

    assert calls == 0
    reply = _hand_off(session, real_fixture_app_db, asked)
    assert reply["state"] == CaseState.ESCALATED
    assert_escalation_notice(reply, EscalationReason.CHARGE_NOT_IDENTIFIED, charge_named=False)


def test_the_show_charges_button_on_a_closed_case_gets_the_closed_case_reply(session, real_fixture_app_db):
    asked, _ = _escalated_by_a_tap(session, real_fixture_app_db)
    escalated = _hand_off(session, real_fixture_app_db, asked)

    reply, calls = _turn(
        session, real_fixture_app_db, "Ver mis últimos cargos", escalated["case_id"],
        action=CustomerAction.SHOW_CHARGES,
    )

    assert calls == 0
    assert (reply["case_id"], reply["state"]) == (escalated["case_id"], CaseState.ESCALATED)
    assert reply["options"] == []


def test_a_retried_button_turn_is_replayed(session, real_fixture_app_db):
    turn_id = str(uuid.uuid4())
    first, _ = _turn(
        session, real_fixture_app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES, turn_id=turn_id,
    )

    retried, calls = _turn(
        session, real_fixture_app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES, turn_id=turn_id,
    )

    assert calls == 0
    assert retried == first
    assert logged_events(real_fixture_app_db, "turn_replayed")


def _stale_turn(session, app_db, case_id, seen_state):
    """A request that loaded the case in `seen_state` before another one moved it."""
    case = replace(cases.get_case(case_id, db_path=app_db), state=seen_state)
    return Turn(session, case, "es", uuid.uuid4().hex, app_db, from_menu=True)


def test_a_show_charges_tap_that_loses_the_race_does_not_move_the_case(session, real_fixture_app_db):
    proposed = _confirming(session, real_fixture_app_db)
    turn = _stale_turn(session, real_fixture_app_db, proposed["case_id"], CaseState.AWAITING_REPORT)

    reply = state_machine._route(turn, "Ver mis últimos cargos", None, CustomerAction.SHOW_CHARGES)

    case = cases.get_case(proposed["case_id"], db_path=real_fixture_app_db)
    assert (case.state, case.matched_transaction_id) == (CaseState.CONFIRMING, AUTO_RESOLVE_CHARGE)
    assert reply["state"] == CaseState.CONFIRMING
    assert logged_events(real_fixture_app_db, "case_transition_lost_race")


def test_a_not_that_one_tap_that_loses_the_race_does_not_move_the_case(session, real_fixture_app_db):
    proposed = _confirming(session, real_fixture_app_db)
    _turn(session, real_fixture_app_db, "Sí, es ese", proposed["case_id"], action=CustomerAction.CONFIRM_YES)
    turn = _stale_turn(session, real_fixture_app_db, proposed["case_id"], CaseState.CONFIRMING)

    reply = state_machine._route(turn, "No es ese", None, CustomerAction.CONFIRM_NO)

    case = cases.get_case(proposed["case_id"], db_path=real_fixture_app_db)
    assert (case.state, case.matched_transaction_id) == (CaseState.AWAITING_EXPLANATION, AUTO_RESOLVE_CHARGE)
    assert reply["state"] == CaseState.AWAITING_EXPLANATION
