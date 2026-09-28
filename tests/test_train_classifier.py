"""Chronological split + reproducibility tests (Task 3.2)."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from etl.features import FEATURE_COLUMNS, TARGET_COLUMN, load_features
from etl.train_classifier import build_baseline, build_pipeline, chronological_split, train


@pytest.fixture()
def warehouse(tmp_path):
    db_path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE complaints (complaint_id VARCHAR, creation_date TIMESTAMP, customer_id VARCHAR, "
        "category VARCHAR, subcategory VARCHAR, claimed_amount DOUBLE, currency VARCHAR, "
        "reception_channel VARCHAR, affected_product_id VARCHAR, priority VARCHAR)"
    )
    con.execute("CREATE TABLE customers (customer_id VARCHAR, segment VARCHAR, credit_score DOUBLE)")
    con.execute("CREATE TABLE products (product_id VARCHAR, product_type VARCHAR)")

    priorities = ["Low", "Medium", "High", "Critical"]
    rows = []
    for i in range(40):
        rows.append(
            (
                f"CMP-{i:03d}", f"2024-01-{(i % 28) + 1:02d} 00:00:00", "CLI-1", "Transactions",
                "Cargo no reconocido", float(50 + i), "USD", "App", None, priorities[i % 4],
            )
        )
    con.executemany(
        "INSERT INTO complaints (complaint_id, creation_date, customer_id, category, subcategory, "
        "claimed_amount, currency, reception_channel, affected_product_id, priority) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', 700.0)")
    con.close()
    return db_path


def test_chronological_split_sizes_and_ordering(warehouse):
    df = load_features(warehouse)
    train_df, test_df, split = chronological_split(df)

    assert split.train_size + split.test_size == len(df)
    assert split.test_size == round(len(df) * 0.15) or split.test_size == int(len(df) * 0.15)
    assert train_df["creation_date"].max() <= test_df["creation_date"].min()


def test_chronological_split_rejects_unsorted_input():
    df = pd.DataFrame(
        {
            "creation_date": pd.to_datetime(["2024-01-02", "2024-01-01", "2024-01-03"]),
            "priority": ["Low", "Medium", "High"],
        }
    )
    with pytest.raises(ValueError, match="sorted"):
        chronological_split(df)


def test_training_is_deterministic_across_runs(warehouse):
    result1 = train(warehouse)
    result2 = train(warehouse)

    assert result1["proposed_pred"] == result2["proposed_pred"]
    assert result1["baseline_pred"] == result2["baseline_pred"]
    assert result1["split"] == result2["split"]


def test_baseline_always_predicts_the_single_most_frequent_training_class(warehouse):
    df = load_features(warehouse)
    train_df, test_df, _ = chronological_split(df)

    baseline = build_baseline()
    baseline.fit(train_df[list(FEATURE_COLUMNS)], train_df[TARGET_COLUMN])
    predictions = set(baseline.predict(test_df[list(FEATURE_COLUMNS)]))

    assert len(predictions) == 1
    assert next(iter(predictions)) == train_df[TARGET_COLUMN].mode().iloc[0]


def test_pipeline_handles_missing_claimed_amount_without_crashing(warehouse):
    """Real-data finding: claimed_amount is NULL in ~67% of real complaints
    — the pipeline must train and predict on rows with NaN features, not
    crash (this is exactly what SimpleImputer(add_indicator=True) is for).
    """
    df = load_features(warehouse)
    df.loc[df.index[:10], "claimed_amount"] = None
    train_df, test_df, _ = chronological_split(df)

    pipeline = build_pipeline()
    pipeline.fit(train_df[list(FEATURE_COLUMNS)], train_df[TARGET_COLUMN])
    predictions = pipeline.predict(test_df[list(FEATURE_COLUMNS)])

    assert len(predictions) == len(test_df)


def test_live_features_match_the_training_schema_and_produce_a_real_prediction(warehouse, tmp_path):
    """`app/classifier.py::predict_priority` swallows every failure into None,
    so a drift between the live feature frame and FEATURE_COLUMNS would
    silently disable the classifier. This fails loud instead.
    """
    import joblib

    from app.classifier import build_live_features, predict_priority
    from app.transactions import CustomerProfile

    live = build_live_features(
        CustomerProfile(customer_id="CLI-1", segment="Plus", credit_score=700.0, country="México",
                        customer_status="Active"),
        claimed_amount=100.0, currency="USD", prior_complaint_count=2,
    )
    assert list(live.columns) == list(FEATURE_COLUMNS)

    df = load_features(warehouse)
    train_df, _, _ = chronological_split(df)
    pipeline = build_pipeline()
    pipeline.fit(train_df[list(FEATURE_COLUMNS)], train_df[TARGET_COLUMN])
    model_path = tmp_path / "classifier.joblib"
    joblib.dump(pipeline, model_path)

    assert predict_priority(live, model_path=model_path) in set(train_df[TARGET_COLUMN])
