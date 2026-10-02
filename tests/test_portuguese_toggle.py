"""Portuguese simulation toggle (Task 2.5, AD-8).

Runs the same demo-customer scenarios as `tests/test_conversation_flows.py`
through the `pt` language toggle, asserting each still reaches its expected
AD-11 state and that only the Portuguese prompts are used. This is a
demonstration that the toggle works across the required scenarios, NOT a
validated NLU capability claim: the dataset has zero Portuguese data (AD-8's
disclosed limitation).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app import cases
from app.case_model import EscalationReason
from app.llm import _EXTRACTION_SYSTEM_PROMPT, _RESPONSE_SYSTEM_PROMPT, Language
from app.state_machine import CaseState, handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    FRAUD_SCORE_CHARGE,
    STATEMENT,
    assert_asks_for_statement,
    assert_escalation_notice,
    charge_extraction,
    demo_session,
    mock_anthropic_client,
    requires_real_fixture,
)

pytestmark = requires_real_fixture

PT_NLG_TEXT = "Seu caso foi processado."
PT_OPENING = "Tenho uma cobrança que não reconheço"


def _pt_turn(session, app_db, extraction, captured, case_id=None, text=PT_OPENING, **kwargs):
    client = mock_anthropic_client(extraction, PT_NLG_TEXT, captured_prompts=captured)
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        return handle_message(session, case_id, text, language="pt", db_path=app_db, **kwargs)


def _only_portuguese_prompts(captured):
    assert _EXTRACTION_SYSTEM_PROMPT["pt"] in captured
    assert _EXTRACTION_SYSTEM_PROMPT["es"] not in captured
    assert _RESPONSE_SYSTEM_PROMPT["es"] not in captured


@pytest.mark.parametrize(
    ("charge", "expected_state"),
    [
        (AUTO_RESOLVE_CHARGE, CaseState.CONFIRMING),  # AD-12: eligible matches ask first
        (FRAUD_SCORE_CHARGE, CaseState.ESCALATED),
        (None, CaseState.SELECTING),  # nothing specific: the charge list
    ],
)
def test_pt_toggle_reaches_the_expected_state(real_fixture_app_db, charge, expected_state):
    session = demo_session(real_fixture_app_db)
    captured: list[str] = []

    reply = _pt_turn(session, real_fixture_app_db, charge_extraction(charge), captured)
    if expected_state == CaseState.ESCALATED:
        # The statement step comes first, in Portuguese too.
        assert_asks_for_statement(reply, Language.PT)
        reply = _pt_turn(session, real_fixture_app_db, charge_extraction(), captured, reply["case_id"], STATEMENT)

    assert reply["state"] == expected_state
    _only_portuguese_prompts(captured)
    if expected_state == CaseState.ESCALATED:
        # The escalation notice is a template: no response prompt at all.
        assert _RESPONSE_SYSTEM_PROMPT["pt"] not in captured
        assert_escalation_notice(reply, EscalationReason.NEEDS_REVIEW, charge_named=True, language=Language.PT)
    else:
        assert _RESPONSE_SYSTEM_PROMPT["pt"] in captured
    assert cases.get_case(reply["case_id"], db_path=real_fixture_app_db).language == "pt"


def test_pt_toggle_pick_from_list_resolves(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    captured: list[str] = []
    listed = _pt_turn(session, real_fixture_app_db, charge_extraction(), captured)

    picked = _pt_turn(
        session, real_fixture_app_db, charge_extraction(), captured, case_id=listed["case_id"],
        text="Uber", selected_transaction_id=AUTO_RESOLVE_CHARGE,
    )
    assert picked["state"] == CaseState.AWAITING_EXPLANATION
    reply = _pt_turn(
        session, real_fixture_app_db, charge_extraction(), captured, case_id=listed["case_id"],
        text="Não uso Uber há meses, estou com o cartão e vi a cobrança ontem no app do banco",
    )

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert "bloque" in reply["reply"].lower()
    _only_portuguese_prompts(captured)
