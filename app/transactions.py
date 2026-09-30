"""Session-scoped customer-data reads (AD-3's confused-deputy boundary).

Every function here reads ONE specific customer's data, and every one of them
takes a `Session` (from `app/auth.py`) — never a bare `customer_id` a caller
could supply. This is a structural guarantee, not a convention: a caller
cannot pass in someone else's `customer_id` because there is no parameter to
put it in. `tests/test_state_machine.py` enforces this with a signature-
inspection test over the exact function inventory below — any new function
added to this module must be added to that inventory too.

Function inventory (kept in sync with the signature-inspection test):
  - search_own_transactions(session, reported_amount, reported_date)
  - list_own_charges(session)
  - get_own_transaction(session, transaction_id)
  - get_customer_profile(session)
  - get_case_history(session, category, before_date)
  - count_prior_complaints(session, before_date)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app import fixture_db
from app.auth import Session


@dataclass(frozen=True)
class TransactionCandidate:
    transaction_id: str
    transaction_date: date
    amount: float
    currency: str
    amount_usd: float | None
    fraud_score: float | None
    transaction_status: str
    merchant_name: str | None
    merchant_category: str | None
    channel: str | None
    is_synthetic: bool


@dataclass(frozen=True)
class CustomerProfile:
    customer_id: str
    segment: str | None
    credit_score: float | None
    country: str | None
    customer_status: str | None


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def _row_to_candidate(row: tuple) -> TransactionCandidate:
    return TransactionCandidate(
        transaction_id=row[0],
        transaction_date=_parse_date(row[1]),
        amount=row[2],
        currency=row[3],
        amount_usd=row[4],
        fraud_score=row[5],
        transaction_status=row[6],
        merchant_name=row[7],
        merchant_category=row[8],
        channel=row[9],
        is_synthetic=bool(row[10]),
    )


def search_own_transactions(
    session: Session,
    reported_amount: float,
    reported_date: date,
    *,
    amount_tolerance: float,
    date_tolerance_days: int,
    currency: str,
    db_path: Path | None = None,
) -> list[TransactionCandidate]:
    """Fuzzy-matches this session's OWN transactions against a customer-
    reported amount/date (AD-11's match condition). `customer_id` is never a
    parameter — it comes only from `session`.
    """
    con = fixture_db.get_connection(db_path)
    try:
        rows = con.execute(
            """
            SELECT transaction_id, CAST(transaction_date AS TIMESTAMP), CAST(amount AS DOUBLE),
                   currency, CAST(amount_usd AS DOUBLE), CAST(fraud_score AS DOUBLE),
                   transaction_status, merchant_name, merchant_category, channel,
                   CAST(_is_synthetic AS BOOLEAN)
            FROM transactions
            WHERE customer_id = ?
              AND currency = ?
              AND ABS(CAST(amount AS DOUBLE) - ?) <= ?
              AND ABS(DATE_DIFF('day', CAST(transaction_date AS DATE), CAST(? AS DATE))) <= ?
            """,
            [
                session.customer_id,
                currency,
                reported_amount,
                amount_tolerance,
                reported_date,
                date_tolerance_days,
            ],
        ).fetchall()
    finally:
        con.close()

    return [_row_to_candidate(row) for row in rows]


_TRANSACTION_COLUMNS = """
    transaction_id, CAST(transaction_date AS TIMESTAMP), CAST(amount AS DOUBLE),
    currency, CAST(amount_usd AS DOUBLE), CAST(fraud_score AS DOUBLE),
    transaction_status, merchant_name, merchant_category, channel,
    CAST(_is_synthetic AS BOOLEAN)
"""


def list_own_charges(
    session: Session,
    *,
    transaction_types: tuple[str, ...],
    limit: int,
    around_date: date | None = None,
    date_window_days: int = 0,
    amount: float | None = None,
    amount_tolerance: float = 0.0,
    merchant_hint: str | None = None,
    transaction_ids: tuple[str, ...] | None = None,
    db_path: Path | None = None,
) -> list[TransactionCandidate]:
    """This session's OWN outgoing charges, optionally narrowed by a date
    window, an amount band, a merchant-name substring and/or specific ids: what
    the customer is shown to pick from when they cannot quote an exact amount
    and date. `customer_id` is never a parameter.
    """
    clauses = ["customer_id = ?", f"transaction_type IN ({', '.join('?' for _ in transaction_types)})"]
    params: list[object] = [session.customer_id, *transaction_types]
    if around_date is not None:
        clauses.append("ABS(DATE_DIFF('day', CAST(transaction_date AS DATE), CAST(? AS DATE))) <= ?")
        params += [around_date, date_window_days]
    if amount is not None:
        clauses.append("ABS(CAST(amount AS DOUBLE) - ?) <= ?")
        params += [amount, amount_tolerance]
    if merchant_hint:
        clauses.append("merchant_name ILIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(merchant_hint)}%")
    if transaction_ids is not None:
        clauses.append(f"transaction_id IN ({', '.join('?' for _ in transaction_ids) or 'NULL'})")
        params += list(transaction_ids)
    order = "CAST(transaction_date AS TIMESTAMP) DESC, transaction_id"
    order_params: list[object] = []
    if around_date is not None:
        order = "ABS(DATE_DIFF('day', CAST(transaction_date AS DATE), CAST(? AS DATE))), " + order
        order_params.append(around_date)
    con = fixture_db.get_connection(db_path)
    try:
        rows = con.execute(
            f"SELECT {_TRANSACTION_COLUMNS} FROM transactions WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order} LIMIT ?",
            [*params, *order_params, limit],
        ).fetchall()
    finally:
        con.close()
    return [_row_to_candidate(row) for row in rows]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_own_transaction(
    session: Session, transaction_id: str, *, db_path: Path | None = None
) -> TransactionCandidate | None:
    """One transaction by id, ONLY if it belongs to this session's customer;
    another customer's id returns None exactly like an unknown one.
    """
    con = fixture_db.get_connection(db_path)
    try:
        row = con.execute(
            f"SELECT {_TRANSACTION_COLUMNS} FROM transactions WHERE customer_id = ? AND transaction_id = ?",
            [session.customer_id, transaction_id],
        ).fetchone()
    finally:
        con.close()
    return _row_to_candidate(row) if row else None


def get_customer_profile(session: Session, *, db_path: Path | None = None) -> CustomerProfile | None:
    con = fixture_db.get_connection(db_path)
    try:
        row = con.execute(
            """
            SELECT customer_id, segment, CAST(credit_score AS DOUBLE), country, customer_status
            FROM customers WHERE customer_id = ?
            """,
            [session.customer_id],
        ).fetchone()
    finally:
        con.close()

    if row is None:
        return None
    return CustomerProfile(
        customer_id=row[0], segment=row[1], credit_score=row[2], country=row[3], customer_status=row[4]
    )


def get_case_history(
    session: Session, category: str, before_date: date, *, window_days: int, db_path: Path | None = None
) -> int:
    """Count of this session's OWN prior complaints in `category`, strictly
    before `before_date`, within the trailing `window_days` — the AD-11
    abuse-guard signal. `customer_id` is never a parameter.
    """
    con = fixture_db.get_connection(db_path)
    try:
        row = con.execute(
            """
            SELECT COUNT(*) FROM complaints
            WHERE customer_id = ?
              AND category = ?
              AND CAST(creation_date AS DATE) >= CAST(? AS DATE) - CAST(? AS INTEGER)
              AND CAST(creation_date AS DATE) < CAST(? AS DATE)
            """,
            [session.customer_id, category, before_date, window_days, before_date],
        ).fetchone()
    finally:
        con.close()
    return row[0]


def count_prior_complaints(session: Session, before_date: date, *, db_path: Path | None = None) -> int:
    """Count of this session's OWN complaints of ANY category, all-time,
    strictly before `before_date` — the live counterpart of
    `etl/features.py`'s `prior_complaint_count` training feature. It must keep
    those exact semantics (no category filter, no window): feeding the
    classifier the category-scoped, 90-day abuse-guard count instead would be
    train/serve skew.
    """
    con = fixture_db.get_connection(db_path)
    try:
        row = con.execute(
            """
            SELECT COUNT(*) FROM complaints
            WHERE customer_id = ?
              AND CAST(creation_date AS DATE) < CAST(? AS DATE)
            """,
            [session.customer_id, before_date],
        ).fetchone()
    finally:
        con.close()
    return row[0]
