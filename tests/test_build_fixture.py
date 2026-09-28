from datetime import date

import duckdb
import pytest

from etl.build_fixture import _classify_candidate, _needed_dates, _select_personas


@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute(
        """
        CREATE TABLE persona_transactions_candidates (
            transaction_id VARCHAR, transaction_date TIMESTAMP, product_id VARCHAR,
            customer_id VARCHAR, transaction_type VARCHAR, transaction_category VARCHAR,
            amount DOUBLE, currency VARCHAR, amount_usd DOUBLE, channel VARCHAR,
            branch_id VARCHAR, merchant_name VARCHAR, merchant_category VARCHAR,
            transaction_country VARCHAR, transaction_city VARCHAR, transaction_status VARCHAR,
            response_code VARCHAR, is_fraud BOOLEAN, fraud_score DOUBLE,
            latitude DOUBLE, longitude DOUBLE, _source_file VARCHAR
        )
        """
    )
    c.execute(
        """
        CREATE TABLE complaints (
            complaint_id VARCHAR, customer_id VARCHAR, category VARCHAR,
            subcategory VARCHAR, claimed_amount DOUBLE, currency VARCHAR, creation_date TIMESTAMP,
            affected_product_id VARCHAR
        )
        """
    )
    c.execute("CREATE TABLE customers (customer_id VARCHAR, country VARCHAR)")
    c.execute(
        "CREATE TABLE daily_exchange_rates "
        "(date DATE, source_currency VARCHAR, target_currency VARCHAR, exchange_rate DOUBLE)"
    )
    yield c
    c.close()


def _insert_txn(con, transaction_id, customer_id, transaction_date, amount, currency, amount_usd, fraud_score, status):
    con.execute(
        "INSERT INTO persona_transactions_candidates "
        "(transaction_id, transaction_date, customer_id, amount, currency, amount_usd, "
        " fraud_score, transaction_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [transaction_id, transaction_date, customer_id, amount, currency, amount_usd, fraud_score, status],
    )


def _candidate(**overrides):
    base = {
        "complaint_id": "CMP-1",
        "customer_id": "CLI-1",
        "claimed_amount": 100.0,
        "currency": "USD",
        "creation_date": date(2024, 3, 10),
        "affected_product_id": "PRD-1",
        "country": "México",
    }
    base.update(overrides)
    return base


def test_classify_confident_match_eligible_for_auto_resolve(con):
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", 100.0, 5.0, "Approved")
    bucket, matches = _classify_candidate(con, _candidate())
    assert bucket == "clean_auto_resolve"
    assert matches == ["TRX-1"]


def test_classify_confident_match_over_amount_threshold_forces_escalation(con):
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", 500.0, 5.0, "Approved")
    bucket, matches = _classify_candidate(con, _candidate())
    assert bucket == "escalation"
    assert matches == ["TRX-1"]


def test_classify_confident_match_high_fraud_score_forces_escalation(con):
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", 100.0, 85.0, "Approved")
    bucket, matches = _classify_candidate(con, _candidate())
    assert bucket == "escalation"


def test_classify_no_match_is_ambiguous(con):
    bucket, matches = _classify_candidate(con, _candidate())
    assert bucket == "ambiguous"
    assert matches == []


def test_classify_multiple_matches_is_ambiguous(con):
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", 100.0, 5.0, "Approved")
    _insert_txn(con, "TRX-2", "CLI-1", "2024-03-11", 102.0, "USD", 102.0, 5.0, "Approved")
    bucket, matches = _classify_candidate(con, _candidate())
    assert bucket == "ambiguous"
    assert set(matches) == {"TRX-1", "TRX-2"}


def test_classify_different_currency_transaction_is_not_a_match(con):
    """A same-amount transaction in a DIFFERENT currency must never be treated
    as a match — comparing raw amounts across currencies (e.g. COP vs USD) is
    meaningless (COP amounts are ~1000x larger numerically for the same real
    value), a real bug caught during this script's development against live
    data.
    """
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "COP", 0.025, 5.0, "Approved")
    bucket, matches = _classify_candidate(con, _candidate(currency="USD"))
    assert bucket == "ambiguous"
    assert matches == []


def test_classify_usd_currency_with_null_amount_usd_still_evaluates_eligibility(con):
    """Real data finding: `amount_usd` is NULL for ~57% of transactions —
    specifically whenever `currency == 'USD'` (no conversion needed, so the
    dataset leaves it unset). The eligibility check must not silently treat
    every USD transaction as ineligible because of this.
    """
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", None, 5.0, "Approved")
    bucket, matches = _classify_candidate(con, _candidate(currency="USD"))
    assert bucket == "clean_auto_resolve"


