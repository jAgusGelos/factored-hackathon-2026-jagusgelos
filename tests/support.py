from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest

from app.auth import Session, create_session, get_session, verify_credentials

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_FIXTURE_PATH = REPO_ROOT / "data" / "fixture.duckdb"
REAL_DEMO_USERS_PATH = REPO_ROOT / "data" / "demo_users.json"

requires_real_fixture = pytest.mark.skipif(
    not (REAL_FIXTURE_PATH.exists() and REAL_DEMO_USERS_PATH.exists()),
    reason="Requires the ETL fixture (run `python etl/extract.py && python etl/build_fixture.py` first)",
)


def mock_anthropic_client(
    extraction_payload: dict,
    nlg_text: str = "Respuesta generada.",
    *,
    captured_prompts: list[str] | None = None,
) -> MagicMock:
    """Answers the JSON-extraction system prompt with `extraction_payload` and
    every other call with `nlg_text`; optionally records every system prompt
    and user message sent, for privacy/language assertions.
    """

    def create(*, model, max_tokens, system, messages, timeout):
        if captured_prompts is not None:
            captured_prompts.append(system)
            captured_prompts.extend(m["content"] for m in messages)
        response = MagicMock()
        text = json.dumps(extraction_payload) if "JSON" in system else nlg_text
        response.content = [MagicMock(type="text", text=text)]
        return response

    client = MagicMock()
    client.messages.create.side_effect = create
    return client


def persona_session(username: str, app_db: Path) -> Session:
    demo_users = json.loads(REAL_DEMO_USERS_PATH.read_text())
    customer_id = verify_credentials(username, demo_users[username]["password"])
    assert customer_id is not None
    token, _ = create_session(customer_id, db_path=app_db)
    session = get_session(token, db_path=app_db)
    assert session is not None
    return session


def persona_complaint(customer_id: str) -> dict:
    con = duckdb.connect(str(REAL_FIXTURE_PATH), read_only=True)
    try:
        row = con.execute(
            "SELECT claimed_amount, currency, CAST(creation_date AS DATE) "
            "FROM complaints WHERE customer_id = ?",
            [customer_id],
        ).fetchone()
    finally:
        con.close()
    return {"amount": float(row[0]), "currency": row[1], "date": row[2].isoformat()}
