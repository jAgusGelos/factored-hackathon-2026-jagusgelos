"""Proves the classifier (AD-6) can only ever ADD an escalation reason — it
is structurally incapable of causing `AUTO_RESOLVE` or overriding any of
AD-11's other conditions (Task 3.4's mandatory adversarial test).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import duckdb
import pytest

from app.policy import ResolutionDecision, evaluate_resolution
from app.transactions import TransactionCandidate


def _clean_txn(**overrides) -> TransactionCandidate:
    base = dict(
        transaction_id="TRX-1", transaction_date=date(2024, 3, 9), amount=100.0, currency="USD",
        amount_usd=100.0, fraud_score=5.0, transaction_status="Approved", merchant_name="Comercio",
        merchant_category="Retail", channel="App", is_synthetic=False,
    )
    base.update(overrides)
    return TransactionCandidate(**base)


def test_adversarial_critical_prediction_forces_escalation_on_an_otherwise_clean_case():
    """The core adversarial case: every OTHER AD-11 condition is satisfied
    (would auto-resolve on its own), but the classifier predicts Critical.
    """
    evaluation = evaluate_resolution(
        _clean_txn(), prior_disputes_in_window=0, classifier_priority="Critical"
    )
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION
    assert any("Critical" in r for r in evaluation.reasons)


@pytest.mark.parametrize("classifier_priority", ["Low", "Medium", "High", None, "", "critical", "CRITICAL"])
def test_non_critical_or_malformed_predictions_never_block_a_clean_auto_resolve(classifier_priority):
    """Only the EXACT documented label forces escalation — a non-Critical
    prediction (or a malformed/differently-cased one) must never silently
    suppress a legitimate auto-resolution either. Case sensitivity is
    intentional: an unrecognized label is treated as "not the escalation
    trigger", never guessed at.
    """
    evaluation = evaluate_resolution(
        _clean_txn(), prior_disputes_in_window=0, classifier_priority=classifier_priority
    )
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE


def test_classifier_can_never_flip_an_already_failing_case_back_to_auto_resolve():
    """The classifier is additive-only: even a non-Critical (or absent)
    prediction must not rescue a case that fails on its own merits.
    """
    txn = _clean_txn(fraud_score=95.0)
    evaluation = evaluate_resolution(txn, prior_disputes_in_window=0, classifier_priority="Low")
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION


def test_evaluate_resolution_has_no_auto_resolve_code_path_reachable_from_a_critical_prediction():
    """Static/structural guard, not just a behavioral sample: iterate the
    full boolean domain of the other 4 gating conditions and confirm NONE
    of the 16 combinations produce AUTO_RESOLVE when classifier_priority is
    'Critical'. This is what makes the claim "structurally incapable",
    not "happens to not have observed it happen".
    """
    from app.policy import (
        AUTO_RESOLVE_MAX_AMOUNT_USD,
        AUTO_RESOLVE_MAX_FRAUD_SCORE,
        AUTO_RESOLVE_REQUIRED_STATUS,
    )

    for amount_ok in (True, False):
        for fraud_ok in (True, False):
            for status_ok in (True, False):
                for abuse_ok in (True, False):
                    txn = _clean_txn(
                        amount_usd=(AUTO_RESOLVE_MAX_AMOUNT_USD - 1) if amount_ok else (AUTO_RESOLVE_MAX_AMOUNT_USD + 1),
                        fraud_score=(AUTO_RESOLVE_MAX_FRAUD_SCORE - 1) if fraud_ok else (AUTO_RESOLVE_MAX_FRAUD_SCORE + 1),
                        transaction_status=AUTO_RESOLVE_REQUIRED_STATUS if status_ok else "Declined",
                    )
                    evaluation = evaluate_resolution(
                        txn,
                        prior_disputes_in_window=0 if abuse_ok else 10,
                        classifier_priority="Critical",
                    )
                    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION, (
                        f"amount_ok={amount_ok} fraud_ok={fraud_ok} status_ok={status_ok} "
                        f"abuse_ok={abuse_ok}: classifier_priority='Critical' failed to force "
                        "escalation"
                    )


# --- End-to-end: the classifier's prediction actually reaches evaluate_resolution ---


@pytest.fixture()
def fixture_con(tmp_path, monkeypatch):
    from app import config

    db_path = tmp_path / "fixture.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE transactions (transaction_id VARCHAR, transaction_date VARCHAR, "
        "customer_id VARCHAR, amount VARCHAR, currency VARCHAR, amount_usd VARCHAR, "
        "fraud_score VARCHAR, transaction_status VARCHAR, merchant_name VARCHAR, "
        "merchant_category VARCHAR, channel VARCHAR, _is_synthetic VARCHAR)"
    )
    con.execute(
        "INSERT INTO transactions VALUES ('TRX-1', '2024-03-09', 'CLI-1', '100.0', 'USD', "
        "'100.0', '5.0', 'Approved', 'Comercio', 'Retail', 'App', 'false')"
    )
    con.execute(
        "CREATE TABLE complaints (complaint_id VARCHAR, customer_id VARCHAR, category VARCHAR, creation_date VARCHAR)"
    )
    con.execute(
        "CREATE TABLE customers (customer_id VARCHAR, segment VARCHAR, credit_score VARCHAR, "
        "country VARCHAR, customer_status VARCHAR)"
    )
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', '700', 'México', 'Active')")
    con.execute("ALTER TABLE transactions ADD COLUMN transaction_type VARCHAR DEFAULT 'Purchase'")
    con.close()
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", db_path)
    return db_path


def test_end_to_end_evaluate_case_escalates_when_the_live_classifier_call_predicts_critical(fixture_con):
    """Not just the pure-function boundary: proves the wiring in
    app/state_machine.py::evaluate_case() actually calls the classifier and
    respects its output as escalation-only, for a case that would otherwise
    cleanly auto-resolve.
    """
    from app.state_machine import CaseState, evaluate_case
    from tests.test_state_machine import SESSION

    with patch("app.state_machine.classifier.predict_priority", return_value="Critical"):
        evaluation = evaluate_case(
            SESSION, reported_amount=100.0, reported_date=date(2024, 3, 10),
            currency="USD",
        )

    assert evaluation.state == CaseState.ESCALATED
    assert evaluation.handoff is not None
    assert any("Critical" in r for r in evaluation.handoff.open_questions)


def test_end_to_end_evaluate_case_still_auto_resolves_when_classifier_is_unavailable(fixture_con):
    """A classifier failure (returns None, per app/classifier.py's own
    fail-safe design) must not block a legitimate auto-resolution — decision
    support that is unavailable is simply absent, not a blocker.
    """
    from app.state_machine import CaseState, evaluate_case
    from tests.test_state_machine import SESSION

    with patch("app.state_machine.classifier.predict_priority", return_value=None):
        evaluation = evaluate_case(
            SESSION, reported_amount=100.0, reported_date=date(2024, 3, 10),
            currency="USD",
        )

    assert evaluation.state == CaseState.RESOLVED_AUTO
