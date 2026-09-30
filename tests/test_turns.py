"""AD-4: idempotent chat turns. A client retry with the same `turn_id` never
applies a turn twice: a finished turn is replayed byte for byte (no model call,
no new messages), a running one answers 409, and one whose request died is
never run again. The key space is per customer.

Runs against the real fixture through the HTTP endpoint, with the Anthropic
client mocked.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import cases, config, db, replies, turns
from app.case_model import CaseState
from app.state_machine import handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    DEMO_USERNAME,
    OPENING,
    REAL_DEMO_USERS_PATH,
    app_db_rows,
    charge_extraction,
    demo_session,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
    session_for,
)

pytestmark = requires_real_fixture



@pytest.fixture()
def model(real_fixture_app_db):
    client = mock_anthropic_client(charge_extraction(AUTO_RESOLVE_CHARGE))
    with patch("app.llm.anthropic.Anthropic", return_value=client):
        yield client


@pytest.fixture()
def api(real_fixture_app_db, model):
    from app.main import app

    password = json.loads(REAL_DEMO_USERS_PATH.read_text())[DEMO_USERNAME]["password"]
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/auth/login", json={"username": DEMO_USERNAME, "password": password}).status_code == 200
        yield client


def _message_count(app_db) -> int:
    return app_db_rows(app_db, "SELECT COUNT(*) FROM messages")[0][0]


def _age(app_db, turn_id: str, seconds: int) -> None:
    old = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    con = sqlite3.connect(str(app_db))
    try:
        con.execute("UPDATE chat_turns SET created_at = ? WHERE turn_id = ?", [old, turn_id])
        con.commit()
    finally:
        con.close()


def test_a_retried_turn_gets_the_same_reply_without_running_again(api, model, real_fixture_app_db):
    turn_id = str(uuid.uuid4())
    first = api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id})
    calls, messages = model.messages.create.call_count, _message_count(real_fixture_app_db)

    retry = api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id})

    assert first.status_code == retry.status_code == 200
    assert retry.content == first.content
    assert first.json()["state"] == CaseState.CONFIRMING
    assert model.messages.create.call_count == calls
    assert _message_count(real_fixture_app_db) == messages
    # The first message created the case: the replay names that same case.
    assert app_db_rows(real_fixture_app_db, "SELECT COUNT(*) FROM cases")[0][0] == 1
    assert logged_events(real_fixture_app_db, "turn_replayed") == [{"turn_id": turn_id}]


def test_a_retried_button_tap_is_not_applied_twice(api, real_fixture_app_db):
    case_id = api.post("/api/chat", json={"message": OPENING}).json()["case_id"]
    tap = {"case_id": case_id, "message": "Sí, es ese", "action": "confirm_yes", "turn_id": str(uuid.uuid4())}
    first = api.post("/api/chat", json=tap)
    retry = api.post("/api/chat", json=tap)

    assert first.json()["state"] == retry.json()["state"] == CaseState.AWAITING_EXPLANATION
    assert retry.content == first.content
    assert logged_events(real_fixture_app_db, "action_rejected") == []
    assert len(logged_events(real_fixture_app_db, "confirmation_received")) == 1


def test_a_turn_still_running_answers_409(api, model, real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    turns.claim(session.customer_id, turn_id, db_path=real_fixture_app_db)

    res = api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id})

    assert res.status_code == 409
    assert res.json() == {"detail": "turn_in_progress"}
    model.messages.create.assert_not_called()
    assert _message_count(real_fixture_app_db) == 0
    assert logged_events(real_fixture_app_db, "turn_in_flight") == [{"turn_id": turn_id}]


def test_an_abandoned_turn_reports_the_case_as_it_is_without_running_again(api, model, real_fixture_app_db):
    opened = api.post("/api/chat", json={"message": OPENING}).json()
    session = demo_session(real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    turns.claim(session.customer_id, turn_id, db_path=real_fixture_app_db)
    turns.attach_case(session.customer_id, turn_id, opened["case_id"], db_path=real_fixture_app_db)
    _age(real_fixture_app_db, turn_id, config.PENDING_TIMEOUT_SECONDS)
    calls, messages = model.messages.create.call_count, _message_count(real_fixture_app_db)

    res = api.post(
        "/api/chat",
        json={"case_id": opened["case_id"], "message": "Sí, es ese", "action": "confirm_yes", "turn_id": turn_id},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["case_id"] == opened["case_id"]
    assert body["state"] == CaseState.CONFIRMING
    assert body["reply"] == replies.CASE_MOVED_ON["es"]
    assert model.messages.create.call_count == calls
    assert _message_count(real_fixture_app_db) == messages
    assert cases.get_case(opened["case_id"], db_path=real_fixture_app_db).state == CaseState.CONFIRMING
    # Never reprocessed, and the row stays pending.
    assert app_db_rows(real_fixture_app_db, "SELECT reply_json FROM chat_turns WHERE turn_id = ?", [turn_id]) == [(None,)]


def test_an_abandoned_first_message_without_a_case_changes_nothing(api, real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    turns.claim(session.customer_id, turn_id, db_path=real_fixture_app_db)
    _age(real_fixture_app_db, turn_id, config.PENDING_TIMEOUT_SECONDS + 60)

    body = api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id}).json()

    assert body["case_id"] is None
    assert body["state"] == CaseState.AWAITING_REPORT
    assert app_db_rows(real_fixture_app_db, "SELECT COUNT(*) FROM cases")[0][0] == 0


@pytest.mark.parametrize("turn_id", ["not-a-uuid", str(uuid.uuid4()).upper(), "", str(uuid.uuid4()) + "0"])
def test_a_malformed_turn_id_is_rejected(api, turn_id):
    assert api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id}).status_code == 422


def test_without_a_turn_id_nothing_is_recorded_and_every_send_is_a_turn(api, real_fixture_app_db):
    first = api.post("/api/chat", json={"message": OPENING}).json()
    second = api.post("/api/chat", json={"message": OPENING}).json()
    assert first["case_id"] != second["case_id"]
    assert app_db_rows(real_fixture_app_db, "SELECT COUNT(*) FROM chat_turns")[0][0] == 0


def test_the_same_turn_id_under_another_customer_is_that_customers_own_turn(model, real_fixture_app_db):
    turn_id = str(uuid.uuid4())
    mine = handle_message(demo_session(real_fixture_app_db), None, OPENING, db_path=real_fixture_app_db,
                          turn_id=turn_id)
    other = session_for("CLI-SOMEONE-ELSE", real_fixture_app_db)
    theirs = handle_message(other, None, OPENING, db_path=real_fixture_app_db, turn_id=turn_id)

    assert theirs["customer_id"] == "CLI-SOMEONE-ELSE"
    assert theirs["case_id"] != mine["case_id"]
    assert logged_events(real_fixture_app_db, "turn_replayed") == []


def test_a_refused_turn_can_be_retried(model, real_fixture_app_db):
    """A case of another customer is refused before anything changes, so the
    claim is dropped instead of leaving the retry stuck on 409.
    """
    mine = handle_message(demo_session(real_fixture_app_db), None, OPENING, db_path=real_fixture_app_db)
    other = session_for("CLI-SOMEONE-ELSE", real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    for _ in range(2):
        with pytest.raises(cases.CaseOwnershipError):
            handle_message(other, mine["case_id"], "hola", db_path=real_fixture_app_db, turn_id=turn_id)
    assert app_db_rows(real_fixture_app_db, "SELECT COUNT(*) FROM chat_turns")[0][0] == 0


def test_a_turn_that_fails_is_answered_with_the_case_state_on_retry_not_409(api, model, real_fixture_app_db):
    """An unexpected error inside the turn marks it abandoned at once: the
    retry is not stuck on 409 until the pending timeout, and the turn is never
    run again.
    """
    opened = api.post("/api/chat", json={"message": OPENING}).json()
    session = demo_session(real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    tap = {"case_id": opened["case_id"], "message": "Sí, es ese", "action": "confirm_yes", "turn_id": turn_id}
    with patch("app.state_machine._route", side_effect=RuntimeError("boom")), pytest.raises(RuntimeError):
        handle_message(session, opened["case_id"], tap["message"], action="confirm_yes", turn_id=turn_id,
                       db_path=real_fixture_app_db)
    assert logged_events(real_fixture_app_db, "turn_failed") == [{"turn_id": turn_id}]
    calls, messages = model.messages.create.call_count, _message_count(real_fixture_app_db)

    res = api.post("/api/chat", json=tap)

    assert res.status_code == 200
    body = res.json()
    assert (body["case_id"], body["state"]) == (opened["case_id"], CaseState.CONFIRMING)
    assert body["reply"] == replies.CASE_MOVED_ON["es"]
    assert model.messages.create.call_count == calls
    assert _message_count(real_fixture_app_db) == messages
    assert logged_events(real_fixture_app_db, "confirmation_received") == []
    assert app_db_rows(real_fixture_app_db, "SELECT reply_json FROM chat_turns WHERE turn_id = ?", [turn_id]) == [(None,)]


def test_an_abandoned_turn_without_an_attached_case_answers_with_the_requests_case(api, real_fixture_app_db):
    """The turn died before it attached its case: the case the request names
    is still the one to report, not "no case".
    """
    opened = api.post("/api/chat", json={"message": OPENING}).json()
    session = demo_session(real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    turns.claim(session.customer_id, turn_id, db_path=real_fixture_app_db)
    _age(real_fixture_app_db, turn_id, config.PENDING_TIMEOUT_SECONDS)

    body = api.post(
        "/api/chat",
        json={"case_id": opened["case_id"], "message": "Sí, es ese", "action": "confirm_yes", "turn_id": turn_id},
    ).json()

    assert body["case_id"] == opened["case_id"]
    assert body["state"] == CaseState.CONFIRMING
    assert logged_events(real_fixture_app_db, "confirmation_received") == []


def test_an_abandoned_turn_never_answers_with_another_customers_case(model, real_fixture_app_db):
    mine = handle_message(demo_session(real_fixture_app_db), None, OPENING, db_path=real_fixture_app_db)
    other = session_for("CLI-SOMEONE-ELSE", real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    turns.claim(other.customer_id, turn_id, db_path=real_fixture_app_db)
    _age(real_fixture_app_db, turn_id, config.PENDING_TIMEOUT_SECONDS)

    body = handle_message(other, mine["case_id"], "hola", db_path=real_fixture_app_db, turn_id=turn_id)

    assert body["case_id"] is None
    assert body["customer_id"] == "CLI-SOMEONE-ELSE"
    assert body["state"] == CaseState.AWAITING_REPORT


def test_startup_drops_old_replies_but_keeps_a_tombstone_that_is_never_reprocessed(real_fixture_app_db):
    now = datetime.now(UTC)
    two_days_ago = (now - timedelta(days=2)).isoformat()
    long_ago = (now - db.TURN_ROW_RETENTION - timedelta(days=1)).isoformat()
    con = sqlite3.connect(str(real_fixture_app_db))
    try:
        con.executemany(
            "INSERT INTO chat_turns (customer_id, turn_id, reply_json, created_at, completed_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("C", "old", "{}", two_days_ago, two_days_ago),
                ("C", "recent", "{}", now.isoformat(), now.isoformat()),
                ("C", "pending", None, two_days_ago, None),
                ("C", "ancient", "{}", long_ago, long_ago),
            ],
        )
        con.commit()
    finally:
        con.close()

    db.init_db(real_fixture_app_db)

    rows = app_db_rows(
        real_fixture_app_db,
        "SELECT turn_id, reply_json IS NOT NULL, completed_at IS NOT NULL, failed_at IS NOT NULL "
        "FROM chat_turns ORDER BY turn_id",
    )
    assert rows == [("old", 0, 1, 0), ("pending", 0, 0, 0), ("recent", 1, 1, 0)]
    assert turns.claim("C", "old", db_path=real_fixture_app_db).status == turns.TurnStatus.EXPIRED


def test_a_turn_id_reused_after_its_reply_expired_is_not_run_again(api, model, real_fixture_app_db):
    turn_id = str(uuid.uuid4())
    first = api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id}).json()
    old = (datetime.now(UTC) - db.COMPLETED_TURN_RETENTION - timedelta(hours=1)).isoformat()
    con = sqlite3.connect(str(real_fixture_app_db))
    try:
        con.execute("UPDATE chat_turns SET completed_at = ? WHERE turn_id = ?", [old, turn_id])
        con.commit()
    finally:
        con.close()
    db.init_db(real_fixture_app_db)
    calls, messages = model.messages.create.call_count, _message_count(real_fixture_app_db)

    late = api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id, "case_id": first["case_id"]})

    assert late.status_code == 200
    assert late.json()["case_id"] == first["case_id"]
    assert late.json()["reply"] == replies.CASE_MOVED_ON["es"]
    assert model.messages.create.call_count == calls
    assert _message_count(real_fixture_app_db) == messages
    assert logged_events(real_fixture_app_db, "turn_expired")
    assert logged_events(real_fixture_app_db, "turn_abandoned") == []
