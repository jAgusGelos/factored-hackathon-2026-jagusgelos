"""Case + message persistence (SQLite `cases`/`messages`/`events` tables).

Deliberately returns plain data (a `Case` dataclass with `state: str`, not
`app.case_model.CaseState`) to avoid a circular import — the conversation
modules convert at their boundary. This module never imports `state_machine`.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import db
from app.policy import (
    CREDIT_WINDOW_DAYS,
    MAX_AUTO_CREDIT_TOTAL_USD,
    MAX_UNRECOGNIZED_AUTO_CREDITS,
    DisputeReason,
)


class CaseOwnershipError(Exception):
    pass


class DuplicateCreditError(Exception):
    """The transaction, or the duplicate pair it belongs to (its credit key),
    already has a resolved (credited) case for this customer.
    """


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
    # A request for a person escalates at once: set by a first request the
    # agent deferred (plan.md AD-8), or silently after details the agent still
    # could not resolve (see state_machine._unlocks_handoff).
    handoff_unlocked: bool = False
    dispute_reason: str | None = None
    # The customer's explanation so far (their own words, kept in the app db
    # only; handoffs carry the model's neutral summary, never this text).
    explanation_text: str | None = None
    explanation_attempts: int = 0
    # The automatic credit this case granted, if any (AD-13 exposure limits).
    credit_key: str | None = None
    # Why the case went to a person (`case_model.EscalationReason`), written in
    # the same update that moves it to `escalated`; NULL on cases escalated
    # before the column existed.
    escalation_reason: str | None = None
    # The charges last shown to the customer to pick from; a selection is only
    # ever accepted if it is one of these (and it is re-checked as their own).
    offered_transaction_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreditHistory:
    """The automatic credits THIS system granted a customer in a window."""

    unrecognized_count: int
    total_usd: float


@dataclass(frozen=True)
class CreditGrant:
    """An automatic credit claimed together with the `resolved_auto`
    transition. The AD-13 limits are re-checked INSIDE the same UPDATE, so two
    concurrent cases of one customer cannot both slip under them.
    """

    key: str
    reason: DisputeReason
    amount_usd: float


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
        handoff_unlocked=bool(row["handoff_unlocked"]),
        dispute_reason=row["dispute_reason"],
        explanation_text=row["explanation_text"],
        explanation_attempts=row["explanation_attempts"],
        credit_key=row["credit_key"],
        escalation_reason=row["escalation_reason"],
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
    unlock_handoff: bool = False,
    dispute_reason: DisputeReason | None = None,
    append_explanation: str | None = None,
    add_explanation_attempt: bool = False,
    clear_fields: tuple[str, ...] = (),
    expected_states: tuple[str, ...] | None = None,
    expected_offered_transaction_ids: tuple[str, ...] | None = None,
    expected_matched_transaction_id: str | None = None,
    credit: CreditGrant | None = None,
    escalation_reason: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """A compare-and-set: returns False (and writes nothing) when the case no
    longer matches `expected_states` / `expected_offered_transaction_ids` /
    `expected_matched_transaction_id`, so
    two concurrent requests on the same case cannot both win a transition.
    None arguments leave a column as it is; `clear_fields` resets one to NULL.
    Raises DuplicateCreditError if the write would credit an already-credited
    transaction (or duplicate pair) a second time. With `credit`, the write
    also returns False when the customer's credit limits would be exceeded.
    """
    unknown = set(clear_fields) - CLEARABLE_FIELDS
    if unknown:
        raise ValueError(f"Not clearable: {sorted(unknown)}")
    matched_sql = "NULL" if "matched_transaction_id" in clear_fields else "COALESCE(?, matched_transaction_id)"
    matched_params = [] if "matched_transaction_id" in clear_fields else [matched_transaction_id]

    guards, guard_params = _expectation_guards(
        expected_states, expected_offered_transaction_ids, expected_matched_transaction_id,
    )
    now = datetime.now(UTC)
    credit_params: list[object] = [None, None, None]
    if credit is not None:
        credit_params = [credit.key, credit.amount_usd, now.isoformat()]
        limit_guards, limit_params = _credit_limit_guards(credit, now)
        guards += limit_guards
        guard_params += limit_params

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
                    handoff_unlocked = MAX(handoff_unlocked, ?),
                    dispute_reason = COALESCE(?, dispute_reason),
                    explanation_text = CASE WHEN ? IS NULL THEN explanation_text
                        ELSE COALESCE(explanation_text || char(10), '') || ? END,
                    explanation_attempts = explanation_attempts + ?,
                    credit_key = COALESCE(?, credit_key),
                    credited_amount_usd = COALESCE(?, credited_amount_usd),
                    credited_at = COALESCE(?, credited_at),
                    escalation_reason = COALESCE(?, escalation_reason),
                    updated_at = ?
                WHERE case_id = ?{guards}
                """,
                [
                    state, reported_amount, reported_currency, reported_date, reported_merchant,
                    *matched_params, resolution_reference,
                    json.dumps(handoff, ensure_ascii=False) if handoff is not None else None,
                    json.dumps(list(offered_transaction_ids)) if offered_transaction_ids is not None else None,
                    clarification_rounds, 1 if add_clarification_round else 0,
                    1 if unlock_handoff else 0,
                    dispute_reason, append_explanation, append_explanation, 1 if add_explanation_attempt else 0,
                    *credit_params, escalation_reason, now.isoformat(), case_id, *guard_params,
                ],
            )
            con.commit()
    except sqlite3.IntegrityError as exc:
        raise DuplicateCreditError(f"Case {case_id}: transaction already credited") from exc
    return cursor.rowcount == 1


