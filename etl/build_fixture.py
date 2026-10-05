"""Demo-fixture generator (Task 1.5, reworked in Milestone 8).

Reads the local warehouse built by `etl/extract.py` (no S3 access, no AWS
credentials needed at this step) and writes the small deployable DuckDB
fixture (`data/fixture.duckdb`) plus `data/demo_users.json` (AD-4's
demo-credentials map). The deployed app reads ONLY this fixture, never the
full warehouse and never S3 (AD-2).

## One demo customer, three scenarios (Milestone 8)

The challenge asks the system to demonstrate three SITUATIONS inside the
dispute workflow (automated resolution, ambiguity, human escalation). They are
not customer types: the same customer produces all three depending on what they
report or pick. Milestones 1-7 used three accounts, each chosen to land in one
bucket; that made the demo artificial (an "ambiguous" customer was one with no
findable charge at all). The fixture now holds ONE real customer with a real
transaction history, supplemented with a handful of clearly-labeled synthetic
charges so every scenario is reachable from a single login.

## Customer-selection criteria (reproducible, not hand-picked)

1. `customers.country = 'Colombia'`, `customer_status = 'Active'`.
   Colombia because it is the one market whose transactions are denominated in
   the local currency AND in Spanish (verified finding: every México-customer
   transaction in the dataset is in USD; there is no MXN transaction at all).
2. Only COP transactions, from the warehouse's extracted window.
3. Ranked by the number of purchases with a named merchant (so the "pick your
   charge" list reads like a real statement), then by total transactions.
4. Zero prior complaints in the dispute category, so the AD-11 abuse guard
   does not pre-decide every scenario.
5. Ties broken by `customer_id` ascending.

## Synthetic supplement (disclosed, labeled `_is_synthetic = true`)

The warehouse window holds ~6 real transactions per customer, not enough to
exercise duplicated charges or the fraud-score gate. `SYNTHETIC_CHARGES` adds
team-generated COP charges on the SAME customer and product, inside the same
date window, each designed for one scenario (see the `scenario` field). They
are never presented as real dataset rows: the column is set, the source file
is `synthetic`, and the README's "Demo data" paragraph describes them.

## Fraud-risk estimate, precomputed offline (fraud-integration)

Every fixture charge, real or synthetic, is scored here with
`etl.train_fraud_model.score_transactions` and the result is stored next to it
(`fraud_risk`, `fraud_risk_threshold`, `fraud_model_version`). The app never
loads the model: it reads these columns like any other fixture value. The
synthetic charges carry only their scenario's attributes (amount, channel,
merchant, time, the vendor `fraud_score`); the model scores them like any
other charge, nothing is hard-coded. The customer's earlier transactions come
from the two-year fraud warehouse when it exists (the history the model was
trained with), otherwise from the main warehouse. The estimate is decision
support for the advisor (the handoff), never a policy input (AD-15).
"""

from __future__ import annotations

import json
import logging
import math
import secrets
import string
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from etl.evaluate_fraud_model import DEFAULT_REPORT_PATH as DEFAULT_FRAUD_REPORT_PATH
from etl.extract import DATA_DIR, DEFAULT_WAREHOUSE_PATH
from etl.fraud_features import DEFAULT_FRAUD_WAREHOUSE_PATH, RAW_COLUMNS
from etl.train_fraud_model import DEFAULT_MODEL_PATH, load_model_bundle, score_transactions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.build_fixture")

DEFAULT_FIXTURE_PATH = DATA_DIR / "fixture.duckdb"
DEFAULT_DEMO_USERS_PATH = DATA_DIR / "demo_users.json"

DEMO_USERNAME = "cliente.demo"
DEMO_COUNTRY = "Colombia"
DEMO_CURRENCY = "COP"
DISPUTE_CATEGORY = "Transactions"

SYNTHETIC_SOURCE = "synthetic"