def test_classify_abuse_guard_forces_escalation_despite_otherwise_clean_match(con):
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", 100.0, 5.0, "Approved")
    for i in range(3):
        con.execute(
            "INSERT INTO complaints (complaint_id, customer_id, category, creation_date) "
            "VALUES (?, 'CLI-1', 'Transactions', ?)",
            [f"CMP-PRIOR-{i}", date(2024, 2, 1 + i)],
        )
    bucket, matches = _classify_candidate(con, _candidate())
    assert bucket == "escalation"


def test_needed_dates_spans_search_window_around_creation_date():
    candidates = [_candidate(creation_date=date(2024, 3, 10))]
    dates = _needed_dates(candidates)
    assert date(2024, 3, 3) in dates  # -7 days
    assert date(2024, 3, 17) in dates  # +7 days
    assert date(2024, 3, 10) in dates
    assert date(2024, 3, 2) not in dates
    assert date(2024, 3, 18) not in dates


def _seed_complaint_pool(con, complaints: list[dict]) -> None:
    for c in complaints:
        con.execute(
            "INSERT INTO complaints "
            "(complaint_id, customer_id, category, subcategory, claimed_amount, currency, "
            " creation_date, affected_product_id) "
            "VALUES (?, ?, 'Transactions', 'Cargo no reconocido', ?, ?, ?, ?)",
            [c["complaint_id"], c["customer_id"], c["claimed_amount"], c["currency"],
             c["creation_date"], c.get("affected_product_id")],
        )
        con.execute(
            "INSERT INTO customers VALUES (?, ?)", [c["customer_id"], c["country"]]
        )


def test_select_personas_synthesizes_when_no_real_match_exists(con):
    """The documented, verified real-data finding: real amount+date matches
    are near-nonexistent, so clean_auto_resolve/escalation personas fall
    back to a synthesized (clearly labeled) transaction rather than the
    whole pipeline failing.
    """
    candidates = [_candidate(complaint_id="CMP-1", customer_id="CLI-1", claimed_amount=50.0, currency="USD")]
    # Extra pool-only complaints (not in `candidates`) for the synthesis
    # fallback to draw from for the clean_auto_resolve/escalation buckets.
    pool_extra = [
        _candidate(complaint_id="CMP-2", customer_id="CLI-2", claimed_amount=60.0, currency="USD",
                   country="Colombia", creation_date=date(2024, 4, 10)),
        _candidate(complaint_id="CMP-3", customer_id="CLI-3", claimed_amount=70.0, currency="USD",
                   country="Argentina", creation_date=date(2024, 5, 10)),
    ]
    _seed_complaint_pool(con, [*candidates, *pool_extra])

    selected = _select_personas(con, candidates)

    assert set(selected.keys()) == {"clean_auto_resolve", "ambiguous", "escalation"}
    assert selected["clean_auto_resolve"].is_synthetic_transaction is True
    assert selected["escalation"].is_synthetic_transaction is True
    assert selected["ambiguous"].is_synthetic_transaction is False
    assert selected["ambiguous"].complaint_id == "CMP-1"

    # The synthesized transaction must actually be queryable in the table the
    # app will read from — not just referenced by id.
    row = con.execute(
        "SELECT _is_synthetic FROM persona_transactions_candidates WHERE transaction_id = ?",
        [selected["clean_auto_resolve"].matched_transaction_ids[0]],
    ).fetchone()
    assert row is not None
    assert row[0] is True


def test_select_personas_raises_when_ambiguous_bucket_empty(con):
    """Every candidate having a real match would be a red flag (contradicts
    the verified norm) — this stays a hard failure, not a silent synthesis.
    """
    candidates = [_candidate(complaint_id="CMP-1", customer_id="CLI-1", claimed_amount=100.0, currency="USD")]
    _seed_complaint_pool(con, candidates)
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", 100.0, 5.0, "Approved")

    with pytest.raises(RuntimeError, match="ambiguous"):
        _select_personas(con, candidates)


def test_select_personas_prefers_real_match_over_synthesis(con):
    candidates = [
        _candidate(complaint_id="CMP-1", customer_id="CLI-1", claimed_amount=100.0, currency="USD", country="México"),
        _candidate(complaint_id="CMP-2", customer_id="CLI-2", claimed_amount=100.0, currency="USD",
                   country="Colombia", creation_date=date(2024, 4, 10)),
    ]
    # A third complaint, not in `candidates`, only present so the escalation
    # bucket (empty here -> synthesized) has something in the pool to pick.
    pool_only = _candidate(complaint_id="CMP-3", customer_id="CLI-3", claimed_amount=100.0, currency="USD",
                            country="Argentina", creation_date=date(2024, 5, 10))
    _seed_complaint_pool(con, [*candidates, pool_only])
    _insert_txn(con, "TRX-1", "CLI-1", "2024-03-09", 100.0, "USD", 100.0, 5.0, "Approved")

    selected = _select_personas(con, candidates)

    assert selected["clean_auto_resolve"].is_synthetic_transaction is False
    assert selected["clean_auto_resolve"].customer_id == "CLI-1"
    assert selected["clean_auto_resolve"].matched_transaction_ids == ["TRX-1"]
