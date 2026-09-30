"""Correlation IDs + structured logging (Task 5.1, plan.md's Observability NFR).

Proves the `events` audit-trail table is sufficient to reconstruct the same
structured handoff object the UI shows (`/api/case/{id}`'s `handoff` field) —
log and UI are sourced from the same data, not two independently-maintained
copies that could silently drift.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import duckdb
import pytest

from app import cases, config, db
from app.auth import Session
from app.state_machine import handle_message
from tests.support import mock_anthropic_client

SESSION = Session(customer_id="CLI-1", expires_at=datetime.now(UTC) + timedelta(hours=1))


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    app_db_path = tmp_path / "app.db"
    db.init_db(app_db_path)

    fixture_path = tmp_path / "fixture.duckdb"
    con = duckdb.connect(str(fixture_path))
    con.execute(
        "CREATE TABLE transactions (transaction_id VARCHAR, transaction_date VARCHAR, "
        "customer_id VARCHAR, amount VARCHAR, currency VARCHAR, amount_usd VARCHAR, "
        "fraud_score VARCHAR, transaction_status VARCHAR, merchant_name VARCHAR, "
        "merchant_category VARCHAR, channel VARCHAR, _is_synthetic VARCHAR)"
    )
    con.execute(
        "INSERT INTO transactions VALUES ('TRX-1', '2026-06-09', 'CLI-1', '500.0', 'USD', "
        "'500.0', '5.0', 'Approved', 'Comercio', 'Retail', 'App', 'false')"  # amount_usd > 200 -> ineligible
    )
    con.execute(
        "CREATE TABLE complaints (complaint_id VARCHAR, customer_id VARCHAR, category VARCHAR, creation_date VARCHAR)"
    )
    con.execute(
        "CREATE TABLE customers (customer_id VARCHAR, segment VARCHAR, credit_score VARCHAR, "
        "country VARCHAR, customer_status VARCHAR)"
    )
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', '700', 'México', 'Active')")
    con.execute("ALTER TABLE transactions ADD COLUMN transaction_type VARCHAR DEFAULT 'Purchase'")
    con.close()

    monkeypatch.setattr(config, "FIXTURE_DB_PATH", fixture_path)
    return app_db_path


def _events_for_case(app_db_path, case_id: str, event_type: str) -> list[dict]:
    con = sqlite3.connect(str(app_db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT correlation_id, payload_json FROM events WHERE case_id = ? AND event_type = ?",
        [case_id, event_type],
    ).fetchall()
    con.close()
    return [{"correlation_id": r["correlation_id"], "payload": json.loads(r["payload_json"])} for r in rows]


def test_escalation_log_event_reconstructs_the_same_handoff_the_case_record_shows(app_db):
    extraction = {"amount": 500.0, "currency": "USD", "date": "2026-06-10", "merchant_hint": None, "wants_human": False}

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(SESSION, None, "Tengo un cargo que no reconozco", db_path=app_db)

    assert reply["state"] == "escalated"

    case = cases.get_case(reply["case_id"], db_path=app_db)
    escalation_events = _events_for_case(app_db, reply["case_id"], "case_escalated")

    assert len(escalation_events) == 1
    logged_handoff = escalation_events[0]["payload"]

    # The exact same structured object — not an approximation, not a subset.
    assert logged_handoff == case.handoff


def test_every_event_for_a_conversation_shares_one_correlation_id_per_turn(app_db):
    extraction = {"amount": 500.0, "currency": "USD", "date": "2026-06-10", "merchant_hint": None, "wants_human": False}

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(SESSION, None, "Tengo un cargo que no reconozco", db_path=app_db)

    con = sqlite3.connect(str(app_db))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT DISTINCT correlation_id FROM events WHERE case_id = ?", [reply["case_id"]]
    ).fetchall()
    con.close()

    assert len(rows) == 1  # a single turn's events all share one correlation ID
    assert rows[0]["correlation_id"]


def test_case_evaluated_breadcrumb_and_case_escalated_detail_are_both_logged(app_db):
    """Both a lightweight breadcrumb (every transition) and the full
    structured detail (escalation-specific) are logged — verifies the NFR's
    "every state-machine transition... is logged" isn't satisfied by only
    the detailed event, which not every transition produces.
    """
    extraction = {"amount": 500.0, "currency": "USD", "date": "2026-06-10", "merchant_hint": None, "wants_human": False}

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(extraction)):
        reply = handle_message(SESSION, None, "Tengo un cargo que no reconozco", db_path=app_db)

    breadcrumbs = _events_for_case(app_db, reply["case_id"], "case_evaluated")
    details = _events_for_case(app_db, reply["case_id"], "case_escalated")

    assert len(breadcrumbs) == 1
    assert breadcrumbs[0]["payload"]["state"] == "escalated"
    assert len(details) == 1
