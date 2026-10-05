"""Dispute-resolution policy (AD-11) — explicit, falsifiable thresholds.

This module IS the policy table from plan.md's AD-11, implemented as data +
pure functions (no DB access, no I/O). `app/state_machine.py`'s guard
functions are thin wrappers that call into this module — this file is what
makes "eligible" / "ambiguous" / "high-risk" testable, not asserted.

AD-11's rows, in order:
  1. Transaction match: fuzzy-match candidates = this customer's own
     transactions where `abs(amount - reported_amount) <= max(reported_amount
     * 0.05, 2 USD-equivalent)` AND `abs(date diff) <= 3 days`.
  2. Confident match: exactly one candidate.
  3. Ambiguous match: 0 or 2+ candidates -> clarify (max 2 rounds), then
     escalate if still ambiguous.
  4. Auto-resolution eligible (AD-13). Before anything else the charge must
     pass the SCREENING conditions, common to every reason: status ==
     "Approved", fraud_score < 30, amount_usd <= 200, no older than MAX_TRANSACTION_AGE_DAYS,
     customer_status == "Active", < 3 dataset disputes in the same category
     in the trailing 90 days, the classifier does not predict Critical, and
     the automatic credits this system granted the customer in the trailing
     CREDIT_WINDOW_DAYS plus this one stay within MAX_AUTO_CREDIT_TOTAL_USD.
     Then the customer explains what happened (Milestone 9). The LLM only
     ASSESSES that explanation (`ExplanationAssessment`); here the
     assessment can only ask for one more detail or force escalation, never
     make a charge eligible (`evaluate_explanation`). The reason it names
     picks which evidence check applies, and that check runs on the DATA,
     never on the claim:
       - duplicate: a verifiable twin exists (an Approved charge at the same
         merchant, exact amount, currency and type, at most
         DUPLICATE_WINDOW_MINUTES apart by full timestamp: a pending hold or
         a declined retry was never collected, so it is not a second charge)
         and no charge of the pair was credited before -> reverse it. Equal
         charges further apart are two purchases: they go to a person, named
         as evidence, never reversed on the customer's word.
       - unrecognized: card-not-present purchase (Web/App), no other charge
         of theirs at the same merchant (an existing relationship with the
         merchant contradicts "I never used it"), and
         fewer than MAX_UNRECOGNIZED_AUTO_CREDITS such credits in the
         trailing CREDIT_WINDOW_DAYS -> provisional credit + simulated card
         block + back-office review. A second "I don't recognize it" right
         after a card block goes to a person.
       - not_received / wrong_amount / card_lost_stolen / unclear: never an
         automatic credit (a chargeback against the merchant, a partial
         amount, or a fraud investigation across several charges needs a
         person).
  5. Forced escalation: confident match but fails a Row-4 condition, OR the
     classifier (AD-6, Milestone 3) predicts Critical, OR the customer
     explicitly requests a human, OR any tool/LLM call fails after its retry
     budget is exhausted.
  6. Auto-resolution action: simulated provisional credit + case reference —
     never a real transfer. An unrecognized charge also logs a simulated card
     block and queues the credit for back-office review (it is reversed if
     the investigation shows the customer made the charge).

Simplification, disclosed: the "2 USD-equivalent" floor in Row 1 is applied
as a flat 2-unit floor in the complaint's OWN currency, not currency-converted
via exchange rates — the runtime fixture (AD-2) does not ship
`daily_exchange_rates` (no consumer needs it at request time). This matches
what `etl/build_fixture.py` already used when empirically classifying real
data during persona selection. In practice this floor is dominated by the 5%
relative term for any non-trivial claimed amount, so the currency-unit
mismatch has negligible effect on the match boundary — documented as a
hackathon-scope simplification, not a validated FX-aware threshold.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.transactions import TransactionCandidate

MATCH_DATE_TOLERANCE_DAYS = 3
MATCH_AMOUNT_PCT_TOLERANCE = 0.05
MATCH_AMOUNT_MIN_TOLERANCE = 2.0

AUTO_RESOLVE_MAX_AMOUNT_USD = 200.0
AUTO_RESOLVE_MAX_FRAUD_SCORE = 30.0
AUTO_RESOLVE_REQUIRED_STATUS = "Approved"
AUTO_RESOLVE_REQUIRED_CUSTOMER_STATUS = "Active"
MAX_TRANSACTION_AGE_DAYS = 60

ABUSE_GUARD_MAX_DISPUTES = 3
ABUSE_GUARD_WINDOW_DAYS = 90

# AD-13 exposure limits, over the credits THIS system granted (the dataset's
# complaints history above is a separate, independent signal).
CREDIT_WINDOW_DAYS = 90
MAX_UNRECOGNIZED_AUTO_CREDITS = 1
MAX_AUTO_CREDIT_TOTAL_USD = 200.0

# "Card not present": the card was not physically used, so a leaked card
# number is a plausible explanation. A chip/PIN charge at a POS or an ATM
# operation needs an investigation, not a same-minute credit.
CARD_NOT_PRESENT_CHANNELS = ("Web", "App")
UNRECOGNIZED_ELIGIBLE_TYPES = ("Purchase",)
DUPLICATE_ELIGIBLE_TYPES = ("Purchase", "Payment")
# A real duplicate (a double swipe, a processor retry) posts seconds to
# minutes after the original; two equal taxi fares on consecutive days are two
# rides. DESIGN ARGUMENT, not a measurement: the warehouse holds no pair of
# same-customer, same-merchant, same-amount charges at any distance, so it
# cannot size the window (docs/policy/duplicate-window.md).
DUPLICATE_WINDOW_MINUTES = 10

MAX_CLARIFICATION_ROUNDS = 2

# The contact deadline the escalation notice promises (a demo assumption: this
# simulated bank has no real contact process). Sized from the warehouse's
# `complaints`, subcategory "Cargo no reconocido": 7,567 of its 12,297
# complaints have a recorded first response, with a median of 37 h, a p90 of
# 58 h and an observed maximum of 72 calendar hours; the other 4,730 have none
# yet, so the data says nothing about them (docs/analysis/demand-report.md).
# 3 business days always span at least 72 calendar hours, and
# tests/test_analyze_demand.py fails if this drops below the recorded maximum.
# It promises contact, not a resolution (a median of 15 days in Transactions).
ESCALATION_CONTACT_BUSINESS_DAYS = 3

# Milestone 8: "pick your charge" list. Showing the list is how a clarification
# round is spent now (Row 3), instead of a free-text question. A turn that
# brings NEW information (an amount, date or merchant the case did not have)
# does not spend a round; MAX_CASE_TURNS is the hard stop that keeps a
# conversation feeding "new" details forever from looping.
DISPUTABLE_TRANSACTION_TYPES = ("Purchase", "Payment", "Withdrawal", "Transfer")
CHARGE_LIST_MAX_ITEMS = 8
CHARGE_LIST_DATE_WINDOW_DAYS = 7
MAX_CASE_TURNS = 6

DISPUTE_COMPLAINT_CATEGORY = "Transactions"

# AD-6/AD-11 Row 5 (Milestone 3): a classifier prediction of this label is
# ONE OR-condition that can force escalation — see evaluate_resolution()'s
# docstring for the hard boundary on what this can and cannot do.
CLASSIFIER_ESCALATION_LABEL = "Critical"

# Milestone 9: the customer's own explanation of what happened. The LLM only
# ASSESSES it (ExplanationAssessment, a strict JSON contract); what that
# assessment is allowed to change is decided here, in code.
MIN_EXPLANATION_WORDS = 5
MAX_EXPLANATION_ATTEMPTS = 2


class DisputeReason(StrEnum):
    UNRECOGNIZED = "unrecognized"
    DUPLICATE = "duplicate"
    NOT_RECEIVED = "not_received"
    WRONG_AMOUNT = "wrong_amount"
    CARD_LOST_STOLEN = "card_lost_stolen"
    UNCLEAR = "unclear"


# The only reasons that can ever be credited automatically, each with its own
# evidence check in evaluate_resolution().
AUTO_CREDITABLE_REASONS = frozenset({DisputeReason.UNRECOGNIZED, DisputeReason.DUPLICATE})

# Why every other reason goes to a person (Spanish: the handoff is internal).
REASONS_REQUIRING_A_PERSON = {
    DisputeReason.NOT_RECEIVED: (
        "El cliente reconoce la compra pero dice que no recibió el producto o servicio: es una "
        "disputa con el comercio (contracargo), no un reintegro automático."
    ),
    DisputeReason.WRONG_AMOUNT: (
        "El cliente reconoce la compra pero discute el monto: requiere determinar el monto "
        "correcto (un reintegro parcial no se automatiza)."
    ),
    DisputeReason.CARD_LOST_STOLEN: (
        "El cliente reporta tarjeta perdida o robada: posible fraude. Bloquear la tarjeta y "
        "revisar otros cargos recientes."
    ),
}


class MissingDetail(StrEnum):
    """The one detail a vague explanation still lacks, as the model reads it.
    Only the follow-up question uses it (to ask for that and nothing the
    customer already said); no decision in this module ever reads it.
    """

    HOW_NOTICED = "how_noticed"
    CARD_POSSESSION = "card_possession"
    MERCHANT_KNOWN = "merchant_known"
    ITEM_RECEIVED = "item_received"


@dataclass(frozen=True)
class ExplanationAssessment:
    reason: DisputeReason
    specific: bool
    consistent: bool
    contradictions: tuple[str, ...]
    summary: str
    missing_detail: MissingDetail | None = None
    # A detection flag, not an assessment: the text asks to talk to a person
    # (app/explanation.py routes it to the human request). `evaluate_explanation`
    # never reads it.
    wants_human: bool = False


class ExplanationVerdict(StrEnum):
    ACCEPT = "accept"
    NEEDS_DETAIL = "needs_detail"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class ExplanationDecision:
    """An ESCALATE always says why (for the handoff); the other verdicts never do."""

    verdict: ExplanationVerdict
    escalation_reason: str | None = None

    def __post_init__(self) -> None:
        if self.verdict == ExplanationVerdict.ESCALATE:
            if not isinstance(self.escalation_reason, str) or self.escalation_reason == "":
                raise ValueError(
                    "ExplanationDecision: an ESCALATE needs a non-empty str escalation_reason, "
                    f"got {self.escalation_reason!r}"
                )
        elif self.escalation_reason is not None:
            raise ValueError(
                f"ExplanationDecision: only an ESCALATE carries an escalation_reason, got "
                f"{self.escalation_reason!r} on {self.verdict.name}"
            )

    @property
    def reason_to_escalate(self) -> str:
        if self.escalation_reason is None:
            raise ValueError(f"ExplanationDecision: {self.verdict} carries no escalation reason")
        return self.escalation_reason

    @classmethod
    def accept(cls) -> ExplanationDecision:
        return cls(ExplanationVerdict.ACCEPT)

    @classmethod
    def needs_detail(cls) -> ExplanationDecision:
        return cls(ExplanationVerdict.NEEDS_DETAIL)

    @classmethod
    def escalate(cls, reason: str) -> ExplanationDecision:
        return cls(ExplanationVerdict.ESCALATE, reason)


def evaluate_explanation(assessment: ExplanationAssessment, *, attempts_left: bool) -> ExplanationDecision:
    """What the model's read of the explanation may change: ask for one more
    detail, or force escalation. ACCEPT is NOT eligibility: it only means the
    explanation raised no red flag, and the charge still has to pass the
    evidence check for its reason in evaluate_resolution(). A persuasive
    story therefore cannot credit anything by itself.
    """
    if not assessment.specific or assessment.reason == DisputeReason.UNCLEAR:
        if attempts_left:
            return ExplanationDecision.needs_detail()
        return ExplanationDecision.escalate(
            "La explicación del cliente no fue lo bastante concreta para decidir, aun después de "
            "pedirle más detalle."
        )
    if not assessment.consistent:
        contradictions = "; ".join(assessment.contradictions) or "sin detalle"
        return ExplanationDecision.escalate(
            f"La explicación contradice los datos del cargo: {contradictions}."
        )
    person_needed = REASONS_REQUIRING_A_PERSON.get(assessment.reason)
    if person_needed is not None:
        return ExplanationDecision.escalate(person_needed)
    return ExplanationDecision.accept()


class MatchOutcome(StrEnum):
    CONFIDENT = "confident"
    AMBIGUOUS = "ambiguous"


class ResolutionDecision(StrEnum):
    AUTO_RESOLVE = "auto_resolve"
    FORCED_ESCALATION = "forced_escalation"


@dataclass(frozen=True)
class DisputeContext:
    """Everything the Row 4/5 verdict needs besides the transaction itself,
    gathered by the state machine through session-scoped reads. No field has a
    default: a caller that forgets one fails loudly instead of silently
    getting the permissive value.
    """

    # None until the customer's explanation names one: only screening applies.
    reason: DisputeReason | None
    as_of: date
    customer_status: str | None
    prior_disputes_in_window: int
    classifier_priority: str | None
    # How many OTHER charges of this customer are at the same merchant (None:
    # the merchant has no name, so the relationship cannot be checked).
    other_charges_at_merchant: int | None
    # Ids of this customer's charges that make this one a verifiable duplicate.
    duplicate_twins: tuple[str, ...]
    # Ids of equal charges (same merchant, amount, currency, type, Approved)
    # further apart than the window: separate purchases, the advisor's evidence.
    repeat_charges: tuple[str, ...]
    # A charge of the duplicate pair was already credited by this system.
    duplicate_pair_credited: bool
    recent_unrecognized_credits: int
    recent_credited_usd: float


@dataclass(frozen=True)
class ResolutionEvaluation:
    decision: ResolutionDecision
    reasons: tuple[str, ...]  # which condition(s) drove the decision — for the handoff record


def match_amount_tolerance(reported_amount: float) -> float:
    return max(reported_amount * MATCH_AMOUNT_PCT_TOLERANCE, MATCH_AMOUNT_MIN_TOLERANCE)


def evaluate_match(candidates: list[TransactionCandidate]) -> MatchOutcome:
    return MatchOutcome.CONFIDENT if len(candidates) == 1 else MatchOutcome.AMBIGUOUS


def effective_amount_usd(txn: TransactionCandidate) -> float | None:
    """`amount_usd` is NULL in the real dataset whenever `currency == 'USD'`
    (no conversion needed) — verified during Milestone 1's ETL development.
    """
    if txn.amount_usd is not None:
        return txn.amount_usd
    if txn.currency == "USD":
        return txn.amount
    return None


def credit_key(txn: TransactionCandidate, reason: DisputeReason, twins: tuple[str, ...]) -> str:
    """What the app db's unique index allows to be credited once. A duplicate
    PAIR shares one key, whichever of its charges the customer picked.
    """
    if reason == DisputeReason.DUPLICATE:
        return f"duplicate:{min((txn.transaction_id, *twins))}"
    return txn.transaction_id


def _day(value: date) -> date:
    # The fixture's timestamps come back as datetimes (a subclass of date).
    return value.date() if isinstance(value, datetime) else value


def screening_failures(txn: TransactionCandidate, ctx: DisputeContext) -> tuple[str, ...]:
    """Conditions every automatic credit must meet, whatever the reason: a
    charge failing one goes to a person without asking the customer to explain.
    """
    reasons: list[str] = []
    amount_usd = effective_amount_usd(txn)
    if amount_usd is None or amount_usd > AUTO_RESOLVE_MAX_AMOUNT_USD:
        reasons.append(f"amount_usd={amount_usd} exceeds the {AUTO_RESOLVE_MAX_AMOUNT_USD} auto-resolve cap")
    elif ctx.recent_credited_usd + amount_usd > MAX_AUTO_CREDIT_TOTAL_USD:
        reasons.append(
            f"automatic credits in the trailing {CREDIT_WINDOW_DAYS} days ({ctx.recent_credited_usd} USD) "
            f"plus this one exceed the {MAX_AUTO_CREDIT_TOTAL_USD} USD cap"
        )
    if txn.transaction_status != AUTO_RESOLVE_REQUIRED_STATUS:
        reasons.append(f"transaction_status={txn.transaction_status!r}, not Approved")
    if txn.fraud_score is None or txn.fraud_score >= AUTO_RESOLVE_MAX_FRAUD_SCORE:
        reasons.append(f"fraud_score={txn.fraud_score} at/above the {AUTO_RESOLVE_MAX_FRAUD_SCORE} threshold")
    age_days = (ctx.as_of - _day(txn.transaction_date)).days
    if age_days < 0:
        reasons.append(f"charge is dated {-age_days} day(s) after the data as-of date {ctx.as_of}")
    elif age_days > MAX_TRANSACTION_AGE_DAYS:
        reasons.append(f"charge is {age_days} days old, over the {MAX_TRANSACTION_AGE_DAYS}-day window")
    if ctx.customer_status != AUTO_RESOLVE_REQUIRED_CUSTOMER_STATUS:
        reasons.append(f"customer_status={ctx.customer_status!r}, not Active")
    if ctx.prior_disputes_in_window >= ABUSE_GUARD_MAX_DISPUTES:
        reasons.append(
            f"{ctx.prior_disputes_in_window} prior disputes in the trailing "
            f"{ABUSE_GUARD_WINDOW_DAYS} days (abuse guard)"
        )
    if ctx.classifier_priority == CLASSIFIER_ESCALATION_LABEL:
        reasons.append(f"priority classifier predicted {CLASSIFIER_ESCALATION_LABEL!r} (decision support only)")
    return tuple(reasons)


def _unrecognized_failures(txn: TransactionCandidate, ctx: DisputeContext) -> list[str]:
    reasons: list[str] = []
    if txn.channel not in CARD_NOT_PRESENT_CHANNELS:
        reasons.append(f"channel={txn.channel!r}: card-present charge, needs a fraud investigation")
    if txn.transaction_type not in UNRECOGNIZED_ELIGIBLE_TYPES:
        reasons.append(f"transaction_type={txn.transaction_type!r}: not a card purchase")
    if ctx.other_charges_at_merchant is None:
        reasons.append("merchant has no name: the customer's history with it cannot be checked")
    elif ctx.other_charges_at_merchant > 0:
        reasons.append(
            f"customer has {ctx.other_charges_at_merchant} other charge(s) at {txn.merchant_name!r} "
            "they do not dispute"
        )
    if ctx.recent_unrecognized_credits >= MAX_UNRECOGNIZED_AUTO_CREDITS:
        reasons.append(
            f"{ctx.recent_unrecognized_credits} unrecognized-charge credit(s) already granted in the "
            f"trailing {CREDIT_WINDOW_DAYS} days (limit {MAX_UNRECOGNIZED_AUTO_CREDITS})"
        )
    return reasons


def _duplicate_failures(txn: TransactionCandidate, ctx: DisputeContext) -> list[str]:
    reasons: list[str] = []
    if txn.transaction_type not in DUPLICATE_ELIGIBLE_TYPES:
        reasons.append(f"transaction_type={txn.transaction_type!r}: not a purchase or payment")
    if not ctx.duplicate_twins and ctx.repeat_charges:
        reasons.append(
            f"the other charge(s) at the same merchant and amount ({', '.join(ctx.repeat_charges)}) posted "
            f"more than {DUPLICATE_WINDOW_MINUTES} minutes apart: separate purchases, not a duplicate"
        )
    elif not ctx.duplicate_twins:
        reasons.append(
            "customer reports a duplicate but no other charge at the same merchant and amount "
            f"within {DUPLICATE_WINDOW_MINUTES} minutes was found"
        )
    if ctx.duplicate_pair_credited:
        reasons.append("the other charge of the duplicate pair was already credited")
    return reasons


# One evidence check per reason in AUTO_CREDITABLE_REASONS (pinned by tests/test_policy.py).
_EVIDENCE_CHECKS = {
    DisputeReason.DUPLICATE: _duplicate_failures,
    DisputeReason.UNRECOGNIZED: _unrecognized_failures,
}


def evaluate_resolution(txn: TransactionCandidate, ctx: DisputeContext) -> ResolutionEvaluation:
    """Row 4/5 of AD-11 (with AD-13's reason-specific rows), given a single
    CONFIDENT match. Never called for an ambiguous match — that path is Row
    3's clarification loop, not this.

    With `ctx.reason` None (the customer has not explained yet) only the
    screening conditions run, and AUTO_RESOLVE means "may go on to the
    explanation", never "credit it": `app/explanation.py` only credits
    after a verdict WITH a reason.

    `ctx.classifier_priority` (Milestone 3, AD-6) is DECISION SUPPORT ONLY: a
    "Critical" prediction is one OR-condition among several that can force
    `FORCED_ESCALATION`. It is structurally incapable of ever causing
    `AUTO_RESOLVE` by itself or overriding any of the other conditions —
    every condition can only ADD a reason, and only an empty reason list
    auto-resolves. Enforced by `tests/test_policy_not_overridden.py`.
    """
    reasons = list(screening_failures(txn, ctx))
    if ctx.reason in AUTO_CREDITABLE_REASONS:
        reasons += _EVIDENCE_CHECKS[ctx.reason](txn, ctx)
    elif ctx.reason is not None:
        reasons.append(REASONS_REQUIRING_A_PERSON.get(
            ctx.reason, f"dispute reason {ctx.reason!r} is never credited automatically",
        ))
    if reasons:
        return ResolutionEvaluation(decision=ResolutionDecision.FORCED_ESCALATION, reasons=tuple(reasons))
    return ResolutionEvaluation(decision=ResolutionDecision.AUTO_RESOLVE, reasons=())


# -- The customer's statement before a handoff (app/statement.py) ---------------


class Tristate(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class HowNoticed(StrEnum):
    APP_ALERT = "app_alert"
    STATEMENT = "statement"
    SMS_OR_EMAIL = "sms_or_email"
    OTHER = "other"
    UNKNOWN = "unknown"


class CardLoss(StrEnum):
    LOST = "lost"
    STOLEN = "stolen"
    UNKNOWN = "unknown"


class StatementField(StrEnum):
    """The key facts of the customer's statement, as the handoff names them."""

    DENIES_PURCHASE = "denies_purchase"
    MERCHANT_KNOWN = "merchant_known"
    CARD_POSSESSION = "card_possession"
    CARD_LOSS = "card_loss"
    HOW_NOTICED = "how_noticed"
    NOTICED_ON = "noticed_on"
    OTHER_SUSPICIOUS_ACTIVITY = "other_suspicious_activity"


@dataclass(frozen=True)
class StatementAssessment:
    """The model's read of the customer's statement before a handoff
    (`app/statement.py`): a bounded neutral summary and closed-enum facts,
    so no free customer text reaches the handoff. Every fact the customer
    did not state is UNKNOWN (or None), never guessed.
    """

    summary: str
    # About the latest message only: it refuses to tell more, or asks for a person.
    declines: bool
    wants_human: bool
    denies_purchase: Tristate
    merchant_known: Tristate
    card_possession: Tristate
    how_noticed: HowNoticed
    noticed_on: str | None
    other_suspicious_activity: Tristate
    card_loss: CardLoss = CardLoss.UNKNOWN

    def facts(self) -> dict[StatementField, str | None]:
        return {field: getattr(self, field) for field in StatementField}


# Every key fact the customer can tell, which the statement step's one
# follow-up asks for when missing, in this order (`app/statement.py`); only
# what the customer still leaves unknown becomes an advisor task.
FOLLOWUP_FACTS = (
    StatementField.DENIES_PURCHASE, StatementField.CARD_POSSESSION, StatementField.CARD_LOSS,
    StatementField.MERCHANT_KNOWN, StatementField.HOW_NOTICED, StatementField.OTHER_SUSPICIOUS_ACTIVITY,
)


def known_fact(value: object) -> bool:
    """A key fact the customer stated: neither None nor "unknown"."""
    return value not in (None, Tristate.UNKNOWN, HowNoticed.UNKNOWN, CardLoss.UNKNOWN)


def card_possession_matters(facts: Mapping[str, object]) -> bool:
    """Card possession only matters when the customer does not say they made
    the purchase (the statement step's follow-up and the advisor's tasks).
    """
    return facts.get(StatementField.DENIES_PURCHASE) != Tristate.NO


def open_facts(facts: Mapping[str, object], among: Iterable[StatementField]) -> list[StatementField]:
    """The facts of `among`, in order, the customer has not stated and that
    still matter (the statement step's follow-up and the advisor's tasks).
    """
    return [fact for fact in among if not known_fact(facts.get(fact)) and _fact_matters(fact, facts)]


def _fact_matters(fact: StatementField, facts: Mapping[str, object]) -> bool:
    if fact == StatementField.CARD_POSSESSION:
        return card_possession_matters(facts)
    if fact == StatementField.CARD_LOSS:
        return facts.get(StatementField.CARD_POSSESSION) == Tristate.NO
    return True
