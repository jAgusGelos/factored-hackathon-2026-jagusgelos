"""Smoke test for the priority signal-ceiling analysis (etl/priority_signal_ceiling.py)."""

from __future__ import annotations

import duckdb
import pytest

from etl.priority_signal_ceiling import run


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
    con.executemany(
        "INSERT INTO complaints VALUES (?, ?, ?, 'Transactions', 'Cargo no reconocido', ?, 'USD', 'App', NULL, ?)",
        [(f"CMP-{i:03d}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d} 00:00:00", f"CLI-{i % 5}",
          float(50 + i), priorities[i % 4]) for i in range(80)],
    )
    con.executemany("INSERT INTO customers VALUES (?, 'Plus', 700.0)", [(f"CLI-{i}",) for i in range(5)])
    con.close()
    return db_path


def test_report_shape_and_bounds(warehouse):
    report = run(warehouse, n_permutations=3, n_mi_permutations=3)
    perm = report["permutation_test"]
    assert 0 < perm["p_value"] <= 1
    assert perm["n_permutations"] == 3
    assert 0 <= perm["real_macro_f1"] <= 1
    features = report["mutual_information"]["features"]
    assert "prior_complaint_count" in features
    assert all(v["mi_nats"] >= 0 for v in features.values())
