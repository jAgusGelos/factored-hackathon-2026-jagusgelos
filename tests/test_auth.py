import json
from datetime import UTC, datetime

import pytest

from app import auth, config, db


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    demo_users_path = tmp_path / "demo_users.json"
    demo_users_path.write_text(
        json.dumps(
            {
                "maria.gonzalez": {"password": "demo-pass-1", "customer_id": "CLI-TESTAAAA0001"},
            }
        )
    )
    monkeypatch.setattr(config, "DEMO_USERS_PATH", demo_users_path)

    db_path = tmp_path / "app.db"
    db.init_db(db_path)

    return demo_users_path, db_path


def test_verify_credentials_valid(tmp_env):
    _, _ = tmp_env
    customer_id = auth.verify_credentials("maria.gonzalez", "demo-pass-1")
    assert customer_id == "CLI-TESTAAAA0001"


def test_verify_credentials_invalid_password(tmp_env):
    assert auth.verify_credentials("maria.gonzalez", "wrong-password") is None


def test_verify_credentials_unknown_user(tmp_env):
    assert auth.verify_credentials("nobody", "whatever") is None


def test_login_mints_session_and_stores_hash_not_raw_token(tmp_env):
    _, db_path = tmp_env
    token, expires_at = auth.create_session("CLI-TESTAAAA0001", db_path=db_path)

    assert isinstance(token, str) and len(token) > 20
    assert expires_at > datetime.now(UTC)

    con = db.get_connection(db_path)
    try:
        rows = con.execute("SELECT token_hash, customer_id FROM sessions").fetchall()
    finally:
        con.close()

    assert len(rows) == 1
    assert rows[0]["customer_id"] == "CLI-TESTAAAA0001"
    # The raw token must never be what's persisted.
    assert rows[0]["token_hash"] != token
    assert len(rows[0]["token_hash"]) == 64  # sha256 hex digest


def test_get_session_valid_token(tmp_env):
    _, db_path = tmp_env
    token, _ = auth.create_session("CLI-TESTAAAA0001", db_path=db_path)

    session = auth.get_session(token, db_path=db_path)

    assert session is not None
    assert session.customer_id == "CLI-TESTAAAA0001"


def test_get_session_unknown_token_returns_none(tmp_env):
    _, db_path = tmp_env
    assert auth.get_session("not-a-real-token", db_path=db_path) is None


def test_get_session_expired_token_returns_none(tmp_env):
    _, db_path = tmp_env
    token, _ = auth.create_session("CLI-TESTAAAA0001", db_path=db_path, ttl_hours=-1)

    assert auth.get_session(token, db_path=db_path) is None


def test_invalidate_session_removes_it(tmp_env):
    _, db_path = tmp_env
    token, _ = auth.create_session("CLI-TESTAAAA0001", db_path=db_path)
    assert auth.get_session(token, db_path=db_path) is not None

    auth.invalidate_session(token, db_path=db_path)

    assert auth.get_session(token, db_path=db_path) is None
