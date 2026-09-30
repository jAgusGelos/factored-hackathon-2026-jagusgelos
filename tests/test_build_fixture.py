"""Tests for the Milestone 8 demo-fixture generator (etl/build_fixture.py),
against a tiny in-memory stand-in for the warehouse.
"""

from datetime import date

import duckdb
import pytest

from etl.build_fixture import (
    DEMO_USERNAME,
    OVER_LIMIT_CHARGE_ID,
    SYNTHETIC_CHARGES,
    build_fixture_db,
    select_demo_customer,
    write_demo_users,
)


@pytest.fixture()
def warehouse():
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE customers (customer_id VARCHAR, country VARCHAR, customer_status VARCHAR, "
        "segment VARCHAR, email VARCHAR)"
    )
    c.execute("CREATE TABLE products (product_id VARCHAR, customer_id VARCHAR, product_type VARCHAR)")
    c.execute(
        "CREATE TABLE complaints (complaint_id VARCHAR, customer_id VARCHAR, category VARCHAR, "
        "creation_date TIMESTAMP)"
    )
    c.execute(
        """
        CREATE TABLE transactions (
            transaction_id VARCHAR, transaction_date TIMESTAMP, product_id VARCHAR,
            customer_id VARCHAR, transaction_type VARCHAR, transaction_category VARCHAR,
            amount DOUBLE, currency VARCHAR, amount_usd DOUBLE, channel VARCHAR,
            merchant_name VARCHAR, merchant_category VARCHAR, transaction_country VARCHAR,
            transaction_city VARCHAR, transaction_status VARCHAR, response_code VARCHAR,
            is_fraud BOOLEAN, fraud_score DOUBLE, _source_file VARCHAR
        )
        """
    )
    c.execute(
        "CREATE TABLE daily_exchange_rates "
        "(date DATE, source_currency VARCHAR, target_currency VARCHAR, exchange_rate DOUBLE)"
    )
    c.execute("INSERT INTO daily_exchange_rates VALUES ('2026-01-01', 'COP', 'USD', 0.00025)")
    yield c
    c.close()


def _customer(con, customer_id, *, country="Colombia", status="Active"):
    con.execute(
        "INSERT INTO customers VALUES (?, ?, ?, 'Basic', 'x@example.com')", [customer_id, country, status]
    )
    con.execute("INSERT INTO products VALUES (?, ?, 'Credit Card')", [f"PRD-{customer_id}", customer_id])


def _purchase(con, txn_id, customer_id, *, merchant="Uber", currency="COP", day="2026-06-01"):
    con.execute(
        "INSERT INTO transactions (transaction_id, transaction_date, product_id, customer_id, "
        "transaction_type, amount, currency, merchant_name, transaction_city, transaction_status, "
        "fraud_score) VALUES (?, ?, ?, ?, 'Purchase', 1000, ?, ?, 'Cartagena', 'Approved', 5)",
        [txn_id, day, f"PRD-{customer_id}", customer_id, currency, merchant],
    )


def test_selects_the_active_colombian_customer_with_most_named_purchases(warehouse):
    _customer(warehouse, "CLI-A")
    _customer(warehouse, "CLI-B")
    for i in range(2):
        _purchase(warehouse, f"A{i}", "CLI-A")
    for i in range(3):
        _purchase(warehouse, f"B{i}", "CLI-B")
    assert select_demo_customer(warehouse) == "CLI-B"


def test_skips_inactive_foreign_currency_and_prior_disputers(warehouse):
    _customer(warehouse, "CLI-INACTIVE", status="Inactive")
    _customer(warehouse, "CLI-MX", country="México")
    _customer(warehouse, "CLI-DISPUTER")
    _customer(warehouse, "CLI-OK")
    for cid in ("CLI-INACTIVE", "CLI-MX", "CLI-DISPUTER"):
        for i in range(5):
            _purchase(warehouse, f"{cid}-{i}", cid, currency="USD" if cid == "CLI-MX" else "COP")
    warehouse.execute(
        "INSERT INTO complaints VALUES ('CMP-1', 'CLI-DISPUTER', 'Transactions', '2026-05-01')"
    )
    _purchase(warehouse, "OK-1", "CLI-OK")
    assert select_demo_customer(warehouse) == "CLI-OK"


def test_ties_break_by_customer_id(warehouse):
    for cid in ("CLI-Z", "CLI-M"):
        _customer(warehouse, cid)
        _purchase(warehouse, f"{cid}-1", cid)
    assert select_demo_customer(warehouse) == "CLI-M"


def test_no_eligible_customer_raises(warehouse):
    with pytest.raises(RuntimeError):
        select_demo_customer(warehouse)


def test_fixture_keeps_only_the_demo_customer_and_labels_synthetic_rows(warehouse, tmp_path):
    _customer(warehouse, "CLI-DEMO")
    _customer(warehouse, "CLI-OTHER")
    _purchase(warehouse, "REAL-1", "CLI-DEMO")
    _purchase(warehouse, "OTHER-1", "CLI-OTHER")
    fixture = tmp_path / "fixture.duckdb"

    build_fixture_db(warehouse, fixture, "CLI-DEMO")

    f = duckdb.connect(str(fixture), read_only=True)
    try:
        assert f.execute("SELECT DISTINCT customer_id FROM customers").fetchall() == [("CLI-DEMO",)]
        assert f.execute("SELECT DISTINCT customer_id FROM transactions").fetchall() == [("CLI-DEMO",)]
        labels = dict(f.execute("SELECT transaction_id, _is_synthetic FROM transactions").fetchall())
        rows = f.execute(
            "SELECT transaction_id, CAST(amount_usd AS DOUBLE), _source_file FROM transactions "
            "WHERE _is_synthetic = 'True'"
        ).fetchall()
    finally:
        f.close()
    assert labels["REAL-1"] == "False"
    assert {r[0] for r in rows} == {c.transaction_id for c in SYNTHETIC_CHARGES}
    assert all(r[2] == "synthetic" for r in rows)
    boutique = next(r for r in rows if r[0] == OVER_LIMIT_CHARGE_ID)
    assert boutique[1] == pytest.approx(2_450_000 * 0.00025)


def test_synthetic_charges_cover_every_demo_scenario():
    scenarios = {c.scenario for c in SYNTHETIC_CHARGES}
    assert {"auto_resolve", "duplicate_pair", "escalate_fraud_score", "escalate_amount"} <= scenarios
    pair = [c for c in SYNTHETIC_CHARGES if c.scenario == "duplicate_pair"]
    assert len(pair) == 2 and pair[0].amount == pair[1].amount
    assert abs((pair[0].day - pair[1].day).days) <= 3
    assert all(date(2026, 5, 18) <= c.day <= date(2026, 6, 17) for c in SYNTHETIC_CHARGES)


def test_demo_users_has_a_single_account(tmp_path):
    path = tmp_path / "demo_users.json"
    users = write_demo_users("CLI-DEMO", path)
    assert list(users) == [DEMO_USERNAME]
    assert users[DEMO_USERNAME]["customer_id"] == "CLI-DEMO"
    assert users[DEMO_USERNAME]["password"].startswith("demo-")
