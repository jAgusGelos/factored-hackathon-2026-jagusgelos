"""Dispute conversation state machine.

MILESTONE 1 STATUS: this is a deliberate stub — a single hardcoded scripted
exchange, wired end-to-end (login -> chat -> response) to prove the walking
skeleton works with zero AWS dependency at runtime. Milestone 2 replaces this
with the real guard-function state table driven by `app/policy.py` (AD-11) and
`app/transactions.py`'s session-scoped fuzzy matching (AD-3), plus real LLM
calls for NLU/NLG (AD-5/AD-10).

States are defined now so Milestone 2 can slot in without renaming; the stub
always jumps straight to `resolved_auto` in one hardcoded step.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict

from app.auth import Session


class CaseState(StrEnum):
    AWAITING_REPORT = "awaiting_report"
    CLARIFYING = "clarifying"
    MATCHING = "matching"
    RESOLVED_AUTO = "resolved_auto"
    ESCALATED = "escalated"


class ChatReply(TypedDict):
    case_id: str
    state: CaseState
    customer_id: str
    reply: str


_SCRIPTED_REPLY = (
    "Gracias por tu mensaje. Este es un flujo de demostración del Milestone 1: "
    "en el Milestone 2 este backend va a verificar tu transacción y aplicar la "
    "política de resolución de disputas en código. Por ahora, este es el "
    "intercambio de chat de referencia (walking skeleton)."
)


def handle_message(session: Session, case_id: str, text: str) -> ChatReply:
    """M1 stub: always returns the same scripted reply regardless of input.

    Signature intentionally takes a `Session` (never a bare `customer_id`) so
    Milestone 2's real implementation can be dropped in without touching the
    confused-deputy boundary any caller of this function relies on.
    """
    return {
        "case_id": case_id,
        "state": CaseState.RESOLVED_AUTO,
        "customer_id": session.customer_id,
        "reply": _SCRIPTED_REPLY,
    }
