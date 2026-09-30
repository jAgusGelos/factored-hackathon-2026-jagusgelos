"""SQLite connection helper for the app's read/write operational store.

Deliberately separate from the DuckDB files: `data/warehouse.duckdb` is an
offline ETL artifact that never ships to the deployed runtime (AD-2), and the
sanitized demo fixture is its own read-only DuckDB file
(`config.FIXTURE_DB_PATH`, built by `etl/build_fixture.py`). This SQLite file
(`config.APP_DB_PATH`) holds what the running app writes: sessions (AD-4),
cases, messages, audit events and the idempotent chat turns.

Using SQLite (not an in-memory dict) for sessions is a deliberate AD-4 choice:
it must survive a process restart mid-demo.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import config

logger = logging.getLogger("app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'es',
    state TEXT NOT NULL,
    reported_amount REAL,
    reported_currency TEXT,
    reported_date TEXT,
    matched_transaction_id TEXT,
    clarification_rounds INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    offered_transaction_ids TEXT,
    reported_merchant TEXT,
    handoff_unlocked INTEGER NOT NULL DEFAULT 0,
    dispute_reason TEXT,
    explanation_text TEXT,
    explanation_attempts INTEGER NOT NULL DEFAULT 0,
    credit_key TEXT,
    credited_amount_usd REAL,
    credited_at TEXT,
    predicted_priority TEXT,
    resolution_reference TEXT,
    handoff_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- The same-charge lookups (cases.credited_case_for_transaction and friends).
CREATE INDEX IF NOT EXISTS idx_cases_customer_transaction ON cases (customer_id, matched_transaction_id);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases (case_id)
);

CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures_lookup ON login_failures (scope, key, created_at);
CREATE INDEX IF NOT EXISTS idx_login_failures_created ON login_failures (created_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT NOT NULL,
    case_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- One row per client-generated turn id (AD-4): pending while reply_json is
-- NULL, complete once the reply the client got is stored. Keyed per customer,
-- so one customer's id can never reach another customer's reply.
CREATE TABLE IF NOT EXISTS chat_turns (
    customer_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    case_id TEXT,
    reply_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    failed_at TEXT,
    PRIMARY KEY (customer_id, turn_id)
);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    return con


# Columns added after a database may already exist on a persistent volume:
# CREATE TABLE IF NOT EXISTS leaves an old table untouched, so add them here.
_ADDED_COLUMNS = {
    "cases": (
        ("turn_count", "INTEGER NOT NULL DEFAULT 0"),
        ("offered_transaction_ids", "TEXT"),
        ("reported_merchant", "TEXT"),
        ("handoff_unlocked", "INTEGER NOT NULL DEFAULT 0"),
        ("dispute_reason", "TEXT"),
        ("explanation_text", "TEXT"),
        ("explanation_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("credit_key", "TEXT"),
        ("credited_amount_usd", "REAL"),
        ("credited_at", "TEXT"),
    ),
    "chat_turns": (
        ("failed_at", "TEXT"),
    ),
}

# At most one simulated credit per transaction and customer. Created after the
# column migration; skipped (the app-level check in the state machine still
# applies) on an old database that already holds duplicate credits.
_ONE_CREDIT_PER_TRANSACTION = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_one_credit_per_transaction "
    "ON cases (customer_id, matched_transaction_id) WHERE state = 'resolved_auto'"
)
# At most one credit per credit key: a duplicate PAIR shares one key, so it is
# reversed once whichever of its two charges the customer picks (AD-13).
_ONE_CREDIT_PER_KEY = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_one_credit_per_key "
    "ON cases (customer_id, credit_key) WHERE state = 'resolved_auto' AND credit_key IS NOT NULL"
)


def _add_missing_columns(con: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns:
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


# A completed chat turn is kept this long for replays of a client retry.
COMPLETED_TURN_RETENTION = timedelta(days=1)


def _purge_completed_turns(con: sqlite3.Connection) -> None:
    cutoff = (datetime.now(UTC) - COMPLETED_TURN_RETENTION).isoformat()
    con.execute("DELETE FROM chat_turns WHERE completed_at IS NOT NULL AND completed_at < ?", [cutoff])


def init_db(db_path: Path) -> None:
    con = get_connection(db_path)
    try:
        con.executescript(SCHEMA)
        _add_missing_columns(con)
        _purge_completed_turns(con)
        for name, index in (
            ("idx_cases_one_credit_per_transaction", _ONE_CREDIT_PER_TRANSACTION),
            ("idx_cases_one_credit_per_key", _ONE_CREDIT_PER_KEY),
        ):
            try:
                con.execute(index)
            except sqlite3.IntegrityError:
                logger.warning("Existing duplicate credits: unique index %s not created", name)
        con.commit()
    finally:
        con.close()


@contextmanager
def app_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    # Resolved at call time (not as a default-argument value), so tests can
    # monkeypatch `config.APP_DB_PATH` and have it take effect.
    with closing(get_connection(db_path if db_path is not None else config.APP_DB_PATH)) as con:
        yield con
