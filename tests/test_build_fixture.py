"""Tests for the Milestone 8 demo-fixture generator (etl/build_fixture.py),
against a tiny in-memory stand-in for the warehouse.
"""

from datetime import date

import duckdb
import pytest

from app.policy import DUPLICATE_WINDOW_MINUTES
from etl import build_fixture
from etl.build_fixture import (
    DEMO_USERNAME,
    FRAUD_SCORE_CHARGE_ID,
    OVER_LIMIT_CHARGE_ID,
    SYNTHETIC_CHARGES,
    FraudModel,
    build_fixture_db,
    select_demo_customer,
    write_demo_users,
)
from tests.support import REAL_FIXTURE_PATH, requires_real_fixture


@pytest.fixture()
def warehouse():
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE customers (customer_id VARCHAR, country VARCHAR, customer_status VARCHAR, "
        "segment VARCHAR, email VARCHAR, registration_date DATE)"
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
            is_fraud BOOLEAN, fraud_score DOUBLE, latitude DOUBLE, longitude DOUBLE, _source_file VARCHAR
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
        "INSERT INTO customers VALUES (?, ?, ?, 'Basic', 'x@example.com', '2020-01-01')", [customer_id, country, status]
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
    # AD-14: a real double charge, minutes apart on the same day.
    gap_minutes = abs((pair[0].posted_at - pair[1].posted_at).total_seconds()) / 60
    assert pair[0].posted_at.date() == pair[1].posted_at.date()
    assert gap_minutes <= DUPLICATE_WINDOW_MINUTES
    # The same fare on consecutive days: two purchases, outside the window.
    repeat = [c for c in SYNTHETIC_CHARGES if c.scenario == "repeat_purchase"]
    assert len(repeat) == 2 and repeat[0].amount == repeat[1].amount
    assert repeat[0].merchant_name == repeat[1].merchant_name
    assert (repeat[1].posted_at.date() - repeat[0].posted_at.date()).days == 1
    assert all(date(2026, 5, 18) <= c.posted_at.date() <= date(2026, 6, 17) for c in SYNTHETIC_CHARGES)


def test_demo_users_has_a_single_account(tmp_path):
    path = tmp_path / "demo_users.json"
    users = write_demo_users("CLI-DEMO", path)
    assert list(users) == [DEMO_USERNAME]
    assert users[DEMO_USERNAME]["customer_id"] == "CLI-DEMO"
    assert users[DEMO_USERNAME]["password"].startswith("demo-")


def test_every_fixture_charge_carries_the_model_estimate_scored_offline(warehouse, tmp_path, monkeypatch):
    """The model scores every fixture charge, synthetic ones included, with
    the customer's earlier transactions as history and without the label or
    the authorization outcome; the fixture stores what it returned.
    """
    _customer(warehouse, "CLI-DEMO")
    _purchase(warehouse, "REAL-1", "CLI-DEMO")
    _purchase(warehouse, "OLD-USD", "CLI-DEMO", currency="USD", day="2026-05-20")
    seen = {}

    def fake_score(frame, model_path):
        seen["frame"] = frame
        return frame["fraud_score"].to_numpy() / 100.0

    monkeypatch.setattr(build_fixture, "score_transactions", fake_score)
    fixture = tmp_path / "fixture.duckdb"
    model = FraudModel(model_path=tmp_path / "unused.joblib", version="logistic_stacked-test", threshold=0.0035)

    build_fixture_db(warehouse, fixture, "CLI-DEMO", model, history_warehouse=None)

    frame = seen["frame"]
    assert "is_fraud" not in frame and "transaction_status" not in frame
    assert {"OLD-USD", "REAL-1", FRAUD_SCORE_CHARGE_ID} <= set(frame["transaction_id"])
    assert frame["transaction_id"].is_unique
    f = duckdb.connect(str(fixture), read_only=True)
    try:
        rows = f.execute(
            "SELECT transaction_id, CAST(fraud_risk AS DOUBLE), CAST(fraud_risk_threshold AS DOUBLE), "
            "fraud_model_version FROM transactions"
        ).fetchall()
    finally:
        f.close()
    by_id = {r[0]: r[1:] for r in rows}
    assert "OLD-USD" not in by_id and len(by_id) == 1 + len(SYNTHETIC_CHARGES)
    assert by_id[FRAUD_SCORE_CHARGE_ID] == (pytest.approx(0.91), 0.0035, "logistic_stacked-test")
    assert all(r[0] is not None for r in by_id.values())


def test_the_model_and_its_report_must_come_from_the_same_run(tmp_path, monkeypatch):
    monkeypatch.setattr(build_fixture, "load_model_bundle", lambda path: {"selected": "m", "run_group": "A"})
    report = tmp_path / "report.json"
    report.write_text('{"run_group": "B", "thresholds": {"scores": {"m": {"threshold_chosen_on_val": 0.1}}}}')
    (tmp_path / "model.joblib").touch()
    with pytest.raises(RuntimeError, match="does not describe"):
        FraudModel.load(tmp_path / "model.joblib", report)


@requires_real_fixture
def test_the_real_fixture_scenarios_hold_under_the_model_estimate():
    """AD-15: the synthetic charges get their risk from the model, not a
    hard-coded value. The online fraud scenario lands above the model's cost
    threshold and every charge built to auto-resolve lands below it.
    """
    f = duckdb.connect(str(REAL_FIXTURE_PATH), read_only=True)
    try:
        rows = f.execute(
            "SELECT transaction_id, CAST(fraud_risk AS DOUBLE), CAST(fraud_risk_threshold AS DOUBLE), "
            "fraud_model_version FROM transactions"
        ).fetchall()
    finally:
        f.close()
    by_id = {r[0]: r[1:] for r in rows}
    assert all(risk is not None and version for risk, _, version in by_id.values())
    risk, threshold, _ = by_id[FRAUD_SCORE_CHARGE_ID]
    assert risk > threshold
    for charge in SYNTHETIC_CHARGES:
        if charge.scenario in ("auto_resolve", "duplicate_pair", "repeat_purchase"):
            assert by_id[charge.transaction_id][0] < threshold, charge.transaction_id


def test_a_missing_model_says_how_to_build_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="etl.train_fraud_model"):
        FraudModel.load(tmp_path / "fraud_model.joblib", tmp_path / "fraud_eval_report.json")


def test_a_charge_the_model_cannot_score_stops_the_build(warehouse, tmp_path, monkeypatch):
    _customer(warehouse, "CLI-DEMO")
    _purchase(warehouse, "REAL-1", "CLI-DEMO")
    monkeypatch.setattr(build_fixture, "score_transactions", lambda frame, path: frame["fraud_score"] * float("nan"))
    model = FraudModel(model_path=tmp_path / "unused.joblib", version="v", threshold=0.0035)
    with pytest.raises(RuntimeError, match="no score for fixture charge"):
        build_fixture_db(warehouse, tmp_path / "fixture.duckdb", "CLI-DEMO", model, history_warehouse=None)
