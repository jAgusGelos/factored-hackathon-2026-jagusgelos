"""Leakage-safety tests for the fraud-risk features (etl/fraud_features.py).

The property that matters most: every customer-history feature of a charge at
time t reads only that customer's transactions with transaction_date < t.
Each test compares the vectorized implementation against an independent
brute-force loop, including ties at the same timestamp and other customers'
rows interleaved in time.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from etl.fraud_features import FEATURE_COLUMNS, STACKED_FEATURE_COLUMNS, build_features


def _row(txn_id, when, customer="CLI-1", **fields):
    row = dict(
        transaction_id=txn_id, transaction_date=pd.Timestamp(when), customer_id=customer,
        transaction_type="Purchase", amount=100.0, currency="USD", amount_usd=math.nan,
        channel="POS", merchant_name="Shop A", merchant_category="Food",
        transaction_country="México", transaction_city="Monterrey", latitude=None,
        longitude=None, fraud_score=10.0, is_fraud=False, home_country="México",
        registration_date=pd.Timestamp("2020-01-01"),
    )
    row.update(fields)
    return row


def _brute_force(raw: pd.DataFrame) -> pd.DataFrame:
    """Recomputes the history features row by row from strictly earlier
    same-customer rows, with no shared code path with build_features."""
    out = {}
    for _, r in raw.iterrows():
        prior = raw[(raw.customer_id == r.customer_id) & (raw.transaction_date < r.transaction_date)]
        t = pd.Timestamp(r.transaction_date)
        last = prior.sort_values("transaction_date").iloc[-1] if len(prior) else None
        geo = prior.dropna(subset=["latitude", "longitude"]).sort_values("transaction_date")
        rate = {"USD": 1.0, "COP": 4000.0, "ARS": 350.0}[r.currency]
        usd = r.amount_usd if pd.notna(r.amount_usd) else r.amount / rate
        past_usd = [
            (p.amount_usd if pd.notna(p.amount_usd) else p.amount / {"USD": 1.0, "COP": 4000.0, "ARS": 350.0}[p.currency])
            for _, p in prior.iterrows()
        ]
        logs = np.log1p(past_usd)
        z = math.nan
        if len(logs) >= 2:
            z = (math.log1p(usd) - logs.mean()) / max(logs.std(), 0.25)
        out[r.transaction_id] = dict(
            n_prior_txns=float(len(prior)),
            hours_since_prev_txn=(t - last.transaction_date).total_seconds() / 3600 if last is not None else math.nan,
            txns_last_1h=float(((t - prior.transaction_date).dt.total_seconds() <= 3600).sum()),
            txns_last_24h=float(((t - prior.transaction_date).dt.total_seconds() <= 86400).sum()),
            amount_zscore=z,
            is_new_merchant=float(r.merchant_name not in set(prior.merchant_name.dropna())) if pd.notna(r.merchant_name) else math.nan,
            is_new_city=float(r.transaction_city not in set(prior.transaction_city.dropna())),
            is_new_country=float(r.transaction_country not in set(prior.transaction_country.dropna())),
            prev_geo_hours=(t - geo.iloc[-1].transaction_date).total_seconds() / 3600 if len(geo) else math.nan,
        )
    return pd.DataFrame.from_dict(out, orient="index")


@pytest.fixture()
def raw():
    rows = [
        _row("T1", "2025-01-01 08:00:00", amount=50.0, latitude=19.43, longitude=-99.13),
        # T2 and T3 share a timestamp: neither may see the other.
        _row("T2", "2025-01-01 09:00:00", amount=60.0, merchant_name="Shop B"),
        _row("T3", "2025-01-01 09:00:00", amount=70.0, merchant_name="Shop B", latitude=25.68, longitude=-100.31),
        # Another customer interleaved in time, with the same merchant.
        _row("O1", "2025-01-01 08:30:00", customer="CLI-2", merchant_name="Shop C"),
        _row("T4", "2025-01-01 09:30:00", amount=5000.0, currency="COP", amount_usd=1.25,
             merchant_name="Shop C", transaction_city="Cancún"),
        _row("T5", "2025-01-02 10:00:00", amount=80.0, merchant_name=None, latitude=20.97, longitude=-89.62),
        _row("T6", "2025-01-02 10:00:00", amount=90.0, merchant_name="Shop A"),
        _row("O2", "2025-01-02 10:00:00", customer="CLI-2", merchant_name="Shop A"),
        # Sub-second order: S2 is strictly after S1 within the same second.
        _row("S1", "2025-01-03 08:00:00.200", customer="CLI-3", merchant_name="Shop S"),
        _row("S2", "2025-01-03 08:00:00.700", customer="CLI-3", merchant_name="Shop S",
             transaction_country="Colombia"),
    ]
    return pd.DataFrame(rows)


def test_history_features_match_brute_force_strictly_prior(raw):
    feats = build_features(raw).set_index("transaction_id")
    expected = _brute_force(raw)
    for col in ("n_prior_txns", "hours_since_prev_txn", "txns_last_1h", "txns_last_24h",
                "amount_zscore", "is_new_merchant", "is_new_city", "is_new_country"):
        for txn_id in expected.index:
            got, want = feats.loc[txn_id, col], expected.loc[txn_id, col]
            if pd.isna(want):
                assert pd.isna(got), f"{txn_id}.{col}: expected NaN, got {got}"
            else:
                assert got == pytest.approx(want, rel=1e-9, abs=1e-9), f"{txn_id}.{col}: {got} != {want}"


def test_tied_timestamps_never_see_each_other(raw):
    feats = build_features(raw).set_index("transaction_id")
    assert feats.loc["T2", "n_prior_txns"] == feats.loc["T3", "n_prior_txns"] == 1
    # Shop B is new for both tied rows: neither counts the other as history.
    assert feats.loc["T2", "is_new_merchant"] == feats.loc["T3", "is_new_merchant"] == 1.0
    # T3's own coordinates are not its "previous" location; T1's are.
    assert feats.loc["T3", "km_from_prev_geo"] > 0


def test_other_customers_never_count(raw):
    feats = build_features(raw).set_index("transaction_id")
    # CLI-2 bought at Shop C before T4, but that is another customer's history.
    assert feats.loc["T4", "is_new_merchant"] == 1.0
    assert feats.loc["O1", "n_prior_txns"] == 0
    assert feats.loc["O2", "n_prior_txns"] == 1


def test_previous_geolocated_transaction_distance_and_speed(raw):
    feats = build_features(raw).set_index("transaction_id")
    expected = _brute_force(raw)
    # T5 is geolocated; its previous geolocated row is T3 (Monterrey), 25 h earlier.
    km = feats.loc["T5", "km_from_prev_geo"]
    assert 1150 < km < 1260  # Monterrey to Mérida great-circle, about 1,210 km
    assert feats.loc["T5", "kmh_from_prev_geo"] == pytest.approx(km / expected.loc["T5", "prev_geo_hours"])
    # T1 has no earlier geolocated row.
    assert pd.isna(feats.loc["T1", "km_from_prev_geo"])


def test_future_rows_do_not_change_past_features(raw):
    """Appending later transactions must leave every earlier row's features
    unchanged: the definition of "uses only the past"."""
    before = build_features(raw).set_index("transaction_id")
    later = pd.DataFrame([
        *raw.to_dict("records"),
        _row("T7", "2025-01-03 00:00:00", amount=99999.0, merchant_name="Shop Z", is_fraud=True),
    ])
    after = build_features(later).set_index("transaction_id")
    cols = list(FEATURE_COLUMNS)
    pd.testing.assert_frame_equal(before[cols], after.loc[before.index, cols])


def test_baseline_score_and_label_are_not_features():
    assert "fraud_score" not in FEATURE_COLUMNS
    assert "is_fraud" not in STACKED_FEATURE_COLUMNS
    for leaky in ("transaction_status", "response_code", "process_date"):
        assert leaky not in STACKED_FEATURE_COLUMNS


def test_output_is_chronological(raw):
    feats = build_features(raw)
    assert feats["transaction_date"].is_monotonic_increasing
