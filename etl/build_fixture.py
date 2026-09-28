"""Sanitized demo-fixture generator (Task 1.5).

Reads the local warehouse (`etl/extract.py`'s output — `complaints`,
`customers`, `products` must already be extracted), selects a small number of
REAL personas per the documented criteria below, does a second, narrowly
targeted transactions pull scoped to just those personas' dates (never the
full table), and writes a small deployable DuckDB fixture
(`data/fixture.duckdb`) plus `data/demo_users.json` (AD-4's demo-credentials
map). The deployed app reads ONLY this fixture — never the full warehouse,
never S3 (AD-2).

Persona-selection criteria (resolves plan.md's Open Question on demo
personas — documented here, not hand-picked):

1. Candidate pool: `complaints` rows where `category = 'Transactions'` AND
   `subcategory = 'Cargo no reconocido'` (the real working subset — ~90% of
   the 13,580 "Transactions"-category complaints per findings.md), joined to
   `customers` (must exist) and `products` (via `affected_product_id`, may be
   null), with `claimed_amount > 0`.
2. A reproducible random sample of `CANDIDATE_POOL_SIZE` rows is drawn via
   DuckDB's `USING SAMPLE ... (reservoir, {SAMPLE_SEED})` — deterministic
   given the same warehouse content, not hand-picked.
3. Since `complaints` has no FK to `transactions` (findings.md), each
   candidate's "matching transaction(s)" are discovered empirically: this
   script pulls that customer's transactions within
   `MATCH_SEARCH_WINDOW_DAYS` of the complaint's `creation_date`, then
   applies AD-11's exact match tolerance (`abs(amount - claimed_amount) <=
   max(claimed_amount * 0.05, 2 USD-equivalent)` AND `abs(date diff) <= 3
   days`) to classify the candidate:
     - 0 matches within tolerance -> "ambiguous" bucket (a clarifying
       question has nothing to confirm against).
     - >=2 matches within tolerance -> "ambiguous" bucket (multiple
       candidates, needs disambiguation).
     - exactly 1 match: eligible for auto-resolution (AD-11: amount_usd <=
       200, fraud_score < 30, status == 'Approved', and this customer has
       < 3 disputes in the same category in the trailing 90 days by
       `creation_date`) -> "clean_auto_resolve" bucket; otherwise (a
       confident match that fails one of those conditions) -> "escalation"
       bucket.
4. Exactly one persona is selected per bucket (clean_auto_resolve, ambiguous,
   escalation), preferring the combination that reaches >= 2 of the 3
   countries (MX/CO/AR) across the 3 selected personas.

VERIFIED DURING DEVELOPMENT — a real, documented finding, not a hypothetical:
`complaints` and `transactions` are independently generated synthetic data
with NO deliberate amount+date linkage baked in. A direct check against 2,000
real "Transactions"-category complaints found a real matching transaction
(within AD-11's own tolerance) for fewer than 1 in 1,000 of them. This means
the "ambiguous" bucket (0 real matches) is trivially, honestly satisfied by
real data — it is the norm, not the exception, which is itself a finding
worth reporting (it is *why* the state machine's clarification flow is load-
bearing, not optional). But "clean_auto_resolve" and "escalation" each
require a CONFIDENT match, which real data essentially never provides. For
those two buckets, if the empirical search over the sampled pool comes up
empty, `_synthesize_persona()` builds ONE plausible transaction row for a
selected real complaint (real customer_id/claimed_amount/currency/
creation_date/country — only the disputed transaction itself is
team-generated) and marks it `_is_synthetic = true` in the fixture, so it is
never presented as a real dataset row. This is exactly the brief's own
allowance ("Label inputs as real/de-identified/synthetic/team-generated"),
applied honestly rather than silently pretending an empirical match exists
where none does.

The AD-11 threshold constants below are intentionally duplicated (not
imported) from plan.md's policy table: `app/policy.py` (the canonical,
importable version) is a Milestone 2 deliverable and does not exist during
Milestone 1. Milestone 2's `tests/test_policy.py` is what keeps the two in
sync going forward.
"""

from __future__ import annotations

import json
import logging
import secrets
import string
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from etl.extract import DATA_DIR, DEFAULT_WAREHOUSE_PATH, REPO_ROOT, extract_dates_fact
from etl.extract import connect as connect_with_s3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.build_fixture")

