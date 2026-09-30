"""Fixture-driven integration tests for the 3 required conversation cases
(Task 2.4) — normal auto-resolution, ambiguous/clarify, human escalation.

Runs against the REAL `data/fixture.duckdb` + `data/demo_users.json` built by
`etl/build_fixture.py` in Milestone 1 (the exact personas the deployed app
serves from), with the Anthropic client mocked (no real API key in this
environment — see todo.md Task 2.3b). Skipped gracefully if the ETL fixture
hasn't been generated yet, since it's a documented prerequisite, not a bug.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from app import cases
from app.state_machine import CaseState, handle_message
from tests.support import (
    mock_anthropic_client,
    persona_complaint,
    persona_session,
    requires_real_fixture,
)

pytestmark = requires_real_fixture


def test_normal_case_clean_auto_resolve(real_fixture_app_db):
    session = persona_session("cliente.claro", real_fixture_app_db)
    report = persona_complaint(session.customer_id)

    extraction = {**report, "merchant_hint": None, "wants_human": False}
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        first = handle_message(session, None, "Tengo un cargo que no reconozco", db_path=real_fixture_app_db)
        # AD-12: a policy-eligible match is NOT resolved in the first turn.
        assert first["state"] == CaseState.CONFIRMING
        reply = handle_message(session, first["case_id"], "Sí, es ese", db_path=real_fixture_app_db)

    assert reply["state"] == CaseState.RESOLVED_AUTO

    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.state == "resolved_auto"
    assert case.resolution_reference is not None
    assert case.resolution_reference.startswith("REF-")

    # AD-11 Row 6: a SIMULATED credit, logged as such — never a real payment call.
    con = sqlite3.connect(str(real_fixture_app_db))
    events = con.execute(
        "SELECT payload_json FROM events WHERE event_type = 'simulated_credit'"
    ).fetchall()
    con.close()
    assert len(events) == 1
    payload = json.loads(events[0][0])
    assert payload["simulated"] is True


def test_ambiguous_case_asks_at_most_two_clarifying_questions_then_settles(real_fixture_app_db):
    session = persona_session("cliente.ambiguo", real_fixture_app_db)
    report = persona_complaint(session.customer_id)
    extraction = {**report, "merchant_hint": None, "wants_human": False}

    case_id = None
    states = []
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        for _ in range(3):  # at most 2 clarification rounds + the settling turn
            reply = handle_message(session, case_id, "Tengo un cargo que no reconozco", db_path=real_fixture_app_db)
            case_id = reply["case_id"]
            states.append(reply["state"])
            if reply["state"] != CaseState.CLARIFYING:
                break

    clarifying_turns = sum(1 for s in states if s == CaseState.CLARIFYING)
    assert clarifying_turns <= 2
    assert states[-1] in (CaseState.RESOLVED_AUTO, CaseState.ESCALATED)

    # The real dataset finding (build_fixture.py's docstring): this persona has
    # zero real matches, so after exhausting clarification rounds it must escalate.
    assert states[-1] == CaseState.ESCALATED
    case = cases.get_case(case_id, db_path=real_fixture_app_db)
    assert case.handoff is not None
    assert case.handoff["facts"]
    assert case.handoff["open_questions"]


def test_escalation_case_confident_match_ineligible_produces_structured_handoff(real_fixture_app_db):
    session = persona_session("cliente.escalado", real_fixture_app_db)
    report = persona_complaint(session.customer_id)
    extraction = {**report, "merchant_hint": None, "wants_human": False}

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(session, None, "Tengo un cargo que no reconozco", db_path=real_fixture_app_db)

    assert reply["state"] == CaseState.ESCALATED

    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.state == "escalated"
    assert case.matched_transaction_id == case.handoff["evidence"][0]
    assert case.reported_currency is not None
    assert case.handoff is not None
    # Structured handoff (facts/actions/evidence/open_questions) — never a raw transcript.
    assert set(case.handoff.keys()) == {"facts", "actions_taken", "evidence", "open_questions"}
    assert case.handoff["facts"]
    assert case.handoff["evidence"]  # the matched (ineligible) transaction id
    assert case.handoff["open_questions"]  # why it failed AD-11's eligibility rows
    # No raw transcript dump: the customer's free-text message must not appear verbatim.
    assert "Tengo un cargo que no reconozco" not in json.dumps(case.handoff)


def test_customer_requesting_human_escalates_immediately(real_fixture_app_db):
    session = persona_session("cliente.claro", real_fixture_app_db)
    extraction = {"amount": None, "currency": None, "date": None, "merchant_hint": None, "wants_human": True}

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(session, None, "Quiero hablar con una persona", db_path=real_fixture_app_db)

    assert reply["state"] == CaseState.ESCALATED


def test_clarification_reply_without_a_currency_keeps_the_originally_reported_one(real_fixture_app_db):
    """Seen live with Claude Haiku: cliente.ambiguo (Colombian profile) reported
    MXN; the clarification reply omitted the currency and the case silently
    switched to COP (inferred from the profile country), searching and replying
    with the wrong currency.
    """
    session = persona_session("cliente.ambiguo", real_fixture_app_db)
    report = persona_complaint(session.customer_id)
    first_extraction = {**report, "currency": "MXN", "merchant_hint": None, "wants_human": False}
    followup_extraction = {
        "amount": None, "currency": None, "date": None, "merchant_hint": None, "wants_human": False,
    }

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(first_extraction)):
        first = handle_message(session, None, "Tengo un cargo que no reconozco", db_path=real_fixture_app_db)
    assert first["state"] == CaseState.CLARIFYING

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(followup_extraction)):
        handle_message(
            session, first["case_id"], "No me acuerdo del comercio", db_path=real_fixture_app_db
        )

    case = cases.get_case(first["case_id"], db_path=real_fixture_app_db)
    assert case.reported_currency == "MXN"
