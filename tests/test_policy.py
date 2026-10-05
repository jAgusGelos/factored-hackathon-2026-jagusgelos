from dataclasses import replace
from datetime import date

import pytest

from app import policy, replies
from app.llm import Language
from app.policy import (
    AUTO_CREDITABLE_REASONS,
    MAX_AUTO_CREDIT_TOTAL_USD,
    MAX_TRANSACTION_AGE_DAYS,
    REASONS_REQUIRING_A_PERSON,
    DisputeContext,
    DisputeReason,
    ExplanationDecision,
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
from tests.support import clean_assessment, clean_ctx, clean_txn

AS_OF = date(2026, 6, 18)



def _duplicate_ctx(**overrides) -> DisputeContext:
    return clean_ctx(reason=DisputeReason.DUPLICATE, duplicate_twins=("TRX-0",), **overrides)


def _escalates_because(evaluation, fragment: str) -> bool:
    return evaluation.decision == ResolutionDecision.FORCED_ESCALATION and any(
        fragment in r for r in evaluation.reasons
    )


def test_match_amount_tolerance_uses_percentage_for_large_amounts():
    assert match_amount_tolerance(1000.0) == 50.0  # 5% > $2 floor


def test_match_amount_tolerance_uses_floor_for_small_amounts():
    assert match_amount_tolerance(10.0) == 2.0  # 5% of 10 = 0.5 < $2 floor


def test_evaluate_match_confident_with_exactly_one_candidate():
    assert evaluate_match([clean_txn()]) == MatchOutcome.CONFIDENT


def test_evaluate_match_ambiguous_with_zero_candidates():
    assert evaluate_match([]) == MatchOutcome.AMBIGUOUS


def test_evaluate_match_ambiguous_with_multiple_candidates():
    assert evaluate_match([clean_txn(transaction_id="TRX-1"), clean_txn(transaction_id="TRX-2")]) == MatchOutcome.AMBIGUOUS


# -- Unrecognized charge: the evidence, not the claim, decides ----------------------


def test_clean_card_not_present_unrecognized_charge_auto_resolves():
    evaluation = evaluate_resolution(clean_txn(), clean_ctx())
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE
    assert evaluation.reasons == ()


@pytest.mark.parametrize("channel", ["POS", "ATM", "Branch", "Transfer", None])
def test_card_present_or_unknown_channel_is_never_credited_as_unrecognized(channel):
    assert _escalates_because(evaluate_resolution(clean_txn(channel=channel), clean_ctx()), "channel=")


@pytest.mark.parametrize("transaction_type", ["Withdrawal", "Transfer", "Payment", None])
def test_only_card_purchases_can_be_credited_as_unrecognized(transaction_type):
    evaluation = evaluate_resolution(clean_txn(transaction_type=transaction_type), clean_ctx())
    assert _escalates_because(evaluation, "transaction_type=")


def test_other_charges_at_the_same_merchant_contradict_an_unrecognized_claim():
    evaluation = evaluate_resolution(clean_txn(), clean_ctx(other_charges_at_merchant=1))
    assert _escalates_because(evaluation, "other charge(s)")


def test_a_merchant_without_a_name_cannot_be_checked_so_it_escalates():
    evaluation = evaluate_resolution(clean_txn(merchant_name=None), clean_ctx(other_charges_at_merchant=None))
    assert _escalates_because(evaluation, "merchant has no name")


@pytest.mark.parametrize("fraud_score", [30.0, 85.0, None])
def test_high_or_unknown_fraud_score_forces_escalation(fraud_score):
    evaluation = evaluate_resolution(clean_txn(fraud_score=fraud_score), clean_ctx())
    assert _escalates_because(evaluation, "fraud_score")


def test_a_second_unrecognized_credit_in_the_window_goes_to_a_person():
    evaluation = evaluate_resolution(clean_txn(), clean_ctx(recent_unrecognized_credits=1))
    assert _escalates_because(evaluation, "unrecognized-charge credit(s) already granted")


# -- Duplicate: only a verifiable twin in the data counts --------------------------


def test_duplicate_with_a_verifiable_twin_auto_resolves_even_at_a_pos():
    """A double charge is proven by the data, so the card-present and
    merchant-history rules of an unrecognized claim do not apply.
    """
    evaluation = evaluate_resolution(clean_txn(channel="POS"), _duplicate_ctx(other_charges_at_merchant=1))
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE


def test_duplicate_claim_without_a_twin_escalates():
    evaluation = evaluate_resolution(clean_txn(), clean_ctx(reason=DisputeReason.DUPLICATE, duplicate_twins=()))
    assert _escalates_because(evaluation, "no other charge at the same merchant")


def test_an_equal_charge_outside_the_window_is_a_separate_purchase():
    """Two equal fares on consecutive days are two rides: the other charge is
    named as the advisor's evidence and nothing is reversed automatically.
    """
    ctx = clean_ctx(reason=DisputeReason.DUPLICATE, duplicate_twins=(), repeat_charges=("TRX-0",))
    evaluation = evaluate_resolution(clean_txn(), ctx)
    assert _escalates_because(evaluation, "TRX-0")
    assert _escalates_because(evaluation, f"more than {policy.DUPLICATE_WINDOW_MINUTES} minutes apart")


def test_a_twin_inside_the_window_wins_over_an_older_equal_charge():
    evaluation = evaluate_resolution(clean_txn(), _duplicate_ctx(repeat_charges=("TRX-9",)))
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE


def test_the_duplicate_window_is_minutes_not_days():
    """AD-14: a double swipe or a processor retry posts within minutes."""
    assert 0 < policy.DUPLICATE_WINDOW_MINUTES <= 60
    assert not hasattr(policy, "DUPLICATE_WINDOW_DAYS")


def test_duplicate_pair_already_credited_escalates():
    evaluation = evaluate_resolution(clean_txn(), _duplicate_ctx(duplicate_pair_credited=True))
    assert _escalates_because(evaluation, "already credited")


def test_duplicate_withdrawal_is_not_reversed_automatically():
    evaluation = evaluate_resolution(clean_txn(transaction_type="Withdrawal"), _duplicate_ctx())
    assert _escalates_because(evaluation, "transaction_type=")


def test_both_charges_of_a_duplicate_pair_share_one_credit_key():
    first, second = clean_txn(transaction_id="TRX-A"), clean_txn(transaction_id="TRX-B")
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
    evaluation = evaluate_resolution(clean_txn(), clean_ctx(reason=reason, duplicate_twins=("TRX-0",)))
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION


def test_without_a_reason_only_the_screening_conditions_apply():
    """Before the customer explains, AUTO_RESOLVE only means "ask them what
    happened": a card-present charge still passes screening.
    """
    assert evaluate_resolution(clean_txn(channel="POS"), clean_ctx(reason=None)).decision == ResolutionDecision.AUTO_RESOLVE
    assert screening_failures(clean_txn(channel="POS"), clean_ctx(reason=None)) == ()


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
    ctx = _duplicate_ctx(**ctx_overrides) if reason == DisputeReason.DUPLICATE else clean_ctx(**ctx_overrides)
    assert _escalates_because(evaluate_resolution(clean_txn(**txn_overrides), ctx), fragment)


def test_abuse_guard_allows_up_to_two_prior_disputes():
    assert evaluate_resolution(clean_txn(), clean_ctx(prior_disputes_in_window=2)).decision == ResolutionDecision.AUTO_RESOLVE


def test_credit_total_cap_is_inclusive():
    txn = clean_txn(amount_usd=50.0)
    ctx = clean_ctx(recent_credited_usd=MAX_AUTO_CREDIT_TOTAL_USD - 50.0)
    assert evaluate_resolution(txn, ctx).decision == ResolutionDecision.AUTO_RESOLVE


def test_age_window_is_inclusive():
    txn = clean_txn(transaction_date=date.fromordinal(AS_OF.toordinal() - MAX_TRANSACTION_AGE_DAYS))
    assert evaluate_resolution(txn, clean_ctx()).decision == ResolutionDecision.AUTO_RESOLVE


def test_reasons_accumulate_for_the_handoff():
    evaluation = evaluate_resolution(clean_txn(channel="POS", amount_usd=500.0), clean_ctx(other_charges_at_merchant=2))
    assert len(evaluation.reasons) == 3


# -- The explanation assessment can only ask for more or escalate -------------------


def test_a_vague_explanation_asks_for_one_more_detail_then_escalates():
    vague = clean_assessment(specific=False)
    assert evaluate_explanation(vague, attempts_left=True) == ExplanationDecision.needs_detail()
    decision = evaluate_explanation(vague, attempts_left=False)
    assert decision.verdict == ExplanationVerdict.ESCALATE and decision.escalation_reason


def test_an_inconsistent_explanation_escalates_with_the_contradictions():
    decision = evaluate_explanation(
        clean_assessment(consistent=False, contradictions=("dice que fue en marzo",)), attempts_left=True,
    )
    assert decision.verdict == ExplanationVerdict.ESCALATE
    assert "marzo" in decision.escalation_reason


@pytest.mark.parametrize("reason", [DisputeReason.NOT_RECEIVED, DisputeReason.WRONG_AMOUNT, DisputeReason.CARD_LOST_STOLEN])
def test_explanations_naming_a_person_only_reason_escalate(reason):
    decision = evaluate_explanation(clean_assessment(reason=reason), attempts_left=True)
    assert decision.verdict == ExplanationVerdict.ESCALATE and decision.escalation_reason


def test_accepting_an_explanation_does_not_make_an_ineligible_charge_creditable():
    """The most convincing explanation possible still leaves the charge to
    the evidence check: a card-present "unrecognized" charge escalates.
    """
    assert evaluate_explanation(clean_assessment(), attempts_left=True) == ExplanationDecision.accept()
    assert evaluate_resolution(clean_txn(channel="POS"), clean_ctx()).decision == ResolutionDecision.FORCED_ESCALATION


@pytest.mark.parametrize("reason", [None, ""])
def test_an_escalation_always_carries_its_reason(reason):
    with pytest.raises(ValueError):
        ExplanationDecision(ExplanationVerdict.ESCALATE, reason)


@pytest.mark.parametrize("verdict", [ExplanationVerdict.ACCEPT, ExplanationVerdict.NEEDS_DETAIL])
def test_only_an_escalation_carries_a_reason(verdict):
    with pytest.raises(ValueError):
        ExplanationDecision(verdict, "why")
    with pytest.raises(ValueError):
        _ = ExplanationDecision(verdict).reason_to_escalate


# -- One set of automatically creditable reasons ----------------------------------------


def test_every_creditable_reason_has_exactly_one_evidence_check():
    assert set(policy._EVIDENCE_CHECKS) == AUTO_CREDITABLE_REASONS


def test_no_creditable_reason_is_also_sent_to_a_person():
    assert not AUTO_CREDITABLE_REASONS & set(REASONS_REQUIRING_A_PERSON)


@pytest.mark.parametrize("language", [Language.ES, Language.PT])
def test_every_creditable_reason_has_a_resolution_message_and_its_disclosures(language):
    assert set(replies._RESOLVED[language]) == AUTO_CREDITABLE_REASONS
    assert set(_REQUIRED_DISCLOSURES[str(language)]) == AUTO_CREDITABLE_REASONS


# -- USD pricing ----------------------------------------------------------------------


def test_effective_amount_usd_prefers_real_amount_usd():
    assert effective_amount_usd(clean_txn(amount=1000.0, currency="MXN", amount_usd=55.0)) == 55.0


def test_effective_amount_usd_falls_back_to_amount_when_currency_is_usd_and_amount_usd_null():
    """Real data finding: `amount_usd` is NULL for ~57% of transactions —
    specifically whenever `currency == 'USD'`.
    """
    assert effective_amount_usd(clean_txn(amount=150.0, currency="USD", amount_usd=None)) == 150.0


def test_effective_amount_usd_is_none_for_non_usd_currency_with_null_amount_usd():
    assert effective_amount_usd(clean_txn(amount=1000.0, currency="MXN", amount_usd=None)) is None


def test_context_has_no_permissive_defaults():
    """A caller that forgets a policy input must fail loudly, not silently
    get "no history", "no prior credits" or "active customer".
    """
    with pytest.raises(TypeError):
        DisputeContext(reason=DisputeReason.UNRECOGNIZED, as_of=AS_OF)  # type: ignore[call-arg]
    assert replace(clean_ctx(), recent_credited_usd=1.0).recent_credited_usd == 1.0


def test_a_charge_dated_after_the_data_as_of_date_escalates():
    evaluation = evaluate_resolution(clean_txn(transaction_date=date(2026, 6, 25)), clean_ctx())
    assert _escalates_because(evaluation, "after the data as-of date")


# -- What a resolution message must disclose ------------------------------------------


# One fact per entry, satisfied by any of its word stems (lowercase): an
# unrecognized charge's credit is provisional, the card is blocked and the
# credit is reversed if the charge was theirs; a duplicate was duplicated and
# one of the two charges was returned.
_REQUIRED_DISCLOSURES = {
    "es": {
        DisputeReason.UNRECOGNIZED: (("provisional",), ("bloque",), ("revier", "revert")),
        DisputeReason.DUPLICATE: (("duplicad",), ("uno de los dos", "devolvimos", "reintegr")),
    },
    "pt": {
        DisputeReason.UNRECOGNIZED: (("provisóri", "provisori"), ("bloque",), ("revert",)),
        DisputeReason.DUPLICATE: (("duplicad",), ("uma das duas", "devolvemos", "reembols")),
    },
}


@pytest.mark.parametrize("language", ["es", "pt"])
@pytest.mark.parametrize("reason", [DisputeReason.UNRECOGNIZED, DisputeReason.DUPLICATE])
def test_every_resolution_template_states_the_reference_and_the_required_disclosures(language, reason):
    """The resolution message is always this template (never model-written),
    so the template itself must carry every disclosure the policy requires.
    """
    from app import replies
    from app.llm import Language

    template = replies.resolved("REF-X", reason, Language(language)).lower()
    assert "ref-x" in template
    for stems in _REQUIRED_DISCLOSURES[language][reason]:
        assert any(stem in template for stem in stems), (reason, language, stems)
