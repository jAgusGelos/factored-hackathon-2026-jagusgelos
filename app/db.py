"""SQLite connection helper for the app's read/write operational store.

Deliberately separate from the DuckDB files: `data/warehouse.duckdb` is an
offline ETL artifact that never ships to the deployed runtime (AD-2), and the
sanitized demo fixture is its own read-only DuckDB file
(`config.FIXTURE_DB_PATH`, built by `etl/build_fixture.py`). This SQLite file
(`config.APP_DB_PATH`) holds what the running app writes: sessions (AD-4),
cases, messages and audit events.

Using SQLite (not an in-memory dict) for sessions is a deliberate AD-4 choice:
it must survive a process restart mid-demo.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from app import config

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
    predicted_priority TEXT,
    resolution_reference TEXT,
    handoff_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def init_db(db_path: Path) -> None:
    con = get_connection(db_path)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


@contextmanager
def app_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    # Resolved at call time (not as a default-argument value), so tests can
    # monkeypatch `config.APP_DB_PATH` and have it take effect.
    with closing(get_connection(db_path if db_path is not None else config.APP_DB_PATH)) as con:
        yield con
