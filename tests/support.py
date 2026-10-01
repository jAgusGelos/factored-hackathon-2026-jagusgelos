"""Test-only re-exports of `support.py` plus the pytest skip marker.

`support.py` (repo root) has no pytest dependency, since `eval/run_eval.py`
(a standalone script) imports from it too — this module adds the
pytest-specific bits on top, for `tests/` only.
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

import pytest

from app import llm, replies
from app.case_model import CaseState, CustomerAction, EscalationReason
from app.llm import Language
from app.policy import (
    ESCALATION_CONTACT_BUSINESS_DAYS,
    DisputeContext,
    DisputeReason,
    ExplanationAssessment,
)
from app.state_machine import handle_message
from app.transactions import TransactionCandidate
from support import (
    AUTO_RESOLVE_CHARGE,
    CARD_PRESENT_CHARGE,
    CONTRADICTED_ASSESSMENT,
    CONVINCING_ASSESSMENT,
    CURRENCY_PARITY_REPORT,
    DEMO_USERNAME,
    DUPLICATE_ASSESSMENT,
    DUPLICATE_CHARGES,
    EXPLANATION,
    FRAUD_SCORE_CHARGE,
    NOT_RECEIVED_ASSESSMENT,
    OPENING,
    OVER_LIMIT_CHARGE,
    REAL_DEMO_USERS_PATH,
    REAL_FIXTURE_PATH,
    REPO_ROOT,
    SECOND_ONLINE_CHARGE,
    STATEMENT,
    app_db_rows,
    charge_extraction,
    charge_report,
    demo_session,
    event_sequence,
    logged_events,
    mock_anthropic_client,
    session_for,
)

__all__ = [
    "REAL_DEMO_USERS_PATH",
    "REAL_FIXTURE_PATH",
    "REPO_ROOT",
    "AUTO_RESOLVE_CHARGE",
    "CURRENCY_PARITY_REPORT",
    "CARD_PRESENT_CHARGE",
    "CONTRADICTED_ASSESSMENT",
    "CONVINCING_ASSESSMENT",
    "DUPLICATE_ASSESSMENT",
    "EXPLANATION",
    "OPENING",
    "NOT_RECEIVED_ASSESSMENT",
    "SECOND_ONLINE_CHARGE",
    "STATEMENT",
    "app_db_rows",
    "clean_assessment",
    "clean_ctx",
    "clean_txn",
    "event_sequence",
    "logged_events",
    "DEMO_USERNAME",
    "DUPLICATE_CHARGES",
    "FRAUD_SCORE_CHARGE",
    "OVER_LIMIT_CHARGE",
    "charge_extraction",
    "charge_report",
    "demo_session",
    "mock_anthropic_client",
    "session_for",
    "requires_real_fixture",
    "STATIC",
    "chat_js_language_block",
    "assert_escalation_notice",
    "CONTACT_DEADLINE",
    "mocked_turn",
    "reach_confirming",
    "reach_explaining",
    "assert_asks_for_statement",
    "finish_statement",
]

STATIC = REPO_ROOT / "static"

HANDOFF_KEYS = {
    "request_summary", "verified_facts", "customer_reported", "policy_reasons", "actions_taken", "evidence",
    "open_questions",
}

requires_real_fixture = pytest.mark.skipif(
    not (REAL_FIXTURE_PATH.exists() and REAL_DEMO_USERS_PATH.exists()),
    reason="Requires the ETL fixture (run `python etl/extract.py && python etl/build_fixture.py` first)",
)


def chat_js_language_block(chat_js: str, lang: str) -> str:
    """The body of `STRINGS.<lang>` in chat.js, up to its closing `  },` at the same indent."""
    match = re.search(rf"^  {lang}: \{{\n(.*?)^  \}},?$", chat_js, re.MULTILINE | re.DOTALL)
    assert match, f"STRINGS.{lang} not found in chat.js"
    return match.group(1)


# How the escalation notice words its contact deadline, per language.
CONTACT_DEADLINE = {
    Language.ES: f"{ESCALATION_CONTACT_BUSINESS_DAYS} días hábiles",
    Language.PT: f"{ESCALATION_CONTACT_BUSINESS_DAYS} dias úteis",
}


def assert_escalation_notice(
    reply: dict, reason: EscalationReason, *, charge_named: bool, language: Language = Language.ES,
) -> None:
    """The reply escalated with the notice for `reason`: its `escalation`
    object and its text carry the same case number, reason and deadline, and
    the charge is named exactly when `charge_named`.
    """
    assert reply["state"] == CaseState.ESCALATED
    escalation = reply["escalation"]
    reason_text = replies.escalation_summary(reply["case_id"], reason, charge=None, language=language)["reason"]
    assert escalation["case_number"] == reply["case_id"]
    assert escalation["reason"] == reason_text
    assert escalation["contact_business_days"] == ESCALATION_CONTACT_BUSINESS_DAYS
    assert (escalation["charge"] is not None) == charge_named
    text = reply["reply"]
    assert reply["case_id"] in text and reason_text in text
    assert CONTACT_DEADLINE[language] in text
    assert (("El cargo es" if language == Language.ES else "A cobrança é") in text) == charge_named
    if charge_named:
        assert escalation["charge"]["merchant"] in text


def assert_asks_for_statement(reply: dict, language: Language = Language.ES) -> None:
    """The escalation is held: the reply asks for the customer's statement
    and nothing is handed off yet.
    """
    assert reply["state"] == CaseState.AWAITING_STATEMENT
    assert reply["reply"] == replies.ASK_FOR_STATEMENT[Language(language)]
    assert reply["escalation"] is None


def finish_statement(session, app_db, reply: dict, text: str = STATEMENT, *, language="es", **kwargs) -> dict:
    """The statement step after a reply that asked for it: `text` as the
    customer's account, and the reply that hands the case off.
    """
    assert_asks_for_statement(reply, language)
    return mocked_turn(session, app_db, text, reply["case_id"], language=language, **kwargs)


def mocked_turn(session, app_db, text, case_id=None, *, extraction=None, language="es", client=None, **kwargs):
    """One `handle_message` turn against `client`, or a mocked model that
    extracts `extraction` (`mock`: its other answers), with retries unslept.
    """
    client = client or mock_anthropic_client(extraction or charge_extraction(), **kwargs.pop("mock", {}))
    with patch("app.llm.anthropic.Anthropic", return_value=client), patch.object(llm.time, "sleep"):
        return handle_message(session, case_id, text, language=language, db_path=app_db, **kwargs)


def reach_confirming(session, app_db, language="es") -> str:
    """A new case proposing `AUTO_RESOLVE_CHARGE`, handoff still locked."""
    reply = mocked_turn(session, app_db, OPENING, language=language, extraction=charge_extraction(AUTO_RESOLVE_CHARGE))
    assert reply["state"] == CaseState.CONFIRMING
    return reply["case_id"]


def reach_explaining(session, app_db, language="es") -> str:
    """`reach_confirming`, then "yes": the case waits for the explanation."""
    case_id = reach_confirming(session, app_db, language)
    reply = mocked_turn(session, app_db, "Sí, es ese", case_id, language=language, action=CustomerAction.CONFIRM_YES)
    assert reply["state"] == CaseState.AWAITING_EXPLANATION
    return case_id


def clean_txn(**overrides) -> TransactionCandidate:
    """A card-not-present purchase every policy condition accepts."""
    base = dict(
        transaction_id="TRX-1", transaction_date=date(2026, 6, 9), amount=100.0, currency="USD",
        amount_usd=100.0, fraud_score=5.0, transaction_status="Approved", merchant_name="Comercio Demo",
        merchant_category="Retail", channel="App", is_synthetic=False, transaction_type="Purchase",
    )
    return TransactionCandidate(**{**base, **overrides})


def clean_ctx(**overrides) -> DisputeContext:
    """A context under which `clean_txn()` is creditable as unrecognized."""
    base = dict(
        reason=DisputeReason.UNRECOGNIZED, as_of=date(2026, 6, 18), customer_status="Active",
        prior_disputes_in_window=0, classifier_priority=None, other_charges_at_merchant=0,
        duplicate_twins=(), duplicate_pair_credited=False, recent_unrecognized_credits=0,
        recent_credited_usd=0.0,
    )
    return DisputeContext(**{**base, **overrides})



def clean_assessment(**overrides) -> ExplanationAssessment:
    """A specific, consistent `unrecognized` read that `evaluate_explanation` accepts."""
    base = dict(reason=DisputeReason.UNRECOGNIZED, specific=True, consistent=True, contradictions=(), summary="")
    return ExplanationAssessment(**{**base, **overrides})
