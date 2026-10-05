import json
import os
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from app import config
from tests.support import mock_anthropic_client

TEST_CUSTOMER_ID = "CLI-TESTAAAA0001"
_EMPTY_EXTRACTION = {
    "amount": None, "currency": None, "date": None, "merchant_hint": None, "wants_human": False,
}


def _build_fixture_db(db_path):
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE customers (customer_id VARCHAR, segment VARCHAR, credit_score VARCHAR, country VARCHAR, customer_status VARCHAR)")
    con.execute("INSERT INTO customers VALUES (?, 'Plus', '700', 'México', 'Active')", [TEST_CUSTOMER_ID])
    con.execute(
        "CREATE TABLE transactions (transaction_id VARCHAR, transaction_date VARCHAR, "
        "customer_id VARCHAR, amount VARCHAR, currency VARCHAR, amount_usd VARCHAR, "
        "fraud_score VARCHAR, transaction_status VARCHAR, merchant_name VARCHAR, "
        "merchant_category VARCHAR, channel VARCHAR, _is_synthetic VARCHAR)"
    )
    con.execute("CREATE TABLE complaints (complaint_id VARCHAR, customer_id VARCHAR, category VARCHAR, creation_date VARCHAR)")
    con.execute("ALTER TABLE transactions ADD COLUMN transaction_type VARCHAR DEFAULT 'Purchase'")
    con.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    demo_users_path = tmp_path / "demo_users.json"
    demo_users_path.write_text(
        json.dumps({"maria.gonzalez": {"password": "demo-pass-1", "customer_id": TEST_CUSTOMER_ID}})
    )
    monkeypatch.setattr(config, "DEMO_USERS_PATH", demo_users_path)
    monkeypatch.setattr(config, "APP_DB_PATH", tmp_path / "app.db")

    fixture_path = tmp_path / "fixture.duckdb"
    _build_fixture_db(fixture_path)
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", fixture_path)

    from app.main import app  # imported after config patch so module-level state (if any) is fresh

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(
        _EMPTY_EXTRACTION, "Gracias por tu mensaje, estamos procesando tu caso."
    )):
        # base_url must be https:// — the session cookie is Secure (AD-4), and a
        # Secure cookie is never sent back by an http:// client, test or real.
        with TestClient(app, base_url="https://testserver") as c:
            yield c


def test_login_then_chat_end_to_end(client):
    login_res = client.post(
        "/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["customer_id"] == TEST_CUSTOMER_ID
    assert "session_token" in login_res.cookies

    chat_res = client.post("/api/chat", json={"message": "Hola, tengo un cargo que no reconozco"})
    assert chat_res.status_code == 200
    body = chat_res.json()
    assert body["customer_id"] == TEST_CUSTOMER_ID
    assert body["reply"]


def test_login_invalid_credentials_rejected(client):
    res = client.post("/auth/login", json={"username": "maria.gonzalez", "password": "wrong"})
    assert res.status_code == 401


def test_chat_without_session_rejected(client):
    res = client.post("/api/chat", json={"message": "hola"})
    assert res.status_code == 401


def test_me_without_session_rejected(client):
    res = client.get("/api/me")
    assert res.status_code == 401


def test_app_serves_with_aws_env_unset(client, monkeypatch):
    """Proves AD-2's zero-runtime-AWS-dependency claim: the app must serve a
    working login -> chat exchange even with no AWS credentials in the
    process environment at all.
    """
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "DATA_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    assert "AWS_ACCESS_KEY_ID" not in os.environ

    login_res = client.post(
        "/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"}
    )
    assert login_res.status_code == 200

    chat_res = client.post("/api/chat", json={"message": "hola"})
    assert chat_res.status_code == 200


def test_index_page_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"LATAM Bank" in res.content


def test_chat_rejects_an_unsupported_language_with_422_not_500(client):
    client.post("/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"})
    res = client.post("/api/chat", json={"message": "hola", "language": "fr"})
    assert res.status_code == 422


def test_get_case_returns_structured_status_for_the_owning_session(client):
    client.post("/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"})
    chat_res = client.post("/api/chat", json={"message": "Tengo un cargo que no reconozco"})
    case_id = chat_res.json()["case_id"]

    res = client.get(f"/api/case/{case_id}")

    assert res.status_code == 200
    body = res.json()
    assert body["case_id"] == case_id
    assert body["state"] == chat_res.json()["state"]
    assert "handoff" in body


def test_get_case_without_session_rejected(client):
    res = client.get("/api/case/CASE-DOES-NOT-EXIST")
    assert res.status_code == 401


def test_get_case_unknown_id_returns_404(client):
    client.post("/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"})
    res = client.get("/api/case/CASE-DOES-NOT-EXIST")
    assert res.status_code == 404


def test_get_case_belonging_to_another_customer_returns_403(client):
    from app import cases

    client.post("/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"})
    other_case = cases.create_case("CLI-OTHER", "es", db_path=config.APP_DB_PATH)

    res = client.get(f"/api/case/{other_case.case_id}")

    assert res.status_code == 403


def _login(client):
    client.post("/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"})


def test_me_serves_the_welcome_message_in_both_languages(client):
    _login(client)
    welcome = client.get("/api/me").json()["welcome"]
    assert set(welcome) == {"es", "pt"}
    assert "LATAM Bank" in welcome["es"] and "LATAM Bank" in welcome["pt"]


def test_customer_data_is_never_cached_but_static_files_revalidate(client):
    _login(client)
    assert client.get("/api/me").headers["cache-control"] == "no-store"
    assert client.get("/js/chat.js").headers["cache-control"] == "no-cache"