def _expectation_guards(
    expected_states: tuple[str, ...] | None,
    expected_offered_transaction_ids: tuple[str, ...] | None,
    expected_matched_transaction_id: str | None,
) -> tuple[str, list[object]]:
    """The compare-and-set part of update_case's WHERE clause, and its parameters."""
    guards = ""
    params: list[object] = []
    if expected_states is not None:
        guards += f" AND state IN ({', '.join('?' for _ in expected_states)})"
        params += list(expected_states)
    if expected_offered_transaction_ids is not None:
        guards += " AND offered_transaction_ids = ?"
        params.append(json.dumps(list(expected_offered_transaction_ids)))
    if expected_matched_transaction_id is not None:
        guards += " AND matched_transaction_id = ?"
        params.append(expected_matched_transaction_id)
    return guards, params


def _credit_limit_guards(credit: CreditGrant, now: datetime) -> tuple[str, list[object]]:
    """The AD-13 exposure limits, checked inside the same UPDATE that claims the credit."""
    granted = _granted_since("cases.customer_id")
    since = _credit_window_start(now)
    guards = f" AND (SELECT {_CREDITED_USD} {granted}) + ? <= ?"
    params: list[object] = [MAX_AUTO_CREDIT_TOTAL_USD, since, credit.amount_usd, MAX_AUTO_CREDIT_TOTAL_USD]
    if credit.reason == DisputeReason.UNRECOGNIZED:
        guards += f" AND (SELECT {_UNRECOGNIZED_CREDITS} {granted}) < ?"
        params += [DisputeReason.UNRECOGNIZED, since, MAX_UNRECOGNIZED_AUTO_CREDITS]
    return guards, params


# The credits one customer was granted since a timestamp, for both the policy
# read (credit_history) and the guard inside the claiming UPDATE. A credit from
# before these columns existed has no amount or reason: it counts as a full-cap,
# unrecognized credit, and its updated_at stands in for credited_at.
_CREDITED_USD = "COALESCE(SUM(COALESCE(granted.credited_amount_usd, ?)), 0)"
_UNRECOGNIZED_CREDITS = "COALESCE(SUM(granted.dispute_reason IS NULL OR granted.dispute_reason = ?), 0)"


def _granted_since(owner_sql: str) -> str:
    return (
        f"FROM cases AS granted WHERE granted.customer_id = {owner_sql} "
        "AND granted.state = 'resolved_auto' AND COALESCE(granted.credited_at, granted.updated_at) >= ?"
    )


def _credit_window_start(now: datetime) -> str:
    return (now - timedelta(days=CREDIT_WINDOW_DAYS)).isoformat()


def credit_history(customer_id: str, *, db_path: Path | None = None) -> CreditHistory:
    """The automatic credits this system granted `customer_id` in the trailing
    CREDIT_WINDOW_DAYS (AD-13's exposure limits).
    """
    with db.app_connection(db_path) as con:
        row = con.execute(
            f"SELECT {_CREDITED_USD} AS total_usd, {_UNRECOGNIZED_CREDITS} AS unrecognized_count "
            f"{_granted_since('?')}",
            [MAX_AUTO_CREDIT_TOTAL_USD, DisputeReason.UNRECOGNIZED, customer_id, _credit_window_start(datetime.now(UTC))],
        ).fetchone()
    return CreditHistory(unrecognized_count=row["unrecognized_count"], total_usd=row["total_usd"])


def credited_case_for_key(customer_id: str, credit_key: str, *, db_path: Path | None = None) -> str | None:
    """The case that already used this credit key (e.g. a duplicate pair), if any."""
    with db.app_connection(db_path) as con:
        row = con.execute(
            "SELECT case_id FROM cases WHERE customer_id = ? AND credit_key = ? AND state = 'resolved_auto' LIMIT 1",
            [customer_id, credit_key],
        ).fetchone()
    return row["case_id"] if row else None


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


def explained_case_for_transaction(
    customer_id: str, transaction_id: str, *, exclude_case_id: str, db_path: Path | None = None,
) -> str | None:
    """Another case of this customer on this transaction where the customer's
    explanation was already assessed: one a person has after that assessment
    (`dispute_reason` is only persisted by then), or one still open that
    already spent an explanation attempt (asked for more detail; its reason is
    not stored on that path). Escalations for other causes (a request for a
    person, a service failure) have no reason and do not count, nor does an
    open case that only identified the charge.
    """
    with db.app_connection(db_path) as con:
        row = con.execute(
            "SELECT case_id FROM cases WHERE customer_id = ? AND matched_transaction_id = ? AND case_id != ? "
            "AND ((state = 'escalated' AND dispute_reason IS NOT NULL) "
            "OR (state NOT IN ('escalated', 'resolved_auto') AND explanation_attempts > 0)) "
            "ORDER BY created_at LIMIT 1",
            [customer_id, transaction_id, exclude_case_id],
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
