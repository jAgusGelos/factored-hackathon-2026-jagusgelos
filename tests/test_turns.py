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

from app import cases, replies, turns
from app.case_model import CaseState
from app.state_machine import handle_message
from tests.support import (
    AUTO_RESOLVE_CHARGE,
    DEMO_USERNAME,
    REAL_DEMO_USERS_PATH,
    charge_extraction,
    demo_session,
    logged_events,
    mock_anthropic_client,
    requires_real_fixture,
    session_for,
)

pytestmark = requires_real_fixture

OPENING = "Tengo un cargo que no reconozco"


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


def _rows(app_db, sql: str, params=()) -> list[tuple]:
    con = sqlite3.connect(str(app_db))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _message_count(app_db) -> int:
    return _rows(app_db, "SELECT COUNT(*) FROM messages")[0][0]


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
    assert _rows(real_fixture_app_db, "SELECT COUNT(*) FROM cases")[0][0] == 1
    assert logged_events(real_fixture_app_db, "turn_replayed") == [{"turn_id": turn_id}]


def test_a_retried_button_tap_is_not_applied_twice(api, real_fixture_app_db):
    case_id = api.post("/api/chat", json={"message": OPENING})
    case_id = case_id.json()["case_id"]
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
    _age(real_fixture_app_db, turn_id, turns.PENDING_TIMEOUT_SECONDS)
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
    assert _rows(real_fixture_app_db, "SELECT reply_json FROM chat_turns WHERE turn_id = ?", [turn_id]) == [(None,)]


def test_an_abandoned_first_message_without_a_case_changes_nothing(api, real_fixture_app_db):
    session = demo_session(real_fixture_app_db)
    turn_id = str(uuid.uuid4())
    turns.claim(session.customer_id, turn_id, db_path=real_fixture_app_db)
    _age(real_fixture_app_db, turn_id, turns.PENDING_TIMEOUT_SECONDS + 60)

    body = api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id}).json()

    assert body["case_id"] is None
    assert body["state"] == CaseState.AWAITING_REPORT
    assert _rows(real_fixture_app_db, "SELECT COUNT(*) FROM cases")[0][0] == 0


@pytest.mark.parametrize("turn_id", ["not-a-uuid", str(uuid.uuid4()).upper(), "", str(uuid.uuid4()) + "0"])
def test_a_malformed_turn_id_is_rejected(api, turn_id):
    assert api.post("/api/chat", json={"message": OPENING, "turn_id": turn_id}).status_code == 422


def test_without_a_turn_id_nothing_is_recorded_and_every_send_is_a_turn(api, real_fixture_app_db):
    first = api.post("/api/chat", json={"message": OPENING}).json()
    second = api.post("/api/chat", json={"message": OPENING}).json()
    assert first["case_id"] != second["case_id"]
    assert _rows(real_fixture_app_db, "SELECT COUNT(*) FROM chat_turns")[0][0] == 0


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
    assert _rows(real_fixture_app_db, "SELECT COUNT(*) FROM chat_turns")[0][0] == 0
