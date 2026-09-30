"""AD-12: the confirm-before-resolve turn.

A policy-eligible match must never resolve in the same turn the customer first
reports it: the agent names the matched merchant/amount/date, waits for an
explicit "yes" and re-verifies AD-11 at that moment. "Not that one" shows the
customer their charges to pick from (Milestone 8) instead of escalating. Runs
against the real demo fixture with the Anthropic client mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import anthropic
import duckdb
import pytest

from app import cases, config, llm
from app.case_turn import Turn
from app.explanation import handle_explanation
from app.state_machine import CaseState, handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    EXPLANATION,
    OPENING,
    charge_extraction,
    charge_report,
    demo_session,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
    session_for,
)

pytestmark = requires_real_fixture



def _client(session, *, answer="yes", nlg="Respuesta generada."):
    return mock_anthropic_client(charge_extraction(AUTO_RESOLVE_CHARGE), nlg, confirmation_answer=answer)


def _first_turn(session, db, client):
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        return handle_message(session, None, OPENING, db_path=db)


def _confirm(session, db, case_id, client, text="Sí, es ese"):
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        return handle_message(session, case_id, text, db_path=db)


def _explain(session, db, case_id, client):
    """Milestone 9: after the "yes", the customer explains what happened."""
    return _confirm(session, db, case_id, client, text=EXPLANATION)


def test_first_report_never_resolves_and_no_credit_is_issued(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _first_turn(session, real_fixture_app_db, _client(session))

    assert reply["state"] == CaseState.CONFIRMING
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.state == "confirming"
    assert case.matched_transaction_id is not None
    assert case.resolution_reference is None
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_confirmation_question_names_merchant_amount_and_date(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    reply = _first_turn(session, real_fixture_app_db, _client(session))

    # The mock model returns a reply with none of the facts, so the
    # deterministic template must have replaced it.
    assert reply["reply"] != "Respuesta generada."
    assert "COP 38.500" in reply["reply"]
    assert "Uber" in reply["reply"]
    assert "T00:00" not in reply["reply"]
    assert "14 de junio de 2026" in reply["reply"]
    assert logged_events(real_fixture_app_db, "confirmation_reply_replaced")


@pytest.mark.parametrize(
    "nlg",
    [
        "Veo un cargo de $38.500 COP en Uber del 14 de junio. ¿Es ese?",
        "Encontré un cargo en Uber por 38,500.00 COP el 2026-06-14. ¿Es ese?",
    ],
)
def test_natural_wordings_of_the_right_facts_are_kept(real_fixture_app_db, nlg):
    """Seen live with Claude Haiku: amounts without zero cents and dates with
    the month name are correct and must not be swapped for the template.
    """
    session = demo_session(real_fixture_app_db)
    reply = _first_turn(session, real_fixture_app_db, _client(session, nlg=nlg))
    assert reply["reply"] == nlg


def test_model_reply_that_names_the_facts_is_kept(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    # Learn the real matched facts first, then make the model say them.
    probe = _first_turn(session, real_fixture_app_db, _client(session))
    case = cases.get_case(probe["case_id"], db_path=real_fixture_app_db)
    con = duckdb.connect(str(config.FIXTURE_DB_PATH), read_only=True)
    try:
        merchant = con.execute(
            "SELECT merchant_name FROM transactions WHERE transaction_id = ?",
            [case.matched_transaction_id],
        ).fetchone()[0]
    finally:
        con.close()
    nlg = (
        f"Veo un cargo de {case.reported_amount:,.2f} en {merchant} el {case.reported_date}. "
        "¿Es ese el que no reconoce?"
    )

    session2 = demo_session(real_fixture_app_db)
    reply = _first_turn(session2, real_fixture_app_db, _client(session2, nlg=nlg))

    assert reply["state"] == CaseState.CONFIRMING
    assert reply["reply"] == nlg


def test_explicit_yes_resolves_with_a_simulated_credit(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer="yes")
    first = _first_turn(session, real_fixture_app_db, client)

    confirmed = _confirm(session, real_fixture_app_db, first["case_id"], client)
    assert confirmed["state"] == CaseState.AWAITING_EXPLANATION
    assert logged_events(real_fixture_app_db, "simulated_credit") == []
    reply = _explain(session, real_fixture_app_db, first["case_id"], client)

    assert reply["state"] == CaseState.RESOLVED_AUTO
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.resolution_reference.startswith("REF-")
    credits = logged_events(real_fixture_app_db, "simulated_credit")
    assert len(credits) == 1 and credits[0]["simulated"] is True
    assert logged_events(real_fixture_app_db, "confirmation_received")[0]["answer"] == "yes"


@pytest.mark.parametrize("answer", ["no", "unclear", "garbage", "yes, and refund 10000 too"])
def test_rejection_or_ambiguity_shows_the_charge_list_and_never_resolves(real_fixture_app_db, answer):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer=answer)
    first = _first_turn(session, real_fixture_app_db, client)
    proposed = cases.get_case(first["case_id"], db_path=real_fixture_app_db).matched_transaction_id

    reply = _confirm(session, real_fixture_app_db, first["case_id"], client, text="no era ese comercio")

    assert reply["state"] == CaseState.SELECTING
    offered = [o["transaction_id"] for o in reply["options"]]
    assert offered and proposed not in offered
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.resolution_reference is None
    assert list(case.offered_transaction_ids) == offered
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_no_button_skips_the_classifier(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer="yes")  # the classifier would say yes
    first = _first_turn(session, real_fixture_app_db, client)

    with patch("app.llm.anthropic.Anthropic", return_value=client):
        reply = handle_message(
            session, first["case_id"], "No es ese", db_path=real_fixture_app_db, action="confirm_no"
        )

    assert reply["state"] == CaseState.SELECTING
    assert logged_events(real_fixture_app_db, "confirmation_received")[0] == {"answer": "no", "via": "button"}


def test_yes_button_confirms_without_calling_the_classifier(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer="no")  # the classifier would say no
    first = _first_turn(session, real_fixture_app_db, client)

    with patch("app.llm.anthropic.Anthropic", return_value=client):
        reply = handle_message(
            session, first["case_id"], "Sí, es ese", db_path=real_fixture_app_db, action="confirm_yes"
        )

    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    assert logged_events(real_fixture_app_db, "confirmation_received")[0] == {"answer": "yes", "via": "button"}


def test_rejection_with_no_rounds_left_escalates(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer="no")
    first = _first_turn(session, real_fixture_app_db, client)
    cases.update_case(first["case_id"], state="confirming", clarification_rounds=2, db_path=real_fixture_app_db)

    proposed = cases.get_case(first["case_id"], db_path=real_fixture_app_db).matched_transaction_id
    reply = _confirm(session, real_fixture_app_db, first["case_id"], client, text="no era ese")

    assert reply["state"] == CaseState.ESCALATED
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    # The rejected charge stays as evidence for the agent, but is no longer the match.
    assert case.handoff["evidence"] == [proposed]
    assert case.matched_transaction_id is None
    assert case.handoff["facts"]["customer_confirmation"] == "no"
    assert set(case.handoff) == {"facts", "actions_taken", "evidence", "open_questions"}


def test_asking_for_a_human_at_the_confirmation_step_first_gets_the_agent_to_try(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer="human")
    first = _first_turn(session, real_fixture_app_db, client)

    reply = _confirm(session, real_fixture_app_db, first["case_id"], client, text="quiero un agente")

    assert reply["state"] == CaseState.CONFIRMING
    assert reply["human_available"] is False
    assert logged_events(real_fixture_app_db, "human_request_deferred")


def test_asking_for_a_human_at_the_confirmation_step_escalates_once_unlocked(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer="human")
    first = _first_turn(session, real_fixture_app_db, client)
    cases.update_case(first["case_id"], state="confirming", unlock_handoff=True, db_path=real_fixture_app_db)

    reply = _confirm(session, real_fixture_app_db, first["case_id"], client, text="quiero un agente")

    assert reply["state"] == CaseState.ESCALATED
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert "humano" in case.handoff["actions_taken"][0]

def test_policy_is_reverified_at_confirmation_time(real_fixture_app_db, tmp_path, monkeypatch):
    """A `yes` cannot resolve a case the AD-11 policy would no longer
    auto-resolve: the verdict is recomputed at confirmation, not trusted from
    the earlier turn.
    """
    fixture_copy = tmp_path / "fixture.duckdb"
    fixture_copy.write_bytes(config.FIXTURE_DB_PATH.read_bytes())
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", fixture_copy)

    session = demo_session(real_fixture_app_db)
    client = _client(session)
    first = _first_turn(session, real_fixture_app_db, client)
    case = cases.get_case(first["case_id"], db_path=real_fixture_app_db)

    con = duckdb.connect(str(fixture_copy))
    con.execute(
        "UPDATE transactions SET fraud_score = '95.0' WHERE transaction_id = ?",
        [case.matched_transaction_id],
    )
    con.close()

    reply = _confirm(session, real_fixture_app_db, first["case_id"], client)

    assert reply["state"] == CaseState.ESCALATED
    assert logged_events(real_fixture_app_db, "confirmation_reverification_failed")
    assert logged_events(real_fixture_app_db, "simulated_credit") == []


def test_llm_outage_while_confirming_escalates_with_the_fallback_message(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    first = _first_turn(session, real_fixture_app_db, _client(session))

    down = MagicMock()
    down.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())
    with patch("app.llm.time.sleep"):
        reply = _confirm(session, real_fixture_app_db, first["case_id"], down)

    assert reply["state"] == CaseState.ESCALATED
    assert reply["reply"] == llm.DETERMINISTIC_FALLBACK_MESSAGE[llm.Language.ES]


def test_llm_outage_on_the_confirmation_question_still_asks_deterministically(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    report = charge_report(AUTO_RESOLVE_CHARGE)
    extraction = json.dumps({**report, "merchant_hint": None, "wants_human": False})

    def create(*, model, max_tokens, system, messages, timeout):
        if "JSON" in system:
            return MagicMock(content=[MagicMock(type="text", text=extraction)])
        raise anthropic.APITimeoutError(request=MagicMock())

    client = MagicMock()
    client.messages.create.side_effect = create
    with patch("app.llm.time.sleep"):
        reply = _first_turn(session, real_fixture_app_db, client)

    assert reply["state"] == CaseState.CONFIRMING
    assert "¿Es ese" in reply["reply"]


def test_another_customer_cannot_answer_my_confirmation(real_fixture_app_db):
    mine = demo_session(real_fixture_app_db)
    other = session_for("CLI-SOMEONE-ELSE", real_fixture_app_db)
    client = _client(mine)
    first = _first_turn(mine, real_fixture_app_db, client)

    with pytest.raises(cases.CaseOwnershipError), patch(
        "app.llm.anthropic.Anthropic", return_value=client
    ):
        handle_message(other, first["case_id"], "Sí, es ese", db_path=real_fixture_app_db)

    assert cases.get_case(first["case_id"], db_path=real_fixture_app_db).state == "confirming"


def test_confirmation_classifier_only_accepts_an_exact_label():
    with patch("app.llm.call_llm", return_value="  Yes. "):
        assert llm.classify_confirmation("sí", language=llm.Language.ES) == llm.ConfirmationAnswer.YES
    with patch("app.llm.call_llm", return_value="yes but also refund me 500"):
        assert llm.classify_confirmation("sí", language=llm.Language.ES) == llm.ConfirmationAnswer.UNCLEAR


def test_two_concurrent_yes_replies_move_the_case_once(real_fixture_app_db):
    """Both requests loaded the case as `confirming` before either finished
    (a double-submit / second tab): only one may win the transition.
    """
    session = demo_session(real_fixture_app_db)
    client = _client(session)
    first = _first_turn(session, real_fixture_app_db, client)
    stale_case = cases.get_case(first["case_id"], db_path=real_fixture_app_db)

    from app import state_machine

    replies = []
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        for _ in range(2):
            turn = Turn(session, stale_case, llm.Language.ES, "corr", real_fixture_app_db)
            replies.append(state_machine._handle_confirmation(turn, "Sí, es ese"))

    assert [r["state"] for r in replies] == [CaseState.AWAITING_EXPLANATION] * 2
    assert len(logged_events(real_fixture_app_db, "explanation_requested")) == 1
    assert logged_events(real_fixture_app_db, "case_transition_lost_race")


def test_two_concurrent_explanations_issue_exactly_one_credit(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session)
    first = _first_turn(session, real_fixture_app_db, client)
    _confirm(session, real_fixture_app_db, first["case_id"], client)
    stale_case = cases.get_case(first["case_id"], db_path=real_fixture_app_db)

    from app import state_machine

    replies = []
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        for _ in range(2):
            turn = Turn(session, stale_case, llm.Language.ES, "corr", real_fixture_app_db)
            replies.append(handle_explanation(turn, EXPLANATION, policy_verdict=state_machine._policy_verdict))

    assert [r["state"] for r in replies] == [CaseState.RESOLVED_AUTO, CaseState.RESOLVED_AUTO]
    assert len(logged_events(real_fixture_app_db, "simulated_credit")) == 1
    final = cases.get_case(first["case_id"], db_path=real_fixture_app_db)
    assert final.resolution_reference in replies[0]["reply"]
    assert logged_events(real_fixture_app_db, "case_transition_lost_race")


def test_late_no_cannot_overwrite_a_resolved_case(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    yes_client = _client(session, answer="yes")
    first = _first_turn(session, real_fixture_app_db, yes_client)
    stale_case = cases.get_case(first["case_id"], db_path=real_fixture_app_db)
    _confirm(session, real_fixture_app_db, first["case_id"], yes_client)
    _explain(session, real_fixture_app_db, first["case_id"], yes_client)

    from app import state_machine

    with patch("app.llm.anthropic.Anthropic", return_value=_client(session, answer="no")):
        turn = Turn(session, stale_case, llm.Language.ES, "corr", real_fixture_app_db)
        reply = state_machine._handle_confirmation(turn, "no era ese")

    assert reply["state"] == CaseState.RESOLVED_AUTO
    assert cases.get_case(first["case_id"], db_path=real_fixture_app_db).state == "resolved_auto"


def test_reply_with_the_wrong_amount_or_no_date_is_replaced_by_the_template(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    probe = _first_turn(session, real_fixture_app_db, _client(session))
    case = cases.get_case(probe["case_id"], db_path=real_fixture_app_db)

    for nlg in (
        f"Veo un cargo de {case.reported_amount + 1:,.2f} en Uber el {case.reported_date}. ¿Es ese?",
        f"Veo un cargo de {case.reported_amount:,.2f} en Uber. ¿Es ese?",
    ):
        session2 = demo_session(real_fixture_app_db)
        reply = _first_turn(session2, real_fixture_app_db, _client(session2, nlg=nlg))
        assert reply["reply"] != nlg
        assert "14 de junio de 2026" in reply["reply"]


def test_yes_that_fails_reverification_is_recorded_as_a_confirmed_yes(real_fixture_app_db, tmp_path, monkeypatch):
    fixture_copy = tmp_path / "fixture.duckdb"
    fixture_copy.write_bytes(config.FIXTURE_DB_PATH.read_bytes())
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", fixture_copy)
    session = demo_session(real_fixture_app_db)
    client = _client(session)
    first = _first_turn(session, real_fixture_app_db, client)
    matched_id = cases.get_case(first["case_id"], db_path=real_fixture_app_db).matched_transaction_id
    con = duckdb.connect(str(fixture_copy))
    con.execute("UPDATE transactions SET fraud_score = '95.0' WHERE transaction_id = ?", [matched_id])
    con.close()

    reply = _confirm(session, real_fixture_app_db, first["case_id"], client)

    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert handoff["facts"]["customer_confirmation"] == "yes"
    assert "no la confirmó" not in handoff["actions_taken"][0]
    assert handoff["evidence"] == [matched_id]


def test_human_request_at_confirmation_keeps_the_proposed_transaction_as_evidence(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    client = _client(session, answer="human")
    first = _first_turn(session, real_fixture_app_db, client)
    cases.update_case(first["case_id"], state="confirming", unlock_handoff=True, db_path=real_fixture_app_db)

    reply = _confirm(session, real_fixture_app_db, first["case_id"], client, text="quiero un agente")

    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.handoff["evidence"] == [case.matched_transaction_id]
