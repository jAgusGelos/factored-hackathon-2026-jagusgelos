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


class DuplicateCreditError(Exception):
    """The transaction already has a resolved (credited) case for this customer."""


# Columns a transition may explicitly reset to NULL (every other column is
# only ever overwritten by a non-NULL value, see update_case).
CLEARABLE_FIELDS = frozenset({"matched_transaction_id"})


@dataclass
class Case:
    case_id: str
    customer_id: str
    language: str
    state: str
    reported_amount: float | None
    reported_currency: str | None
    reported_date: str | None
    matched_transaction_id: str | None
    clarification_rounds: int
    resolution_reference: str | None
    handoff: dict | None
    turn_count: int = 0
    reported_merchant: str | None = None
    # The charges last shown to the customer to pick from; a selection is only
    # ever accepted if it is one of these (and it is re-checked as their own).
    offered_transaction_ids: tuple[str, ...] = ()


def _row_to_case(row: sqlite3.Row) -> Case:
    return Case(
        case_id=row["case_id"],
        customer_id=row["customer_id"],
        language=row["language"],
        state=row["state"],
        reported_amount=row["reported_amount"],
        reported_currency=row["reported_currency"],
        reported_date=row["reported_date"],
        matched_transaction_id=row["matched_transaction_id"],
        clarification_rounds=row["clarification_rounds"],
        resolution_reference=row["resolution_reference"],
        handoff=json.loads(row["handoff_json"]) if row["handoff_json"] else None,
        turn_count=row["turn_count"],
        reported_merchant=row["reported_merchant"],
        offered_transaction_ids=tuple(json.loads(row["offered_transaction_ids"] or "[]")),
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
        reported_amount=None, reported_currency=None, reported_date=None, matched_transaction_id=None,
        clarification_rounds=0, resolution_reference=None, handoff=None,
    )


def update_case(
    case_id: str,
    *,
    state: str,
    reported_amount: float | None = None,
    reported_currency: str | None = None,
    reported_date: str | None = None,
    reported_merchant: str | None = None,
    matched_transaction_id: str | None = None,
    resolution_reference: str | None = None,
    handoff: dict | None = None,
    offered_transaction_ids: tuple[str, ...] | None = None,
    clarification_rounds: int | None = None,
    add_clarification_round: bool = False,
    clear_fields: tuple[str, ...] = (),
    expected_states: tuple[str, ...] | None = None,
    expected_offered_transaction_ids: tuple[str, ...] | None = None,
    expected_matched_transaction_id: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """A compare-and-set: returns False (and writes nothing) when the case no
    longer matches `expected_states` / `expected_offered_transaction_ids` /
    `expected_matched_transaction_id`, so
    two concurrent requests on the same case cannot both win a transition.
    None arguments leave a column as it is; `clear_fields` resets one to NULL.
    Raises DuplicateCreditError if the write would credit an already-credited
    transaction a second time.
    """
    unknown = set(clear_fields) - CLEARABLE_FIELDS
    if unknown:
        raise ValueError(f"Not clearable: {sorted(unknown)}")
    matched_sql = "NULL" if "matched_transaction_id" in clear_fields else "COALESCE(?, matched_transaction_id)"
    matched_params = [] if "matched_transaction_id" in clear_fields else [matched_transaction_id]

    guards = ""
    guard_params: list[str] = []
    if expected_states is not None:
        guards += f" AND state IN ({', '.join('?' for _ in expected_states)})"
        guard_params += list(expected_states)
    if expected_offered_transaction_ids is not None:
        guards += " AND offered_transaction_ids = ?"
        guard_params.append(json.dumps(list(expected_offered_transaction_ids)))
    if expected_matched_transaction_id is not None:
        guards += " AND matched_transaction_id = ?"
        guard_params.append(expected_matched_transaction_id)

    try:
        with db.app_connection(db_path) as con:
            cursor = con.execute(
                f"""
                UPDATE cases SET
                    state = ?,
                    reported_amount = COALESCE(?, reported_amount),
                    reported_currency = COALESCE(?, reported_currency),
                    reported_date = COALESCE(?, reported_date),
                    reported_merchant = COALESCE(?, reported_merchant),
                    matched_transaction_id = {matched_sql},
                    resolution_reference = COALESCE(?, resolution_reference),
                    handoff_json = COALESCE(?, handoff_json),
                    offered_transaction_ids = COALESCE(?, offered_transaction_ids),
                    clarification_rounds = COALESCE(?, clarification_rounds) + ?,
                    updated_at = ?
                WHERE case_id = ?{guards}
                """,
                [
                    state, reported_amount, reported_currency, reported_date, reported_merchant,
                    *matched_params, resolution_reference,
                    json.dumps(handoff, ensure_ascii=False) if handoff is not None else None,
                    json.dumps(list(offered_transaction_ids)) if offered_transaction_ids is not None else None,
                    clarification_rounds, 1 if add_clarification_round else 0,
                    datetime.now(UTC).isoformat(), case_id, *guard_params,
                ],
            )
            con.commit()
    except sqlite3.IntegrityError as exc:
        raise DuplicateCreditError(f"Case {case_id}: transaction already credited") from exc
    return cursor.rowcount == 1


def credited_case_for_transaction(
    customer_id: str, transaction_id: str, *, db_path: Path | None = None
) -> str | None:
    """The case that already credited this transaction for this customer, if any."""
    with db.app_connection(db_path) as con:
        row = con.execute(
            "SELECT case_id FROM cases WHERE customer_id = ? AND matched_transaction_id = ? "
            "AND state = 'resolved_auto' LIMIT 1",
            [customer_id, transaction_id],
        ).fetchone()
    return row["case_id"] if row else None


def increment_turn_count(case_id: str, *, db_path: Path | None = None) -> int:
    with db.app_connection(db_path) as con:
        row = con.execute(
            "UPDATE cases SET turn_count = turn_count + 1 WHERE case_id = ? RETURNING turn_count",
            [case_id],
        ).fetchone()
        con.commit()
    return row["turn_count"]


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
