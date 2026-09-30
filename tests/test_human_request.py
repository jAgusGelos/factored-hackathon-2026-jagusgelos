"""Friction #6: one "let me try first" per request for a person (plan.md
AD-8). The first request keeps the case where it is, spends no round, unlocks
the handoff in the same compare-and-set and ends the reply with the offer; the
second request (typed or the button) escalates with HUMAN_REQUESTED, in every
non-terminal state. A request that comes with details escalating by policy
escalates for that reason, and a case the agent already unlocked silently
escalates on the first request.
"""

from __future__ import annotations

from unittest.mock import patch

import anthropic
import pytest

from app import cases, replies
from app.case_model import CaseState, CustomerAction, EscalationReason
from app.llm import Language
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    FRAUD_SCORE_CHARGE,
    assert_escalation_notice,
    charge_extraction,
    demo_session,
    logged_events,
    mock_anthropic_client,
    mocked_turn,
    reach_confirming,
    reach_explaining,
    requires_real_fixture,
    session_for,
)

pytestmark = requires_real_fixture

ASK_ES = "Quiero hablar con una persona"
ASK_PT = "Quero falar com uma pessoa"
# Under `policy.MIN_EXPLANATION_WORDS`: in `awaiting_explanation` it goes to
# the extraction call, not to the assessment.
SHORT_ASK = "Hablar con alguien"
LONG_ASK = "Prefiero que me atienda una persona del banco, por favor"


def _ask(session, app_db, case_id, *, language="es", via="text", text=None):
    if via == "button":
        label = "Hablar con una persona" if language == "es" else "Falar com uma pessoa"
        return mocked_turn(session, app_db, label, case_id, language=language, action=CustomerAction.HUMAN)
    text = text or (ASK_ES if language == "es" else ASK_PT)
    # The mocked model reads it as a request for a person in every state
    # (`confirming` classifies typed text instead of extracting from it).
    return mocked_turn(
        session, app_db, text, case_id, language=language, extraction=charge_extraction(wants_human=True),
        mock={"confirmation_answer": "human"},
    )


def _case(case_id, app_db):
    return cases.get_case(case_id, db_path=app_db)


# -- Reaching each state (no request for a person yet, handoff still locked) --


def _awaiting_report(session, app_db, language):
    return None


def _selecting(session, app_db, language):
    return mocked_turn(session, app_db, "Ver mis últimos cargos", language=language,
                       action=CustomerAction.SHOW_CHARGES)["case_id"]


_SETUPS = {
    CaseState.AWAITING_REPORT: (_awaiting_report, CaseState.SELECTING),
    CaseState.SELECTING: (_selecting, CaseState.SELECTING),
    CaseState.CONFIRMING: (reach_confirming, CaseState.CONFIRMING),
    CaseState.AWAITING_EXPLANATION: (reach_explaining, CaseState.AWAITING_EXPLANATION),
}


@pytest.mark.parametrize("second_via", ["text", "button"])
@pytest.mark.parametrize("state", list(_SETUPS))
def test_the_first_request_defers_with_the_offer_and_the_second_escalates(real_fixture_app_db, state, second_via):
    session = demo_session(real_fixture_app_db)
    setup, deferred_state = _SETUPS[state]
    case_id = setup(session, real_fixture_app_db, "es")
    rounds_before = _case(case_id, real_fixture_app_db).clarification_rounds if case_id else 0
    first_text = SHORT_ASK if state == CaseState.AWAITING_EXPLANATION else None

    first = _ask(session, real_fixture_app_db, case_id, text=first_text)

    assert first["state"] == deferred_state
    assert first["reply"].endswith(replies.HUMAN_OFFER["es"])
    assert first["human_available"] is True
    stored = _case(first["case_id"], real_fixture_app_db)
    assert stored.clarification_rounds == rounds_before
    assert stored.handoff_unlocked is True
    assert [e["offer"] for e in logged_events(real_fixture_app_db, "human_request_deferred")] == [True]

    second = _ask(session, real_fixture_app_db, first["case_id"], via=second_via, text=first_text)

    assert second["state"] == CaseState.ESCALATED
    assert _case(first["case_id"], real_fixture_app_db).escalation_reason == EscalationReason.HUMAN_REQUESTED
    assert replies.HUMAN_OFFER["es"] not in second["reply"]


