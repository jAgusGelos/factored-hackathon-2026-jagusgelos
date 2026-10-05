"""Fixture-driven integration tests for the 3 required conversation SCENARIOS
(normal auto-resolution, ambiguous/clarify, human escalation), all on the one
demo customer, plus the Milestone 8 "pick your charge" flow.

Runs against the REAL `data/fixture.duckdb` + `data/demo_users.json` built by
`etl/build_fixture.py` (what the deployed app serves), with the Anthropic
client mocked. Skipped gracefully if the ETL fixture hasn't been generated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from unittest.mock import patch

from app import cases, db, handoffs, llm
from app.case_model import EscalationReason
from app.case_turn import Turn
from app.policy import MAX_CASE_TURNS, DisputeReason
from app.state_machine import CaseState, handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    CARD_PRESENT_CHARGE,
    CONTRADICTED_ASSESSMENT,
    CONVINCING_ASSESSMENT,
    DUPLICATE_ASSESSMENT,
    DUPLICATE_CHARGES,
    EXPLANATION,
    FRAUD_SCORE_CHARGE,
    GIVEN_STATEMENT,
    HANDOFF_KEYS,
    NOT_RECEIVED_ASSESSMENT,
    OPENING,
    OVER_LIMIT_CHARGE,
    REPEAT_FARE_CHARGES,
    SECOND_ONLINE_CHARGE,
    assert_asks_for_statement,
    assert_escalation_notice,
    charge_extraction,
    charge_report,
    demo_session,
    event_sequence,
    finish_statement,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
    session_for,
    statement_down_client,
)

pytestmark = requires_real_fixture



def _say(session, app_db, extraction, text=OPENING, case_id=None, *, assessment=None, **kwargs):
    client = mock_anthropic_client(extraction, assessment=assessment)
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        return handle_message(session, case_id, text, db_path=app_db, **kwargs)


def _explain(session, app_db, case_id, assessment=None, text=EXPLANATION):
    """The customer's account of what happened, with the mocked model's read of it."""
    return _say(session, app_db, charge_extraction(), text, case_id=case_id, assessment=assessment)


def _pick(session, app_db, transaction_id, label="cargo"):
    listed = _say(session, app_db, charge_extraction(), "no sé el monto")
    return _say(session, app_db, charge_extraction(), label, case_id=listed["case_id"],
                selected_transaction_id=transaction_id)


def _pick_and_explain(session, app_db, transaction_id, assessment=None):
    picked = _pick(session, app_db, transaction_id)
    if picked["state"] != CaseState.AWAITING_EXPLANATION:
        return picked
    return _explain(session, app_db, picked["case_id"], assessment)


# -- The 3 required scenarios -------------------------------------------------


def test_normal_case_clean_auto_resolve(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    extraction = charge_extraction(AUTO_RESOLVE_CHARGE)

    first = _say(session, real_fixture_app_db, extraction)
    # AD-12: a policy-eligible match is NOT resolved in the first turn.
    assert first["state"] == CaseState.CONFIRMING
    confirmed = _say(session, real_fixture_app_db, extraction, "Sí, es ese", case_id=first["case_id"])
    # Milestone 9: before any credit the customer explains what happened.
    assert confirmed["state"] == CaseState.AWAITING_EXPLANATION
    assert logged_events(real_fixture_app_db, "simulated_credit") == []
    reply = _explain(session, real_fixture_app_db, first["case_id"])

    assert reply["state"] == CaseState.RESOLVED_AUTO
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.matched_transaction_id == AUTO_RESOLVE_CHARGE
    assert case.resolution_reference.startswith("REF-")
    # AD-11 Row 6: a SIMULATED credit, logged as such — never a real payment call.
    credits = logged_events(real_fixture_app_db, "simulated_credit")
    assert len(credits) == 1 and credits[0]["simulated"] is True


def test_ambiguous_report_shows_the_customers_charges_instead_of_repeating_a_question(real_fixture_app_db):
    """The live conversation that motivated Milestone 8: the customer does not
    know the amount and asks to see their charges.
    """
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(), "Se me perdió un monto, ¿me mostrás mis cargos?")

    assert reply["state"] == CaseState.SELECTING
    assert 0 < len(reply["options"]) <= 8
    dates = [o["date"] for o in reply["options"]]
    assert dates == sorted(dates, reverse=True)
    assert {"transaction_id", "date", "amount", "currency", "merchant", "category"} <= set(reply["options"][0])
    assert logged_events(real_fixture_app_db, "charges_offered")[0]["list_filter"] == "recent"


def test_duplicate_charges_are_listed_for_the_customer_to_pick(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(DUPLICATE_CHARGES[0]))

    assert reply["state"] == CaseState.SELECTING
    assert {o["transaction_id"] for o in reply["options"]} == set(DUPLICATE_CHARGES)


def test_escalation_case_confident_match_ineligible_produces_structured_handoff(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    asked = _say(session, real_fixture_app_db, charge_extraction(FRAUD_SCORE_CHARGE))
    reply = finish_statement(session, real_fixture_app_db, asked)

    assert reply["state"] == CaseState.ESCALATED
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.matched_transaction_id == FRAUD_SCORE_CHARGE == case.handoff["evidence"][0]
    assert set(case.handoff) == HANDOFF_KEYS
    assert any("fraud_score" in q for q in case.handoff["policy_reasons"])
    assert OPENING not in json.dumps(case.handoff)


def test_an_early_human_request_gets_the_agent_to_try_once(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(wants_human=True), "Quiero hablar con una persona")

    assert reply["state"] == CaseState.SELECTING
    assert reply["options"]
    assert reply["human_available"] is True
    assert logged_events(real_fixture_app_db, "human_request_deferred")

    again = _say(session, real_fixture_app_db, charge_extraction(wants_human=True), "Quiero hablar con una persona",
                 case_id=reply["case_id"])
    assert finish_statement(session, real_fixture_app_db, again)["state"] == CaseState.ESCALATED


def test_a_human_request_is_honored_after_details_the_agent_could_not_match(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    unmatched = _say(session, real_fixture_app_db, charge_extraction(date="2024-04-22"), "fue el 22/04/2024")
    assert unmatched["human_available"] is True

    asked = _say(session, real_fixture_app_db, charge_extraction(wants_human=True), "Quiero hablar con una persona",
                 case_id=unmatched["case_id"])
    reply = finish_statement(session, real_fixture_app_db, asked)

    assert reply["state"] == CaseState.ESCALATED

def _open_list(session, app_db):
    return _say(session, app_db, charge_extraction(), "no sé el monto")


def test_picking_an_eligible_charge_asks_what_happened_then_resolves(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    listed = _open_list(session, real_fixture_app_db)
    assert AUTO_RESOLVE_CHARGE in {o["transaction_id"] for o in listed["options"]}

    picked = _say(
        session, real_fixture_app_db, charge_extraction(), "Uber", case_id=listed["case_id"],
        selected_transaction_id=AUTO_RESOLVE_CHARGE,
    )
    assert picked["state"] == CaseState.AWAITING_EXPLANATION
    reply = _explain(session, real_fixture_app_db, listed["case_id"])

    assert reply["state"] == CaseState.RESOLVED_AUTO
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.matched_transaction_id == AUTO_RESOLVE_CHARGE
    # The customer never stated an amount: the case keeps only what they said.
    assert case.reported_amount is None
    credits = logged_events(real_fixture_app_db, "simulated_credit")
    assert len(credits) == 1 and credits[0]["amount"] == charge_report(AUTO_RESOLVE_CHARGE)["amount"]


def test_picking_an_ineligible_charge_escalates_with_the_policy_reasons(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    listed = _open_list(session, real_fixture_app_db)

    asked = _say(
        session, real_fixture_app_db, charge_extraction(), "Boutique", case_id=listed["case_id"],
        selected_transaction_id=OVER_LIMIT_CHARGE,
    )
    reply = finish_statement(session, real_fixture_app_db, asked)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["evidence"] == [OVER_LIMIT_CHARGE]
    assert "eligió" in handoff["actions_taken"][0]
    assert any("amount_usd" in q for q in handoff["policy_reasons"])
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_a_charge_that_was_not_offered_is_never_accepted(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    extraction = charge_extraction(DUPLICATE_CHARGES[0])
    listed = _say(session, real_fixture_app_db, extraction)  # offers only the two taxis
    assert AUTO_RESOLVE_CHARGE not in {o["transaction_id"] for o in listed["options"]}

    reply = _say(
        session, real_fixture_app_db, extraction, "Uber", case_id=listed["case_id"],
        selected_transaction_id=AUTO_RESOLVE_CHARGE,
    )

    assert reply["state"] == CaseState.SELECTING
    assert logged_events(real_fixture_app_db, "selection_rejected")
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_another_customers_transaction_id_is_never_accepted(real_fixture_app_db, tmp_path, monkeypatch):
    """Even if a foreign id is on the offered list (a tampered row), the
    session-scoped lookup refuses it.
    """
    other = session_for("CLI-SOMEONE-ELSE", real_fixture_app_db)
    listed = _open_list(other, real_fixture_app_db)  # that customer has no charges
    cases.update_case(
        listed["case_id"], state="selecting", offered_transaction_ids=(AUTO_RESOLVE_CHARGE,),
        db_path=real_fixture_app_db,
    )

    reply = _say(
        other, real_fixture_app_db, charge_extraction(), "Uber", case_id=listed["case_id"],
        selected_transaction_id=AUTO_RESOLVE_CHARGE,
    )

    assert reply["state"] == CaseState.SELECTING
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_not_in_the_list_without_any_detail_asks_for_one(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    listed = _open_list(session, real_fixture_app_db)

    reply = _say(
        session, real_fixture_app_db, charge_extraction(), "No está en la lista",
        case_id=listed["case_id"], action="none_of_these",
    )

    assert reply["state"] == CaseState.SELECTING
    assert logged_events(real_fixture_app_db, "details_requested")


def test_not_in_the_list_after_details_escalates_with_what_was_shown_as_evidence(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    listed = _say(session, real_fixture_app_db, charge_extraction(date="2026-06-14"), "fue el 14 de junio")

    asked = _say(
        session, real_fixture_app_db, charge_extraction(), "No está en la lista",
        case_id=listed["case_id"], action="none_of_these",
    )
    reply = finish_statement(session, real_fixture_app_db, asked)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["evidence"] == [o["transaction_id"] for o in listed["options"]]
    assert handoff["open_questions"]

def test_human_button_escalates_once_the_agent_tried(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    early = _open_list(session, real_fixture_app_db)
    deferred = _say(session, real_fixture_app_db, charge_extraction(), "Hablar con una persona",
                    case_id=early["case_id"], action="human")
    assert deferred["state"] == CaseState.SELECTING

    asked = _say(session, real_fixture_app_db, charge_extraction(), "Hablar con una persona",
                 case_id=early["case_id"], action="human")
    reply = finish_statement(session, real_fixture_app_db, asked)

    assert reply["state"] == CaseState.ESCALATED
    assert "humano" in cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff["actions_taken"][0]


def test_rejecting_the_proposed_charge_unlocks_the_human_option(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    assert first["human_available"] is False

    reply = _say(session, real_fixture_app_db, charge_extraction(), "No es ese", case_id=first["case_id"],
                 action="confirm_no")

    assert reply["human_available"] is True

def test_naming_a_single_merchant_proposes_that_charge_for_confirmation(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(merchant_hint="Uber"), "el de Uber")

    assert reply["state"] == CaseState.CONFIRMING
    assert cases.get_case(reply["case_id"], db_path=real_fixture_app_db).matched_transaction_id == AUTO_RESOLVE_CHARGE


def test_a_date_only_report_lists_the_charges_around_that_date(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(date="2026-06-14"), "fue el 14 de junio")

    assert reply["state"] == CaseState.SELECTING
    assert all("2026-06-07" <= o["date"] <= "2026-06-21" for o in reply["options"])
    assert logged_events(real_fixture_app_db, "charges_offered")[0]["list_filter"] == "filtered"


def test_a_detail_with_no_match_falls_back_to_recent_charges_and_says_so(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(date="2024-04-22"), "del 22/04/2024")

    assert reply["state"] == CaseState.SELECTING
    assert reply["options"]
    assert logged_events(real_fixture_app_db, "charges_offered")[0]["list_filter"] == "fallback_recent"


def test_turns_that_bring_new_details_do_not_spend_clarification_rounds(real_fixture_app_db):
    """The escalation the user hit live: a vague opener, then a date. The date
    is new information, so the case keeps going instead of escalating.
    """
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(), "se me perdió un monto")
    second = _say(session, real_fixture_app_db, charge_extraction(), "no lo tengo", case_id=first["case_id"])
    third = _say(
        session, real_fixture_app_db, charge_extraction(date="2026-06-14"), "del 14/06", case_id=first["case_id"]
    )

    assert [first["state"], second["state"], third["state"]] == [CaseState.SELECTING] * 3


def test_turns_with_nothing_new_eventually_escalate(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = None
    states = []
    for _ in range(4):
        reply = _say(session, real_fixture_app_db, charge_extraction(), "no sé", case_id=case_id)
        case_id = reply["case_id"]
        states.append(reply["state"])
        if reply["state"] == CaseState.AWAITING_STATEMENT:
            break
    assert states[-1] == CaseState.AWAITING_STATEMENT
    assert states.count(CaseState.SELECTING) == 2
    assert finish_statement(session, real_fixture_app_db, reply)["state"] == CaseState.ESCALATED


def test_conversation_is_capped_at_max_case_turns(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _open_list(session, real_fixture_app_db)
    con = db.get_connection(real_fixture_app_db)
    con.execute("UPDATE cases SET turn_count = ? WHERE case_id = ?", [MAX_CASE_TURNS, first["case_id"]])
    con.commit()
    con.close()

    asked = _say(session, real_fixture_app_db, charge_extraction(date="2026-06-01"), "otra fecha", case_id=first["case_id"])

    assert_asks_for_statement(asked)
    assert finish_statement(session, real_fixture_app_db, asked)["state"] == CaseState.ESCALATED


def test_clarification_reply_without_a_currency_keeps_the_originally_reported_one(real_fixture_app_db):
    """Seen live with Claude Haiku: a follow-up that omitted the currency
    silently switched the case to the profile country's currency.
    """
    session = demo_session(real_fixture_app_db)
    first = _say(
        session, real_fixture_app_db, charge_extraction(amount=12.0, currency="USD", date="2026-06-14"),
        "Tengo un cargo de 12 dólares del 14 de junio que no reconozco",
    )
    _say(session, real_fixture_app_db, charge_extraction(), "No me acuerdo del comercio", case_id=first["case_id"])

    assert cases.get_case(first["case_id"], db_path=real_fixture_app_db).reported_currency == "USD"


# -- Greeting and out-of-scope requests ---------------------------------------


def test_a_greeting_gets_an_introduction_not_a_charge_list(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(intent="greeting"), "Hola")

    assert reply["state"] == CaseState.AWAITING_REPORT
    assert reply["options"] == []
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.clarification_rounds == 0
    assert logged_events(real_fixture_app_db, "introduction")


def test_an_out_of_scope_request_is_declined_without_guessing(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(intent="other"), "¿Cuál es mi saldo?")

    assert reply["state"] == CaseState.AWAITING_REPORT
    assert reply["options"] == []
    assert logged_events(real_fixture_app_db, "out_of_scope_request")


def test_asking_to_see_charges_shows_the_list(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(intent="show_charges"), "Mostrame mis cargos")

    assert reply["state"] == CaseState.SELECTING
    assert reply["options"]


def test_a_greeting_that_also_names_a_charge_is_treated_as_a_report(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(
        session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE, intent="greeting"),
        "Hola, no reconozco un Uber de 38.500",
    )

    assert reply["state"] == CaseState.CONFIRMING


# -- Review fixes (Milestone 8 review) ----------------------------------------


def test_a_stale_confirming_write_cannot_reopen_a_resolved_case(real_fixture_app_db):
    """Two tabs: one resolves by tapping while the other, loaded earlier,
    proposes a charge. The late proposal must not pull the case back into
    `confirming` (which would allow a second credit).
    """
    from app import llm as llm_module
    from app import state_machine

    session = demo_session(real_fixture_app_db)
    listed = _open_list(session, real_fixture_app_db)
    stale_case = cases.get_case(listed["case_id"], db_path=real_fixture_app_db)
    _say(session, real_fixture_app_db, charge_extraction(), "Uber", case_id=listed["case_id"],
         selected_transaction_id=AUTO_RESOLVE_CHARGE)
    _explain(session, real_fixture_app_db, listed["case_id"])

    turn = Turn(session, stale_case, llm_module.Language.ES, "corr", real_fixture_app_db)
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(charge_extraction())):
        reply = state_machine._handle_report(turn, "el de Uber")  # merchant path would propose Uber

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert cases.get_case(listed["case_id"], db_path=real_fixture_app_db).state == "resolved_auto"
    assert len(logged_events(real_fixture_app_db, "simulated_credit")) == 1


def test_the_same_charge_is_never_credited_twice(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    for _ in range(2):
        reply = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    # The second pick is screened as already credited: a handoff, after the statement.
    reply = finish_statement(session, real_fixture_app_db, reply)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert "credited_in_case" in handoff["verified_facts"]
    assert len(logged_events(real_fixture_app_db, "simulated_credit")) == 1


def test_a_typed_report_of_an_already_credited_charge_escalates_instead_of_confirming(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE), "Sí", case_id=first["case_id"],
         action="confirm_yes")
    _explain(session, real_fixture_app_db, first["case_id"])

    asked = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    again = finish_statement(session, real_fixture_app_db, asked)

    assert again["state"] == CaseState.ESCALATED
    assert_escalation_notice(again, EscalationReason.ALREADY_CREDITED, charge_named=True)


def test_a_rejected_proposal_is_not_kept_as_the_match(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    listed = _say(session, real_fixture_app_db, charge_extraction(), "No es ese", case_id=first["case_id"],
                  action="confirm_no")
    assert cases.get_case(first["case_id"], db_path=real_fixture_app_db).matched_transaction_id is None

    asked = _say(session, real_fixture_app_db, charge_extraction(), "No está en la lista",
                 case_id=listed["case_id"], action="none_of_these")
    reply = finish_statement(session, real_fixture_app_db, asked)

    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.matched_transaction_id is None
    assert AUTO_RESOLVE_CHARGE not in case.handoff["evidence"]


def test_a_rejected_tap_resends_the_current_list(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    extraction = charge_extraction(DUPLICATE_CHARGES[0])
    listed = _say(session, real_fixture_app_db, extraction)

    reply = _say(session, real_fixture_app_db, extraction, "Uber", case_id=listed["case_id"],
                 selected_transaction_id=AUTO_RESOLVE_CHARGE)

    assert [o["transaction_id"] for o in reply["options"]] == [o["transaction_id"] for o in listed["options"]]
    assert logged_events(real_fixture_app_db, "selection_rejected")[0]["reason"] == "not_offered"


def test_a_merchant_with_no_charges_never_proposes_another_merchants_charge(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(
        session, real_fixture_app_db, charge_extraction(date="2026-06-02", merchant_hint="Netflix"), "el de Netflix",
    )

    assert reply["state"] == CaseState.SELECTING
    assert cases.get_case(reply["case_id"], db_path=real_fixture_app_db).matched_transaction_id is None


def test_repeating_the_same_merchant_is_not_new_information(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    extraction = charge_extraction(merchant_hint="Taxi Seguro")
    first = _say(session, real_fixture_app_db, extraction, "un taxi")
    turns = [_say(session, real_fixture_app_db, extraction, "un taxi", case_id=first["case_id"]) for _ in range(3)]

    # Same as saying nothing new: two rounds spent, then the third escalates.
    assert [r["state"] for r in turns] == [CaseState.SELECTING, CaseState.SELECTING, CaseState.AWAITING_STATEMENT]
    final = finish_statement(session, real_fixture_app_db, turns[-1])
    assert_escalation_notice(final, EscalationReason.CHARGE_NOT_IDENTIFIED, charge_named=False)


def test_a_full_report_still_ambiguous_after_the_rounds_escalates_without_naming_a_charge(real_fixture_app_db):
    # 27.000 COP around 15 June: both Taxi Seguro charges match.
    session = demo_session(real_fixture_app_db)
    extraction = charge_extraction(amount=27000.0, currency="COP", date="2026-06-15")
    first = _say(session, real_fixture_app_db, extraction, "27.000 del 15 de junio")
    assert first["state"] == CaseState.SELECTING
    turns = [
        _say(session, real_fixture_app_db, extraction, "27.000 del 15 de junio", case_id=first["case_id"])
        for _ in range(3)
    ]

    final = finish_statement(session, real_fixture_app_db, turns[-1])
    assert final["state"] == CaseState.ESCALATED
    assert_escalation_notice(final, EscalationReason.CHARGE_NOT_IDENTIFIED, charge_named=False)


def test_greetings_do_not_count_towards_the_turn_cap(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(intent="greeting"), "Hola")
    for _ in range(MAX_CASE_TURNS + 2):
        _say(session, real_fixture_app_db, charge_extraction(intent="greeting"), "Hola", case_id=first["case_id"])

    assert cases.get_case(first["case_id"], db_path=real_fixture_app_db).turn_count == 0


def test_turn_cap_handoff_keeps_what_the_customer_reported(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(date="2026-06-14"), "del 14 de junio")
    con = db.get_connection(real_fixture_app_db)
    con.execute("UPDATE cases SET turn_count = ? WHERE case_id = ?", [MAX_CASE_TURNS, first["case_id"]])
    con.commit()
    con.close()

    asked = _say(session, real_fixture_app_db, charge_extraction(), "otra cosa", case_id=first["case_id"])
    reply = finish_statement(session, real_fixture_app_db, asked)

    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["customer_reported"]["date"] == "2026-06-14"
    assert handoff["evidence"]


def test_asking_to_see_charges_does_not_spend_a_round(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(intent="show_charges"), "mostrame mis cargos")

    assert cases.get_case(reply["case_id"], db_path=real_fixture_app_db).clarification_rounds == 0


def test_a_wildcard_merchant_hint_matches_nothing(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(merchant_hint="%"), "%")

    assert reply["state"] == CaseState.SELECTING
    assert logged_events(real_fixture_app_db, "charges_offered")[0]["list_filter"] == "fallback_recent"


def test_a_stale_tap_from_a_replaced_list_neither_resolves_nor_claims_escalation(real_fixture_app_db):
    from app import llm as llm_module
    from app import replies, state_machine

    session = demo_session(real_fixture_app_db)
    taxis = _say(session, real_fixture_app_db, charge_extraction(DUPLICATE_CHARGES[0]))
    stale_case = cases.get_case(taxis["case_id"], db_path=real_fixture_app_db)
    relisted = _say(session, real_fixture_app_db, charge_extraction(date="2026-06-05"), "fue el 5 de junio",
                    case_id=taxis["case_id"])

    turn = Turn(session, stale_case, llm_module.Language.ES, "corr", real_fixture_app_db)
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(charge_extraction())):
        reply = state_machine._handle_selection(turn, DUPLICATE_CHARGES[0])

    assert reply["state"] == CaseState.SELECTING
    assert reply["reply"] == replies.CASE_MOVED_ON[llm_module.Language.ES]
    assert [o["transaction_id"] for o in reply["options"]] == [o["transaction_id"] for o in relisted["options"]]
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_a_stale_yes_cannot_credit_a_charge_the_customer_rejected(real_fixture_app_db):
    from app import llm as llm_module
    from app import state_machine
    from tests.support import charge_report

    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    stale_case = cases.get_case(first["case_id"], db_path=real_fixture_app_db)
    _say(session, real_fixture_app_db, charge_extraction(), "No es ese", case_id=first["case_id"], action="confirm_no")
    farmacia = charge_report(CARD_PRESENT_CHARGE)
    now = _say(session, real_fixture_app_db, charge_extraction(**farmacia), "era la farmacia", case_id=first["case_id"])
    assert now["state"] == CaseState.CONFIRMING

    turn = Turn(session, stale_case, llm_module.Language.ES, "corr", real_fixture_app_db)
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(charge_extraction())):
        state_machine._handle_confirmation(turn, "Sí", state_machine.CustomerAction.CONFIRM_YES)

    assert logged_events(real_fixture_app_db, "simulated_credit") == []
    assert cases.get_case(first["case_id"], db_path=real_fixture_app_db).matched_transaction_id == CARD_PRESENT_CHARGE


# -- Try first, but never trap the customer -----------------------------------


def test_insisting_on_a_person_without_details_reaches_one_on_the_second_request(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    case_id = None
    states = []
    for _ in range(2):
        reply = _say(session, real_fixture_app_db, charge_extraction(), "Hablar con una persona",
                     case_id=case_id, action="human")
        case_id = reply["case_id"]
        states.append(reply["state"])

    assert states == [CaseState.SELECTING, CaseState.AWAITING_STATEMENT]
    assert finish_statement(session, real_fixture_app_db, reply)["state"] == CaseState.ESCALATED


def test_repeating_not_in_the_list_without_details_eventually_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    listed = _open_list(session, real_fixture_app_db)
    replies = [
        _say(session, real_fixture_app_db, charge_extraction(), "No está en la lista",
             case_id=listed["case_id"], action="none_of_these")
        for _ in range(3)
    ]
    states = [r["state"] for r in replies]

    assert states[-1] == CaseState.AWAITING_STATEMENT
    assert CaseState.SELECTING in states
    # A later tap on the old button changes nothing: the first one that asks is the handoff.
    asked = next(r for r in replies if r["state"] == CaseState.AWAITING_STATEMENT)
    assert finish_statement(session, real_fixture_app_db, asked)["state"] == CaseState.ESCALATED


def test_insisting_on_a_person_while_confirming_escalates_on_the_second_request(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    replies = [
        _say(session, real_fixture_app_db, charge_extraction(), "Hablar con una persona",
             case_id=first["case_id"], action="human")
        for _ in range(2)
    ]

    assert [r["state"] for r in replies] == [CaseState.CONFIRMING, CaseState.AWAITING_STATEMENT]
    assert finish_statement(session, real_fixture_app_db, replies[-1])["state"] == CaseState.ESCALATED


def test_a_human_request_with_details_tries_the_details_first(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE, wants_human=True),
                 "quiero una persona, es un Uber de 38.500 del 14 de junio")

    # The details are tried, and that is the one deferral: the offer follows.
    assert reply["state"] == CaseState.CONFIRMING
    assert reply["human_available"] is True
    assert logged_events(real_fixture_app_db, "human_request_with_details")
    assert logged_events(real_fixture_app_db, "human_request_deferred")[0]["offer"] is True


def test_a_customer_with_no_charges_can_reach_a_person_after_giving_a_detail(real_fixture_app_db):
    other = session_for("CLI-SOMEONE-ELSE", real_fixture_app_db)
    asked = _say(other, real_fixture_app_db, charge_extraction(date="2026-06-05"), "fue el 5 de junio")
    assert asked["state"] == CaseState.CLARIFYING
    assert asked["human_available"] is True

    held = _say(other, real_fixture_app_db, charge_extraction(), "Hablar con una persona",
                case_id=asked["case_id"], action="human")
    reply = finish_statement(other, real_fixture_app_db, held)

    assert reply["state"] == CaseState.ESCALATED


# -- AD-13: the evidence, not the claim, decides the credit --------------------


def _credited_amount_usd(app_db, case_id) -> float | None:
    """The amount the credit limits count for this case (a column the app only reads in SQL)."""
    with db.app_connection(app_db) as con:
        return con.execute("SELECT credited_amount_usd FROM cases WHERE case_id = ?", [case_id]).fetchone()[0]


def test_an_unrecognized_credit_is_provisional_and_blocks_the_card(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert "bloque" in reply["reply"].lower()
    assert logged_events(real_fixture_app_db, "simulated_card_block")
    assert logged_events(real_fixture_app_db, "credit_review_queued")[0]["reversible"] is True
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.dispute_reason == "unrecognized"
    assert case.credit_key == AUTO_RESOLVE_CHARGE
    assert _credited_amount_usd(real_fixture_app_db, reply["case_id"]) > 0


def test_a_card_present_charge_is_never_credited_on_the_customers_word(real_fixture_app_db):
    """Farmacia Salud is a POS (chip/PIN) purchase: however convincing the
    explanation, it goes to a fraud investigation, not a same-minute credit.
    """
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, CARD_PRESENT_CHARGE)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert any("card-present" in q for q in handoff["policy_reasons"])
    assert handoff["customer_reported"]["explanation_specific"] == "sí"
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_an_unrecognized_claim_on_a_merchant_the_customer_uses_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[1])

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert any("other charge(s) at 'Taxi Seguro'" in q for q in handoff["policy_reasons"])
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_only_one_unrecognized_credit_per_window(real_fixture_app_db):
    """After one provisional credit (and card block), a second "I don't
    recognize it" on another clean online charge goes to a person.
    """
    session = demo_session(real_fixture_app_db)
    first = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    second = _pick_and_explain(session, real_fixture_app_db, SECOND_ONLINE_CHARGE)

    assert first["state"] == CaseState.RESOLVED_AUTO
    assert second["state"] == CaseState.ESCALATED
    handoff = cases.get_case(second["case_id"], db_path=real_fixture_app_db).handoff
    assert any("already granted" in q for q in handoff["policy_reasons"])
    assert len(logged_events(real_fixture_app_db, "simulated_credit")) == 1


def test_a_verified_duplicate_is_reversed_without_blocking_the_card(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[1], DUPLICATE_ASSESSMENT)

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert "duplicado" in reply["reply"]
    assert logged_events(real_fixture_app_db, "simulated_card_block") == []
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.dispute_reason == "duplicate"
    assert case.credit_key == f"duplicate:{min(DUPLICATE_CHARGES)}"


def test_the_same_fare_on_the_next_day_is_not_reversed_as_a_duplicate(real_fixture_app_db):
    """Cabify 18.500 COP on two consecutive mornings: two rides. The customer
    who calls it a duplicate goes to a person with both charges as evidence.
    """
    session = demo_session(real_fixture_app_db)
    listed = _say(session, real_fixture_app_db, charge_extraction(merchant_hint="Cabify"), "me cobraron dos veces Cabify")
    assert REPEAT_FARE_CHARGES[1] in [o["transaction_id"] for o in listed["options"]]
    picked = _say(session, real_fixture_app_db, charge_extraction(), "cargo", case_id=listed["case_id"],
                  selected_transaction_id=REPEAT_FARE_CHARGES[1])
    assert picked["state"] == CaseState.AWAITING_EXPLANATION
    reply = _explain(session, real_fixture_app_db, picked["case_id"], DUPLICATE_ASSESSMENT)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert any(REPEAT_FARE_CHARGES[0] in q and "separate purchases" in q for q in handoff["policy_reasons"])
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def _credit_events(app_db, case_id) -> list[str]:
    """The events of a resolved case from the assessment to the resolution."""
    events = event_sequence(app_db, case_id)
    return events[events.index("explanation_assessed"):events.index("case_resolved") + 1]


def test_an_unrecognized_credit_logs_the_card_block_and_the_review_in_order(real_fixture_app_db):
    """Pins the credit path's event order: after the credit, an unrecognized
    charge adds the card block and the review, then the case is resolved.
    """
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert _credit_events(real_fixture_app_db, reply["case_id"]) == [
        "explanation_assessed", "simulated_credit", "simulated_card_block", "credit_review_queued", "case_resolved",
    ]


def test_a_duplicate_reversal_logs_only_the_credit_and_the_resolution(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[1], DUPLICATE_ASSESSMENT)

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert _credit_events(real_fixture_app_db, reply["case_id"]) == [
        "explanation_assessed", "simulated_credit", "case_resolved",
    ]


def test_a_duplicate_pair_is_reversed_only_once(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[1], DUPLICATE_ASSESSMENT)
    second = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[0], DUPLICATE_ASSESSMENT)

    assert first["state"] == CaseState.RESOLVED_AUTO
    assert second["state"] == CaseState.ESCALATED
    handoff = cases.get_case(second["case_id"], db_path=real_fixture_app_db).handoff
    assert any("duplicate pair was already credited" in q for q in handoff["policy_reasons"])
    assert len(logged_events(real_fixture_app_db, "simulated_credit")) == 1


def test_a_duplicate_claim_without_a_twin_in_the_data_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE, DUPLICATE_ASSESSMENT)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert any("no other charge at the same merchant" in q for q in handoff["policy_reasons"])


def test_not_received_goes_to_a_person_as_a_merchant_dispute(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE, NOT_RECEIVED_ASSESSMENT)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert any("contracargo" in q for q in handoff["policy_reasons"])
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_a_vague_explanation_gets_one_follow_up_then_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    vague = {**CONVINCING_ASSESSMENT, "specific": False}

    follow_up = _explain(session, real_fixture_app_db, picked["case_id"], vague)
    final = _explain(session, real_fixture_app_db, picked["case_id"], vague)

    assert follow_up["state"] == CaseState.AWAITING_EXPLANATION
    assert final["state"] == CaseState.ESCALATED
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_a_high_fraud_score_escalates_before_asking_for_an_explanation(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = finish_statement(session, real_fixture_app_db, _pick(session, real_fixture_app_db, FRAUD_SCORE_CHARGE))

    assert reply["state"] == CaseState.ESCALATED
    assert logged_events(real_fixture_app_db, "explanation_requested") == []




# -- AD-14: a fraud escalation blocks the card before the handoff --------------


def _card_block(app_db, case_id):
    return [e for e in logged_events(app_db, "simulated_card_block") if e.get("trigger") == "escalation"]


def test_a_denied_charge_with_a_high_fraud_score_blocks_the_card_on_escalation(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = finish_statement(session, real_fixture_app_db, _pick(session, real_fixture_app_db, FRAUD_SCORE_CHARGE))

    assert reply["state"] == CaseState.ESCALATED
    assert "bloqueamos su tarjeta" in reply["reply"]
    block = _card_block(real_fixture_app_db, reply["case_id"])
    assert len(block) == 1 and block[0]["signals"] == ["high_fraud_score"] and block[0]["simulated"] is True
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert any(a.startswith("Tarjeta bloqueada preventivamente") for a in handoff["actions_taken"])
    assert handoffs.CARD_BLOCKED_TASK in handoff["open_questions"]
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_the_block_is_announced_in_portuguese_too(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    listed = _say(session, real_fixture_app_db, charge_extraction(), "não sei o valor", language="pt")
    picked = _say(session, real_fixture_app_db, charge_extraction(), "cobrança", case_id=listed["case_id"],
                  selected_transaction_id=FRAUD_SCORE_CHARGE, language="pt")
    reply = finish_statement(session, real_fixture_app_db, picked, language="pt")

    assert "bloqueamos seu cartão" in reply["reply"]


def test_a_lost_card_replaces_the_advisor_block_question_with_the_reissue_task(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    lost = {**GIVEN_STATEMENT, "card_possession": "no", "card_loss": "lost"}
    picked = _pick(session, real_fixture_app_db, OVER_LIMIT_CHARGE)
    reply = finish_statement(session, real_fixture_app_db, picked, mock={"statement": lost})

    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert not any("bloquear" in question for question in handoff["open_questions"])
    assert handoffs.CARD_BLOCKED_TASK in handoff["open_questions"]
    assert "card_out_of_hands" in _card_block(real_fixture_app_db, reply["case_id"])[0]["signals"]


def test_a_card_present_charge_the_customer_denies_blocks_the_card(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, CARD_PRESENT_CHARGE)

    assert reply["state"] == CaseState.ESCALATED
    assert _card_block(real_fixture_app_db, reply["case_id"])[0]["signals"] == ["card_present_denied"]
    assert "bloqueamos su tarjeta" in reply["reply"]


def test_an_amount_cap_escalation_the_customer_does_not_deny_never_blocks(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    recognized = {**GIVEN_STATEMENT, "denies_purchase": "no", "summary": "El cliente hizo la compra."}
    picked = _pick(session, real_fixture_app_db, OVER_LIMIT_CHARGE)
    reply = finish_statement(session, real_fixture_app_db, picked, mock={"statement": recognized})

    assert reply["state"] == CaseState.ESCALATED
    assert _card_block(real_fixture_app_db, reply["case_id"]) == []
    assert "bloque" not in reply["reply"]
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert handoffs.CARD_BLOCKED_TASK not in handoff["open_questions"]


def test_a_merchant_dispute_never_blocks_the_card(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, CARD_PRESENT_CHARGE, NOT_RECEIVED_ASSESSMENT)

    assert reply["state"] == CaseState.ESCALATED
    assert _card_block(real_fixture_app_db, reply["case_id"]) == []


def test_an_unreadable_statement_never_blocks_the_card(real_fixture_app_db):
    """No denial on record (the statement could not be read), so no block,
    even on the charge with the high fraud score.
    """
    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, FRAUD_SCORE_CHARGE)
    reply = finish_statement(session, real_fixture_app_db, picked, client=statement_down_client(charge_extraction()))

    assert reply["state"] == CaseState.ESCALATED
    assert _card_block(real_fixture_app_db, reply["case_id"]) == []


def test_a_service_failure_never_blocks_the_card(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, CARD_PRESENT_CHARGE)
    with patch("app.explanation.llm.assess_explanation", side_effect=llm.LLMUnavailable("down")):
        reply = _explain(session, real_fixture_app_db, picked["case_id"])

    assert reply["state"] == CaseState.ESCALATED
    assert cases.get_case(reply["case_id"], db_path=real_fixture_app_db).escalation_reason == "service_issue"
    assert _card_block(real_fixture_app_db, reply["case_id"]) == []


# -- Review fixes (Milestone 9 review) -------------------------------------------


@dataclass
class _StaleReadProbe:
    """How many policy reads went through the patch, and how many of those had
    a credit history to hide (so a test can prove the patch reached the path).
    """

    calls: int = 0
    zeroed: int = 0


def _stale_limits_read(monkeypatch) -> _StaleReadProbe:
    """Makes every policy read see no earlier credits, as a second chat that
    read the history before the first one committed would.
    """
    from app import state_machine

    fresh = state_machine._dispute_context
    probe = _StaleReadProbe()

    def stale(*args, **kwargs):
        ctx = fresh(*args, **kwargs)
        probe.calls += 1
        if ctx.duplicate_pair_credited or ctx.recent_unrecognized_credits or ctx.recent_credited_usd:
            probe.zeroed += 1
        return replace(ctx, duplicate_pair_credited=False, recent_unrecognized_credits=0, recent_credited_usd=0.0)

    monkeypatch.setattr(state_machine, "_dispute_context", stale)
    return probe


def test_a_pending_twin_is_not_a_second_charge(real_fixture_app_db, tmp_path, monkeypatch):
    """A pending hold (or a declined retry) next to the real charge was never
    collected: claiming a duplicate against it must not reverse the real one.
    """
    import duckdb

    from app import config

    fixture_copy = tmp_path / "fixture.duckdb"
    fixture_copy.write_bytes(config.FIXTURE_DB_PATH.read_bytes())
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", fixture_copy)
    con = duckdb.connect(str(fixture_copy))
    con.execute("UPDATE transactions SET transaction_status = 'Pending' WHERE transaction_id = ?", [DUPLICATE_CHARGES[0]])
    con.close()

    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[1], DUPLICATE_ASSESSMENT)

    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert any("no other charge at the same merchant" in q for q in handoff["policy_reasons"])
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_a_credit_granted_before_the_new_columns_counts_towards_the_limits(real_fixture_app_db):
    """A resolved case from before this migration has no amount or reason: it
    counts as a full-cap unrecognized credit, never as zero.
    """
    session = demo_session(real_fixture_app_db)
    legacy = cases.create_case(session.customer_id, "es", db_path=real_fixture_app_db)
    cases.update_case(legacy.case_id, state="resolved_auto", resolution_reference="REF-LEGACY", db_path=real_fixture_app_db)

    history = cases.credit_history(session.customer_id, db_path=real_fixture_app_db)
    # Screening at the pick already counts the legacy credit: a handoff, after the statement.
    reply = finish_statement(session, real_fixture_app_db, _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE))

    assert history.unrecognized_count == 1 and history.total_usd > 0
    assert reply["state"] == CaseState.ESCALATED
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_two_chats_cannot_both_slip_under_the_credit_limit(real_fixture_app_db, monkeypatch):
    """The second chat's policy read is stale (it saw no credit yet); the
    guard inside the claiming UPDATE still refuses its credit.
    """
    session = demo_session(real_fixture_app_db)
    first = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    probe = _stale_limits_read(monkeypatch)
    second = _pick_and_explain(session, real_fixture_app_db, SECOND_ONLINE_CHARGE)

    assert probe.calls >= 1 and probe.zeroed >= 1
    assert first["state"] == CaseState.RESOLVED_AUTO
    assert second["state"] == CaseState.ESCALATED
    assert_escalation_notice(second, EscalationReason.NEEDS_REVIEW, charge_named=True)
    assert logged_events(real_fixture_app_db, "credit_limit_reached")
    assert logged_events(real_fixture_app_db, "credit_already_granted") == []
    assert len(logged_events(real_fixture_app_db, "simulated_credit")) == 1
    case = cases.get_case(second["case_id"], db_path=real_fixture_app_db)
    assert case.handoff["customer_reported"]["dispute_reason"] == "unrecognized"


def test_two_chats_cannot_reverse_both_charges_of_a_duplicate_pair(real_fixture_app_db, monkeypatch):
    session = demo_session(real_fixture_app_db)
    first = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[1], DUPLICATE_ASSESSMENT)
    probe = _stale_limits_read(monkeypatch)
    second = _pick_and_explain(session, real_fixture_app_db, DUPLICATE_CHARGES[0], DUPLICATE_ASSESSMENT)

    assert probe.calls >= 1 and probe.zeroed >= 1
    assert first["state"] == CaseState.RESOLVED_AUTO
    assert second["state"] == CaseState.ESCALATED
    assert logged_events(real_fixture_app_db, "credit_already_granted")
    handoff = cases.get_case(second["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["verified_facts"]["credited_in_case"] == first["case_id"]
    assert len(logged_events(real_fixture_app_db, "simulated_credit")) == 1


def test_the_explanation_step_uses_the_state_machines_policy_verdict(real_fixture_app_db, monkeypatch):
    """The dispatcher hands the explanation step `state_machine._policy_verdict`
    as it is at call time, so patching it is seen by a real explanation turn.
    """
    from app import state_machine

    real_verdict = state_machine._policy_verdict
    reasons = []

    def recording_verdict(*args, **kwargs):
        reasons.append(kwargs.get("reason"))
        return real_verdict(*args, **kwargs)

    monkeypatch.setattr(state_machine, "_policy_verdict", recording_verdict)
    session = demo_session(real_fixture_app_db)
    reply = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert DisputeReason.UNRECOGNIZED in reasons


def test_an_unusable_assessment_is_reported_as_the_models_failure(real_fixture_app_db):
    from app import handoffs

    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    broken = {"not": "the contract"}

    retried = _explain(session, real_fixture_app_db, picked["case_id"], broken)
    final = _explain(session, real_fixture_app_db, picked["case_id"], broken)

    assert retried["state"] == CaseState.AWAITING_EXPLANATION
    assert final["state"] == CaseState.ESCALATED
    assert len(logged_events(real_fixture_app_db, "explanation_parse_failed")) == 2
    handoff = cases.get_case(final["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["policy_reasons"] == [handoffs.ASSESSMENT_FAILED]
    assert handoff["open_questions"] == [handoffs.EXPLANATION_REVIEW_QUESTION]


def test_a_too_short_explanation_is_not_labelled_as_a_model_summary(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    _explain(session, real_fixture_app_db, picked["case_id"], text="no sé")
    final = _explain(session, real_fixture_app_db, picked["case_id"], text="nada")

    facts = cases.get_case(final["case_id"], db_path=real_fixture_app_db).handoff["customer_reported"]
    assert "demasiado breve" in facts["explanation_assessment"]
    assert "explanation_summary" not in facts


def test_explanation_turns_are_appended_not_overwritten(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    for text in ("primera parte", "segunda parte"):
        cases.update_case(
            picked["case_id"], state="awaiting_explanation", append_explanation=text, db_path=real_fixture_app_db,
        )

    assert cases.get_case(picked["case_id"], db_path=real_fixture_app_db).explanation_text == (
        "primera parte\nsegunda parte"
    )


# -- Stale quick-replies change nothing (AD-6) ----------------------------------


def _case_progress(app_db, case_id) -> tuple:
    case = cases.get_case(case_id, db_path=app_db)
    return (case.state, case.explanation_attempts, case.explanation_text, case.clarification_rounds,
            case.matched_transaction_id)


def test_an_old_yes_while_explaining_does_not_spend_an_explanation_attempt(real_fixture_app_db):
    from app import replies

    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    before = _case_progress(real_fixture_app_db, picked["case_id"])

    reply = _say(session, real_fixture_app_db, charge_extraction(), "Sí, es ese", case_id=picked["case_id"],
                 action="confirm_yes")

    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    assert reply["reply"] == replies.ACTION_UNAVAILABLE["es"]
    assert _case_progress(real_fixture_app_db, picked["case_id"]) == before
    assert logged_events(real_fixture_app_db, "action_rejected") == [
        {"action": "confirm_yes", "state": "awaiting_explanation"}
    ]
    assert logged_events(real_fixture_app_db, "explanation_assessed") == []


def test_an_old_not_in_the_list_while_confirming_changes_nothing(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    before = _case_progress(real_fixture_app_db, first["case_id"])

    reply = _say(session, real_fixture_app_db, charge_extraction(), "No está en la lista", case_id=first["case_id"],
                 action="none_of_these", language="pt")

    assert reply["state"] == CaseState.CONFIRMING
    assert reply["reply"] == (
        "Essa opção não está mais disponível. Você pode continuar a partir da última mensagem."
    )
    assert _case_progress(real_fixture_app_db, first["case_id"]) == before
    assert logged_events(real_fixture_app_db, "action_rejected") == [
        {"action": "none_of_these", "state": "confirming"}
    ]


def test_an_old_no_while_selecting_resends_the_current_list(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    listed = _open_list(session, real_fixture_app_db)
    before = _case_progress(real_fixture_app_db, listed["case_id"])

    reply = _say(session, real_fixture_app_db, charge_extraction(), "No es ese", case_id=listed["case_id"],
                 action="confirm_no")

    assert reply["state"] == CaseState.SELECTING
    assert reply["options"] == listed["options"]
    assert _case_progress(real_fixture_app_db, listed["case_id"]) == before


# -- The same charge, already with a person after an explanation (AD-2) ---------


def _escalated_after_explaining(session, app_db, transaction_id):
    reply = _pick_and_explain(session, app_db, transaction_id, CONTRADICTED_ASSESSMENT)
    assert reply["state"] == CaseState.ESCALATED
    assert cases.get_case(reply["case_id"], db_path=app_db).dispute_reason == "unrecognized"
    return reply


def test_a_new_case_on_a_charge_already_escalated_after_an_explanation_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    prior = _escalated_after_explaining(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)

    retry = finish_statement(session, real_fixture_app_db, _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE))

    assert retry["state"] == CaseState.ESCALATED
    assert retry["case_id"] != prior["case_id"]
    assert_escalation_notice(retry, EscalationReason.ALREADY_IN_REVIEW, charge_named=True)
    # No second explanation is asked for, and nothing internal reaches the customer.
    assert event_sequence(real_fixture_app_db, retry["case_id"]).count("explanation_requested") == 0
    assert prior["case_id"] not in retry["reply"]
    handoff = cases.get_case(retry["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["verified_facts"]["prior_case"] == prior["case_id"]
    assert any(prior["case_id"] in q for q in handoff["open_questions"])
    assert logged_events(real_fixture_app_db, "prior_escalation_same_charge") == [
        {"matched_transaction_id": AUTO_RESOLVE_CHARGE, "prior_case_id": prior["case_id"]}
    ]
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_a_typed_report_of_a_charge_escalated_after_an_explanation_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    prior = _escalated_after_explaining(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)

    asked = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    retry = finish_statement(session, real_fixture_app_db, asked)

    assert retry["state"] == CaseState.ESCALATED
    handoff = cases.get_case(retry["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["verified_facts"]["prior_case"] == prior["case_id"]


def test_a_charge_escalated_on_a_request_for_a_person_can_still_be_explained(real_fixture_app_db):
    """Only an escalation after an assessed explanation blocks a retry: one on
    a request for a person (no dispute reason) does not.
    """
    session = demo_session(real_fixture_app_db)
    first = _say(session, real_fixture_app_db, charge_extraction(AUTO_RESOLVE_CHARGE))
    for _ in range(2):
        asked = _say(session, real_fixture_app_db, charge_extraction(), "Hablar con una persona",
                     case_id=first["case_id"], action="human")
    handed_off = finish_statement(session, real_fixture_app_db, asked)
    assert handed_off["state"] == CaseState.ESCALATED
    prior = cases.get_case(first["case_id"], db_path=real_fixture_app_db)
    assert prior.matched_transaction_id == AUTO_RESOLVE_CHARGE and prior.dispute_reason is None

    retry = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)

    assert retry["state"] == CaseState.AWAITING_EXPLANATION
    assert logged_events(real_fixture_app_db, "prior_escalation_same_charge") == []


def test_a_new_case_on_a_charge_still_open_after_an_explanation_attempt_escalates(real_fixture_app_db):
    """Case A asked for more detail (an attempt spent, no reason stored) and
    stays open: case B on the same charge must not start over with a new story
    and fresh attempts.
    """
    session = demo_session(real_fixture_app_db)
    vague = {**CONVINCING_ASSESSMENT, "specific": False}
    prior = _pick_and_explain(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE, vague)
    assert prior["state"] == CaseState.AWAITING_EXPLANATION
    open_case = cases.get_case(prior["case_id"], db_path=real_fixture_app_db)
    assert open_case.explanation_attempts == 1 and open_case.dispute_reason is None

    retry = finish_statement(session, real_fixture_app_db, _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE))

    assert retry["state"] == CaseState.ESCALATED
    assert retry["case_id"] != prior["case_id"]
    assert prior["case_id"] not in retry["reply"]
    handoff = cases.get_case(retry["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["verified_facts"]["prior_case"] == prior["case_id"]
    assert logged_events(real_fixture_app_db, "prior_escalation_same_charge") == [
        {"matched_transaction_id": AUTO_RESOLVE_CHARGE, "prior_case_id": prior["case_id"]}
    ]
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_an_open_case_that_only_identified_the_charge_does_not_block_a_new_one(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    prior = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    assert prior["state"] == CaseState.AWAITING_EXPLANATION

    retry = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)

    assert retry["state"] == CaseState.AWAITING_EXPLANATION
    assert retry["case_id"] != prior["case_id"]
    assert logged_events(real_fixture_app_db, "prior_escalation_same_charge") == []


# -- The resolution message (AD-8) -----------------------------------------------


def test_the_resolution_is_the_fixed_template_not_a_model_reply(real_fixture_app_db):
    from app import replies

    session = demo_session(real_fixture_app_db)
    picked = _pick(session, real_fixture_app_db, AUTO_RESOLVE_CHARGE)
    client = mock_anthropic_client(charge_extraction(), "Texto libre del modelo sin referencia.")
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        reply = handle_message(session, picked["case_id"], EXPLANATION, db_path=real_fixture_app_db)

    case = cases.get_case(picked["case_id"], db_path=real_fixture_app_db)
    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert reply["reply"] == replies.resolved(case.resolution_reference, DisputeReason.UNRECOGNIZED, "es")
    # Only the assessment reached the model on the resolving turn.
    assert client.messages.create.call_count == 1
