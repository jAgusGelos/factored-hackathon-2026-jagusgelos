"""The code check on a model-written summary (`app/summary_guard.py`): the
explanation's summary gets the same guarantee as the statement's.
"""

from __future__ import annotations

import json

import pytest

from app import cases
from app.case_model import CaseState
from app.summary_guard import SummaryDrop, summary_drop
from tests.support import (
    CONTRADICTED_ASSESSMENT,
    CONVINCING_ASSESSMENT,
    EXPLANATION,
    clean_txn,
    demo_session,
    logged_events,
    mocked_turn,
    reach_explaining,
    requires_real_fixture,
)

UBER = clean_txn(amount=38500.0, currency="COP", amount_usd=9.6, merchant_name="Uber Eats")


@pytest.mark.parametrize(
    ("summary", "cause"),
    [
        ("El cliente no reconoce el cargo de 38.500 en Uber Eats.", None),
        ("El cliente no reconoce el cargo de «Uber Eats» del 14 de junio de 2026.", None),
        ("Llamar al 300 555 1234.", SummaryDrop.NUMBER),
        ("El cliente María José Gómez no reconoce el cargo.", SummaryDrop.NAME),
        ("Escribir a cliente arroba correo punto com.", SummaryDrop.CONTACT_OR_QUOTE),
    ],
    ids=["charge_facts", "quoted_merchant_and_date_in_words", "phone", "full_name", "spelled_email"],
)
def test_the_drop_cause_names_the_check_that_failed(summary, cause):
    assert summary_drop(summary, "No reconozco ese cargo", UBER) == cause


@pytest.fixture()
def session(real_fixture_app_db):
    return demo_session(real_fixture_app_db)


@requires_real_fixture
def test_an_explanation_summary_with_personal_data_never_reaches_the_handoff(session, real_fixture_app_db):
    case_id = reach_explaining(session, real_fixture_app_db)
    leaky = {**CONTRADICTED_ASSESSMENT, "summary": "El cliente pide que lo llamen al 300 555 1234."}

    reply = mocked_turn(session, real_fixture_app_db, EXPLANATION, case_id, mock={"assessment": leaky})

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(case_id, db_path=real_fixture_app_db).handoff
    assert "300 555 1234" not in json.dumps(handoff, ensure_ascii=False)
    assert "explanation_summary" not in handoff["customer_reported"]
    assert handoff["customer_reported"]["explanation_consistent"]
    assert logged_events(real_fixture_app_db, "explanation_summary_dropped") == [{"cause": "number"}]


@requires_real_fixture
def test_a_clean_explanation_summary_is_kept(session, real_fixture_app_db):
    case_id = reach_explaining(session, real_fixture_app_db)

    mocked_turn(session, real_fixture_app_db, EXPLANATION, case_id, mock={"assessment": CONTRADICTED_ASSESSMENT})

    handoff = cases.get_case(case_id, db_path=real_fixture_app_db).handoff
    assert handoff["customer_reported"]["explanation_summary"].startswith(CONVINCING_ASSESSMENT["summary"])
    assert logged_events(real_fixture_app_db, "explanation_summary_dropped") == []