def test_the_first_request_in_clarifying_defers_with_the_offer(real_fixture_app_db):
    # A customer with no charges to list: the agent asks for a detail instead.
    session = session_for("CLI-SOMEONE-ELSE", real_fixture_app_db)
    asked = mocked_turn(session, real_fixture_app_db, "no sé")
    assert asked["state"] == CaseState.CLARIFYING
    assert asked["human_available"] is False
    rounds_before = _case(asked["case_id"], real_fixture_app_db).clarification_rounds

    first = _ask(session, real_fixture_app_db, asked["case_id"])

    assert first["state"] == CaseState.CLARIFYING
    assert first["reply"] == f"{replies.ASK_FOR_DETAILS['es']} {replies.HUMAN_OFFER['es']}"
    assert _case(asked["case_id"], real_fixture_app_db).clarification_rounds == rounds_before

    second = _ask(session, real_fixture_app_db, asked["case_id"], via="button")
    assert_escalation_notice(second, EscalationReason.HUMAN_REQUESTED, charge_named=False)


def test_a_request_with_details_tries_them_once_then_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = mocked_turn(session, real_fixture_app_db, "quiero una persona, es un Uber de 38.500 del 14 de junio",
                        extraction=charge_extraction(AUTO_RESOLVE_CHARGE, wants_human=True))

    assert first["state"] == CaseState.CONFIRMING
    assert first["reply"].endswith(replies.HUMAN_OFFER["es"])
    assert first["human_available"] is True
    assert _case(first["case_id"], real_fixture_app_db).clarification_rounds == 0

    second = _ask(session, real_fixture_app_db, first["case_id"])
    assert_escalation_notice(second, EscalationReason.HUMAN_REQUESTED, charge_named=False)