FRAUD_RISK_COLUMNS = ("fraud_risk", "fraud_risk_threshold", "fraud_model_version")
# What scoring reads: the label and the authorization outcome are never inputs.
_SCORING_COLUMNS = tuple(c for c in RAW_COLUMNS if c not in ("is_fraud", "transaction_status"))
_NUMERIC_SCORING_COLUMNS = ("amount", "amount_usd", "latitude", "longitude", "fraud_score")


@dataclass(frozen=True)
class SyntheticCharge:
    transaction_id: str
    # Full timestamp: the duplicate check compares minutes, not days (AD-14).
    posted_at: datetime
    merchant_name: str
    merchant_category: str
    amount: float
    fraud_score: float
    status: str
    channel: str
    scenario: str


# Charges the tests and the eval harness drive each scenario with.
AUTO_RESOLVE_CHARGE_ID = "SYN-DEMO-UBER"
FRAUD_SCORE_CHARGE_ID = "SYN-DEMO-ONLINE"
OVER_LIMIT_CHARGE_ID = "SYN-DEMO-BOUTIQUE"
DUPLICATE_CHARGE_IDS = ("SYN-DEMO-TAXI-1", "SYN-DEMO-TAXI-2")
REPEAT_FARE_CHARGE_IDS = ("SYN-DEMO-RIDE-1", "SYN-DEMO-RIDE-2")
CARD_PRESENT_CHARGE_ID = "SYN-DEMO-FARMACIA"
SECOND_ONLINE_CHARGE_ID = "SYN-DEMO-CINE"

# Amounts are COP. Converted to USD at build time with the dataset's own
# daily_exchange_rates, so AD-11's USD thresholds apply exactly as to real rows.
SYNTHETIC_CHARGES: tuple[SyntheticCharge, ...] = (
    SyntheticCharge(AUTO_RESOLVE_CHARGE_ID, datetime(2026, 6, 14, 12, 0), "Uber", "Transport",
                    38_500.0, 6.0, "Approved", "App", "auto_resolve"),
    SyntheticCharge(CARD_PRESENT_CHARGE_ID, datetime(2026, 6, 11, 12, 0), "Farmacia Salud", "Health",
                    64_900.0, 4.0, "Approved", "POS", "auto_resolve"),
    SyntheticCharge(SECOND_ONLINE_CHARGE_ID, datetime(2026, 6, 9, 12, 0), "Cine Premium", "Entertainment",
                    52_000.0, 7.5, "Approved", "Web", "auto_resolve"),
    SyntheticCharge("SYN-DEMO-SUPER", datetime(2026, 6, 5, 12, 0), "Super Ahorro", "Food",
                    187_350.0, 9.0, "Approved", "POS", "auto_resolve"),
    # A double charge: same merchant and amount, 4 minutes apart (inside
    # DUPLICATE_WINDOW_MINUTES). Amount + date alone cannot tell them apart
    # (AD-11 Row 3), so the customer has to pick one from the list.
    SyntheticCharge(DUPLICATE_CHARGE_IDS[0], datetime(2026, 6, 15, 12, 0), "Taxi Seguro", "Transport",
                    27_000.0, 5.0, "Approved", "App", "duplicate_pair"),
    SyntheticCharge(DUPLICATE_CHARGE_IDS[1], datetime(2026, 6, 15, 12, 4), "Taxi Seguro", "Transport",
                    27_000.0, 5.0, "Approved", "App", "duplicate_pair"),
    # The same fare on two consecutive mornings: two rides, never reversed as
    # a duplicate (AD-14).
    SyntheticCharge(REPEAT_FARE_CHARGE_IDS[0], datetime(2026, 6, 3, 8, 10), "Cabify", "Transport",
                    18_500.0, 4.0, "Approved", "App", "repeat_purchase"),
    SyntheticCharge(REPEAT_FARE_CHARGE_IDS[1], datetime(2026, 6, 4, 8, 12), "Cabify", "Transport",
                    18_500.0, 4.0, "Approved", "App", "repeat_purchase"),
    SyntheticCharge(FRAUD_SCORE_CHARGE_ID, datetime(2026, 6, 12, 12, 0), "Tienda Online Global", "Other",
                    689_000.0, 91.0, "Approved", "Web", "escalate_fraud_score"),
    SyntheticCharge(OVER_LIMIT_CHARGE_ID, datetime(2026, 6, 13, 12, 0), "Boutique Moda", "Other",
                    2_450_000.0, 9.0, "Approved", "POS", "escalate_amount"),
)


def select_demo_customer(con: duckdb.DuckDBPyConnection) -> str:
    row = con.execute(
        f"""
        SELECT t.customer_id
        FROM transactions t
        JOIN customers cu ON cu.customer_id = t.customer_id
        WHERE cu.country = ? AND cu.customer_status = 'Active' AND t.currency = ?
          AND NOT EXISTS (
              SELECT 1 FROM complaints c
              WHERE c.customer_id = t.customer_id AND c.category = '{DISPUTE_CATEGORY}'
          )
        GROUP BY t.customer_id
        ORDER BY COUNT(*) FILTER (WHERE t.transaction_type = 'Purchase' AND t.merchant_name IS NOT NULL) DESC,
                 COUNT(*) DESC,
                 t.customer_id
        LIMIT 1
        """,
        [DEMO_COUNTRY, DEMO_CURRENCY],
    ).fetchone()
    if row is None:
        raise RuntimeError("No customer in the warehouse meets the demo-customer criteria")
    return row[0]


def _usd_rate(con: duckdb.DuckDBPyConnection, currency: str, on_date: date) -> float:
    row = con.execute(
        """
        SELECT exchange_rate FROM daily_exchange_rates
        WHERE source_currency = ? AND target_currency = 'USD' AND date <= ?
        ORDER BY date DESC LIMIT 1
        """,
        [currency, on_date],
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No {currency}->USD exchange rate on/before {on_date}")
    return row[0]


def _stringify(row: tuple) -> list[str | None]:
    return [str(v) if v is not None else None for v in row]


def _copy_rows(
    con: duckdb.DuckDBPyConnection, fcon: duckdb.DuckDBPyConnection,
    table: str, where: str, params: list, *, extra_columns: tuple[str, ...] = (),
) -> list[str]:
    cursor = con.execute(f"SELECT * FROM {table} WHERE {where}", params)
    columns = [c[0] for c in cursor.description]
    rows = cursor.fetchall()
    all_columns = columns + list(extra_columns)
    fcon.execute(f"CREATE TABLE {table} ({', '.join(c + ' VARCHAR' for c in all_columns)})")
    padding = [None] * len(extra_columns)
    if rows:
        fcon.executemany(
            f"INSERT INTO {table} VALUES ({', '.join('?' for _ in all_columns)})",
            [_stringify(r) + padding for r in rows],
        )
    logger.info("Copied %d %s row(s)", len(rows), table)
    return all_columns


def _insert_synthetic_charges(
    con: duckdb.DuckDBPyConnection, fcon: duckdb.DuckDBPyConnection,
    customer_id: str, columns: list[str],
) -> None:
    product_id, city = con.execute(
        """
        SELECT product_id, transaction_city FROM transactions
        WHERE customer_id = ? AND currency = ?
        ORDER BY transaction_city IS NULL, transaction_date DESC LIMIT 1
        """,
        [customer_id, DEMO_CURRENCY],
    ).fetchone()
    for charge in SYNTHETIC_CHARGES:
        values = {
            "transaction_id": charge.transaction_id,
            "transaction_date": charge.posted_at,
            "product_id": product_id,
            "customer_id": customer_id,
            "transaction_type": "Purchase",
            "transaction_category": f"Team-generated (synthetic demo charge: {charge.scenario})",
            "amount": charge.amount,
            "currency": DEMO_CURRENCY,
            "amount_usd": round(charge.amount * _usd_rate(con, DEMO_CURRENCY, charge.posted_at.date()), 2),
            "channel": charge.channel,
            "merchant_name": charge.merchant_name,
            "merchant_category": charge.merchant_category,
            "transaction_country": DEMO_COUNTRY,
            "transaction_city": city,
            "transaction_status": charge.status,
            "response_code": "00" if charge.status == "Approved" else "05",
            "is_fraud": False,
            "fraud_score": charge.fraud_score,
            "_source_file": SYNTHETIC_SOURCE,
            "_is_synthetic": True,
        }
        row = [values.get(c) for c in columns]
        fcon.execute(
            f"INSERT INTO transactions VALUES ({', '.join('?' for _ in columns)})", _stringify(tuple(row))
        )
    logger.info("Inserted %d synthetic demo charge(s)", len(SYNTHETIC_CHARGES))


@dataclass(frozen=True)
class FraudModel:
    """The stored fraud-risk model and its cost-optimal threshold, both from
    the fraud-model feature's artifacts (`docs/ml/fraud-model.md`).
    """

    model_path: Path
    version: str
    threshold: float

    @classmethod
    def load(cls, model_path: Path = DEFAULT_MODEL_PATH, report_path: Path = DEFAULT_FRAUD_REPORT_PATH) -> FraudModel:
        missing = [str(path) for path in (model_path, report_path) if not path.exists()]
        if missing:
            raise FileNotFoundError(
                f"{', '.join(missing)} not found: build them first with `python -m etl.train_fraud_model` and "
                "`python -m etl.evaluate_fraud_model` (docs/ml/fraud-model.md, Reproduce)"
            )
        bundle = load_model_bundle(model_path)
        report = json.loads(report_path.read_text())
        selected = bundle["selected"]
        if report["run_group"] != bundle["run_group"]:
            raise RuntimeError(
                f"{report_path} (run {report['run_group']}) does not describe {model_path} (run {bundle['run_group']})"
            )
        threshold = report["thresholds"]["scores"][selected]["threshold_chosen_on_val"]
        return cls(model_path, f"{selected}-{bundle['run_group']}", float(threshold))


def _history_rows(
    con: duckdb.DuckDBPyConnection, customer_id: str, history_warehouse: Path | None,
) -> pd.DataFrame:
    """The customer's own transactions the model reads as history (every
    currency: history is per customer, not per currency).
    """
    query = f"SELECT {', '.join(_SCORING_COLUMNS)} FROM transactions WHERE customer_id = ?"
    if history_warehouse is not None and history_warehouse.exists():
        logger.info("Scoring history: %s", history_warehouse)
        hcon = duckdb.connect(str(history_warehouse), read_only=True)
        try:
            return hcon.execute(query, [customer_id]).df()
        finally:
            hcon.close()
    logger.warning("Scoring history: the main warehouse (%s not found)", history_warehouse)
    return con.execute(query, [customer_id]).df()


def _fixture_rows(fcon: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    df = fcon.execute(f"SELECT {', '.join(_SCORING_COLUMNS)} FROM transactions").df()
    for column in _NUMERIC_SCORING_COLUMNS:
        df[column] = pd.to_numeric(df[column])
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    return df


def score_fixture_charges(
    warehouse_con: duckdb.DuckDBPyConnection, fixture_con: duckdb.DuckDBPyConnection, *, customer_id: str,
    model: FraudModel, history_warehouse: Path | None,
) -> dict[str, float]:
    """Writes the model's estimate onto every fixture charge; returns it by id."""
    fixture = _fixture_rows(fixture_con)
    history = _history_rows(warehouse_con, customer_id, history_warehouse)
    history = history[~history["transaction_id"].isin(fixture["transaction_id"])]
    frame = pd.concat([history, fixture], ignore_index=True)
    country, registered = warehouse_con.execute(
        "SELECT country, registration_date FROM customers WHERE customer_id = ?", [customer_id],
    ).fetchone()
    frame["home_country"] = country
    frame["registration_date"] = pd.to_datetime(registered)
    risk = pd.Series(score_transactions(frame, model.model_path), index=frame["transaction_id"])
    scores = {tid: float(risk[tid]) for tid in fixture["transaction_id"]}
    unscored = sorted(tid for tid, score in scores.items() if not math.isfinite(score))
    if unscored:
        raise RuntimeError(f"the fraud-risk model returned no score for fixture charge(s) {unscored}")
    for column in FRAUD_RISK_COLUMNS:
        fixture_con.execute(f"ALTER TABLE transactions ADD COLUMN {column} VARCHAR")
    fixture_con.executemany(
        f"UPDATE transactions SET {', '.join(f'{c} = ?' for c in FRAUD_RISK_COLUMNS)} WHERE transaction_id = ?",
        [[str(score), str(model.threshold), model.version, tid] for tid, score in scores.items()],
    )
    logger.info("Scored %d fixture charge(s) with %s", len(scores), model.version)
    return scores


def build_fixture_db(
    con: duckdb.DuckDBPyConnection, fixture_path: Path, customer_id: str,
    fraud_model: FraudModel | None = None, history_warehouse: Path | None = None,
) -> None:
    fixture_path.unlink(missing_ok=True)
    fcon = duckdb.connect(str(fixture_path))
    try:
        _copy_rows(con, fcon, "customers", "customer_id = ?", [customer_id])
        _copy_rows(con, fcon, "products", "customer_id = ?", [customer_id])
        _copy_rows(con, fcon, "complaints", "customer_id = ?", [customer_id])
        columns = _copy_rows(
            con, fcon, "transactions", "customer_id = ? AND currency = ?",
            [customer_id, DEMO_CURRENCY], extra_columns=("_is_synthetic",),
        )
        fcon.execute("UPDATE transactions SET _is_synthetic = 'False' WHERE _is_synthetic IS NULL")
        _insert_synthetic_charges(con, fcon, customer_id, columns)
        if fraud_model is not None:
            score_fixture_charges(
                con, fcon, customer_id=customer_id, model=fraud_model, history_warehouse=history_warehouse,
            )
    finally:
        fcon.close()
    logger.info("Fixture written to %s", fixture_path)


def _generate_password() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "demo-" + "".join(secrets.choice(alphabet) for _ in range(8))


def write_demo_users(customer_id: str, path: Path) -> dict:
    demo_users = {DEMO_USERNAME: {"password": _generate_password(), "customer_id": customer_id}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(demo_users, indent=2, ensure_ascii=False))
    logger.info("Wrote demo credentials to %s", path)
    return demo_users


def run(
    warehouse_path: Path = DEFAULT_WAREHOUSE_PATH,
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
    demo_users_path: Path = DEFAULT_DEMO_USERS_PATH,
    fraud_model_path: Path = DEFAULT_MODEL_PATH,
    fraud_report_path: Path = DEFAULT_FRAUD_REPORT_PATH,
    history_warehouse: Path | None = DEFAULT_FRAUD_WAREHOUSE_PATH,
) -> dict:
    # The deployed fixture always carries the estimate: a missing model fails here.
    fraud_model = FraudModel.load(fraud_model_path, fraud_report_path)
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        customer_id = select_demo_customer(con)
        logger.info("Selected demo customer %s", customer_id)
        build_fixture_db(con, fixture_path, customer_id, fraud_model, history_warehouse)
    finally:
        con.close()
    write_demo_users(customer_id, demo_users_path)
    return {
        "customer_id": customer_id, "synthetic_charges": len(SYNTHETIC_CHARGES), "fraud_model": fraud_model.version,
    }


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
