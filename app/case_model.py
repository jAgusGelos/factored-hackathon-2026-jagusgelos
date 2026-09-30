"""The dispute case's vocabulary: states, customer actions, the policy
verdict (`CaseEvaluation`), the structured handoff and what the customer has
reported so far. Shared by `app/state_machine.py`, `app/handoffs.py` and
`app/replies.py`, so none of them has to import the others.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum

from app import cases
from app.transactions import TransactionCandidate


class CaseState(StrEnum):
    AWAITING_REPORT = "awaiting_report"
    CLARIFYING = "clarifying"
    SELECTING = "selecting"
    CONFIRMING = "confirming"
    RESOLVED_AUTO = "resolved_auto"
    ESCALATED = "escalated"


TERMINAL_STATES = frozenset({CaseState.RESOLVED_AUTO, CaseState.ESCALATED})
NON_TERMINAL_STATES = tuple(str(s) for s in CaseState if s not in TERMINAL_STATES)


class CustomerAction(StrEnum):
    """Quick-reply buttons. They carry the customer's intent without going
    through the LLM classifier, so a tap on "Sí, es ese" is never misread.
    """

    CONFIRM_YES = "confirm_yes"
    CONFIRM_NO = "confirm_no"
    NONE_OF_THESE = "none_of_these"
    HUMAN = "human"


@dataclass(frozen=True)
class HandoffRecord:
    """The structured artifact a case that escalates produces — facts,
    actions taken, evidence, open questions. Never a raw transcript dump
    (plan.md's Always-rule).
    """

    facts: dict[str, str]
    actions_taken: tuple[str, ...]
    evidence: tuple[str, ...]
    open_questions: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CaseEvaluation:
    """A policy verdict. `SELECTING` means "no single confident match: the
    customer has to pick"; `RESOLVED_AUTO` means "eligible", which the state
    machine still turns into a confirmation step first (AD-12).
    """

    state: CaseState
    matched_transaction: TransactionCandidate | None = None
    candidates: tuple[TransactionCandidate, ...] = field(default_factory=tuple)
    resolution_reasons: tuple[str, ...] = field(default_factory=tuple)
    handoff: HandoffRecord | None = None


@dataclass(frozen=True)
class ReportedCharge:
    """What the CUSTOMER has said about the charge so far. Handoff facts are
    built from this, so it never holds values the system inferred (a matched
    transaction's own amount/date live on the transaction, not here).
    """

    amount: float | None
    date: date | None
    currency: str
    merchant: str | None = None

    @classmethod
    def from_case(cls, case: cases.Case, default_currency: str) -> ReportedCharge:
        return cls(
            amount=case.reported_amount,
            date=date.fromisoformat(case.reported_date) if case.reported_date else None,
            currency=case.reported_currency or default_currency,
            merchant=case.reported_merchant,
        )

    @property
    def has_details(self) -> bool:
        return self.amount is not None or self.date is not None or bool(self.merchant)

    @property
    def is_complete(self) -> bool:
        return self.amount is not None and self.date is not None

    def update_fields(self) -> dict:
        return {
            "reported_amount": self.amount,
            "reported_currency": self.currency,
            "reported_date": self.date.isoformat() if self.date is not None else None,
            "reported_merchant": self.merchant,
        }
