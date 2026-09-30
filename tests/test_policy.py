from dataclasses import replace
from datetime import date

import pytest

from app.policy import (
    MAX_AUTO_CREDIT_TOTAL_USD,
    MAX_TRANSACTION_AGE_DAYS,
    DisputeContext,
    DisputeReason,
    ExplanationAssessment,
    ExplanationVerdict,
    MatchOutcome,
    ResolutionDecision,
    credit_key,
    effective_amount_usd,
    evaluate_explanation,
    evaluate_match,
    evaluate_resolution,
    match_amount_tolerance,
    screening_failures,
)
from app.transactions import TransactionCandidate

AS_OF = date(2026, 6, 18)


def _txn(**overrides) -> TransactionCandidate:
    base = dict(
        transaction_id="TRX-1",
        transaction_date=date(2026, 6, 9),
        amount=100.0,
        currency="USD",
        amount_usd=100.0,
        fraud_score=5.0,
        transaction_status="Approved",
        merchant_name="Comercio Demo",
        merchant_category="Retail",
        channel="App",
        is_synthetic=False,
        transaction_type="Purchase",
    )
    base.update(overrides)
    return TransactionCandidate(**base)


def _ctx(**overrides) -> DisputeContext:
    """A context under which a clean card-not-present purchase is creditable."""
    base = dict(
        reason=DisputeReason.UNRECOGNIZED, as_of=AS_OF, customer_status="Active",
        prior_disputes_in_window=0, classifier_priority=None, other_charges_at_merchant=0,
        duplicate_twins=(), duplicate_pair_credited=False, recent_unrecognized_credits=0,
        recent_credited_usd=0.0,
    )
    base.update(overrides)
    return DisputeContext(**base)


def _duplicate_ctx(**overrides) -> DisputeContext:
    return _ctx(reason=DisputeReason.DUPLICATE, duplicate_twins=("TRX-0",), **overrides)


def _escalates_because(evaluation, fragment: str) -> bool:
    return evaluation.decision == ResolutionDecision.FORCED_ESCALATION and any(
        fragment in r for r in evaluation.reasons
    )


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


# -- Unrecognized charge: the evidence, not the claim, decides ----------------------


def test_clean_card_not_present_unrecognized_charge_auto_resolves():
    evaluation = evaluate_resolution(_txn(), _ctx())
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE
    assert evaluation.reasons == ()


@pytest.mark.parametrize("channel", ["POS", "ATM", "Branch", "Transfer", None])
def test_card_present_or_unknown_channel_is_never_credited_as_unrecognized(channel):
    assert _escalates_because(evaluate_resolution(_txn(channel=channel), _ctx()), "channel=")


@pytest.mark.parametrize("transaction_type", ["Withdrawal", "Transfer", "Payment", None])
def test_only_card_purchases_can_be_credited_as_unrecognized(transaction_type):
    evaluation = evaluate_resolution(_txn(transaction_type=transaction_type), _ctx())
    assert _escalates_because(evaluation, "transaction_type=")


def test_other_charges_at_the_same_merchant_contradict_an_unrecognized_claim():
    evaluation = evaluate_resolution(_txn(), _ctx(other_charges_at_merchant=1))
    assert _escalates_because(evaluation, "other charge(s)")


def test_a_merchant_without_a_name_cannot_be_checked_so_it_escalates():
    evaluation = evaluate_resolution(_txn(merchant_name=None), _ctx(other_charges_at_merchant=None))
    assert _escalates_because(evaluation, "merchant has no name")


@pytest.mark.parametrize("fraud_score", [30.0, 85.0, None])
def test_high_or_unknown_fraud_score_forces_escalation(fraud_score):
    evaluation = evaluate_resolution(_txn(fraud_score=fraud_score), _ctx())
    assert _escalates_because(evaluation, "fraud_score")


def test_a_second_unrecognized_credit_in_the_window_goes_to_a_person():
    evaluation = evaluate_resolution(_txn(), _ctx(recent_unrecognized_credits=1))
    assert _escalates_because(evaluation, "unrecognized-charge credit(s) already granted")


# -- Duplicate: only a verifiable twin in the data counts --------------------------