DEFAULT_FIXTURE_PATH = DATA_DIR / "fixture.duckdb"
DEFAULT_DEMO_USERS_PATH = DATA_DIR / "demo_users.json"

CANDIDATE_POOL_SIZE = 60
SAMPLE_SEED = 42
MATCH_SEARCH_WINDOW_DAYS = 7  # headroom around AD-11's own +/-3 day match tolerance
SYNTHESIS_POOL_LIMIT = 500
CANDIDATES_TABLE = "persona_transactions_candidates"

DISPUTE_CATEGORY = "Transactions"
DISPUTE_SUBCATEGORY = "Cargo no reconocido"

# Duplicated deliberately until app/policy.py exists (see module docstring).
MATCH_DATE_TOLERANCE_DAYS = 3
MATCH_AMOUNT_PCT_TOLERANCE = 0.05
MATCH_AMOUNT_MIN_USD_TOLERANCE = 2.0
AUTO_RESOLVE_MAX_AMOUNT_USD = 200.0
AUTO_RESOLVE_MAX_FRAUD_SCORE = 30.0
ABUSE_GUARD_MAX_DISPUTES = 3
ABUSE_GUARD_WINDOW_DAYS = 90

# Synthesized clean persona stays comfortably under the auto-resolve amount cap.
SYNTHETIC_CLEAN_AMOUNT_MARGIN = 0.75
SYNTHETIC_CLEAN_FRAUD_SCORE = 5.0
SYNTHETIC_ESCALATION_FRAUD_SCORE = 90.0  # confident match, but fails the fraud threshold


class PersonaBucket(StrEnum):
    CLEAN_AUTO_RESOLVE = "clean_auto_resolve"
    AMBIGUOUS = "ambiguous"
    ESCALATION = "escalation"


BUCKETS = tuple(PersonaBucket)

_CANDIDATE_COLUMNS = (
    "complaint_id", "customer_id", "claimed_amount", "currency", "creation_date",
    "affected_product_id", "country",
)
_CANDIDATE_BASE_QUERY = f"""
    SELECT c.complaint_id, c.customer_id, c.claimed_amount, c.currency,
           c.creation_date, c.affected_product_id, cu.country
    FROM complaints c
    JOIN customers cu ON cu.customer_id = c.customer_id
    WHERE c.category = '{DISPUTE_CATEGORY}'
      AND c.subcategory = '{DISPUTE_SUBCATEGORY}'
      AND c.claimed_amount > 0
"""


@dataclass
class Persona:
    bucket: PersonaBucket
    complaint_id: str
    customer_id: str
    country: str
    currency: str
    claimed_amount: float
    creation_date: date
    affected_product_id: str | None
    matched_transaction_ids: list[str]
    is_synthetic_transaction: bool = False


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _rows_as_candidates(rows: list[tuple]) -> list[dict]:
    return [dict(zip(_CANDIDATE_COLUMNS, r, strict=True)) for r in rows]


