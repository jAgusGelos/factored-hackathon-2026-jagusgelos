"""Finding the customer's own charges to show them (the "pick your charge"
list, Milestone 8). Reads only through `app/transactions.py`'s session-scoped
tools, so a list can never contain another customer's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TypedDict

from app.auth import Session
from app.case_model import ReportedCharge
from app.policy import (
    CHARGE_LIST_DATE_WINDOW_DAYS,
    CHARGE_LIST_MAX_ITEMS,
    DISPUTABLE_TRANSACTION_TYPES,
    MATCH_DATE_TOLERANCE_DAYS,
    match_amount_tolerance,
)
from app.transactions import TransactionCandidate, list_own_charges


class ListFilter(StrEnum):
    RECENT = "recent"
    FILTERED = "filtered"
    FALLBACK_RECENT = "fallback_recent"


class ChargeOption(TypedDict):
    transaction_id: str
    date: str
    amount: float
    currency: str
    merchant: str | None
    category: str | None


@dataclass(frozen=True)
class ChargeSearch:
    charges: tuple[TransactionCandidate, ...]
    list_filter: ListFilter
    # True only when the merchant the customer named was part of the filter
    # that produced these charges (not a looser date/amount-only fallback).
    matched_on_merchant: bool = False


def iso_day(txn: TransactionCandidate) -> str:
    # DuckDB hands back a datetime for the date column; customers see a date.
    return txn.transaction_date.isoformat()[:10]


def txn_day(txn: TransactionCandidate) -> date:
    return date.fromisoformat(iso_day(txn))


def charge_option(txn: TransactionCandidate) -> ChargeOption:
    return {
        "transaction_id": txn.transaction_id,
        "date": iso_day(txn),
        "amount": txn.amount,
        "currency": txn.currency,
        "merchant": txn.merchant_name,
        "category": txn.merchant_category,
    }


def _list_charges(session: Session, **filters) -> tuple[TransactionCandidate, ...]:
    filters.setdefault("date_window_days", CHARGE_LIST_DATE_WINDOW_DAYS)
    return tuple(list_own_charges(
        session, transaction_types=DISPUTABLE_TRANSACTION_TYPES, limit=CHARGE_LIST_MAX_ITEMS, **filters,
    ))


def recent_charges(session: Session) -> ChargeSearch:
    return ChargeSearch(_list_charges(session), ListFilter.RECENT)


def matching_charges(session: Session, report: ReportedCharge) -> tuple[TransactionCandidate, ...]:
    """The disputable charges inside AD-11's own match tolerance (amount and
    +/- MATCH_DATE_TOLERANCE_DAYS): what an ambiguous match is narrowed to.
    """
    return _list_charges(
        session, amount=report.amount, amount_tolerance=match_amount_tolerance(report.amount),
        around_date=report.date, date_window_days=MATCH_DATE_TOLERANCE_DAYS,
    )


def offered_charges(session: Session, offered_ids: tuple[str, ...]) -> tuple[TransactionCandidate, ...]:
    """The charges last offered on a case, in the order they were shown."""
    found = {c.transaction_id: c for c in _list_charges(session, transaction_ids=offered_ids)}
    return tuple(found[i] for i in offered_ids if i in found)


def find_charges(session: Session, report: ReportedCharge) -> ChargeSearch:
    """The customer's own charges narrowed by whatever they told us. Filters
    are relaxed step by step (all of them, then merchant, then date, then
    amount) before falling back to the most recent charges, so a slightly-off
    detail still shows something useful instead of nothing.
    """
    amount_filter = (
        {"amount": report.amount, "amount_tolerance": match_amount_tolerance(report.amount)}
        if report.amount is not None else {}
    )
    date_filter = {"around_date": report.date} if report.date is not None else {}
    merchant_filter = {"merchant_hint": report.merchant} if report.merchant else {}
    ladder = (
        {**amount_filter, **date_filter, **merchant_filter},
        merchant_filter,
        date_filter,
        amount_filter,
    )
    tried: list[dict] = []
    for filters in ladder:
        if not filters or filters in tried:
            continue
        tried.append(filters)
        charges = _list_charges(session, **filters)
        if charges:
            return ChargeSearch(charges, ListFilter.FILTERED, matched_on_merchant="merchant_hint" in filters)
    fallback = recent_charges(session)
    return ChargeSearch(fallback.charges, ListFilter.FALLBACK_RECENT) if tried else fallback
