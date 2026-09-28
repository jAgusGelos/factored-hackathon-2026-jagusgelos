"""Brute-force protection, username-enumeration resistance and API-level
cross-account probes (production-hardening session, Milestone 7).

These reproduce the findings from live probing: 20 rapid wrong passwords used
to all return 401 in ~0.15s, and an unknown username returned ~4x faster than
a real one. Cross-account/no-cookie/guessed-id checks are promoted from the
live probe script into permanent regression tests.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import auth, cases, config, ratelimit
from tests.support import mock_anthropic_client
from tests.test_main import _EMPTY_EXTRACTION, _build_fixture_db

ALICE = "CLI-TESTAAAA0001"
BOB = "CLI-TESTBBBB0002"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    users = tmp_path / "demo_users.json"
    users.write_text(json.dumps({
        "alice": {"password": "alice-pass", "customer_id": ALICE},
        "bob": {"password": "bob-pass", "customer_id": BOB},
    }))
    monkeypatch.setattr(config, "DEMO_USERS_PATH", users)
    monkeypatch.setattr(config, "APP_DB_PATH", tmp_path / "app.db")
    fixture = tmp_path / "fixture.duckdb"
    _build_fixture_db(fixture)
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", fixture)

    from app.main import app

    with patch("app.llm.anthropic.Anthropic", return_value=mock_anthropic_client(_EMPTY_EXTRACTION)):
        with TestClient(app, base_url="https://testserver") as c:
            yield c


def _login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_rapid_wrong_passwords_are_throttled_not_plain_401(client):
    statuses = [_login(client, "alice", f"wrong-{i}").status_code for i in range(20)]

    assert statuses[: ratelimit.MAX_FAILURES_PER_USERNAME] == [401] * ratelimit.MAX_FAILURES_PER_USERNAME
    assert set(statuses[ratelimit.MAX_FAILURES_PER_USERNAME :]) == {429}


def test_throttle_response_carries_retry_after(client):
    for i in range(ratelimit.MAX_FAILURES_PER_USERNAME):
        _login(client, "alice", f"wrong-{i}")

    res = _login(client, "alice", "wrong-again")

    assert res.status_code == 429
    assert int(res.headers["Retry-After"]) >= 1


def test_correct_password_does_not_bypass_an_active_lockout(client):
    for i in range(ratelimit.MAX_FAILURES_PER_USERNAME):
        _login(client, "alice", f"wrong-{i}")

    res = _login(client, "alice", "alice-pass")

    assert res.status_code == 429
    assert "set-cookie" not in res.headers


def test_unknown_username_is_throttled_identically_to_a_real_one(client):
    real = [_login(client, "alice", "x").status_code for _ in range(8)]
    fake = [_login(client, "nobody-here", "x").status_code for _ in range(8)]

    assert real == fake  # no enumeration through throttle behavior


def test_lockout_expires_and_backoff_grows(client):
    base = 1_000_000.0
    with patch("app.ratelimit.time.time", return_value=base):
        for i in range(ratelimit.MAX_FAILURES_PER_USERNAME):
            _login(client, "alice", f"wrong-{i}")
        first_wait = int(_login(client, "alice", "x").headers["Retry-After"])

    # After the delay elapses one more attempt is allowed; failing it doubles the delay.
    later = base + first_wait + 1
    with patch("app.ratelimit.time.time", return_value=later):
        assert _login(client, "alice", "wrong-final").status_code == 401
        second_wait = int(_login(client, "alice", "x").headers["Retry-After"])
    assert second_wait > first_wait

    # Once the whole window has passed, the correct password works again.
    with patch("app.ratelimit.time.time", return_value=later + ratelimit.WINDOW_SECONDS + 1):
        assert _login(client, "alice", "alice-pass").status_code == 200


def test_successful_login_clears_the_username_counter(client):
    for i in range(ratelimit.MAX_FAILURES_PER_USERNAME - 1):
        _login(client, "alice", f"wrong-{i}")
    assert _login(client, "alice", "alice-pass").status_code == 200

    for i in range(ratelimit.MAX_FAILURES_PER_USERNAME - 1):
        assert _login(client, "alice", f"wrong-{i}").status_code == 401


def test_one_username_lockout_does_not_lock_other_accounts(client):
    for i in range(ratelimit.MAX_FAILURES_PER_USERNAME):
        _login(client, "alice", f"wrong-{i}")

    assert _login(client, "bob", "bob-pass").status_code == 200


def test_password_spraying_from_one_ip_hits_the_per_ip_limit(client):
    statuses = [_login(client, f"user-{i}", "Password1").status_code for i in range(ratelimit.MAX_FAILURES_PER_IP + 3)]

    assert statuses[: ratelimit.MAX_FAILURES_PER_IP] == [401] * ratelimit.MAX_FAILURES_PER_IP
    assert set(statuses[ratelimit.MAX_FAILURES_PER_IP :]) == {429}
    # Even a fresh, valid account is refused from the throttled IP until the delay elapses.
    assert _login(client, "bob", "bob-pass").status_code == 429


def test_non_ascii_password_is_a_plain_401_not_a_500(client):
    assert _login(client, "alice", "contraseña-ñandú-🔒").status_code == 401


def test_oversized_credentials_are_rejected_before_any_work(client):
    assert _login(client, "a" * 5000, "x").status_code == 422


def test_password_comparison_runs_unconditionally_even_for_unknown_users(client):
    """Structural timing-side-channel regression: a wall-clock assertion is
    flaky in CI, so assert the property that removes the channel instead —
    `compare_digest` is invoked exactly once on BOTH paths.
    """
    calls = []
    real = auth.secrets.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    with patch("app.auth.secrets.compare_digest", side_effect=spy):
        assert auth.verify_credentials("alice", "wrong") is None
        known_calls = len(calls)
        assert auth.verify_credentials("nobody", "wrong") is None
        unknown_calls = len(calls) - known_calls

    assert known_calls == unknown_calls == 1


def test_unknown_user_never_authenticates_even_with_the_dummy_secret(client):
    assert auth.verify_credentials("nobody", auth._DUMMY_PASSWORD) is None


def test_login_error_body_is_identical_for_wrong_password_and_unknown_user(client):
    wrong = _login(client, "alice", "nope")
    unknown = _login(client, "ghost", "nope")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid credentials"}


# --- Cross-account / session probes, promoted from the live probe script ---


def test_session_tokens_are_high_entropy_and_unique(tmp_path):
    from app import db

    db_path = tmp_path / "app.db"
    db.init_db(db_path)
    tokens = {auth.create_session(ALICE, db_path=db_path)[0] for _ in range(200)}

    assert len(tokens) == 200
    assert all(len(t) >= 43 for t in tokens)  # token_urlsafe(32) = 256 bits


def test_guessed_session_cookie_is_rejected(client):
    client.cookies.set("session_token", "A" * 43)

    assert client.get("/api/me").status_code == 401


def test_no_cookie_is_401(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/chat", json={"message": "hola"}).status_code == 401


def test_other_customers_case_is_403_and_leaks_nothing(client):
    bobs_case = cases.create_case(BOB, "es")
    assert _login(client, "alice", "alice-pass").status_code == 200

    read = client.get(f"/api/case/{bobs_case.case_id}")
    write = client.post("/api/chat", json={"case_id": bobs_case.case_id, "message": "Sí, es ese"})

    assert read.status_code == write.status_code == 403
    assert read.json() == {"detail": "Case does not belong to this session"}
    assert bobs_case.customer_id not in read.text + write.text


def test_nonexistent_case_id_is_404_indistinguishable_from_nothing_to_see(client):
    assert _login(client, "alice", "alice-pass").status_code == 200

    res = client.get("/api/case/CASE-DOESNOTEXIST")

    assert res.status_code == 404
    assert res.json() == {"detail": "Case not found"}


# --- Login screen: demo personas (Task 3) ---


def test_demo_personas_endpoint_lists_provisioned_accounts_for_autofill(client):
    res = client.get("/auth/demo-personas")

    assert res.status_code == 200
    assert {p["username"] for p in res.json()} == {"alice", "bob"}
    assert {p["password"] for p in res.json()} == {"alice-pass", "bob-pass"}


def test_demo_personas_endpoint_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "SHOW_DEMO_CREDENTIALS", False)

    assert client.get("/auth/demo-personas").json() == []


def test_concurrent_wrong_passwords_cannot_all_slip_past_the_throttle(client):
    """The count-then-record race: a burst of parallel attempts must not get
    more than the per-username budget of 401s.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(lambda i: _login(client, "alice", f"wrong-{i}").status_code, range(40)))

    assert statuses.count(401) <= ratelimit.MAX_FAILURES_PER_USERNAME
    assert statuses.count(429) >= 40 - ratelimit.MAX_FAILURES_PER_USERNAME


def test_password_digest_operands_have_equal_length_for_any_input():
    assert len(auth._digest("a")) == len(auth._digest("x" * 500)) == len(auth._digest(auth._DUMMY_PASSWORD))
