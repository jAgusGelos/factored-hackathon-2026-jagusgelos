"""Guard-function + confused-deputy signature tests (Task 2.2).

## Maintained function inventory (AD-3's confused-deputy boundary)

Every function that reads a SPECIFIC customer's data must take a `Session`
(never a bare `customer_id` a caller could supply). This is the full,
maintained inventory — adding a new such function to `app/transactions.py`
requires adding it here too, or this test's completeness assumption breaks
silently.

  - app.transactions.search_own_transactions
  - app.transactions.list_own_charges
  - app.transactions.get_own_transaction
  - app.transactions.get_customer_profile
  - app.transactions.get_case_history
  - app.transactions.count_prior_complaints
  - app.transactions.count_own_charges_at_merchant
  - app.transactions.find_own_duplicate_twins
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from app import cases, db
from app import transactions as txns_module
from app.auth import Session
from app.case_model import ReportedCharge
from app.policy import DisputeReason
from app.state_machine import CaseState, evaluate_case, handle_message
from app.transactions import (
    count_own_charges_at_merchant,
    count_prior_complaints,
    find_own_duplicate_twins,
    get_case_history,
    get_customer_profile,
    get_own_transaction,
    list_own_charges,
    search_own_transactions,
)

CUSTOMER_DATA_FUNCTIONS = (
    search_own_transactions,
    list_own_charges,
    get_own_transaction,
    get_customer_profile,
    get_case_history,
    count_prior_complaints,
    count_own_charges_at_merchant,
    find_own_duplicate_twins,
)


@pytest.mark.parametrize("fn", CUSTOMER_DATA_FUNCTIONS, ids=lambda fn: fn.__name__)
def test_customer_data_functions_never_accept_a_bare_customer_id(fn):
    params = inspect.signature(fn).parameters
    assert "customer_id" not in params, (
        f"{fn.__qualname__} accepts a caller-suppliable 'customer_id' parameter — "
        "this breaks AD-3's confused-deputy boundary. customer_id must come only "
        "from the verified Session."
    )


def test_customer_data_functions_all_require_a_session_parameter():
    for fn in CUSTOMER_DATA_FUNCTIONS:
        params = list(inspect.signature(fn).parameters)
        assert params[0] == "session", f"{fn.__qualname__}'s first parameter must be 'session'"


def test_inventory_is_not_accidentally_stale():
    """If someone adds a new customer-data-reading function to
    app/transactions.py without adding it to CUSTOMER_DATA_FUNCTIONS above,
    this is the tripwire: it fails loud instead of silently under-covering.
    """
    module_functions = [
        obj
        for name, obj in inspect.getmembers(txns_module, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == txns_module.__name__
    ]
    assert set(module_functions) == set(CUSTOMER_DATA_FUNCTIONS), (
        "app/transactions.py's public functions changed — update "
        "CUSTOMER_DATA_FUNCTIONS in this test to match."
    )


FIXTURE_COLUMNS = {
    "transactions": (
        "transaction_id", "transaction_date", "customer_id", "amount", "currency",
        "amount_usd", "fraud_score", "transaction_status", "merchant_name",
        "merchant_category", "channel", "_is_synthetic", "transaction_type",
    ),
    "complaints": ("complaint_id", "customer_id", "category", "creation_date"),
    "customers": ("customer_id", "segment", "credit_score", "country", "customer_status"),
}


@pytest.fixture()
def fixture_con(tmp_path, monkeypatch):
    db_path = tmp_path / "fixture.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        f"CREATE TABLE transactions ({', '.join(c + ' VARCHAR' for c in FIXTURE_COLUMNS['transactions'])})"
    )
    con.execute(
        f"CREATE TABLE complaints ({', '.join(c + ' VARCHAR' for c in FIXTURE_COLUMNS['complaints'])})"
    )
    con.execute(
        f"CREATE TABLE customers ({', '.join(c + ' VARCHAR' for c in FIXTURE_COLUMNS['customers'])})"
    )
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', '700', 'México', 'Active')")
    con.close()

    from app import config

    monkeypatch.setattr(config, "FIXTURE_DB_PATH", db_path)
    return db_path


def _insert_txn(db_path, **fields):
    defaults = dict(
        transaction_id="TRX-1", transaction_date="2026-06-09", customer_id="CLI-1",
        amount="100.0", currency="USD", amount_usd="100.0", fraud_score="5.0",
        transaction_status="Approved", merchant_name="Comercio", merchant_category="Retail",
        channel="App", _is_synthetic="false", transaction_type="Purchase",
    )
    defaults.update(fields)
    cols = FIXTURE_COLUMNS["transactions"]
    con = duckdb.connect(str(db_path))
    con.execute(
        f"INSERT INTO transactions ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [defaults[c] for c in cols],
    )
    con.close()


SESSION = Session(customer_id="CLI-1", expires_at=datetime.now(UTC) + timedelta(hours=1))


def test_confident_clean_match_resolves_auto(fixture_con):
    _insert_txn(fixture_con)
    evaluation = evaluate_case(
        SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
        currency="USD",
    )
    assert evaluation.state == CaseState.RESOLVED_AUTO
    assert evaluation.matched_transaction.transaction_id == "TRX-1"


def test_count_prior_complaints_matches_the_training_feature_semantics(fixture_con):
    """Live counterpart of etl/features.py's prior_complaint_count: any
    category, no trailing window, strictly before the anchor date, own
    customer only. Diverging from that would be train/serve skew.
    """
    con = duckdb.connect(str(fixture_con))
    con.execute(
        "INSERT INTO complaints VALUES "
        "('CMP-OLD-FEES', 'CLI-1', 'Fees', '2020-01-01'), "
        "('CMP-TXN', 'CLI-1', 'Transactions', '2026-06-01'), "
        "('CMP-SAME-DAY', 'CLI-1', 'Transactions', '2026-06-10'), "
        "('CMP-OTHER', 'CLI-OTHER', 'Transactions', '2026-06-01')"
    )
    con.close()

    assert count_prior_complaints(SESSION, date(2026, 6, 10)) == 2
    assert get_case_history(SESSION, "Transactions", date(2026, 6, 10), window_days=90) == 1


def test_confident_match_over_threshold_escalates_with_handoff(fixture_con):
    _insert_txn(fixture_con, amount_usd="500.0")
    evaluation = evaluate_case(
        SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
        currency="USD",
    )
    assert evaluation.state == CaseState.ESCALATED
    assert evaluation.handoff is not None
    assert "TRX-1" in evaluation.handoff.evidence
    assert any("amount_usd" in r for r in evaluation.handoff.open_questions)


def test_zero_matches_asks_the_customer_to_pick(fixture_con):
    evaluation = evaluate_case(
        SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
        currency="USD",
    )
    assert evaluation.state == CaseState.SELECTING
    assert evaluation.candidates == ()


def test_ambiguous_match_handoff_always_has_open_questions():
    from app import handoffs
    from app.case_model import ReportedCharge

    for candidates in ((),):
        evaluation = handoffs.ambiguous_match(ReportedCharge(100.0, date(2026, 6, 10), "USD"), candidates, 2)
        assert evaluation.state == CaseState.ESCALATED
        assert evaluation.handoff.open_questions


def test_multiple_matches_goes_to_clarifying(fixture_con):
    _insert_txn(fixture_con, transaction_id="TRX-1")
    _insert_txn(fixture_con, transaction_id="TRX-2", transaction_date="2026-06-11")
    evaluation = evaluate_case(
        SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
        currency="USD",
    )
    assert evaluation.state == CaseState.SELECTING
    assert len(evaluation.candidates) == 2


def test_customer_requested_human_escalates_immediately_even_with_a_clean_match(fixture_con):
    _insert_txn(fixture_con)
    evaluation = evaluate_case(
        SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
        currency="USD", customer_requested_human=True,
    )
    assert evaluation.state == CaseState.ESCALATED
    assert evaluation.handoff is not None


def test_escalated_case_never_has_empty_handoff_facts(fixture_con):
    """Plan.md's Always-rule: every escalated case produces a structured
    handoff — never a raw transcript dump.
    """
    _insert_txn(fixture_con, amount_usd="500.0")
    evaluation = evaluate_case(
        SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
        currency="USD",
    )
    assert evaluation.state == CaseState.ESCALATED
    assert evaluation.handoff.facts
    assert isinstance(evaluation.handoff.actions_taken, tuple)
    assert isinstance(evaluation.handoff.evidence, tuple)
    assert isinstance(evaluation.handoff.open_questions, tuple)


def test_resuming_another_customers_case_raises_ownership_error(tmp_path):
    app_db = tmp_path / "app.db"
    db.init_db(app_db)
    other_customers_case = cases.create_case("CLI-OTHER", "es", db_path=app_db)

    with pytest.raises(cases.CaseOwnershipError):
        handle_message(SESSION, other_customers_case.case_id, "hola", db_path=app_db)

    assert cases.get_case(other_customers_case.case_id, db_path=app_db).customer_id == "CLI-OTHER"


def test_customer_requested_human_without_amount_or_date_records_no_fabricated_facts():
    from app import handoffs
    from app.case_model import ReportedCharge

    evaluation = handoffs.human_request(ReportedCharge(amount=None, date=None, currency="MXN"))
    assert evaluation.state == CaseState.ESCALATED
    assert "reported_amount" not in evaluation.handoff.facts
    assert "reported_date" not in evaluation.handoff.facts


def test_fixture_lookup_failure_forces_escalation_with_fallback_message(tmp_path, monkeypatch):
    from unittest.mock import patch

    from app import llm, state_machine
    from tests.support import mock_anthropic_client

    def _broken_profile(_session):
        raise duckdb.IOException("fixture unavailable")

    monkeypatch.setattr(state_machine, "get_customer_profile", _broken_profile)
    app_db = tmp_path / "app.db"
    db.init_db(app_db)
    extraction = {"amount": 100.0, "currency": None, "date": "2026-06-10", "merchant_hint": None,
                  "wants_human": False}

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(SESSION, None, "Tengo un cargo que no reconozco", db_path=app_db)

    assert reply["state"] == CaseState.ESCALATED
    assert reply["reply"] == llm.DETERMINISTIC_FALLBACK_MESSAGE[llm.Language.ES]


def test_unknown_case_id_starts_a_new_case_and_logs_it(tmp_path):
    import sqlite3
    from unittest.mock import patch

    from tests.support import mock_anthropic_client

    app_db = tmp_path / "app.db"
    db.init_db(app_db)
    extraction = {"amount": None, "currency": None, "date": None, "merchant_hint": None, "wants_human": False}

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(SESSION, "CASE-DOES-NOT-EXIST", "hola", db_path=app_db)

    assert reply["case_id"] != "CASE-DOES-NOT-EXIST"
    assert cases.get_case(reply["case_id"], db_path=app_db) is not None

    con = sqlite3.connect(str(app_db))
    rows = con.execute(
        "SELECT payload_json FROM events WHERE event_type = 'unknown_case_id_new_case_started'"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert "CASE-DOES-NOT-EXIST" in rows[0][0]


def test_terminal_case_reply_uses_the_current_turns_language_not_the_stored_one(tmp_path):
    from unittest.mock import patch

    from tests.support import mock_anthropic_client

    app_db = tmp_path / "app.db"
    db.init_db(app_db)
    case = cases.create_case(SESSION.customer_id, "es", db_path=app_db)
    cases.update_case(case.case_id, state=CaseState.RESOLVED_AUTO, resolution_reference="REF-TEST", db_path=app_db)

    extraction = {"amount": None, "currency": None, "date": None, "merchant_hint": None, "wants_human": False}
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(SESSION, case.case_id, "oi", language="pt", db_path=app_db)

    assert "REF-TEST" in reply["reply"]
    assert "resolvido" in reply["reply"]  # Portuguese wording, not the Spanish "resuelto"


def test_a_closed_case_stays_closed_and_a_new_claim_is_a_new_case(tmp_path):
    """AD-1 (usability-s1): the server never reopens or auto-replaces a terminal
    case; the chat opens the next claim by sending case_id None."""
    import sqlite3
    from unittest.mock import patch

    from tests.support import mock_anthropic_client

    app_db = tmp_path / "app.db"
    db.init_db(app_db)
    case = cases.create_case(SESSION.customer_id, "es", db_path=app_db)
    cases.update_case(case.case_id, state=CaseState.RESOLVED_AUTO, resolution_reference="REF-TEST", db_path=app_db)
    before = cases.get_case(case.case_id, db_path=app_db)

    def case_count():
        con = sqlite3.connect(str(app_db))
        try:
            return con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        finally:
            con.close()

    extraction = {"amount": None, "currency": None, "date": None, "merchant_hint": None, "wants_human": False}
    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        on_closed = handle_message(SESSION, case.case_id, "No reconozco otra compra", language="es", db_path=app_db)
        assert on_closed["case_id"] == case.case_id
        assert on_closed["state"] == CaseState.RESOLVED_AUTO
        assert "REF-TEST" in on_closed["reply"]
        assert case_count() == 1

        new_claim = handle_message(SESSION, None, "No reconozco otra compra", language="es", db_path=app_db)

    assert new_claim["case_id"] != case.case_id
    assert case_count() == 2
    assert cases.get_case(case.case_id, db_path=app_db) == before


@pytest.mark.parametrize("reason", list(DisputeReason))
def test_a_stored_dispute_reason_reads_back_as_the_same_reason(tmp_path, reason):
    app_db = tmp_path / "app.db"
    db.init_db(app_db)
    case = cases.create_case("CUST-1", "es", db_path=app_db)
    assert cases.update_case(case.case_id, state=CaseState.SELECTING, dispute_reason=reason, db_path=app_db)

    stored = cases.get_case(case.case_id, db_path=app_db)
    assert ReportedCharge.from_case(stored, "USD").reason is reason
