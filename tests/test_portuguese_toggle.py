"""Portuguese simulation toggle (Task 2.5, AD-8).

Reuses the exact same 3 real personas/fixtures as
`tests/test_conversation_flows.py`, run through the `pt` language toggle,
asserting each still completes to its expected AD-11 final state. This is
explicitly a demonstration that the toggle works across all 3 required
cases — NOT a validated NLU capability claim, since the dataset has zero
Portuguese data (AD-8's disclosed limitation). No classifier/eval claim is
attached to this test.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app import cases
from app.llm import _EXTRACTION_SYSTEM_PROMPT, _RESPONSE_SYSTEM_PROMPT
from app.state_machine import CaseState, handle_message
from tests.support import (
    mock_anthropic_client,
    persona_complaint,
    persona_session,
    requires_real_fixture,
)

pytestmark = requires_real_fixture

PT_NLG_TEXT = "Seu caso foi processado."


@pytest.mark.parametrize(
    ("username", "expected_state"),
    [
        ("cliente.claro", CaseState.CONFIRMING),  # AD-12: eligible matches ask first
        ("cliente.escalado", CaseState.ESCALATED),
    ],
)
def test_pt_toggle_completes_to_expected_state(real_fixture_app_db, username, expected_state):
    session = persona_session(username, real_fixture_app_db)
    report = persona_complaint(session.customer_id)
    extraction = {**report, "merchant_hint": None, "wants_human": False}
    captured_systems: list[str] = []

    with patch(
        "app.llm.anthropic.Anthropic",
        return_value=mock_anthropic_client(
            extraction, PT_NLG_TEXT, captured_prompts=captured_systems
        ),
    ):
        reply = handle_message(
            session, None, "Tenho uma cobrança que não reconheço", language="pt", db_path=real_fixture_app_db
        )

    assert reply["state"] == expected_state
    assert _EXTRACTION_SYSTEM_PROMPT["pt"] in captured_systems
    assert _RESPONSE_SYSTEM_PROMPT["pt"] in captured_systems
    assert _EXTRACTION_SYSTEM_PROMPT["es"] not in captured_systems
    assert _RESPONSE_SYSTEM_PROMPT["es"] not in captured_systems

    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.language == "pt"


def test_pt_toggle_ambiguous_case_settles_within_round_budget(real_fixture_app_db):
    session = persona_session("cliente.ambiguo", real_fixture_app_db)
    report = persona_complaint(session.customer_id)
    extraction = {**report, "merchant_hint": None, "wants_human": False}
    captured_systems: list[str] = []

    case_id = None
    states = []
    with patch(
        "app.llm.anthropic.Anthropic",
        return_value=mock_anthropic_client(
            extraction, PT_NLG_TEXT, captured_prompts=captured_systems
        ),
    ):
        for _ in range(3):
            reply = handle_message(
                session, case_id, "Tenho uma cobrança que não reconheço", language="pt", db_path=real_fixture_app_db
            )
            case_id = reply["case_id"]
            states.append(reply["state"])
            if reply["state"] != CaseState.CLARIFYING:
                break

    assert sum(1 for s in states if s == CaseState.CLARIFYING) <= 2
    assert states[-1] == CaseState.ESCALATED
    assert _EXTRACTION_SYSTEM_PROMPT["pt"] in captured_systems
