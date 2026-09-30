"""AD-9: a tap on the menu or a button is deterministic and never calls the
model; typed text still does (extraction, the explanation's assessment, NLG).

Each turn gets its own mocked Anthropic client, so `messages.create`'s call
count is exactly that turn's model calls. Runs against the real fixture.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app import cases, replies
from app.case_model import CaseState, CustomerAction
from app.charge_search import ListFilter
from app.state_machine import handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    EXPLANATION,
    charge_extraction,
    demo_session,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
)

pytestmark = requires_real_fixture

OPENING = "Tengo un cargo que no reconozco"
MODEL_TEXT = "Respuesta generada."


def _turn(session, app_db, text, case_id=None, *, extraction=None, **kwargs):
    """One turn; returns its reply and how many model calls it made."""
    client = mock_anthropic_client(extraction or charge_extraction(), MODEL_TEXT)
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        reply = handle_message(session, case_id, text, db_path=app_db, **kwargs)
    return reply, client.messages.create.call_count


def _show_charges(session, app_db):
    return _turn(session, app_db, "Ver mis últimos cargos", action=CustomerAction.SHOW_CHARGES)


def _confirming(session, app_db):
    reply, calls = _turn(session, app_db, OPENING, extraction=charge_extraction(AUTO_RESOLVE_CHARGE))
    assert reply["state"] == CaseState.CONFIRMING
    assert calls >= 1  # typed text: extraction + NLG
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
    cases.update_case(
        listed["case_id"], state=CaseState.SELECTING, unlock_handoff=True, db_path=real_fixture_app_db,
    )
    escalated, escalated_calls = _turn(
        session, real_fixture_app_db, "Hablar con una persona", listed["case_id"], action=CustomerAction.HUMAN,
    )

    assert (deferred_calls, escalated_calls) == (0, 0)
    assert deferred["state"] == CaseState.SELECTING
    assert deferred["reply"] == replies.HUMAN_DEFERRED["es"]
    assert escalated["state"] == CaseState.ESCALATED
    assert escalated["reply"] == replies.ESCALATED["es"]


def test_the_human_button_while_confirming_is_answered_without_the_model(session, real_fixture_app_db):
    proposed = _confirming(session, real_fixture_app_db)

    reply, calls = _turn(
        session, real_fixture_app_db, "Hablar con una persona", proposed["case_id"], action=CustomerAction.HUMAN,
    )

    assert calls == 0
    assert reply["state"] == CaseState.CONFIRMING
    assert reply["reply"] == replies.HUMAN_DEFERRED_WHILE_CONFIRMING["es"]


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
