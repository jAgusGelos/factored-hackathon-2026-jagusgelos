"""Proves the classifier (AD-6) can only ever ADD an escalation reason — it
is structurally incapable of causing `AUTO_RESOLVE` or overriding any of
AD-11's other conditions (Task 3.4's mandatory adversarial test).

AD-15 extends the same proof to the fraud signals: the vendor `fraud_score`
gate can only add a reason to escalate (or a protective-block signal), and
the offline fraud-risk model's estimate (`TransactionCandidate.fraud_risk`)
cannot change any policy outcome at all: no policy function reads it.
"""

from __future__ import annotations

import ast
import inspect
import itertools
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from app import policy
from app.policy import (
    AUTO_RESOLVE_MAX_AMOUNT_USD,
    AUTO_RESOLVE_MAX_FRAUD_SCORE,
    AUTO_RESOLVE_REQUIRED_STATUS,
    DisputeReason,
    ResolutionDecision,
    evaluate_resolution,
)
from app.transactions import FraudRiskEstimate
from tests.support import clean_ctx, clean_txn


def test_adversarial_critical_prediction_forces_escalation_on_an_otherwise_clean_case():
    """The core adversarial case: every OTHER condition is satisfied (would
    auto-resolve on its own), but the classifier predicts Critical.
    """
    evaluation = evaluate_resolution(clean_txn(), clean_ctx(classifier_priority="Critical"))
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
    evaluation = evaluate_resolution(clean_txn(), clean_ctx(classifier_priority=classifier_priority))
    assert evaluation.decision == ResolutionDecision.AUTO_RESOLVE


def test_classifier_can_never_flip_an_already_failing_case_back_to_auto_resolve():
    """The classifier is additive-only: even a non-Critical (or absent)
    prediction must not rescue a case that fails on its own merits.
    """
    evaluation = evaluate_resolution(clean_txn(fraud_score=95.0), clean_ctx(classifier_priority="Low"))
    assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION


@pytest.mark.parametrize("reason", [None, *DisputeReason])
def test_evaluate_resolution_has_no_auto_resolve_code_path_reachable_from_a_critical_prediction(reason):
    """Static/structural guard, not just a behavioral sample: for EVERY
    dispute reason (and none), iterate the full boolean domain of the other
    gating conditions and confirm NONE of the combinations produce
    AUTO_RESOLVE when classifier_priority is 'Critical'. This is what makes
    the claim "structurally incapable", not "happens to not have observed it
    happen".
    """
    for amount_ok, fraud_ok, status_ok, abuse_ok, channel_ok, history_ok, twin_ok in itertools.product(
        (True, False), repeat=7,
    ):
        txn = clean_txn(
            amount_usd=(AUTO_RESOLVE_MAX_AMOUNT_USD - 1) if amount_ok else (AUTO_RESOLVE_MAX_AMOUNT_USD + 1),
            fraud_score=(AUTO_RESOLVE_MAX_FRAUD_SCORE - 1) if fraud_ok else (AUTO_RESOLVE_MAX_FRAUD_SCORE + 1),
            transaction_status=AUTO_RESOLVE_REQUIRED_STATUS if status_ok else "Declined",
            channel="App" if channel_ok else "POS",
        )
        ctx = clean_ctx(
            reason=reason, classifier_priority="Critical",
            prior_disputes_in_window=0 if abuse_ok else 10,
            other_charges_at_merchant=0 if history_ok else 3,
            duplicate_twins=("TRX-0",) if twin_ok else (),
        )
        evaluation = evaluate_resolution(txn, ctx)
        assert evaluation.decision == ResolutionDecision.FORCED_ESCALATION, (
            f"reason={reason} amount_ok={amount_ok} fraud_ok={fraud_ok} status_ok={status_ok} "
            f"abuse_ok={abuse_ok} channel_ok={channel_ok} history_ok={history_ok} twin_ok={twin_ok}: "
            "classifier_priority='Critical' failed to force escalation"
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
        "INSERT INTO transactions VALUES ('TRX-1', '2026-06-09', 'CLI-1', '100.0', 'USD', "
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
            SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
            currency="USD",
        )

    assert evaluation.state == CaseState.ESCALATED
    assert evaluation.handoff is not None
    assert any("Critical" in r for r in evaluation.handoff.policy_reasons)


def test_end_to_end_evaluate_case_still_auto_resolves_when_classifier_is_unavailable(fixture_con):
    """A classifier failure (returns None, per app/classifier.py's own
    fail-safe design) must not block a legitimate auto-resolution — decision
    support that is unavailable is simply absent, not a blocker.
    """
    from app.state_machine import CaseState, evaluate_case
    from tests.test_state_machine import SESSION

    with patch("app.state_machine.classifier.predict_priority", return_value=None):
        evaluation = evaluate_case(
            SESSION, reported_amount=100.0, reported_date=date(2026, 6, 10),
            currency="USD",
        )

    assert evaluation.state == CaseState.RESOLVED_AUTO


# --- AD-15: the fraud signals can only add a reason; the model estimate decides nothing ---

