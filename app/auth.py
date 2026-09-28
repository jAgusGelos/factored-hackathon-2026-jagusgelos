"""Demo-credentials auth + opaque session tokens (AD-4).

This is explicitly a SIMULATED identity service for the hackathon demo, not a
production-grade one — `data/demo_users.json` provisions a handful of test
accounts with their own credentials (kept structurally separate from
`customers.csv`-derived fields, e.g. never `document_number`), distinct from
"knowledge of a dataset attribute" per the organizer's rule that a customer
number/national ID alone does not prove identity.

`/auth/login` (wired in app/main.py) checks a submitted credential against
this fixture, then mints an opaque `secrets.token_urlsafe(32)` token. Only a
SHA-256 hash of the token is ever persisted (SQLite `sessions` table) — the
raw token exists only in the HttpOnly cookie on the client and in the single
response that sets it.

The `Session` object returned by `get_session()` is the ONLY way any other
module in this app may learn a request's `customer_id` (AD-3's confused-deputy
boundary: every function that reads a specific customer's data takes a
`Session`, never a bare `customer_id` a caller could supply).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import config, db


@dataclass(frozen=True)
class Session:
    customer_id: str
    expires_at: datetime


_session_db = db.app_connection


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_demo_users(path: Path | None = None) -> dict[str, dict]:
    # Resolved at call time for the same monkeypatching reason as `_session_db`.
    resolved_path = path if path is not None else config.DEMO_USERS_PATH
    if not resolved_path.exists():
        raise FileNotFoundError(
            f"{resolved_path} is missing. Run `python etl/build_fixture.py` first "
            "(it generates the demo-credentials fixture alongside the case-data fixture)."
        )
    return json.loads(resolved_path.read_text(encoding="utf-8"))


def verify_credentials(username: str, password: str) -> str | None:
    """Returns the matched `customer_id` if the demo credential is valid, else None."""
    users = _load_demo_users()
    user = users.get(username)
    if user is None:
        return None
    # Constant-time comparison to avoid a trivial timing side-channel on a demo fixture.
    if not secrets.compare_digest(user["password"], password):
        return None
    return user["customer_id"]


def create_session(
    customer_id: str, db_path: Path | None = None, ttl_hours: int | None = None
) -> tuple[str, datetime]:
    """Mints a new opaque session token for `customer_id`. Returns (raw_token, expires_at).

    Only the hash is persisted — the caller (the login endpoint) is responsible
    for putting the raw token in the response cookie and never logging it.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=ttl_hours if ttl_hours is not None else config.SESSION_TTL_HOURS)

    with _session_db(db_path) as con:
        con.execute(
            "INSERT INTO sessions (token_hash, customer_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (_hash_token(token), customer_id, now.isoformat(), expires_at.isoformat()),
        )
        con.commit()

    return token, expires_at


def get_session(token: str, db_path: Path | None = None) -> Session | None:
    """Looks up a raw token's hash and returns a verified Session, or None if
    the token is unknown or expired. This is the ONLY function in the app
    that is allowed to turn client-supplied input into a `customer_id`.
    """
    if not token:
        return None

    with _session_db(db_path) as con:
        row = con.execute(
            "SELECT customer_id, expires_at FROM sessions WHERE token_hash = ?",
            (_hash_token(token),),
        ).fetchone()

    if row is None:
        return None

    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if datetime.now(UTC) >= expires_at:
        return None

    return Session(customer_id=row["customer_id"], expires_at=expires_at)


def invalidate_session(token: str, db_path: Path | None = None) -> None:
    with _session_db(db_path) as con:
        con.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        con.commit()
