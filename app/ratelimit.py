"""Brute-force throttling for `/auth/login` (SQLite-backed, no new dependency).

Every login attempt RESERVES a failure row per key (username and client IP)
inside one `BEGIN IMMEDIATE` transaction that first counts the existing rows,
so concurrent attempts cannot all pass the check before any is recorded. The
row is deleted again if the credentials turn out to be valid, so only failures
persist. Once a key has `max_failures` rows inside `WINDOW_SECONDS`, further
attempts are refused (HTTP 429 + Retry-After) BEFORE credentials are checked,
for an exponentially growing delay (`BASE_DELAY_SECONDS * 2^(failures -
max_failures)`, capped at `MAX_DELAY_SECONDS`) measured from the last failure.
Refused attempts are not recorded, so an attacker cannot extend a lockout
indefinitely by hammering it.

Every submitted username is tracked identically whether or not it exists, so
throttling behavior cannot be used to enumerate accounts. The per-IP limit is
deliberately looser than the per-username one: demo judges may share an IP.

Known limits (README "Known limitations"): a per-username lockout lets an
attacker temporarily lock a known account out (bounded by MAX_DELAY_SECONDS),
and the IP is whatever uvicorn resolves for the peer (the platform proxy's
forwarded address on the shipped Fly/Render configs, see DEPLOY.md).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app import config, db

WINDOW_SECONDS = 15 * 60
BASE_DELAY_SECONDS = 30.0
MAX_DELAY_SECONDS = 15 * 60.0
MAX_FAILURES_PER_USERNAME = 5
MAX_FAILURES_PER_IP = 20


class Scope(StrEnum):
    USERNAME = "username"
    IP = "ip"


@dataclass(frozen=True)
class Throttle:
    retry_after_seconds: int


@dataclass(frozen=True)
class Reservation:
    """The failure rows an in-flight attempt holds until it is released."""

    row_ids: tuple[int, ...]


def _norm(key: str) -> str:
    return key.strip().lower()[: config.MAX_USERNAME_LENGTH]


def _retry_after(failure_times: list[float], max_failures: int, now: float) -> float:
    if len(failure_times) < max_failures:
        return 0.0
    delay = min(BASE_DELAY_SECONDS * 2 ** (len(failure_times) - max_failures), MAX_DELAY_SECONDS)
    return max(0.0, max(failure_times) + delay - now)


def _failure_times(con, scope: Scope, key: str, now: float) -> list[float]:
    rows = con.execute(
        "SELECT created_at FROM login_failures WHERE scope = ? AND key = ? AND created_at > ?",
        (scope, key, now - WINDOW_SECONDS),
    ).fetchall()
    return [r["created_at"] for r in rows]


def reserve_attempt(
    username: str, ip: str, *, db_path: Path | None = None, now: float | None = None
) -> Throttle | Reservation:
    """Atomically decides whether this attempt may proceed and, if so, records
    it as a provisional failure. Call `release()` when the credentials were valid.
    """
    now = time.time() if now is None else now
    keys = ((Scope.USERNAME, _norm(username), MAX_FAILURES_PER_USERNAME), (Scope.IP, _norm(ip), MAX_FAILURES_PER_IP))
    with db.app_connection(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            wait = max(
                _retry_after(_failure_times(con, scope, key, now), limit, now) for scope, key, limit in keys
            )
            if wait > 0:
                con.rollback()
                return Throttle(retry_after_seconds=int(wait) + 1)
            # Rows older than the window can never influence a decision again.
            con.execute("DELETE FROM login_failures WHERE created_at <= ?", (now - WINDOW_SECONDS,))
            row_ids = tuple(
                con.execute(
                    "INSERT INTO login_failures (scope, key, created_at) VALUES (?, ?, ?)",
                    (scope, key, now),
                ).lastrowid
                for scope, key, _ in keys
            )
            con.commit()
        except BaseException:
            con.rollback()
            raise
    return Reservation(row_ids=row_ids)


def release(reservation: Reservation, username: str, *, db_path: Path | None = None) -> None:
    """A valid login: drop this attempt's provisional rows and forgive the
    username's earlier failures.
    """
    with db.app_connection(db_path) as con:
        con.executemany("DELETE FROM login_failures WHERE id = ?", [(i,) for i in reservation.row_ids])
        con.execute(
            "DELETE FROM login_failures WHERE scope = ? AND key = ?", (Scope.USERNAME, _norm(username))
        )
        con.commit()