def test_duplicate_with_a_verifiable_twin_auto_resolves_even_at_a_pos():
    """A double charge is proven by the data, so the card-present and
    merchant-history rules of an unrecognized claim do not apply.
    """
    evaluation = evaluate_resolution(_txn(channel="POS"), _duplicate_ctx(other_charges_at_merchant=1))
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE


def test_duplicate_claim_without_a_twin_escalates():
    evaluation = evaluate_resolution(_txn(), _ctx(reason=DisputeReason.DUPLICATE, duplicate_twins=()))
    assert _escalates_because(evaluation, "no other charge at the same merchant")


def test_duplicate_pair_already_credited_escalates():
    evaluation = evaluate_resolution(_txn(), _duplicate_ctx(duplicate_pair_credited=True))
    assert _escalates_because(evaluation, "already credited")


def test_duplicate_withdrawal_is_not_reversed_automatically():
    evaluation = evaluate_resolution(_txn(transaction_type="Withdrawal"), _duplicate_ctx())
    assert _escalates_because(evaluation, "transaction_type=")


def test_both_charges_of_a_duplicate_pair_share_one_credit_key():
    first, second = _txn(transaction_id="TRX-A"), _txn(transaction_id="TRX-B")
    assert credit_key(first, DisputeReason.DUPLICATE, ("TRX-B",)) == credit_key(
        second, DisputeReason.DUPLICATE, ("TRX-A",)
    )
    assert credit_key(first, DisputeReason.UNRECOGNIZED, ()) == "TRX-A"


# -- Reasons a person must handle ---------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [DisputeReason.NOT_RECEIVED, DisputeReason.WRONG_AMOUNT, DisputeReason.CARD_LOST_STOLEN, DisputeReason.UNCLEAR],
)
def test_reasons_other_than_unrecognized_or_duplicate_are_never_credited(reason):
    """Even on an otherwise perfect charge (a verifiable twin included)."""
    evaluation = evaluate_resolution(_txn(), _ctx(reason=reason, duplicate_twins=("TRX-0",)))
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION


def test_without_a_reason_only_the_screening_conditions_apply():
    """Before the customer explains, AUTO_RESOLVE only means "ask them what
    happened": a card-present charge still passes screening.
    """
    assert evaluate_resolution(_txn(channel="POS"), _ctx(reason=None)).decision == ResolutionDecision.AUTO_RESOLVE
    assert screening_failures(_txn(channel="POS"), _ctx(reason=None)) == ()


# -- Screening: common to every reason ----------------------------------------------


@pytest.mark.parametrize("reason", [DisputeReason.UNRECOGNIZED, DisputeReason.DUPLICATE])
@pytest.mark.parametrize(
    ("txn_overrides", "ctx_overrides", "fragment"),
    [
        ({"amount_usd": 500.0}, {}, "amount_usd"),
        ({"amount": 1000.0, "currency": "MXN", "amount_usd": None}, {}, "amount_usd=None"),
        ({"transaction_status": "Declined"}, {}, "transaction_status"),
        ({"transaction_status": "Pending"}, {}, "transaction_status"),
        ({"fraud_score": 91.0}, {}, "fraud_score"),
        ({"fraud_score": None}, {}, "fraud_score"),
        ({"transaction_date": date(2026, 4, 1)}, {}, "days old"),
        ({}, {"customer_status": "Suspended"}, "customer_status"),
        ({}, {"customer_status": None}, "customer_status"),
        ({}, {"prior_disputes_in_window": 3}, "abuse guard"),
        ({}, {"classifier_priority": "Critical"}, "Critical"),
        ({"amount_usd": 150.0}, {"recent_credited_usd": 60.0}, "USD cap"),
    ],
)
def test_screening_failures_block_every_reason(reason, txn_overrides, ctx_overrides, fragment):
    ctx = _duplicate_ctx(**ctx_overrides) if reason == DisputeReason.DUPLICATE else _ctx(**ctx_overrides)
    assert _escalates_because(evaluate_resolution(_txn(**txn_overrides), ctx), fragment)


