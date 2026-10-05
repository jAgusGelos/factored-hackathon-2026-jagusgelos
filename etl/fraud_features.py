"""Leakage-safe, customer-relative features for the per-charge fraud-risk model.

Predicts `transactions.is_fraud` using ONLY what is knowable when the charge is
authorized: the charge's own attributes plus the SAME customer's strictly
earlier transactions. `fraud_score` (the bank's vendor score) is the baseline
the model is measured against, never one of "our" features; the stacked
variant adds it back explicitly (`STACKED_FEATURE_COLUMNS`).

## Feature-availability table (each column justified as known at authorization)

| Column | Use | Known at authorization? |
|---|---|---|
| transaction_date | features (hour, history windows) + split key | Yes, the authorization timestamp |
| amount, currency, amount_usd | `log_amount_usd` | Yes, part of the authorization request. `amount_usd` is NULL for every USD row, so it is recomputed (`_amount_usd`) |
| transaction_type, channel | categorical features | Yes, part of the request |
| merchant_name | `is_new_merchant` (history only, never the raw name) | Yes |
| merchant_category | categorical feature | Yes |
| transaction_country, transaction_city | `is_new_country`, `is_new_city`, `is_foreign_country` | Yes, terminal location |
| latitude, longitude | `km_from_prev_geo`, `kmh_from_prev_geo` | Yes, terminal location (present for ~20% of rows, card-present channels only) |
| customers.country | `is_foreign_country` | Yes, home country set at onboarding |
| customers.registration_date | `customer_tenure_days` | Yes, immutable onboarding date |
| fraud_score | BASELINE; stacked variant only | Yes, the vendor scores at authorization, but it is the thing we measure against |

## Explicitly EXCLUDED

| Column | Why |
|---|---|
| is_fraud | The label. It is set after investigation, never at authorization |
| transaction_status | The OUTCOME of the authorization decision (Approved / Declined) or a later event (Reversed, Pending settlement). In the 30-day window the fraud rate is flat across statuses (0.077% to 0.106%), so it carries no signal either way, but "Reversed" can be caused by the fraud finding itself, so it is post-outcome by construction |
| response_code | The issuer's reply to this same authorization (00 approved, 51 insufficient funds, 05 do-not-honor, ...). It is produced BY the decision the model is meant to inform, so using it would be circular |
| process_date | Posting date, set after authorization (settlement) |
| branch_id | An identifier with ~350 levels and no history meaning; present only for ATM/Branch rows |
| product_id, transaction_id, customer_id | Identifiers. Used to compute history, never as features (a model that memorizes ids does not generalize to new customers) |
| transaction_category | Duplicates merchant_category (same vocabulary) with more nulls |
| customers.segment, credit_score, customer_status, estimated_monthly_income | Snapshots as of the extraction, not as of the charge. `customer_status` can flip to Blocked BECAUSE of a fraud, so a snapshot can leak the label |
| filename, day, month, year, _source_file | Lineage columns |

## History features: strictly earlier transactions only

Every customer-history feature for a charge at time `t` reads ONLY the same
customer's transactions with `transaction_date < t`. Transactions sharing the
exact same timestamp never see each other. The implementation sorts by
(customer, time) and uses binary search on a (customer, time) key
(`_strictly_prior_index`) rather than `shift(1)`, which would let a tied row
read its twin. `tests/test_fraud_features_leakage.py` checks every history
feature against an independent brute-force computation.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRAUD_WAREHOUSE_PATH = REPO_ROOT / "data" / "fraud_warehouse.duckdb"
DEFAULT_CUSTOMERS_WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"

TARGET_COLUMN = "is_fraud"
BASELINE_SCORE_COLUMN = "fraud_score"

# The dataset converts at a fixed rate: amount / amount_usd is 4000.0 for every
# COP row and 350.0 for every ARS row that has amount_usd (std < 0.2 over the
# 30-day window), and amount_usd is NULL for every USD row.
FIXED_USD_RATES = {"USD": 1.0, "COP": 4000.0, "ARS": 350.0}

# "Mexico" (no accent) appears next to "México" in transaction_country.
COUNTRY_ALIASES = {"Mexico": "México"}

NUMERIC_FEATURES = (
    "log_amount_usd",
    "hour",
    "customer_tenure_days",
    "n_prior_txns",
    "hours_since_prev_txn",
    "txns_last_1h",
    "txns_last_24h",
    "amount_zscore",
    "km_from_prev_geo",
    "kmh_from_prev_geo",
    "is_new_merchant",
    "is_new_city",
    "is_new_country",
    "is_foreign_country",
)
CATEGORICAL_FEATURES = ("transaction_type", "channel", "merchant_category", "currency")
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
STACKED_FEATURE_COLUMNS = FEATURE_COLUMNS + (BASELINE_SCORE_COLUMN,)

RAW_COLUMNS = (
    "transaction_id",
    "transaction_date",
    "customer_id",
    "transaction_type",
    "amount",
    "currency",
    "amount_usd",
    "channel",
    "merchant_name",
    "merchant_category",
    "transaction_country",
    "transaction_city",
    "latitude",
    "longitude",
    "fraud_score",
    "is_fraud",
    # Loaded for the evaluation only (the cost model's auto-credit population
    # is Approved charges), never a feature: see the exclusion table.
    "transaction_status",
)

_SECONDS_PER_HOUR = 3600.0
_EARTH_RADIUS_KM = 6371.0
# Minimum elapsed time for the implied speed, so two geolocated charges seconds
# apart do not divide by ~0 (one minute).
_MIN_HOURS_FOR_SPEED = 1.0 / 60.0
# Floor on the spread of a customer's past log-amounts in `amount_zscore`.
_MIN_LOG_AMOUNT_STD = 0.25


def load_transactions(
    warehouse_path: Path = DEFAULT_FRAUD_WAREHOUSE_PATH,
    customers_warehouse_path: Path | None = DEFAULT_CUSTOMERS_WAREHOUSE_PATH,
) -> pd.DataFrame:
    """Labeled transactions plus the two onboarding attributes used as
    features, one row per `transaction_id` (exact duplicates dropped).
    `customers` lives in the main warehouse, attached read-only.
    """
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        cols = ", ".join(f"t.{c}" for c in RAW_COLUMNS)
        if customers_warehouse_path is not None:
            con.execute(f"ATTACH '{customers_warehouse_path}' AS cw (READ_ONLY)")
            customers = "cw.customers"
        else:
            customers = "customers"
        df = con.execute(
            f"""
            SELECT {cols}, cu.country AS home_country, cu.registration_date
            FROM (SELECT * FROM transactions QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY transaction_id ORDER BY transaction_date) = 1) t
            LEFT JOIN {customers} cu ON cu.customer_id = t.customer_id
            WHERE t.is_fraud IS NOT NULL AND t.transaction_date IS NOT NULL AND t.customer_id IS NOT NULL
            """
        ).df()
    finally:
        con.close()
    return df


def _amount_usd(df: pd.DataFrame) -> pd.Series:
    rate = df["currency"].map(FIXED_USD_RATES)
    return df["amount_usd"].fillna(df["amount"] / rate)


def _haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _sort_key(customer_codes: np.ndarray, seconds: np.ndarray) -> np.ndarray:
    # Customers occupy disjoint key ranges, so one binary search over the key
    # never crosses from one customer into another.
    return customer_codes.astype(np.int64) * np.int64(10**11) + seconds


def _strictly_prior_index(key: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """For each row, the index of the same customer's LAST row with an
    earlier timestamp, or -1. `key` must be sorted; `starts[i]` is the index
    of row i's customer's first row.
    """
    first_at_or_after = np.searchsorted(key, key, side="left")
    prior = first_at_or_after - 1
    return np.where(prior >= starts, prior, -1)


def _take(values: np.ndarray, idx: np.ndarray) -> np.ndarray:
    out = values[np.clip(idx, 0, None)].astype(float)
    out[idx < 0] = np.nan
    return out


def _last_valid_index(valid: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """For each row, the index of the latest row at or before it (same
    customer) where `valid` is True, or -1.
    """
    positions = np.where(valid, np.arange(len(valid)), -1)
    last = np.maximum.accumulate(positions)
    return np.where(last >= starts, last, -1)


def _is_first_seen(df: pd.DataFrame, column: str) -> pd.Series:
    """1.0 when this customer had NO strictly earlier transaction with the
    same value of `column`, 0.0 when they had one, NaN when the value is
    missing. Ties at the first timestamp all count as first seen.
    """
    first_seen = df.groupby(["customer_id", column], dropna=True, sort=False)[
        "transaction_date"
    ].transform("min")
    out = (df["transaction_date"] <= first_seen).astype(float)
    return out.where(df[column].notna())


def _prior_sum(cumulative: np.ndarray, prior: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Sum of the per-row values behind `cumulative` over the same customer's
    rows up to `prior` (inclusive), 0 when there is no prior row."""
    base = np.where(starts > 0, cumulative[np.clip(starts - 1, 0, None)], 0.0)
    return np.where(prior >= 0, _take(cumulative, prior) - base, 0.0)


