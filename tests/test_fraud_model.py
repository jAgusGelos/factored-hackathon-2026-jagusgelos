"""Split, cost model and end-to-end smoke tests for the fraud-risk model
(etl/train_fraud_model.py, etl/evaluate_fraud_model.py) on synthetic data."""

from __future__ import annotations

import importlib.util

import joblib
import numpy as np
import pandas as pd
import pytest

from etl import evaluate_fraud_model as ev
from etl import train_fraud_model as tr
from etl.fraud_features import build_features, clean_transactions


def _synthetic_raw(n: int = 6000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-06-17").value
    end = pd.Timestamp("2026-06-17").value
    dates = pd.to_datetime(rng.integers(start, end, n))
    is_fraud = rng.random(n) < 0.03
    score = np.where(is_fraud, rng.uniform(20, 100, n), rng.uniform(0, 30, n))
    score[rng.random(n) < 0.2] = np.nan
    return pd.DataFrame({
        "transaction_id": [f"T{i:05d}" for i in range(n)],
        "transaction_date": dates,
        "customer_id": [f"C{i:03d}" for i in rng.integers(0, 300, n)],
        "transaction_type": rng.choice(["Purchase", "Payment", "Withdrawal"], n),
        "amount": np.where(is_fraud, rng.uniform(100, 900, n), rng.uniform(5, 300, n)),
        "currency": "USD", "amount_usd": np.nan,
        "channel": rng.choice(["Web", "App", "POS"], n),
        "merchant_name": rng.choice(["A", "B", "C", "D", None], n),
        "merchant_category": rng.choice(["Food", "Other"], n),
        "transaction_country": "México", "transaction_city": rng.choice(["Monterrey", "Cancún"], n),
        "latitude": np.nan, "longitude": np.nan, "fraud_score": score, "is_fraud": is_fraud,
        "transaction_status": rng.choice(["Approved", "Declined"], n, p=[0.95, 0.05]),
        "home_country": "México", "registration_date": pd.Timestamp("2020-01-01"),
    })


def test_split_is_chronological_and_disjoint():
    df = build_features(_synthetic_raw())
    train, val, test, split = tr.chronological_split(df)
    assert train["transaction_date"].min() >= tr.WARMUP_END
    assert train["transaction_date"].max() < tr.TRAIN_END <= val["transaction_date"].min()
    assert val["transaction_date"].max() < tr.VAL_END <= test["transaction_date"].min()
    assert split.train_rows + split.val_rows + split.test_rows == (df["transaction_date"] >= tr.WARMUP_END).sum()


def test_split_rejects_unsorted_input():
    df = build_features(_synthetic_raw(200)).iloc[::-1]
    with pytest.raises(ValueError):
        tr.chronological_split(df)


def test_expected_cost_counts_escalations_and_missed_fraud():
    y = np.array([True, True, False, False])
    score = np.array([90.0, 10.0, 50.0, 5.0])
    amount = np.array([100.0, 150.0, 20.0, 20.0])
    a = {"handle_seconds": 3600.0, "hourly_rate_usd": 2.0, "wrong_credit_ops_usd": 10.0}
    r = ev.expected_cost(y, score, amount, 30.0, a)
    # Two escalations at USD 2 each, one missed fraud: 150 + 10.
    assert r["total_cost_usd"] == pytest.approx(2 * 2.0 + 160.0)
    assert r["frauds_auto_credited"] == 1 and r["escalated"] == 2
    never = ev.expected_cost(y, score, amount, np.inf, a)
    assert never["total_cost_usd"] == pytest.approx(100 + 10 + 150 + 10)


def test_best_threshold_prefers_escalating_when_misses_are_expensive():
    y = np.array([False] * 98 + [True] * 2)
    score = np.concatenate([np.linspace(0, 30, 98), [80.0, 95.0]])
    amount = np.full(100, 150.0)
    a = {"handle_seconds": 425.0, "hourly_rate_usd": 10.0, "wrong_credit_ops_usd": 25.0}
    best = ev.best_threshold(y, score, amount, a)
    assert 30.0 < best["threshold"] <= 80.0
    assert best["frauds_auto_credited"] == 0 and best["escalated"] == 2


def test_end_to_end_train_evaluate_and_score(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "LR_GRID", [{"C": 1.0, "class_weight": None}])
    monkeypatch.setattr(tr, "HGB_GRID", [{"learning_rate": 0.1, "max_leaf_nodes": 15, "class_weight": None}])
    monkeypatch.setattr(ev, "N_BOOTSTRAP", 20)
    raw = _synthetic_raw()
    experiments = tmp_path / "experiments.jsonl"
    result = tr.train(build_features(raw), experiments_path=experiments)
    assert set(result["models"]) == {"logistic_ours", "hgb_ours", "logistic_stacked", "hgb_stacked"}
    # 2 baselines + 4 model fits logged.
    assert len(experiments.read_text().splitlines()) == 6

    predictions = result.pop("predictions")
    model_path = tmp_path / "fraud_model.joblib"
    joblib.dump(result, model_path)
    has_matplotlib = importlib.util.find_spec("matplotlib") is not None
    docs = tmp_path / "docs" if has_matplotlib else None
    report = ev.evaluate(tr.predictions_bundle(result, predictions), docs_dir=docs)
    if has_matplotlib:
        assert (tmp_path / "docs" / "fraud_pr_curve.png").exists()
    fraud_score_threshold = report["thresholds"]["scores"]["fraud_score"]
    assert fraud_score_threshold["cost_equivalent_lower_bound"] < fraud_score_threshold["threshold_chosen_on_val"]
    assert report["test_point_estimates"]["fraud_score"]["pr_auc"] > 0.5  # planted signal
    for name, ci in report["test_bootstrap_95ci"].items():
        assert ci["pr_auc"][0] <= ci["pr_auc"][1], name

    # The scoring path reproduces the evaluated test predictions, whatever the
    # input order, and needs neither the label nor transaction_status.
    unlabeled = raw.drop(columns=["is_fraud", "transaction_status"]).sample(frac=1, random_state=1)
    proba = pd.Series(tr.score_transactions(unlabeled, model_path), index=unlabeled["transaction_id"].to_numpy())
    test_pred = predictions["test"].set_index("transaction_id")[f"p_{result['selected']}"]
    np.testing.assert_allclose(proba.loc[test_pred.index].to_numpy(), test_pred.to_numpy(), rtol=1e-9)

    # Duplicated ids neither crash the reindex nor change any score.
    with_dupes = pd.concat([raw, raw.head(50)], ignore_index=True)
    proba_dupes = tr.score_transactions(with_dupes, model_path)
    assert proba_dupes.shape == (len(with_dupes),)
    np.testing.assert_allclose(proba_dupes[: len(raw)], tr.score_transactions(raw, model_path))


def test_dedupe_keeps_the_earliest_row_whatever_the_input_order():
    rows = _synthetic_raw(20)
    later_twin = rows.iloc[[3]].assign(transaction_date=rows.iloc[3]["transaction_date"] + pd.Timedelta(days=9),
                                       amount=1.0)
    for frame in (pd.concat([rows, later_twin]), pd.concat([later_twin, rows]).iloc[::-1]):
        kept = clean_transactions(frame).set_index("transaction_id")
        assert len(kept) == len(rows)
        assert kept.loc[rows.iloc[3]["transaction_id"], "transaction_date"] == rows.iloc[3]["transaction_date"]


def test_mirrored_policy_constants_match_app_policy():
    """etl/ never imports app/ (the offline pipeline stays independent of the
    runtime), so the evaluation mirrors three policy constants; this keeps
    them in sync."""
    from app import policy

    assert ev.AUTO_RESOLVE_MAX_AMOUNT_USD == policy.AUTO_RESOLVE_MAX_AMOUNT_USD
    assert ev.AUTO_RESOLVE_REQUIRED_STATUS == policy.AUTO_RESOLVE_REQUIRED_STATUS
    assert ev.CURRENT_POLICY_MAX_FRAUD_SCORE == policy.AUTO_RESOLVE_MAX_FRAUD_SCORE


def test_best_threshold_is_the_exact_minimum_over_every_distinct_score():
    rng = np.random.default_rng(3)
    y = rng.random(3000) < 0.02
    score = np.round(np.where(y, rng.uniform(10, 100, 3000), rng.uniform(0, 40, 3000)), 2)
    amount = rng.uniform(5, 200, 3000)
    a = {"handle_seconds": 425.0, "hourly_rate_usd": 10.0, "wrong_credit_ops_usd": 25.0}
    brute = min(
        (ev.expected_cost(y, score, amount, t, a) for t in [*np.unique(score), np.inf]),
        key=lambda r: (round(r["total_cost_usd"], 9), -r["threshold"]),
    )
    fast = ev.best_threshold(y, score, amount, a)
    assert fast["threshold"] == brute["threshold"]
    assert fast["total_cost_usd"] == pytest.approx(brute["total_cost_usd"])