def test_get_case_describes_the_matched_charge(client):
    from app import cases

    _login(client)
    case_id = client.post("/api/chat", json={"message": "hola"}).json()["case_id"]
    con = duckdb.connect(str(config.FIXTURE_DB_PATH))
    con.execute(
        "INSERT INTO transactions (transaction_id, transaction_date, customer_id, amount, currency, "
        "amount_usd, fraud_score, transaction_status, merchant_name, merchant_category, channel, _is_synthetic) "
        "VALUES ('TRX-9', '2026-06-01', ?, '50.0', 'USD', '50.0', '5', 'Approved', 'Cine', 'Entertainment', 'App', 'false')",
        [TEST_CUSTOMER_ID],
    )
    con.close()
    cases.update_case(case_id, state="confirming", matched_transaction_id="TRX-9")

    charge = client.get(f"/api/case/{case_id}").json()["matched_charge"]
    assert charge == {
        "transaction_id": "TRX-9", "date": "2026-06-01", "amount": 50.0, "currency": "USD",
        "merchant": "Cine", "category": "Entertainment",
    }


def test_get_case_never_sends_the_policy_reasons_to_the_customer_session(client):
    from app import cases

    _login(client)
    case_id = client.post("/api/chat", json={"message": "hola"}).json()["case_id"]
    reasons = ["fraud_score=91.0 above the 30.0 threshold", "amount_usd=900.0 exceeds the 500.0 auto-resolve cap"]
    stored = {
        "request_summary": "El cliente disputa un cargo.", "verified_facts": {}, "customer_reported": {},
        "policy_reasons": reasons, "actions_taken": ["x"], "evidence": [], "open_questions": ["y"],
    }
    cases.update_case(case_id, state="escalated", handoff=stored)

    body = client.get(f"/api/case/{case_id}").json()
    assert "policy_reasons" not in body["handoff"]
    assert body["handoff"]["policy_reason_count"] == len(reasons)
    text = json.dumps(body)
    assert "threshold" not in text and "fraud_score" not in text and "auto-resolve cap" not in text
    assert cases.get_case(case_id).handoff["policy_reasons"] == reasons


def test_get_case_never_sends_the_open_questions_of_a_handoff_stored_before_policy_reasons(client):
    from app import cases

    _login(client)
    case_id = client.post("/api/chat", json={"message": "hola"}).json()["case_id"]
    stored = {
        "facts": {"reported_merchant": "Tienda Online Global"}, "actions_taken": ["x"], "evidence": [],
        "open_questions": ["fraud_score=91.0 above the 30.0 threshold"],
    }
    cases.update_case(case_id, state="escalated", handoff=stored)

    handoff = client.get(f"/api/case/{case_id}").json()["handoff"]
    assert handoff == {"facts": stored["facts"], "actions_taken": ["x"], "evidence": []}


def test_get_case_never_shows_the_customer_why_the_card_was_blocked(client):
    """AD-14: the block is an action the customer may see; the fraud signals
    behind it are policy reasons, which the customer's session only counts.
    """
    from app import cases, handoffs
    from app.policy import FraudSignal, ProtectiveAction, ProtectiveDecision

    _login(client)
    case_id = client.post("/api/chat", json={"message": "hola"}).json()["case_id"]
    stored = {
        "request_summary": "El cliente disputa un cargo.", "verified_facts": {}, "customer_reported": {},
        "policy_reasons": [], "actions_taken": ["x"], "evidence": [], "open_questions": [],
    }
    signals = (FraudSignal.HIGH_FRAUD_SCORE, FraudSignal.CARD_PRESENT_DENIED)
    blocked = handoffs.with_card_block(stored, ProtectiveDecision(ProtectiveAction.CARD_BLOCK, signals))
    cases.update_case(case_id, state="escalated", handoff=blocked)

    handoff = client.get(f"/api/case/{case_id}").json()["handoff"]
    assert handoffs.CARD_BLOCKED_ACTION in handoff["actions_taken"]
    assert handoff["policy_reason_count"] == 1
    text = json.dumps(handoff, ensure_ascii=False).lower()
    assert not any(word in text for word in ("fraude", "umbral", "puntaje", "fraud", "score", "tarjeta presente"))


def test_get_case_never_sends_the_fraud_score_or_the_model_estimate_to_the_customer_session(client):
    """AD-15: the advisor's verified facts carry the vendor score and the
    model's estimate; the customer's own session receives the charge record only.
    """
    from app import cases, handoffs
    from app.case_model import ReportedCharge
    from app.transactions import FraudRiskEstimate
    from tests.support import clean_txn

    _login(client)
    case_id = client.post("/api/chat", json={"message": "hola"}).json()["case_id"]
    estimate = FraudRiskEstimate(risk=0.9, threshold=0.0035, model_version="logistic_stacked-d7c46aeb")
    evaluation = handoffs.ineligible_match(
        ReportedCharge(amount=None, date=None, currency=None), clean_txn(fraud_score=91.0, fraud_risk=estimate), ("r",),
        how_identified=handoffs.ChargeIdentification.PICK,
    )
    cases.update_case(case_id, state="escalated", handoff=evaluation.handoff.to_dict())

    facts = client.get(f"/api/case/{case_id}").json()["handoff"]["verified_facts"]
    assert facts["transaction_id"] == "TRX-1" and not set(facts) & handoffs.INTERNAL_FACTS
    text = json.dumps(facts, ensure_ascii=False).lower()
    assert not any(word in text for word in ("fraud", "modelo", "umbral", "91.0", "logistic"))
    stored = cases.get_case(case_id).handoff["verified_facts"]
    assert handoffs.INTERNAL_FACTS <= set(stored)
