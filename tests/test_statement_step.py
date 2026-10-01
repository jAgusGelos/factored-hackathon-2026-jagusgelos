"""The statement step (statement-before-handoff): an escalation decided in
code is held while the customer says what happened, then handed off with the
same reason and handoff (plan.md AD-1, AD-2, AD-5).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import duckdb
import pytest

from app import cases, fixture_db, handoffs, replies
from app.case_model import CaseState, EscalationReason, ReportedCharge
from app.case_turn import PendingEscalation, Turn, finish_escalated
from app.charge_search import charge_option
from app.llm import Language
from tests.support import (
    STATEMENT,
    assert_asks_for_statement,
    clean_txn,
    logged_events,
    mocked_turn,
    session_for,
)

COP_CHARGE = clean_txn(amount=38500.0, currency="COP", amount_usd=9.6, merchant_name="Uber")
REPORT = ReportedCharge(amount=38500.0, date=None, currency="COP")


@pytest.fixture()
def app_db(_isolated_app_db):
    return _isolated_app_db


@pytest.fixture()
def session(app_db):
    return session_for("C1", app_db)


def _turn(session, app_db, state=CaseState.SELECTING, language=Language.ES) -> Turn:
    case = cases.create_case(session.customer_id, language, db_path=app_db)
    assert cases.update_case(case.case_id, state=state, db_path=app_db)
    return Turn(session, cases.get_case(case.case_id, db_path=app_db), language, uuid.uuid4().hex, app_db)


def _policy_escalation():
    return handoffs.ineligible_match(REPORT, COP_CHARGE, ("motivo interno",), how_identified=handoffs.ChargeIdentification.PICK)


def _held(session, app_db, language=Language.ES) -> tuple[dict, cases.Case]:
    turn = _turn(session, app_db, language=language)
    reply = finish_escalated(turn, _policy_escalation(), REPORT)
    return reply, cases.get_case(turn.case.case_id, db_path=app_db)


# -- Phase 1: the escalation is held -----------------------------------------------


@pytest.mark.parametrize("language", list(Language))
def test_an_escalation_waits_for_the_statement_and_hands_nothing_off(session, app_db, language):
    reply, case = _held(session, app_db, language)

    assert_asks_for_statement(reply, language)
    assert case.state == CaseState.AWAITING_STATEMENT
    assert case.handoff is None and case.escalation_reason is None
    evaluation = _policy_escalation()
    assert case.pending_escalation == json.loads(json.dumps(PendingEscalation(
        EscalationReason.NEEDS_REVIEW, evaluation.handoff.to_dict(), COP_CHARGE,
    ).to_dict()))
    assert case.matched_transaction_id == COP_CHARGE.transaction_id
    assert logged_events(app_db, "case_escalated") == []


def test_the_statement_request_event_carries_closed_values_only(session, app_db):
    _held(session, app_db)

    assert logged_events(app_db, "handoff_statement_requested") == [
        {"pending_escalation_reason": "needs_review", "source_state": "selecting"},
    ]


def test_an_escalation_after_the_customer_explained_is_handed_off_at_once(session, app_db):
    turn = _turn(session, app_db, state=CaseState.AWAITING_EXPLANATION)

    reply = finish_escalated(turn, _policy_escalation(), REPORT, account_given=True)

    assert reply["state"] == CaseState.ESCALATED
    case = cases.get_case(turn.case.case_id, db_path=app_db)
    assert case.pending_escalation is None
    assert case.escalation_reason == EscalationReason.NEEDS_REVIEW
    assert logged_events(app_db, "handoff_statement_requested") == []
    assert "statement_status" not in case.handoff["customer_reported"]


def test_the_pending_charge_round_trips_through_its_snapshot():
    pending = PendingEscalation(EscalationReason.NEEDS_REVIEW, {"x": 1}, COP_CHARGE)

    again = PendingEscalation.from_dict(pending.to_dict())

    assert again.reason == pending.reason and again.handoff == pending.handoff
    assert charge_option(again.charge) == charge_option(COP_CHARGE)


# -- Phase 2: the typed statement hands it off ------------------------------------------


@pytest.mark.parametrize("language", list(Language))
def test_a_typed_statement_hands_off_with_the_pending_reason_and_charge(session, app_db, language):
    held, before = _held(session, app_db, language)

    reply = mocked_turn(session, app_db, STATEMENT, held["case_id"], language=language)

    text, notice = replies.escalation_notice(
        held["case_id"], EscalationReason.NEEDS_REVIEW, charge=COP_CHARGE, language=language,
    )
    assert reply["state"] == CaseState.ESCALATED
    assert (reply["reply"], reply["escalation"]) == (text, notice)
    case = cases.get_case(held["case_id"], db_path=app_db)
    assert case.escalation_reason == EscalationReason.NEEDS_REVIEW
    assert case.statement_text == STATEMENT
    assert logged_events(app_db, "case_escalated") == [case.handoff]
    pending_handoff = before.pending_escalation["handoff"]
    assert {k: v for k, v in case.handoff.items() if k != "customer_reported"} == {
        k: v for k, v in pending_handoff.items() if k != "customer_reported"
    }


def test_the_statement_step_never_reads_the_fixture(session, app_db):
    held, _ = _held(session, app_db)

    with patch.object(fixture_db, "get_connection", side_effect=duckdb.Error("down")):
        reply = mocked_turn(session, app_db, STATEMENT, held["case_id"])

    assert reply["state"] == CaseState.ESCALATED
    assert cases.get_case(held["case_id"], db_path=app_db).escalation_reason == EscalationReason.NEEDS_REVIEW
    assert logged_events(app_db, "fixture_unavailable") == []


def test_a_statement_turn_spends_no_turn_and_no_round(session, app_db):
    held, before = _held(session, app_db)

    mocked_turn(session, app_db, STATEMENT, held["case_id"])

    after = cases.get_case(held["case_id"], db_path=app_db)
    assert (after.turn_count, after.clarification_rounds) == (before.turn_count, before.clarification_rounds)


def test_a_case_waiting_for_the_statement_is_not_moved_by_an_ordinary_step(session, app_db):
    held, _ = _held(session, app_db)
    stale = Turn(session, cases.get_case(held["case_id"], db_path=app_db), Language.ES, uuid.uuid4().hex, app_db)

    reply = finish_escalated(stale, handoffs.human_request(REPORT), REPORT, account_given=True)

    assert reply["state"] == CaseState.AWAITING_STATEMENT
    case = cases.get_case(held["case_id"], db_path=app_db)
    assert case.state == CaseState.AWAITING_STATEMENT and case.escalation_reason is None
