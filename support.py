"""Shared test/eval fixture helpers — real personas + a mocked Anthropic client.

No `pytest` dependency here deliberately: both `tests/` (via `tests/support.py`,
which adds the pytest-specific skip marker) and `eval/run_eval.py` (a
standalone script, never run under pytest) import from this module. `eval/`
must not depend on `tests/` — this is the neutral shared location instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import duckdb

from app.auth import Session, create_session, get_session, verify_credentials
from app.llm import ASSESSMENT_MARKER, CONFIRMATION_MARKER
from etl.build_fixture import (
    AUTO_RESOLVE_CHARGE_ID as AUTO_RESOLVE_CHARGE,
)
from etl.build_fixture import (
    DEMO_USERNAME,
)
from etl.build_fixture import (
    DUPLICATE_CHARGE_IDS as DUPLICATE_CHARGES,
)
from etl.build_fixture import (
    FRAUD_SCORE_CHARGE_ID as FRAUD_SCORE_CHARGE,
)
from etl.build_fixture import (
    OVER_LIMIT_CHARGE_ID as OVER_LIMIT_CHARGE,
)

REPO_ROOT = Path(__file__).resolve().parent
REAL_FIXTURE_PATH = REPO_ROOT / "data" / "fixture.duckdb"
REAL_DEMO_USERS_PATH = REPO_ROOT / "data" / "demo_users.json"

__all__ = [
    "AUTO_RESOLVE_CHARGE", "CONVINCING_ASSESSMENT", "DEMO_USERNAME", "EXPLANATION", "DUPLICATE_CHARGES", "FRAUD_SCORE_CHARGE", "OVER_LIMIT_CHARGE",
    "REAL_DEMO_USERS_PATH", "REAL_FIXTURE_PATH", "REPO_ROOT", "charge_extraction", "charge_report",
    "demo_session", "mock_anthropic_client", "session_for",
]


def mock_anthropic_client(
    extraction_payload: dict,
    nlg_text: str = "Respuesta generada.",
    *,
    confirmation_answer: str = "yes",
    assessment: dict | None = None,
    captured_prompts: list[str] | None = None,
    captured_completions: list[str] | None = None,
) -> MagicMock:
    """Answers the JSON-extraction system prompt with `extraction_payload`, the
    confirm-before-resolve classifier (AD-12) with `confirmation_answer`, and
    every other call with `nlg_text`; optionally records every system prompt
    and user message sent (privacy/language assertions) and every completion
    returned (eval/run_eval.py's cost estimate).
    """

    def create(*, model, max_tokens, system, messages, timeout):
        if captured_prompts is not None:
            captured_prompts.append(system)
            captured_prompts.extend(m["content"] for m in messages)
        response = MagicMock()
        if CONFIRMATION_MARKER in system:
            text = confirmation_answer
        elif ASSESSMENT_MARKER in system:
            text = json.dumps(assessment or CONVINCING_ASSESSMENT)
        elif "JSON" in system:
            text = json.dumps(extraction_payload)
        else:
            text = nlg_text
        if captured_completions is not None:
            captured_completions.append(text)
        response.content = [MagicMock(type="text", text=text)]
        return response

    client = MagicMock()
    client.messages.create.side_effect = create
    return client


# What the mocked model answers when asked to assess an explanation, unless a
# test passes its own `assessment`.
CONVINCING_ASSESSMENT = {
    "reason": "unrecognized", "specific": True, "consistent": True, "contradictions": [],
    "summary": "El cliente no reconoce el comercio y tiene la tarjeta consigo.",
}
EXPLANATION = "No uso Uber hace meses, tengo la tarjeta conmigo y ayer vi el cargo en la app del banco"


def demo_session(app_db: Path) -> Session:
    demo_users = json.loads(REAL_DEMO_USERS_PATH.read_text())
    customer_id = verify_credentials(DEMO_USERNAME, demo_users[DEMO_USERNAME]["password"])
    assert customer_id is not None
    return session_for(customer_id, app_db)


def session_for(customer_id: str, app_db: Path) -> Session:
    token, _ = create_session(customer_id, db_path=app_db)
    session = get_session(token, db_path=app_db)
    assert session is not None
    return session


def charge_report(transaction_id: str) -> dict:
    """What a customer disputing this exact charge would report."""
    con = duckdb.connect(str(REAL_FIXTURE_PATH), read_only=True)
    try:
        row = con.execute(
            "SELECT CAST(amount AS DOUBLE), currency, CAST(CAST(transaction_date AS TIMESTAMP) AS DATE) "
            "FROM transactions WHERE transaction_id = ?",
            [transaction_id],
        ).fetchone()
    finally:
        con.close()
    return {"amount": row[0], "currency": row[1], "date": row[2].isoformat()}


def charge_extraction(transaction_id: str | None = None, **overrides) -> dict:
    """The entity-extraction payload for a report of `transaction_id` (or an
    empty report when None), as the mocked model returns it.
    """
    base = charge_report(transaction_id) if transaction_id else {"amount": None, "currency": None, "date": None}
    return {**base, "merchant_hint": None, "wants_human": False, **overrides}