def _amount_zscore(log_amount: np.ndarray, prior: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Z-score of each log-amount vs the customer's strictly earlier
    log-amounts (NaN with fewer than two of them)."""
    has_amount = ~np.isnan(log_amount)
    prior_n = _prior_sum(np.cumsum(has_amount), prior, starts)
    prior_s = _prior_sum(np.cumsum(np.where(has_amount, log_amount, 0.0)), prior, starts)
    prior_q = _prior_sum(np.cumsum(np.where(has_amount, log_amount**2, 0.0)), prior, starts)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = prior_s / prior_n
        var = np.maximum(prior_q / prior_n - mean**2, 0.0)
        # A floor on the spread keeps a customer with identical past amounts
        # from producing an infinite z-score.
        z = (log_amount - mean) / np.maximum(np.sqrt(var), _MIN_LOG_AMOUNT_STD)
    return np.where(prior_n >= 2, z, np.nan)


def _prev_geo_distance_and_speed(
    lat: np.ndarray, lon: np.ndarray, seconds: np.ndarray, prior: np.ndarray, starts: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Distance (km) and implied speed (km/h) from the same customer's
    previous geolocated transaction among the strictly earlier ones."""
    geo_valid = ~np.isnan(lat) & ~np.isnan(lon)
    last_geo = _last_valid_index(geo_valid, starts)
    prev_geo = np.where(prior >= 0, last_geo[np.clip(prior, 0, None)], -1)
    km = _haversine_km(lat, lon, _take(lat, prev_geo), _take(lon, prev_geo))
    hours = (seconds - _take(seconds, prev_geo)) / _SECONDS_PER_HOUR
    return km, km / np.maximum(hours, _MIN_HOURS_FOR_SPEED)


def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    """Adds every model feature to a copy of `raw` and returns it sorted
    chronologically by (transaction_date, transaction_id), the order the
    chronological split needs. Pure function: no I/O.
    """
    df = raw.copy()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["transaction_country"] = df["transaction_country"].replace(COUNTRY_ALIASES)
    df["amount_usd_filled"] = _amount_usd(df)
    df = df.sort_values(["customer_id", "transaction_date", "transaction_id"], kind="mergesort")
    df = df.reset_index(drop=True)

    codes, _ = pd.factorize(df["customer_id"], sort=False)
    seconds = df["transaction_date"].to_numpy().astype("datetime64[s]").astype(np.int64)
    key = _sort_key(codes, seconds)
    n = len(df)
    is_start = np.ones(n, dtype=bool)
    is_start[1:] = codes[1:] != codes[:-1]
    starts = np.maximum.accumulate(np.where(is_start, np.arange(n), 0))

    prior = _strictly_prior_index(key, starts)
    # Index of the first row of this row's tie block: everything before it
    # (same customer) is strictly earlier.
    first_at_or_after = prior + 1
    first_at_or_after[prior < 0] = starts[prior < 0]

    df["n_prior_txns"] = (first_at_or_after - starts).astype(float)
    df["hours_since_prev_txn"] = (seconds - _take(seconds, prior)) / _SECONDS_PER_HOUR
    for label, window_seconds in (("txns_last_1h", 3600), ("txns_last_24h", 86400)):
        window_start = np.searchsorted(key, key - window_seconds, side="left")
        df[label] = (first_at_or_after - np.maximum(window_start, starts)).astype(float)

    log_amount = np.log1p(df["amount_usd_filled"].clip(lower=0).to_numpy())
    df["amount_zscore"] = _amount_zscore(log_amount, prior, starts)
    df["log_amount_usd"] = log_amount

    km, kmh = _prev_geo_distance_and_speed(
        df["latitude"].to_numpy(dtype=float), df["longitude"].to_numpy(dtype=float), seconds, prior, starts
    )
    df["km_from_prev_geo"] = km
    df["kmh_from_prev_geo"] = kmh

    df["is_new_merchant"] = _is_first_seen(df, "merchant_name")
    df["is_new_city"] = _is_first_seen(df, "transaction_city")
    df["is_new_country"] = _is_first_seen(df, "transaction_country")
    df["is_foreign_country"] = (df["transaction_country"] != df["home_country"]).astype(float).where(
        df["transaction_country"].notna() & df["home_country"].notna()
    )

    df["hour"] = df["transaction_date"].dt.hour.astype(float)
    registration = pd.to_datetime(df["registration_date"])
    df["customer_tenure_days"] = (df["transaction_date"] - registration).dt.days.astype(float)
    for col in CATEGORICAL_FEATURES:
        df[col] = df[col].fillna("missing").astype(str)
    df[TARGET_COLUMN] = df[TARGET_COLUMN].astype(bool)

    return df.sort_values(["transaction_date", "transaction_id"], kind="mergesort").reset_index(drop=True)


def load_features(
    warehouse_path: Path = DEFAULT_FRAUD_WAREHOUSE_PATH,
    customers_warehouse_path: Path | None = DEFAULT_CUSTOMERS_WAREHOUSE_PATH,
) -> pd.DataFrame:
    return build_features(load_transactions(warehouse_path, customers_warehouse_path))


def profile_warehouse(warehouse_path: Path = DEFAULT_FRAUD_WAREHOUSE_PATH) -> dict:
    """Quality and label profile of the extracted `transactions`: duplicates,
    null rates of the columns the model reads, label rate per month and the
    shape of fraud_score per class. Feeds the model card's data section."""
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        def one(sql: str):
            return con.execute(sql).fetchone()

        rows, distinct_ids, start, end = one(
            "SELECT COUNT(*), COUNT(DISTINCT transaction_id), MIN(transaction_date), MAX(transaction_date) "
            "FROM transactions"
        )
        nulls = {
            col: round(one(f'SELECT AVG(("{col}" IS NULL)::INT) FROM transactions')[0], 4)
            for col in RAW_COLUMNS
        }
        monthly = con.execute(
            "SELECT strftime(transaction_date, '%Y-%m') AS month, COUNT(*) AS rows, "
            "SUM(is_fraud::INT) AS frauds, ROUND(AVG(is_fraud::INT) * 100, 4) AS fraud_pct "
            "FROM transactions GROUP BY 1 ORDER BY 1"
        ).df()
        score_by_class = con.execute(
            "SELECT is_fraud, COUNT(*) AS rows, COUNT(fraud_score) AS with_score, "
            "MIN(fraud_score) AS min, MEDIAN(fraud_score) AS median, MAX(fraud_score) AS max, "
            "SUM((fraud_score > 30)::INT) AS above_30, SUM((fraud_score >= 30)::INT) AS at_or_above_30 "
            "FROM transactions GROUP BY 1 ORDER BY 1"
        ).df()
        status = con.execute(
            "SELECT transaction_status, COUNT(*) AS rows, SUM(is_fraud::INT) AS frauds, "
            "ROUND(AVG(is_fraud::INT) * 100, 4) AS fraud_pct FROM transactions GROUP BY 1 ORDER BY 2 DESC"
        ).df()
        response = con.execute(
            "SELECT response_code, COUNT(*) AS rows, SUM(is_fraud::INT) AS frauds, "
            "ROUND(AVG(is_fraud::INT) * 100, 4) AS fraud_pct FROM transactions GROUP BY 1 ORDER BY 2 DESC"
        ).df()
    finally:
        con.close()
    return {
        "rows": int(rows), "duplicate_transaction_ids": int(rows - distinct_ids),
        "first_transaction": str(start), "last_transaction": str(end),
        "null_rates": nulls,
        "label_rate_by_month": monthly.to_dict("records"),
        "fraud_score_by_class": score_by_class.to_dict("records"),
        "fraud_rate_by_status": status.to_dict("records"),
        "fraud_rate_by_response_code": response.to_dict("records"),
    }