APP_DIR = Path(__file__).resolve().parent.parent / "app"
LOW_RISK = FraudRiskEstimate(risk=0.0, threshold=0.0035, model_version="test")
HIGH_RISK = FraudRiskEstimate(risk=1.0, threshold=0.0035, model_version="test")


def _policy_domain():
    """Every dispute reason (and none) x the full boolean domain of the other
    gating conditions, fraud score held below the threshold.
    """
    for reason in (None, *DisputeReason):
        for amount_ok, status_ok, abuse_ok, channel_ok, history_ok, twin_ok, critical in itertools.product(
            (True, False), repeat=7,
        ):
            txn = clean_txn(
                amount_usd=(AUTO_RESOLVE_MAX_AMOUNT_USD - 1) if amount_ok else (AUTO_RESOLVE_MAX_AMOUNT_USD + 1),
                fraud_score=AUTO_RESOLVE_MAX_FRAUD_SCORE,
                transaction_status=AUTO_RESOLVE_REQUIRED_STATUS if status_ok else "Declined",
                channel="App" if channel_ok else "POS",
            )
            ctx = clean_ctx(
                reason=reason, classifier_priority="Critical" if critical else None,
                prior_disputes_in_window=0 if abuse_ok else 10,
                other_charges_at_merchant=0 if history_ok else 3,
                duplicate_twins=("TRX-0",) if twin_ok else (),
            )
            yield txn, ctx


def test_a_fraud_score_above_the_threshold_only_ever_adds_one_reason():
    """Raising the score over the cost-justified threshold keeps every other
    reason and adds exactly the fraud-score one: it can never remove a reason
    or turn an escalation into AUTO_RESOLVE, and it always escalates.
    """
    for txn, ctx in _policy_domain():
        below = evaluate_resolution(txn, ctx)
        above = evaluate_resolution(replace(txn, fraud_score=AUTO_RESOLVE_MAX_FRAUD_SCORE + 0.01), ctx)
        assert above.decision == ResolutionDecision.FORCED_ESCALATION
        added = [r for r in above.reasons if r not in below.reasons]
        assert set(below.reasons) <= set(above.reasons)
        assert len(added) == 1 and added[0].startswith("fraud_score=")


def test_the_model_estimate_never_changes_a_policy_verdict():
    """The behavioral half of the proof: for every reason and every gating
    combination, no estimate (none, 0 or 1) changes the verdict or its reasons.
    """
    for txn, ctx in _policy_domain():
        for fraud_score in (AUTO_RESOLVE_MAX_FRAUD_SCORE, AUTO_RESOLVE_MAX_FRAUD_SCORE + 0.01, None):
            base = replace(txn, fraud_score=fraud_score)
            verdicts = {
                evaluate_resolution(replace(base, fraud_risk=risk), ctx) for risk in (None, LOW_RISK, HIGH_RISK)
            }
            assert len(verdicts) == 1


def test_a_high_fraud_score_only_adds_a_block_signal_never_a_block_without_a_claim():
    facts_domain = [
        {},
        {policy.StatementField.DENIES_PURCHASE: policy.Tristate.YES},
        {policy.StatementField.DENIES_PURCHASE: policy.Tristate.NO},
        {policy.StatementField.CARD_POSSESSION: policy.Tristate.NO},
        {policy.StatementField.CARD_LOSS: policy.CardLoss.STOLEN},
    ]
    for reason, facts, channel in itertools.product((None, *DisputeReason), facts_domain, ("App", "POS", None)):
        low = policy.protective_action(reason=reason, facts=facts, high_fraud_score=False, channel=channel)
        high = policy.protective_action(reason=reason, facts=facts, high_fraud_score=True, channel=channel)
        assert set(low.signals) <= set(high.signals)
        assert set(high.signals) - set(low.signals) <= {policy.FraudSignal.HIGH_FRAUD_SCORE}
        denies = reason == DisputeReason.UNRECOGNIZED or facts.get(policy.StatementField.DENIES_PURCHASE) == "yes"
        out_of_hands = reason == DisputeReason.CARD_LOST_STOLEN or bool(
            set(facts) & {policy.StatementField.CARD_POSSESSION, policy.StatementField.CARD_LOSS}
        )
        if not (denies or out_of_hands):
            assert not high.blocks_card


def _names_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
    names |= {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    return names


def test_no_policy_function_can_read_the_model_estimate():
    """The structural half: `app/policy.py` never names the estimate, no
    policy entry point takes it as a parameter, and the only app modules that
    touch it are the fixture read (`app/transactions.py`) and the advisor's
    handoff (`app/handoffs.py`). A future change that routes it into a
    decision has to edit this test.
    """
    assert not {"fraud_risk", "FraudRiskEstimate"} & _names_in(APP_DIR / "policy.py")
    for function in (policy.evaluate_resolution, policy.screening_failures, policy.protective_action):
        assert not any("risk" in name for name in inspect.signature(function).parameters)
    readers = {path.name for path in APP_DIR.glob("*.py") if "fraud_risk" in _names_in(path)}
    assert readers == {"transactions.py", "handoffs.py"}