def _load_candidates(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute(
        f"{_CANDIDATE_BASE_QUERY} USING SAMPLE {CANDIDATE_POOL_SIZE} (reservoir, {SAMPLE_SEED})"
    ).fetchall()
    return _rows_as_candidates(rows)


def _needed_dates(candidates: list[dict]) -> set[date]:
    dates: set[date] = set()
    for c in candidates:
        creation_date = _as_date(c["creation_date"])
        for delta in range(-MATCH_SEARCH_WINDOW_DAYS, MATCH_SEARCH_WINDOW_DAYS + 1):
            dates.add(creation_date + timedelta(days=delta))
    return dates


def _classify_candidate(
    con: duckdb.DuckDBPyConnection, candidate: dict
) -> tuple[PersonaBucket, list[str]]:
    creation_date = _as_date(candidate["creation_date"])

    amount_tolerance = max(candidate["claimed_amount"] * MATCH_AMOUNT_PCT_TOLERANCE, MATCH_AMOUNT_MIN_USD_TOLERANCE)

    matches = con.execute(
        f"""
        SELECT transaction_id, amount,
               COALESCE(amount_usd, CASE WHEN currency = 'USD' THEN amount END) AS amount_usd_eff,
               fraud_score, transaction_status
        FROM {CANDIDATES_TABLE}
        WHERE customer_id = ?
          AND currency = ?
          AND ABS(amount - ?) <= ?
          AND ABS(DATE_DIFF('day', CAST(transaction_date AS DATE), CAST(? AS DATE))) <= ?
        """,
        [
            candidate["customer_id"],
            candidate["currency"],
            candidate["claimed_amount"],
            amount_tolerance,
            creation_date,
            MATCH_DATE_TOLERANCE_DAYS,
        ],
    ).fetchall()

    if len(matches) != 1:
        return PersonaBucket.AMBIGUOUS, [m[0] for m in matches]

    _, _, amount_usd, fraud_score, status = matches[0]

    window_start = creation_date - timedelta(days=ABUSE_GUARD_WINDOW_DAYS)
    prior_disputes = con.execute(
        """
        SELECT COUNT(*) FROM complaints
        WHERE customer_id = ? AND category = ?
          AND creation_date >= ? AND creation_date < ?
        """,
        [candidate["customer_id"], DISPUTE_CATEGORY, window_start, creation_date],
    ).fetchone()[0]

    eligible = (
        (amount_usd is not None and amount_usd <= AUTO_RESOLVE_MAX_AMOUNT_USD)
        and (fraud_score is not None and fraud_score < AUTO_RESOLVE_MAX_FRAUD_SCORE)
        and status == "Approved"
        and prior_disputes < ABUSE_GUARD_MAX_DISPUTES
    )
    bucket = PersonaBucket.CLEAN_AUTO_RESOLVE if eligible else PersonaBucket.ESCALATION
    return bucket, [matches[0][0]]


def _lookup_usd_rate(con: duckdb.DuckDBPyConnection, currency: str, on_date: date) -> float:
    if currency == "USD":
        return 1.0
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


def _fetch_complaint_pool(
    con: duckdb.DuckDBPyConnection, limit: int = SYNTHESIS_POOL_LIMIT
) -> list[dict]:
    rows = con.execute(
        f"{_CANDIDATE_BASE_QUERY} ORDER BY c.complaint_id LIMIT {limit}"
    ).fetchall()
    return _rows_as_candidates(rows)


def _insert_synthetic_transaction(
    con: duckdb.DuckDBPyConnection,
    complaint: dict,
    creation_date: date,
    fraud_score: float,
    status: str,
) -> str:
    txn_id = f"SYN-{complaint['complaint_id']}"
    con.execute(
        f"""
        INSERT INTO {CANDIDATES_TABLE} (
            transaction_id, transaction_date, product_id, customer_id,
            transaction_type, transaction_category, amount, currency, amount_usd,
            channel, branch_id, merchant_name, merchant_category, transaction_country,
            transaction_city, transaction_status, response_code, is_fraud, fraud_score,
            latitude, longitude, _source_file, _is_synthetic
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            txn_id, creation_date, complaint["affected_product_id"], complaint["customer_id"],
            "Purchase", "Team-generated (synthetic demo persona)",
            complaint["claimed_amount"], complaint["currency"],
            complaint["claimed_amount"] * _lookup_usd_rate(con, complaint["currency"], creation_date),
            "App", None, "Comercio Demo", "Retail", complaint["country"],
            None, status, "00" if status == "Approved" else "05", False, fraud_score,
            None, None, "synthetic", True,
        ],
    )
    return txn_id


def _synthesize_persona(
    con: duckdb.DuckDBPyConnection, bucket: PersonaBucket, exclude_complaint_ids: set[str]
) -> Persona:
    pool = _fetch_complaint_pool(con)
    pool = [c for c in pool if c["complaint_id"] not in exclude_complaint_ids]

    if bucket is PersonaBucket.CLEAN_AUTO_RESOLVE:
        chosen = None
        for c in pool:
            usd = c["claimed_amount"] * _lookup_usd_rate(con, c["currency"], _as_date(c["creation_date"]))
            if usd <= AUTO_RESOLVE_MAX_AMOUNT_USD * SYNTHETIC_CLEAN_AMOUNT_MARGIN:
                chosen = c
                break
        if chosen is None:
            raise RuntimeError(
                "Could not find any real complaint with a small enough claimed_amount "
                "to synthesize a clean_auto_resolve persona."
            )
        fraud_score, status = SYNTHETIC_CLEAN_FRAUD_SCORE, "Approved"
    elif bucket is PersonaBucket.ESCALATION:
        chosen = pool[0]
        fraud_score, status = SYNTHETIC_ESCALATION_FRAUD_SCORE, "Approved"
    else:
        raise ValueError(f"Synthesis not defined for bucket {bucket!r}")

    creation_date = _as_date(chosen["creation_date"])
    txn_id = _insert_synthetic_transaction(con, chosen, creation_date, fraud_score, status)

    logger.warning(
        "No real dataset transaction empirically matched any sampled candidate for bucket "
        "'%s' (expected — see module docstring). Synthesized one team-generated transaction "
        "(%s) for real complaint %s to fill this persona.",
        bucket, txn_id, chosen["complaint_id"],
    )

    return Persona(
        bucket=bucket,
        complaint_id=chosen["complaint_id"],
        customer_id=chosen["customer_id"],
        country=chosen["country"],
        currency=chosen["currency"],
        claimed_amount=chosen["claimed_amount"],
        creation_date=creation_date,
        affected_product_id=chosen["affected_product_id"],
        matched_transaction_ids=[txn_id],
        is_synthetic_transaction=True,
    )


def _select_personas(
    con: duckdb.DuckDBPyConnection, candidates: list[dict]
) -> dict[PersonaBucket, Persona]:
    con.execute(
        f"ALTER TABLE {CANDIDATES_TABLE} ADD COLUMN IF NOT EXISTS "
        "_is_synthetic BOOLEAN DEFAULT FALSE"
    )

    by_bucket: dict[PersonaBucket, list[Persona]] = {b: [] for b in BUCKETS}

    for c in candidates:
        bucket, matched_ids = _classify_candidate(con, c)
        persona = Persona(
            bucket=bucket,
            complaint_id=c["complaint_id"],
            customer_id=c["customer_id"],
            country=c["country"],
            currency=c["currency"],
            claimed_amount=c["claimed_amount"],
            creation_date=_as_date(c["creation_date"]),
            affected_product_id=c["affected_product_id"],
            matched_transaction_ids=matched_ids,
        )
        by_bucket[bucket].append(persona)

    for bucket in BUCKETS:
        logger.info("Bucket '%s': %d empirically-real candidate(s)", bucket, len(by_bucket[bucket]))

    if not by_bucket[PersonaBucket.AMBIGUOUS]:
        raise RuntimeError(
            "No candidate fell into the 'ambiguous' (0 or 2+ real matches) bucket out of "
            f"{len(candidates)} sampled complaints — this should be the norm per the module "
            "docstring; increase CANDIDATE_POOL_SIZE and re-run."
        )

    used_complaint_ids = {c["complaint_id"] for c in candidates}
    for bucket in (PersonaBucket.CLEAN_AUTO_RESOLVE, PersonaBucket.ESCALATION):
        if not by_bucket[bucket]:
            synthesized = _synthesize_persona(con, bucket, used_complaint_ids)
            used_complaint_ids.add(synthesized.complaint_id)
            by_bucket[bucket].append(synthesized)

    selected: dict[PersonaBucket, Persona] = {}
    used_countries: set[str] = set()
    for bucket in BUCKETS:
        options = by_bucket[bucket]
        preferred = [p for p in options if p.country not in used_countries]
        chosen = preferred[0] if preferred else options[0]
        selected[bucket] = chosen
        used_countries.add(chosen.country)

    countries = {p.country for p in selected.values()}
    if len(countries) < 2:
        logger.warning(
            "Selected personas span only %d country/countries (%s) — dataset sample didn't "
            "allow >=2 without an emptier bucket; documented as-is, not a bug.",
            len(countries),
            countries,
        )

    return selected


DEMO_USERNAMES = {
    PersonaBucket.CLEAN_AUTO_RESOLVE: "cliente.claro",
    PersonaBucket.AMBIGUOUS: "cliente.ambiguo",
    PersonaBucket.ESCALATION: "cliente.escalado",
}


def _generate_password() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "demo-" + "".join(secrets.choice(alphabet) for _ in range(8))


def _stringify_row(row: tuple) -> list[str | None]:
    return [str(v) if v is not None else None for v in row]


def _copy_rows_to_fixture(
    con: duckdb.DuckDBPyConnection,
    fcon: duckdb.DuckDBPyConnection,
    source_table: str,
    fixture_table: str,
    key_column: str,
    keys: list[str],
) -> int:
    cursor = con.execute(f"SELECT * FROM {source_table} WHERE {key_column} IN ?", [keys])
    columns = [c[0] for c in cursor.description]
    rows = cursor.fetchall()
    fcon.execute(f"CREATE TABLE {fixture_table} ({', '.join(c + ' VARCHAR' for c in columns)})")
    fcon.executemany(
        f"INSERT INTO {fixture_table} VALUES ({', '.join('?' for _ in columns)})",
        [_stringify_row(row) for row in rows],
    )
    return len(rows)


def _build_fixture_db(
    con: duckdb.DuckDBPyConnection, fixture_path: Path, personas: dict[PersonaBucket, Persona]
) -> None:
    fixture_path.unlink(missing_ok=True)
    fcon = duckdb.connect(str(fixture_path))

    customer_ids = [p.customer_id for p in personas.values()]
    product_ids = [p.affected_product_id for p in personas.values() if p.affected_product_id]
    complaint_ids = [p.complaint_id for p in personas.values()]

    try:
        customer_count = _copy_rows_to_fixture(
            con, fcon, "customers", "customers", "customer_id", customer_ids
        )
        product_count = (
            _copy_rows_to_fixture(con, fcon, "products", "products", "product_id", product_ids)
            if product_ids
            else 0
        )
        complaint_count = _copy_rows_to_fixture(
            con, fcon, "complaints", "complaints", "complaint_id", complaint_ids
        )
        # Every candidate in each persona's search window (not just the matched one): the
        # ambiguous persona's multiple candidates must be queryable live by the app's
        # fuzzy-match code during the demo, not just referenced by ID here.
        txn_count = _copy_rows_to_fixture(
            con, fcon, CANDIDATES_TABLE, "transactions", "customer_id", customer_ids
        )
    finally:
        fcon.close()

    logger.info(
        "Fixture written to %s: %d customers, %d products, %d complaints, %d transactions",
        fixture_path, customer_count, product_count, complaint_count, txn_count,
    )


def _write_demo_users(personas: dict[PersonaBucket, Persona], path: Path) -> dict:
    demo_users = {}
    for bucket, persona in personas.items():
        username = DEMO_USERNAMES[bucket]
        demo_users[username] = {
            "password": _generate_password(),
            "customer_id": persona.customer_id,
            "persona_bucket": bucket,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(demo_users, indent=2, ensure_ascii=False))
    logger.info("Wrote demo credentials to %s (%d personas)", path, len(demo_users))
    return demo_users


def run(
    warehouse_path: Path = DEFAULT_WAREHOUSE_PATH,
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
    demo_users_path: Path = DEFAULT_DEMO_USERS_PATH,
) -> dict:
    load_dotenv(REPO_ROOT / ".env")

    con, s3_bucket = connect_with_s3(warehouse_path)
    try:
        candidates = _load_candidates(con)
        logger.info("Loaded %d candidate complaints", len(candidates))

        needed_dates = _needed_dates(candidates)
        logger.info(
            "Pulling transactions for %d specific dates (persona search windows)", len(needed_dates)
        )
        extract_dates_fact(
            con, s3_bucket, "transactions", needed_dates, local_table=CANDIDATES_TABLE
        )

        personas = _select_personas(con, candidates)
        for bucket, p in personas.items():
            logger.info(
                "Selected persona[%s]: complaint=%s customer=%s country=%s amount=%s %s matches=%s",
                bucket, p.complaint_id, p.customer_id, p.country, p.claimed_amount, p.currency,
                p.matched_transaction_ids,
            )

        _build_fixture_db(con, fixture_path, personas)
        demo_users = _write_demo_users(personas, demo_users_path)
    finally:
        con.execute(f"DROP TABLE IF EXISTS {CANDIDATES_TABLE}")
        con.close()

    return {"personas": {k: asdict(v) for k, v in personas.items()}, "demo_users": list(demo_users)}


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
