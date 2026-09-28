"""Case + message persistence (SQLite `cases`/`messages`/`events` tables).

Deliberately returns plain data (a `Case` dataclass with `state: str`, not
`app.state_machine.CaseState`) to avoid a circular import — `state_machine.py`
converts at its boundary. This module never imports `state_machine`.

Ownership: `get_case()` returns a case regardless of who asks and exists for
tests/back-office reads only. Every customer-facing path must go through
`get_case_for_session()`, which raises `CaseOwnershipError` on a
cross-customer access attempt — enforced by `tests/test_state_machine.py`'s
cross-customer-access test.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app import db


class CaseOwnershipError(Exception):
    pass


@dataclass
class Case:
    case_id: str
    customer_id: str
    language: str
    state: str
    reported_amount: float | None
    reported_date: str | None
    matched_transaction_id: str | None
    clarification_rounds: int
    resolution_reference: str | None
    handoff: dict | None


def _row_to_case(row: sqlite3.Row) -> Case:
    return Case(
        case_id=row["case_id"],
        customer_id=row["customer_id"],
        language=row["language"],
        state=row["state"],
        reported_amount=row["reported_amount"],
        reported_date=row["reported_date"],
        matched_transaction_id=row["matched_transaction_id"],
        clarification_rounds=row["clarification_rounds"],
        resolution_reference=row["resolution_reference"],
        handoff=json.loads(row["handoff_json"]) if row["handoff_json"] else None,
    )


def get_case(case_id: str, *, db_path: Path | None = None) -> Case | None:
    with db.app_connection(db_path) as con:
        row = con.execute("SELECT * FROM cases WHERE case_id = ?", [case_id]).fetchone()
    return _row_to_case(row) if row else None


def get_case_for_session(
    case_id: str, customer_id: str, *, db_path: Path | None = None
) -> Case | None:
    """Returns the case only if it belongs to `customer_id` — raises
    `CaseOwnershipError` if it exists but belongs to someone else (never
    silently returns another customer's case data).
    """
    case = get_case(case_id, db_path=db_path)
    if case is None:
        return None
    if case.customer_id != customer_id:
        raise CaseOwnershipError(f"Case {case_id} does not belong to this session")
    return case


def create_case(customer_id: str, language: str, *, db_path: Path | None = None) -> Case:
    case_id = f"CASE-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(UTC).isoformat()
    with db.app_connection(db_path) as con:
        con.execute(
            "INSERT INTO cases (case_id, customer_id, language, state, clarification_rounds, "
            "created_at, updated_at) VALUES (?, ?, ?, 'awaiting_report', 0, ?, ?)",
            [case_id, customer_id, language, now, now],
        )
        con.commit()
    return Case(
        case_id=case_id, customer_id=customer_id, language=language, state="awaiting_report",
        reported_amount=None, reported_date=None, matched_transaction_id=None,
        clarification_rounds=0, resolution_reference=None, handoff=None,
    )


def update_case(
    case_id: str,
    *,
    state: str,
    reported_amount: float | None = None,
    reported_date: str | None = None,
    matched_transaction_id: str | None = None,
    clarification_rounds: int | None = None,
    resolution_reference: str | None = None,
    handoff: dict | None = None,
    db_path: Path | None = None,
) -> None:
    with db.app_connection(db_path) as con:
        con.execute(
            """
            UPDATE cases SET
                state = ?,
                reported_amount = COALESCE(?, reported_amount),
                reported_date = COALESCE(?, reported_date),
                matched_transaction_id = COALESCE(?, matched_transaction_id),
                clarification_rounds = COALESCE(?, clarification_rounds),
                resolution_reference = COALESCE(?, resolution_reference),
                handoff_json = COALESCE(?, handoff_json),
                updated_at = ?
            WHERE case_id = ?
            """,
            [
                state, reported_amount, reported_date, matched_transaction_id,
                clarification_rounds, resolution_reference,
                json.dumps(handoff, ensure_ascii=False) if handoff is not None else None,
                datetime.now(UTC).isoformat(), case_id,
            ],
        )
        con.commit()


def log_message(
    case_id: str, role: str, content: str, *, kind: str = "text", db_path: Path | None = None
) -> None:
    with db.app_connection(db_path) as con:
        con.execute(
            "INSERT INTO messages (case_id, role, kind, content, created_at) VALUES (?, ?, ?, ?, ?)",
            [case_id, role, kind, content, datetime.now(UTC).isoformat()],
        )
        con.commit()


def log_event(
    correlation_id: str,
    case_id: str | None,
    event_type: str,
    payload: dict,
    *,
    db_path: Path | None = None,
) -> None:
    """Structured audit-trail entry (plan.md's Observability NFR): every
    state transition, tool call, and LLM call. `payload` must already be
    redacted per AD-5 by the caller — this function does not filter it.
    """
    with db.app_connection(db_path) as con:
        con.execute(
            "INSERT INTO events (correlation_id, case_id, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [correlation_id, case_id, event_type, json.dumps(payload, ensure_ascii=False),
             datetime.now(UTC).isoformat()],
        )
        con.commit()
