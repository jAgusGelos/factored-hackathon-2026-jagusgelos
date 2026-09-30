"""Test-only re-exports of `support.py` plus the pytest skip marker.

`support.py` (repo root) has no pytest dependency, since `eval/run_eval.py`
(a standalone script) imports from it too — this module adds the
pytest-specific bits on top, for `tests/` only.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.policy import DisputeContext, DisputeReason
from app.transactions import TransactionCandidate
from support import (
    AUTO_RESOLVE_CHARGE,
    CARD_PRESENT_CHARGE,
    CONVINCING_ASSESSMENT,
    DEMO_USERNAME,
    DUPLICATE_ASSESSMENT,
    DUPLICATE_CHARGES,
    EXPLANATION,
    FRAUD_SCORE_CHARGE,
    NOT_RECEIVED_ASSESSMENT,
    OVER_LIMIT_CHARGE,
    REAL_DEMO_USERS_PATH,
    REAL_FIXTURE_PATH,
    REPO_ROOT,
    SECOND_ONLINE_CHARGE,
    charge_extraction,
    charge_report,
    demo_session,
    event_sequence,
    logged_events,
    mock_anthropic_client,
    session_for,
)

__all__ = [
    "REAL_DEMO_USERS_PATH",
    "REAL_FIXTURE_PATH",
    "REPO_ROOT",
    "AUTO_RESOLVE_CHARGE",
    "CARD_PRESENT_CHARGE",
    "CONVINCING_ASSESSMENT",
    "DUPLICATE_ASSESSMENT",
    "EXPLANATION",
    "NOT_RECEIVED_ASSESSMENT",
    "SECOND_ONLINE_CHARGE",
    "clean_ctx",
    "clean_txn",
    "event_sequence",
    "logged_events",
    "DEMO_USERNAME",
    "DUPLICATE_CHARGES",
    "FRAUD_SCORE_CHARGE",
    "OVER_LIMIT_CHARGE",
    "charge_extraction",
    "charge_report",
    "demo_session",
    "mock_anthropic_client",
    "session_for",
    "requires_real_fixture",
]

requires_real_fixture = pytest.mark.skipif(
    not (REAL_FIXTURE_PATH.exists() and REAL_DEMO_USERS_PATH.exists()),
    reason="Requires the ETL fixture (run `python etl/extract.py && python etl/build_fixture.py` first)",
)


def clean_txn(**overrides) -> TransactionCandidate:
    """A card-not-present purchase every policy condition accepts."""
    base = dict(
        transaction_id="TRX-1", transaction_date=date(2026, 6, 9), amount=100.0, currency="USD",
        amount_usd=100.0, fraud_score=5.0, transaction_status="Approved", merchant_name="Comercio Demo",
        merchant_category="Retail", channel="App", is_synthetic=False, transaction_type="Purchase",
    )
    return TransactionCandidate(**{**base, **overrides})


def clean_ctx(**overrides) -> DisputeContext:
    """A context under which `clean_txn()` is creditable as unrecognized."""
    base = dict(
        reason=DisputeReason.UNRECOGNIZED, as_of=date(2026, 6, 18), customer_status="Active",
        prior_disputes_in_window=0, classifier_priority=None, other_charges_at_merchant=0,
        duplicate_twins=(), duplicate_pair_credited=False, recent_unrecognized_credits=0,
        recent_credited_usd=0.0,
    )
    return DisputeContext(**{**base, **overrides})