def test_abuse_guard_allows_up_to_two_prior_disputes():
    assert evaluate_resolution(_txn(), _ctx(prior_disputes_in_window=2)).decision == ResolutionDecision.AUTO_RESOLVE


def test_credit_total_cap_is_inclusive():
    txn = _txn(amount_usd=50.0)
    ctx = _ctx(recent_credited_usd=MAX_AUTO_CREDIT_TOTAL_USD - 50.0)
    assert evaluate_resolution(txn, ctx).decision == ResolutionDecision.AUTO_RESOLVE


def test_age_window_is_inclusive():
    txn = _txn(transaction_date=date.fromordinal(AS_OF.toordinal() - MAX_TRANSACTION_AGE_DAYS))
    assert evaluate_resolution(txn, _ctx()).decision == ResolutionDecision.AUTO_RESOLVE


def test_reasons_accumulate_for_the_handoff():
    evaluation = evaluate_resolution(_txn(channel="POS", amount_usd=500.0), _ctx(other_charges_at_merchant=2))
    assert len(evaluation.reasons) == 3


# -- The explanation assessment can only ask for more or escalate -------------------


def _assessment(**overrides) -> ExplanationAssessment:
    base = dict(reason=DisputeReason.UNRECOGNIZED, specific=True, consistent=True, contradictions=(), summary="")
    base.update(overrides)
    return ExplanationAssessment(**base)


def test_a_vague_explanation_asks_for_one_more_detail_then_escalates():
    vague = _assessment(specific=False)
    assert evaluate_explanation(vague, attempts_left=True) == (ExplanationVerdict.NEEDS_DETAIL, None)
    verdict, why = evaluate_explanation(vague, attempts_left=False)
    assert verdict == ExplanationVerdict.ESCALATE and why


def test_an_inconsistent_explanation_escalates_with_the_contradictions():
    verdict, why = evaluate_explanation(
        _assessment(consistent=False, contradictions=("dice que fue en marzo",)), attempts_left=True,
    )
    assert verdict == ExplanationVerdict.ESCALATE
    assert "marzo" in why


@pytest.mark.parametrize("reason", [DisputeReason.NOT_RECEIVED, DisputeReason.WRONG_AMOUNT, DisputeReason.CARD_LOST_STOLEN])
def test_explanations_naming_a_person_only_reason_escalate(reason):
    verdict, why = evaluate_explanation(_assessment(reason=reason), attempts_left=True)
    assert verdict == ExplanationVerdict.ESCALATE and why


def test_accepting_an_explanation_does_not_make_an_ineligible_charge_creditable():
    """The most convincing explanation possible still leaves the charge to
    the evidence check: a card-present "unrecognized" charge escalates.
    """
    assert evaluate_explanation(_assessment(), attempts_left=True)[0] == ExplanationVerdict.ACCEPT
    assert evaluate_resolution(_txn(channel="POS"), _ctx()).decision == ResolutionDecision.FORCED_ESCALATION


# -- USD pricing ----------------------------------------------------------------------


def test_effective_amount_usd_prefers_real_amount_usd():
    assert effective_amount_usd(_txn(amount=1000.0, currency="MXN", amount_usd=55.0)) == 55.0


def test_effective_amount_usd_falls_back_to_amount_when_currency_is_usd_and_amount_usd_null():
    """Real data finding: `amount_usd` is NULL for ~57% of transactions —
    specifically whenever `currency == 'USD'`.
    """
    assert effective_amount_usd(_txn(amount=150.0, currency="USD", amount_usd=None)) == 150.0


def test_effective_amount_usd_is_none_for_non_usd_currency_with_null_amount_usd():
    assert effective_amount_usd(_txn(amount=1000.0, currency="MXN", amount_usd=None)) is None


def test_context_has_no_permissive_defaults():
    """A caller that forgets a policy input must fail loudly, not silently
    get "no history", "no prior credits" or "active customer".
    """
    with pytest.raises(TypeError):
        DisputeContext(reason=DisputeReason.UNRECOGNIZED, as_of=AS_OF)  # type: ignore[call-arg]
    assert replace(_ctx(), recent_credited_usd=1.0).recent_credited_usd == 1.0
