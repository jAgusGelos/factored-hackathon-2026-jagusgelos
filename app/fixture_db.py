"""Read-only connection helper for the sanitized demo fixture (AD-2).

The deployed app's ONLY source of customer/product/complaint/transaction data
is `data/fixture.duckdb` (built offline by `etl/build_fixture.py`) — never the
full local warehouse, never S3. Every column in the fixture is stored as
VARCHAR (see `etl/build_fixture.py::_copy_rows_to_fixture`), so callers here
cast to the real type at query time.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from app import config

FRAUD_RISK_COLUMNS = ("fraud_risk", "fraud_risk_threshold", "fraud_model_version")


def fraud_risk_is_stored(con: duckdb.DuckDBPyConnection) -> bool:
    """Whether the fixture carries the offline fraud-risk estimate
    (`etl/build_fixture.py`); one built before it has none of its columns.
    """
    placeholders = ", ".join("?" for _ in FRAUD_RISK_COLUMNS)
    stored = con.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        f"WHERE table_name = 'transactions' AND column_name IN ({placeholders})",
        list(FRAUD_RISK_COLUMNS),
    ).fetchone()[0]
    return stored == len(FRAUD_RISK_COLUMNS)


def get_connection(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    # Resolved at call time, not as a default-argument value, so tests can
    # monkeypatch `config.FIXTURE_DB_PATH` and have it take effect.
    resolved_path = db_path if db_path is not None else config.FIXTURE_DB_PATH
    return duckdb.connect(str(resolved_path), read_only=True)
