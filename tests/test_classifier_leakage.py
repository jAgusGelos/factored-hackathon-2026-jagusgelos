"""Leakage-safety tests for the priority-at-intake classifier (Task 3.1).

The single most safety-critical property of `etl/features.py`: the
`prior_complaint_count` feature must never count a complaint that occurs on
or after the complaint being scored — that would leak future information
into a feature computed "at intake time".
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import pytest

from etl.features import load_features


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
    con.close()
    return db_path


def _insert_complaint(db_path, **fields):
    defaults = dict(
        complaint_id="CMP-1", creation_date="2024-01-01 00:00:00", customer_id="CLI-1",
        category="Transactions", subcategory="Cargo no reconocido", claimed_amount=100.0,
        currency="USD", reception_channel="App", affected_product_id=None, priority="Medium",
    )
    defaults.update(fields)
    con = duckdb.connect(str(db_path))
    cols = list(defaults)
    con.execute(
        f"INSERT INTO complaints ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [defaults[c] for c in cols],
    )
    con.close()


def test_prior_complaint_count_excludes_same_day_later_complaints(warehouse):
    """Two complaints from the same customer on the same day, at different
    times: the later one may count the earlier one, never the reverse.
    """
    _insert_complaint(warehouse, complaint_id="CMP-EARLY", customer_id="CLI-1", creation_date="2024-01-01 08:00:00")
    _insert_complaint(warehouse, complaint_id="CMP-LATE", customer_id="CLI-1", creation_date="2024-01-01 17:00:00")
    con = duckdb.connect(str(warehouse))
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', 700.0)")
    con.close()

    df = load_features(warehouse)
    by_id = df.set_index("complaint_id")

    assert by_id.loc["CMP-EARLY", "prior_complaint_count"] == 0
    assert by_id.loc["CMP-LATE", "prior_complaint_count"] == 1


def test_prior_complaint_count_never_counts_a_future_complaint(warehouse):
    """The core invariant: for every complaint, its prior_complaint_count
    must equal the number of THAT SAME customer's complaints with an
    earlier creation_date — verified directly against an independent,
    non-SQL computation (not just "the query ran without error").
    """
    customer_id = "CLI-1"
    dates = [
        "2023-01-01 00:00:00", "2023-06-01 00:00:00", "2024-01-01 00:00:00",
        "2024-06-01 00:00:00", "2025-01-01 00:00:00",
    ]
    for i, d in enumerate(dates):
        _insert_complaint(warehouse, complaint_id=f"CMP-{i}", customer_id=customer_id, creation_date=d)
    con = duckdb.connect(str(warehouse))
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', 700.0)")
    con.close()

    df = load_features(warehouse)
    by_id = df.set_index("complaint_id")

    parsed_dates = [datetime.fromisoformat(d) for d in dates]
    for i, this_date in enumerate(parsed_dates):
        expected = sum(1 for d in parsed_dates if d < this_date)
        assert by_id.loc[f"CMP-{i}", "prior_complaint_count"] == expected, (
            f"CMP-{i}: expected {expected} strictly-prior complaints, "
            f"got {by_id.loc[f'CMP-{i}', 'prior_complaint_count']}"
        )


def test_prior_complaint_count_is_customer_scoped_not_global(warehouse):
    """A different customer's complaints must never count toward this
    customer's prior_complaint_count — this would both leak information
    across customers and inflate the abuse-guard-adjacent signal.
    """
    _insert_complaint(warehouse, complaint_id="CMP-OTHER", customer_id="CLI-OTHER", creation_date="2020-01-01 00:00:00")
    _insert_complaint(warehouse, complaint_id="CMP-MINE", customer_id="CLI-1", creation_date="2024-01-01 00:00:00")
    con = duckdb.connect(str(warehouse))
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', 700.0), ('CLI-OTHER', 'Basic', 600.0)")
    con.close()

    df = load_features(warehouse)
    by_id = df.set_index("complaint_id")

    assert by_id.loc["CMP-MINE", "prior_complaint_count"] == 0


def test_prior_complaint_count_includes_complaints_of_any_category(warehouse):
    """Recomputed count spans ANY category of prior complaint (not just
    'Transactions') per plan.md AD-6 — a customer's non-transaction
    complaint history is still a legitimate signal for this customer's
    overall complaint frequency.
    """
    _insert_complaint(warehouse, complaint_id="CMP-OTHER-CAT", customer_id="CLI-1",
                       category="Fees", creation_date="2023-01-01 00:00:00")
    _insert_complaint(warehouse, complaint_id="CMP-TXN", customer_id="CLI-1",
                       category="Transactions", creation_date="2024-01-01 00:00:00")
    con = duckdb.connect(str(warehouse))
    con.execute("INSERT INTO customers VALUES ('CLI-1', 'Plus', 700.0)")
    con.close()

    df = load_features(warehouse)
    by_id = df.set_index("complaint_id")

    # Only "Transactions"-category rows are RETURNED (the training pool), but
    # the "Fees" complaint must still have counted toward CMP-TXN's total.
    assert "CMP-OTHER-CAT" not in by_id.index
    assert by_id.loc["CMP-TXN", "prior_complaint_count"] == 1
