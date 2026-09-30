"""Idempotent chat turns (AD-4): a client-generated `turn_id` per send, reused
when the client retries, so a retry never applies a turn twice.

The protocol is claim-first: the `(customer_id, turn_id)` row is INSERTed
before anything is processed, so of two requests with the same id exactly one
wins the primary key and runs the turn. The other gets the stored reply when
the winner finished (`COMPLETE`), "still running" while it is young
(`IN_FLIGHT`), or, once it is old enough that the winner must have died
(`ABANDONED`), the case's current state; an abandoned turn is never run again,
because it may already have moved the case.

Every read and write is keyed by the session's customer id, never a
caller-provided one: the same `turn_id` under another customer is simply that
customer's own turn.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from app import db

if TYPE_CHECKING:
    from app.case_turn import ChatReply

# How long a pending turn is presumed to be still running. Well above the
# per-turn model budget (config.TURN_DEADLINE_SECONDS), so a live turn is
# never mistaken for a dead one.
PENDING_TIMEOUT_SECONDS = 120


class TurnStatus(StrEnum):
    NEW = "new"
    COMPLETE = "complete"
    IN_FLIGHT = "in_flight"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class TurnClaim:
    status: TurnStatus
    # The case the turn touched, once known (None for a first message whose
    # case was never created).
    case_id: str | None = None
    # The exact reply the client got, for a COMPLETE turn.
    reply: ChatReply | None = None


class TurnInProgress(Exception):
    """The same turn is still being processed by another request (HTTP 409)."""


def claim(customer_id: str, turn_id: str, *, db_path: Path | None = None) -> TurnClaim:
    now = datetime.now(UTC)
    with db.app_connection(db_path) as con:
        try:
            con.execute(
                "INSERT INTO chat_turns (customer_id, turn_id, created_at) VALUES (?, ?, ?)",
                [customer_id, turn_id, now.isoformat()],
            )
            con.commit()
            return TurnClaim(TurnStatus.NEW)
        except sqlite3.IntegrityError:
            row = con.execute(
                "SELECT case_id, reply_json, created_at FROM chat_turns WHERE customer_id = ? AND turn_id = ?",
                [customer_id, turn_id],
            ).fetchone()
    if row is None:
        # The winner was refused and released its claim between our INSERT and
        # this SELECT: answer "still running" so the client's retry claims it.
        return TurnClaim(TurnStatus.IN_FLIGHT)
    if row["reply_json"] is not None:
        return TurnClaim(TurnStatus.COMPLETE, row["case_id"], json.loads(row["reply_json"]))
    age = now - datetime.fromisoformat(row["created_at"])
    status = TurnStatus.IN_FLIGHT if age < timedelta(seconds=PENDING_TIMEOUT_SECONDS) else TurnStatus.ABANDONED
    return TurnClaim(status, row["case_id"])


def attach_case(customer_id: str, turn_id: str, case_id: str, *, db_path: Path | None = None) -> None:
    """Records the case a pending turn works on as soon as it is known, so an
    abandoned turn can still answer with that case's state.
    """
    with db.app_connection(db_path) as con:
        con.execute(
            "UPDATE chat_turns SET case_id = ? WHERE customer_id = ? AND turn_id = ? AND reply_json IS NULL",
            [case_id, customer_id, turn_id],
        )
        con.commit()


def complete(customer_id: str, turn_id: str, reply: ChatReply, *, db_path: Path | None = None) -> None:
    with db.app_connection(db_path) as con:
        con.execute(
            "UPDATE chat_turns SET reply_json = ?, case_id = ?, completed_at = ? "
            "WHERE customer_id = ? AND turn_id = ? AND reply_json IS NULL",
            [json.dumps(reply, ensure_ascii=False), reply.get("case_id"), datetime.now(UTC).isoformat(),
             customer_id, turn_id],
        )
        con.commit()


def release(customer_id: str, turn_id: str, *, db_path: Path | None = None) -> None:
    """Drops a pending claim whose request was refused before it changed
    anything (e.g. a case of another customer), so a retry is not told it is
    still running.
    """
    with db.app_connection(db_path) as con:
        con.execute(
            "DELETE FROM chat_turns WHERE customer_id = ? AND turn_id = ? AND reply_json IS NULL",
            [customer_id, turn_id],
        )
        con.commit()
