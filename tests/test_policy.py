from datetime import date

from app.policy import (
    MatchOutcome,
    ResolutionDecision,
    evaluate_match,
    evaluate_resolution,
    match_amount_tolerance,
)
from app.transactions import TransactionCandidate


def _txn(**overrides) -> TransactionCandidate:
    base = dict(
        transaction_id="TRX-1",
        transaction_date=date(2024, 3, 9),
        amount=100.0,
        currency="USD",
        amount_usd=100.0,
        fraud_score=5.0,
        transaction_status="Approved",
        merchant_name="Comercio Demo",
        merchant_category="Retail",
        channel="App",
        is_synthetic=False,
    )
    base.update(overrides)
    return TransactionCandidate(**base)


def test_match_amount_tolerance_uses_percentage_for_large_amounts():
    assert match_amount_tolerance(1000.0) == 50.0  # 5% > $2 floor


def test_match_amount_tolerance_uses_floor_for_small_amounts():
    assert match_amount_tolerance(10.0) == 2.0  # 5% of 10 = 0.5 < $2 floor


def test_evaluate_match_confident_with_exactly_one_candidate():
    assert evaluate_match([_txn()]) == MatchOutcome.CONFIDENT


def test_evaluate_match_ambiguous_with_zero_candidates():
    assert evaluate_match([]) == MatchOutcome.AMBIGUOUS


def test_evaluate_match_ambiguous_with_multiple_candidates():
    assert evaluate_match([_txn(transaction_id="TRX-1"), _txn(transaction_id="TRX-2")]) == MatchOutcome.AMBIGUOUS


def test_confident_match_clean_case_auto_resolves():
    evaluation = evaluate_resolution(_txn(), prior_disputes_in_window=0)
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE
    assert evaluation.reasons == ()


def test_amount_over_threshold_forces_escalation():
    txn = _txn(amount_usd=500.0)
    evaluation = evaluate_resolution(txn, prior_disputes_in_window=0)
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION
    assert any("amount_usd" in r for r in evaluation.reasons)


def test_high_fraud_score_forces_escalation():
    txn = _txn(fraud_score=85.0)
    evaluation = evaluate_resolution(txn, prior_disputes_in_window=0)
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION
    assert any("fraud_score" in r for r in evaluation.reasons)


def test_declined_transaction_status_forces_escalation():
    txn = _txn(transaction_status="Declined")
    evaluation = evaluate_resolution(txn, prior_disputes_in_window=0)
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION
    assert any("transaction_status" in r for r in evaluation.reasons)


def test_abuse_guard_forces_escalation():
    txn = _txn()
    evaluation = evaluate_resolution(txn, prior_disputes_in_window=3)
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION
    assert any("abuse guard" in r for r in evaluation.reasons)


def test_abuse_guard_allows_up_to_two_prior_disputes():
    txn = _txn()
    evaluation = evaluate_resolution(txn, prior_disputes_in_window=2)
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE


def test_effective_amount_usd_prefers_real_amount_usd():
    from app.policy import effective_amount_usd

    txn = _txn(amount=1000.0, currency="MXN", amount_usd=55.0)
    assert effective_amount_usd(txn) == 55.0


def test_effective_amount_usd_falls_back_to_amount_when_currency_is_usd_and_amount_usd_null():
    """Real data finding: `amount_usd` is NULL for ~57% of transactions —
    specifically whenever `currency == 'USD'`.
    """
    from app.policy import effective_amount_usd

    txn = _txn(amount=150.0, currency="USD", amount_usd=None)
    assert effective_amount_usd(txn) == 150.0


def test_effective_amount_usd_is_none_for_non_usd_currency_with_null_amount_usd():
    from app.policy import effective_amount_usd

    txn = _txn(amount=1000.0, currency="MXN", amount_usd=None)
    assert effective_amount_usd(txn) is None


def test_null_amount_usd_non_usd_currency_forces_escalation_not_silent_auto_resolve():
    """A transaction we cannot confidently price in USD must never silently
    auto-resolve — it must be routed to a human, not approved on an unknown.
    """
    txn = _txn(amount=1000.0, currency="MXN", amount_usd=None)
    evaluation = evaluate_resolution(txn, prior_disputes_in_window=0)
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION
