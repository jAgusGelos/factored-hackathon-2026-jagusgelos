import json
import os

import pytest
from fastapi.testclient import TestClient

from app import config


@pytest.fixture()
def client(tmp_path, monkeypatch):
    demo_users_path = tmp_path / "demo_users.json"
    demo_users_path.write_text(
        json.dumps({"maria.gonzalez": {"password": "demo-pass-1", "customer_id": "CLI-TESTAAAA0001"}})
    )
    monkeypatch.setattr(config, "DEMO_USERS_PATH", demo_users_path)
    monkeypatch.setattr(config, "APP_DB_PATH", tmp_path / "app.db")

    from app.main import app  # imported after config patch so module-level state (if any) is fresh

    # base_url must be https:// — the session cookie is Secure (AD-4), and a
    # Secure cookie is never sent back by an http:// client, test or real.
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_login_then_chat_end_to_end(client):
    login_res = client.post(
        "/auth/login", json={"username": "maria.gonzalez", "password": "demo-pass-1"}
    )
    assert login_res.status_code == 200
    assert login_res.json()["customer_id"] == "CLI-TESTAAAA0001"
    assert "session_token" in login_res.cookies

    chat_res = client.post("/api/chat", json={"message": "Hola, tengo un cargo que no reconozco"})
    assert chat_res.status_code == 200
    body = chat_res.json()
    assert body["customer_id"] == "CLI-TESTAAAA0001"
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
