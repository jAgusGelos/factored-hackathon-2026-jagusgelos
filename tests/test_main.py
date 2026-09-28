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