def test_the_offer_and_the_escalation_in_portuguese(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _ask(session, real_fixture_app_db, None, language="pt")

    assert first["state"] == CaseState.SELECTING
    assert first["reply"] == f"{replies.HUMAN_DEFERRED['pt']} {replies.HUMAN_OFFER['pt']}"

    second = _ask(session, real_fixture_app_db, first["case_id"], language="pt")
    assert_escalation_notice(second, EscalationReason.HUMAN_REQUESTED, charge_named=False, language=Language.PT)


def test_the_deferral_makes_no_model_call_for_its_text(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = mock_anthropic_client(charge_extraction(wants_human=True), nlg_text="Mirá, te paso con alguien")

    reply = mocked_turn(session, real_fixture_app_db, ASK_ES, client=client)

    # Only the extraction call: the deferral and the offer are fixed texts.
    assert client.messages.create.call_count == 1
    assert reply["reply"] == f"{replies.HUMAN_DEFERRED['es']} {replies.HUMAN_OFFER['es']}"


def test_a_lost_race_gets_no_offer(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = reach_confirming(session, real_fixture_app_db, "es")
    real_update = cases.update_case

    def another_request_escalated_first(case_id_, **kwargs):
        real_update(case_id_, state=CaseState.ESCALATED, escalation_reason=EscalationReason.NEEDS_REVIEW,
                    db_path=kwargs["db_path"])
        return real_update(case_id_, **kwargs)

    with patch("app.cases.update_case", side_effect=another_request_escalated_first):
        reply = _ask(session, real_fixture_app_db, case_id, via="button")

    assert reply["state"] == CaseState.ESCALATED
    assert replies.HUMAN_OFFER["es"] not in reply["reply"]
    assert not logged_events(real_fixture_app_db, "human_request_deferred")


# -- Detection while the customer explains (Task 3.2) ---------------------------------


def test_a_short_request_while_explaining_is_detected_and_not_counted_as_an_explanation(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = reach_explaining(session, real_fixture_app_db, "es")

    first = _ask(session, real_fixture_app_db, case_id, text=SHORT_ASK)

    assert first["state"] == CaseState.AWAITING_EXPLANATION
    assert first["reply"] == f"{replies.HUMAN_DEFERRED_WHILE_EXPLAINING['es']} {replies.HUMAN_OFFER['es']}"
    stored = _case(case_id, real_fixture_app_db)
    assert stored.explanation_text is None and stored.explanation_attempts == 0
    assert logged_events(real_fixture_app_db, "human_request_detected") == [{"via": "extract"}]

    second = _ask(session, real_fixture_app_db, case_id, text=SHORT_ASK)
    assert_escalation_notice(second, EscalationReason.HUMAN_REQUESTED, charge_named=True)
    assert second["escalation"]["charge"]["transaction_id"] == AUTO_RESOLVE_CHARGE


def test_a_long_request_while_explaining_is_detected_by_the_assessment(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = reach_explaining(session, real_fixture_app_db, "es")
    asks = {"reason": "unclear", "specific": False, "consistent": True, "contradictions": [],
            "summary": "El cliente pide hablar con una persona.", "missing_detail": None, "wants_human": True}

    first = mocked_turn(session, real_fixture_app_db, LONG_ASK, case_id, mock={"assessment": asks})

    assert first["state"] == CaseState.AWAITING_EXPLANATION
    assert first["reply"].endswith(replies.HUMAN_OFFER["es"])
    stored = _case(case_id, real_fixture_app_db)
    assert stored.explanation_text is None and stored.explanation_attempts == 0
    assert logged_events(real_fixture_app_db, "human_request_detected") == [{"via": "assessment"}]

    second = mocked_turn(session, real_fixture_app_db, LONG_ASK, case_id, mock={"assessment": asks})
    assert_escalation_notice(second, EscalationReason.HUMAN_REQUESTED, charge_named=True)


def test_a_detection_failure_while_explaining_does_not_escalate(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = reach_explaining(session, real_fixture_app_db, "es")
    client = mock_anthropic_client(charge_extraction())
    client.messages.create.side_effect = anthropic.APITimeoutError(request=None)

    reply = mocked_turn(session, real_fixture_app_db, SHORT_ASK, case_id, client=client)

    # Today's too-short path: one more detail is asked, nothing escalates.
    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    assert _case(case_id, real_fixture_app_db).handoff_unlocked is False
    assert logged_events(real_fixture_app_db, "human_request_detection_failed")
    assert not logged_events(real_fixture_app_db, "human_request_detected")


def test_a_short_explanation_that_asks_for_nobody_is_still_an_explanation(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = reach_explaining(session, real_fixture_app_db, "es")

    reply = mocked_turn(session, real_fixture_app_db, "no fui yo", case_id)

    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    assert _case(case_id, real_fixture_app_db).explanation_attempts == 1
    assert not logged_events(real_fixture_app_db, "human_request_detected")


# -- Policy first and silent unlocks (Task 3.3) ------------------------------------------


def test_a_first_request_with_details_that_escalate_by_policy_gets_the_policy_reason(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = mocked_turn(session, real_fixture_app_db, "quiero una persona, no reconozco este cargo",
                        extraction=charge_extraction(FRAUD_SCORE_CHARGE, wants_human=True))

    assert reply["state"] == CaseState.ESCALATED
    assert _case(reply["case_id"], real_fixture_app_db).escalation_reason != EscalationReason.HUMAN_REQUESTED
    assert replies.HUMAN_OFFER["es"] not in reply["reply"]
    assert not logged_events(real_fixture_app_db, "human_request_deferred")


def test_after_a_silent_unlock_the_first_request_escalates(real_fixture_app_db):
    # The agent could not find a charge for the customer's date: unlocked.
    session = demo_session(real_fixture_app_db)
    unmatched = mocked_turn(session, real_fixture_app_db, "fue el 22/04/2024", extraction=charge_extraction(date="2024-04-22"))
    assert unmatched["human_available"] is True

    reply = _ask(session, real_fixture_app_db, unmatched["case_id"])

    assert_escalation_notice(reply, EscalationReason.HUMAN_REQUESTED, charge_named=False)
    assert not logged_events(real_fixture_app_db, "human_request_deferred")


def test_once_the_rounds_are_used_the_first_request_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = _selecting(session, real_fixture_app_db, "es")
    assert cases.update_case(case_id, state=CaseState.SELECTING, clarification_rounds=2, db_path=real_fixture_app_db)

    reply = _ask(session, real_fixture_app_db, case_id)

    assert_escalation_notice(reply, EscalationReason.HUMAN_REQUESTED, charge_named=False)
