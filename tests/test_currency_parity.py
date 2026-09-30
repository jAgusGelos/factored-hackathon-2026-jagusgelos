"""Friction #8: the same report takes the same path in ES and PT (plan.md
AD-1). With a bare "pesos" the real model guessed COP in Spanish and MXN in
Portuguese, and the charge search filters by currency, so the Portuguese
report missed the charge. A currency only counts when the customer named it
(`llm.stated_currency`); otherwise the search uses the profile's currency and
the case records none.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app import cases, llm
from app.case_model import CaseState
from app.llm import Language
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    charge_extraction,
    demo_session,
    logged_events,
    mock_anthropic_client,
    mocked_turn,
    requires_real_fixture,
)

REPORT = {
    Language.ES: "No reconozco un cargo de 38.500 pesos del 14 de junio",
    Language.PT: "Não reconheço uma cobrança de 38.500 pesos do dia 14 de junho",
}
# What the real model extracted from REPORT (2026-09-30, 3 of 3 runs each).
GUESSED_CURRENCY = {Language.ES: "COP", Language.PT: "MXN"}


@pytest.mark.parametrize(
    ("text", "extracted", "kept"),
    [
        ("38.500 pesos del 14 de junio", "COP", None),
        ("38.500 pesos do dia 14 de junho", "MXN", None),
        ("un cargo de $38.500", "COP", None),
        ("38.500 pesos colombianos", "COP", "COP"),
        ("38.500 Pesos Mexicanos", "MXN", "MXN"),
        ("un peso argentino", "ARS", "ARS"),
        ("20 dólares", "USD", "USD"),
        ("20 dolares", "USD", "USD"),
        ("USD 20", "USD", "USD"),
        ("cobrança de US$ 20", "USD", "USD"),
        ("cop 38.500", "COP", "COP"),
        ("38.500 pesos colombianos", "MXN", None),  # names a different currency
        ("20 dólares", "EUR", None),  # outside the extraction's enum
        ("20 dólares", None, None),
        ("20 dólares", 7, None),
    ],
)
def test_a_currency_is_kept_only_when_the_customer_named_it(text, extracted, kept):
    assert llm.stated_currency(text, extracted) == kept


@pytest.mark.parametrize("language", [Language.ES, Language.PT])
def test_extraction_drops_the_currency_the_model_guessed_for_bare_pesos(language):
    payload = {**charge_extraction(AUTO_RESOLVE_CHARGE), "currency": GUESSED_CURRENCY[language]}
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(payload)):
        extraction = llm.extract_entities(REPORT[language], language=language, today="2026-06-18")
    assert extraction.currency is None
    assert extraction.amount == payload["amount"] and extraction.date == payload["date"]


def test_both_extraction_prompts_say_bare_pesos_is_no_currency():
    assert '"pesos" o "$" sin país no es una moneda: use null' in llm._EXTRACTION_SYSTEM_PROMPT[Language.ES]
    assert '"pesos" ou "$" sem país não é uma moeda: use null' in llm._EXTRACTION_SYSTEM_PROMPT[Language.PT]


@requires_real_fixture
@pytest.mark.parametrize("language", [Language.ES, Language.PT])
def test_the_same_report_proposes_the_same_charge_in_both_languages(real_fixture_app_db, language):
    session = demo_session(real_fixture_app_db)
    extraction = {**charge_extraction(AUTO_RESOLVE_CHARGE), "currency": GUESSED_CURRENCY[language]}
    reply = mocked_turn(session, real_fixture_app_db, REPORT[language], language=language, extraction=extraction)
    assert reply["state"] == CaseState.CONFIRMING
    case = cases.get_case(reply["case_id"], db_path=real_fixture_app_db)
    assert case.matched_transaction_id == AUTO_RESOLVE_CHARGE
    # The profile's currency found the charge; the case does not claim the customer said it.
    assert case.reported_currency is None
    assert logged_events(real_fixture_app_db, "case_confirming")[0]["currency"] == "COP"


@requires_real_fixture
def test_a_named_currency_is_recorded_as_reported(real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    extraction = charge_extraction(AUTO_RESOLVE_CHARGE)  # the charge's own COP
    reply = mocked_turn(
        session, real_fixture_app_db, "No reconozco un cargo de 38.500 pesos colombianos del 14 de junio",
        extraction=extraction,
    )
    assert reply["state"] == CaseState.CONFIRMING
    assert cases.get_case(reply["case_id"], db_path=real_fixture_app_db).reported_currency == "COP"


@requires_real_fixture
@pytest.mark.parametrize("language", [Language.ES, Language.PT])
def test_a_request_for_a_person_without_details_records_no_currency(real_fixture_app_db, language):
    session = demo_session(real_fixture_app_db)
    ask = {Language.ES: "Quiero hablar con una persona", Language.PT: "Quero falar com uma pessoa"}[language]
    human = charge_extraction(wants_human=True)
    first = mocked_turn(session, real_fixture_app_db, ask, language=language, extraction=human)
    reply = mocked_turn(session, real_fixture_app_db, ask, first["case_id"], language=language, extraction=human)
    assert reply["state"] == CaseState.ESCALATED
    handoff = cases.get_case(reply["case_id"], db_path=real_fixture_app_db).handoff
    assert "currency" not in str(handoff)
