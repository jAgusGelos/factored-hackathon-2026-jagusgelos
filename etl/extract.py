"""Offline ETL extraction: pulls the LATAM Bank dataset from S3 into a local,
gitignored DuckDB warehouse (`data/warehouse.duckdb`).

Design (AD-2 in plan.md): this script never runs at request time. The deployed
app never holds AWS credentials or queries S3 — it only ever reads the small
sanitized fixture that `etl/build_fixture.py` derives from this warehouse.

Extraction scope, by table (a deliberate, documented scoping decision, not an
oversight):
  - Dimension tables (customers, products, branches, service_agents,
    daily_exchange_rates): pulled in full — each is a single small-to-medium
    flat CSV, cheap to materialize whole.
  - `complaints`: pulled in full across its entire partition range. The
    priority classifier (AD-6) needs the whole table (chronological split
    over all "Transactions"-category rows, plus a per-customer prior-complaint
    count that can reach back across any category/date).
  - `transactions` / `call_center_interactions` / `call_transcripts`: pulled
    via explicit Hive-partition pruning over a `--start-date`/`--end-date`
    window (default: the most recent `DEFAULT_WINDOW_DAYS` days) — a full
    1097-partition scan is deliberately NOT the default for these three.
    `transactions` (808MB/5M rows) has no consumer that needs the whole
    table: the priority classifier (AD-6) uses complaints/customers/products
    features only, never transactions, and the app's fuzzy-matching only
    ever needs a specific customer's transactions near a specific reported
    date. `call_center_interactions`/`call_transcripts` (800K/200K rows) are
    windowed for the same reason — findings.md verified `origin_interaction_id`
    is empty in 100% of the "Transactions"-category complaints this workflow
    uses, so neither table has a join path into any persona this system will
    ever handle, and AD-6 does not use them as classifier features either.
    The default window still gives all three tables a non-zero row count
    (Task 1.2's verification) without the cost of a full bulk pull that no
    downstream consumer reads. `etl/build_fixture.py` does its OWN separate,
    narrower, per-persona targeted pull of `transactions` for the exact dates
    its chosen personas need (via `extract_dates_fact`), independent of
    whatever window this script's own default run used. The pruning
    mechanism itself — not a full-table scan being the only option — is what
    `tests/test_etl_freshness.py` proves with a local, S3-independent test.

Usage:
    python etl/extract.py
    python etl/extract.py --start-date 2026-06-01 --end-date 2026-06-17
    python etl/extract.py --tables complaints,transactions
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TypeVar

import duckdb
from dotenv import load_dotenv

from etl.schema_contract import ALL_TABLES, TABLES_BY_NAME, TableKind, validate_header

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.extract")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"
DEFAULT_MANIFEST_PATH = DATA_DIR / "extraction_manifest.json"

DATASET_VERSION = "1.0.0"
DATASET_START_DATE = date(2023, 6, 17)
DATASET_END_DATE = date(2026, 6, 17)
DEFAULT_WINDOW_DAYS = 30
DEFAULT_WINDOW_START = DATASET_END_DATE - timedelta(days=DEFAULT_WINDOW_DAYS)

DIMENSION_TABLES = tuple(t.name for t in ALL_TABLES if t.kind is TableKind.DIMENSION)
FULLY_EXTRACTED_FACT_TABLES = ("complaints",)
# Windowed rather than fully extracted: see the module docstring's "Extraction scope".
WINDOWED_FACT_TABLES = ("transactions", "call_center_interactions", "call_transcripts")


@dataclass
class ExtractionResult:
    table: str
    row_count: int
    partitions_pulled: int | None  # None for dimension tables (single file)
    unexpected_columns: list[str]


T = TypeVar("T")


def _with_retries(fn: Callable[[], T], *, attempts: int = 3, base_delay_seconds: float = 2.0) -> T:
    """Retry a flaky S3/httpfs operation with exponential backoff.

    S3 GETs under DuckDB's parallel glob scanning occasionally return a
    transient HTTP error (observed: sporadic HTTP 400 on an object that
    exists and is fetchable in isolation) — the same class of network
    flakiness plan.md's NFR section requires the app to tolerate for LLM/tool
    calls. The ETL is offline and re-runnable, so a bounded retry here is
    strictly better than either a silent partial extraction or a hard crash
    on a transient blip.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except duckdb.duckdb.HTTPException as exc:
            last_exc = exc
            # A 404 is a definitively missing object, never a transient
            # fault — retrying it just burns time. Fail fast so the caller
            # (materialize_table_from_files) can drop it and move on.
            if "404" in str(exc) or attempt == attempts:
                break
            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Transient S3/httpfs error (attempt %d/%d), retrying in %.1fs: %s",
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _s3_settings_from_env() -> dict[str, str]:
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("AWS_REGION")
    bucket = os.environ.get("DATA_BUCKET")
    missing = [
        name
        for name, val in (
            ("AWS_ACCESS_KEY_ID", access_key),
            ("AWS_SECRET_ACCESS_KEY", secret_key),
            ("AWS_REGION", region),
            ("DATA_BUCKET", bucket),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required .env variable(s): {missing}. "
            "Copy .env.example to .env and fill in the dataset credentials."
        )
    return {
        "access_key": access_key,
        "secret_key": secret_key,
        "region": region,
        "bucket": bucket,
    }


def connect(warehouse_path: Path) -> tuple[duckdb.DuckDBPyConnection, str]:
    warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse_path))
    con.execute("INSTALL httpfs;")
    con.execute("LOAD httpfs;")
    s3 = _s3_settings_from_env()
    con.execute(f"SET s3_region='{s3['region']}';")
    con.execute(f"SET s3_access_key_id='{s3['access_key']}';")
    con.execute(f"SET s3_secret_access_key='{s3['secret_key']}';")
    # A full-table glob over ~1097 partition files triggers sporadic HTTP 400s
    # under DuckDB's default settings (observed directly during this ETL's
    # development — a real, verified data-pipeline reliability issue, not a
    # hypothetical). Disabling keep-alive "fixed" it but made every request
    # pay a fresh TLS handshake, turning a multi-minute pull into a 10+
    # minute one — not an acceptable trade for a ~0.2% per-request failure
    # rate. Raising DuckDB's own internal retry budget (which retries a
    # single failing GET in place, keep-alive intact) plus the outer
    # `_with_retries` wrapper (which retries the whole glob operation) is
    # the actual fix: cheap on the common case, resilient on the flaky one.
    con.execute("SET http_retries=6;")
    con.execute("SET http_retry_wait_ms=300;")
    con.execute("SET http_retry_backoff=2;")
    return con, s3["bucket"]


def _dimension_url(bucket: str, table: str) -> str:
    return f"s3://{bucket}/data/{table}.csv"


def _partition_glob(bucket: str, table: str) -> str:
    return f"s3://{bucket}/data/{table}/year=*/month=*/day=*/*.csv"


def _partition_file(bucket: str, table: str, d: date) -> str:
    ymd = d.strftime("%Y%m%d")
    return (
        f"s3://{bucket}/data/{table}/year={d.year:04d}/month={d.month:02d}/"
        f"day={d.day:02d}/{table}_{ymd}.csv"
    )


def _daterange_glob(bucket: str, table: str, start: date, end: date) -> list[str]:
    """Explicit Hive-partition pruning: enumerate exact partition files for
    [start, end] instead of relying on a wildcard glob over the whole table.
    This is what makes the date-range pruning testable/provable, not just a
    convention.
    """
    files = []
    d = start
    while d <= end:
        files.append(_partition_file(bucket, table, d))
        d += timedelta(days=1)
    return files


def _read_header(con: duckdb.DuckDBPyConnection, url: str) -> list[str]:
    cols = con.execute(f"SELECT * FROM read_csv_auto('{url}') LIMIT 0").description
    return [c[0] for c in cols]


def validate_schema(con: duckdb.DuckDBPyConnection, bucket: str) -> dict[str, list[str]]:
    """Validate every contracted table's real CSV header against schema_contract.py.

    For fact tables, checks BOTH the earliest and latest partition in the
    dataset's documented date range — this is what actually exercises the
    "schema evolution over time" risk the brief calls out, not just a single
    spot check.
    """
    unexpected: dict[str, list[str]] = {}

    for table in DIMENSION_TABLES:
        contract = TABLES_BY_NAME[table]
        actual_columns = _read_header(con, _dimension_url(bucket, table))
        extra = validate_header(contract, actual_columns)
        unexpected[table] = extra
        logger.info("Schema OK: %s (%d columns, %d unexpected)", table, len(actual_columns), len(extra))

    fact_tables = FULLY_EXTRACTED_FACT_TABLES + WINDOWED_FACT_TABLES
    for table in fact_tables:
        contract = TABLES_BY_NAME[table]
        for d, label in ((DATASET_START_DATE, "earliest"), (DATASET_END_DATE, "latest")):
            actual_columns = _read_header(con, _partition_file(bucket, table, d))
            extra = validate_header(contract, actual_columns)
            unexpected.setdefault(table, [])
            for c in extra:
                if c not in unexpected[table]:
                    unexpected[table].append(c)
            logger.info(
                "Schema OK: %s [%s partition %s] (%d columns, %d unexpected)",
                table,
                label,
                d,
                len(actual_columns),
                len(extra),
            )

    return unexpected


def extract_dimension(con: duckdb.DuckDBPyConnection, bucket: str, table: str) -> ExtractionResult:
    url = _dimension_url(bucket, table)
    con.execute(f"DROP TABLE IF EXISTS {table};")
    _with_retries(
        lambda: con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto('{url}');")
    )
    row_count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return ExtractionResult(table=table, row_count=row_count, partitions_pulled=None, unexpected_columns=[])


def extract_full_fact(con: duckdb.DuckDBPyConnection, bucket: str, table: str) -> ExtractionResult:
    glob = _partition_glob(bucket, table)
    con.execute(f"DROP TABLE IF EXISTS {table};")
    _with_retries(
        lambda: con.execute(
            f"""
            CREATE TABLE {table} AS
            SELECT *, filename AS _source_file
            FROM read_csv_auto('{glob}', hive_partitioning=1, filename=1, union_by_name=1)
            """
        )
    )
    row_count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    partitions = con.execute(f"SELECT COUNT(DISTINCT _source_file) FROM {table}").fetchone()[0]
    return ExtractionResult(
        table=table, row_count=row_count, partitions_pulled=partitions, unexpected_columns=[]
    )


_MISSING_FILE_RE = re.compile(r'"([^"]+)":\s*404')
MAX_MISSING_PARTITION_REMOVALS = 100


def materialize_table_from_files(
    con: duckdb.DuckDBPyConnection, table: str, files: list[str]
) -> int:
    """Rebuilds `table` from scratch from an explicit list of CSV file paths
    (S3 URLs or local paths — this function doesn't care which).

    Full-replace (DROP + recreate), not an incremental upsert: this is what
    makes re-running extraction for the same window idempotent by
    construction — a late-arriving or corrected partition file simply becomes
    the table's content on the next re-run, with no possibility of stale
    duplicate rows from a prior run. Proven by `tests/test_etl_freshness.py`.

    Unlike `extract_full_fact`'s wildcard glob (which DuckDB resolves via an
    S3 LIST, silently only matching files that exist), this function is
    given an EXPLICIT file list — so a date with no partition file at all
    (a genuinely missing/late-arriving partition, exactly the dataset's
    documented characteristic — verified directly: e.g. no
    `transactions_20230618.csv` partition exists) surfaces as an HTTP 404,
    not something a transient-error retry can ever fix. Those are detected
    and dropped from the list (logged, not silently ignored), while any
    other HTTP error still goes through `_with_retries` as a transient fault.
    """
    files = list(files)
    removed = 0

    def _create(files_sql: str) -> None:
        con.execute(
            f"""
            CREATE TABLE {table} AS
            SELECT *, filename AS _source_file
            FROM read_csv_auto([{files_sql}], filename=1, union_by_name=1)
            """
        )

    while True:
        files_sql = ", ".join(f"'{f}'" for f in files)
        con.execute(f"DROP TABLE IF EXISTS {table};")
        try:
            _with_retries(lambda files_sql=files_sql: _create(files_sql))
            break
        except duckdb.duckdb.HTTPException as exc:
            match = _MISSING_FILE_RE.search(str(exc))
            # The exception reports the HTTPS-translated URL, not the s3://
            # URI stored in `files` — match by filename (unique per
            # partition day) rather than exact string equality.
            missing_filename = match.group(1).rsplit("/", 1)[-1] if match else None
            candidates = [f for f in files if missing_filename and f.endswith(missing_filename)]
            if candidates and removed < MAX_MISSING_PARTITION_REMOVALS:
                for f in candidates:
                    files.remove(f)
                removed += len(candidates)
                logger.warning(
                    "Partition file does not exist (404) — dropping from this extraction "
                    "(late-arriving/missing partition, not a transient fault): %s",
                    candidates,
                )
                continue
            raise
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def extract_windowed_fact(
    con: duckdb.DuckDBPyConnection, bucket: str, table: str, start: date, end: date
) -> ExtractionResult:
    files = _daterange_glob(bucket, table, start, end)
    row_count = materialize_table_from_files(con, table, files)
    return ExtractionResult(
        table=table, row_count=row_count, partitions_pulled=len(files), unexpected_columns=[]
    )


def extract_dates_fact(
    con: duckdb.DuckDBPyConnection,
    bucket: str,
    source_table: str,
    dates: set[date],
    local_table: str | None = None,
) -> ExtractionResult:
    """Pulls exactly the given (deduplicated) partition dates for
    `source_table` (the REAL S3 table name, e.g. "transactions" — this is
    what the partition file paths/names are built from) and materializes
    them into `local_table` (defaults to `source_table`) in the local
    warehouse. Used by `etl/build_fixture.py` to fetch only the specific days
    each selected persona's transaction-matching window needs, instead of a
    contiguous range that would include many unrelated days, into a
    differently-named working table without clobbering `source_table`'s own
    extraction from a prior `extract.py` run.
    """
    local_table = local_table or source_table
    sorted_dates = sorted(dates)
    files = [_partition_file(bucket, source_table, d) for d in sorted_dates]
    row_count = materialize_table_from_files(con, local_table, files)
    return ExtractionResult(
        table=local_table, row_count=row_count, partitions_pulled=len(files), unexpected_columns=[]
    )


def _extract_logged(
    extractor: Callable[[str], ExtractionResult],
    table: str,
    unexpected_columns: dict[str, list[str]],
) -> ExtractionResult:
    logger.info("Extracting table: %s", table)
    res = extractor(table)
    logger.info("  -> %s rows across %s partitions", res.row_count, res.partitions_pulled)
    return replace(res, unexpected_columns=unexpected_columns.get(table, []))


def run(
    warehouse_path: Path = DEFAULT_WAREHOUSE_PATH,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    start_date: date = DEFAULT_WINDOW_START,
    end_date: date = DATASET_END_DATE,
    tables: list[str] | None = None,
) -> dict:
    load_dotenv(REPO_ROOT / ".env")
    con, bucket = connect(warehouse_path)

    selected = set(tables) if tables else None

    logger.info("Validating schema contract against real S3 headers...")
    unexpected_columns = validate_schema(con, bucket)

    logger.info("Windowed fact tables use the %s..%s partition range", start_date, end_date)
    extract_dimension_table = partial(extract_dimension, con, bucket)
    extract_full_fact_table = partial(extract_full_fact, con, bucket)
    extract_windowed_fact_table = partial(
        extract_windowed_fact, con, bucket, start=start_date, end=end_date
    )
    extraction_plan: list[tuple[str, Callable[[str], ExtractionResult]]] = [
        *((t, extract_dimension_table) for t in DIMENSION_TABLES),
        *((t, extract_full_fact_table) for t in FULLY_EXTRACTED_FACT_TABLES),
        *((t, extract_windowed_fact_table) for t in WINDOWED_FACT_TABLES),
    ]
    results = [
        _extract_logged(extractor, table, unexpected_columns)
        for table, extractor in extraction_plan
        if not selected or table in selected
    ]

    con.close()

    manifest = {
        "dataset_version": DATASET_VERSION,
        "extraction_timestamp": datetime.now(UTC).isoformat(),
        "warehouse_path": str(warehouse_path.resolve().relative_to(REPO_ROOT)),
        "windowed_tables": list(WINDOWED_FACT_TABLES),
        "windowed_date_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "tables": [asdict(r) for r in results],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    logger.info("Wrote extraction manifest to %s", manifest_path)

    return manifest


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        type=str,
        default=DEFAULT_WINDOW_START.isoformat(),
        help=f"Start of the windowed-fact-table pull (default: last {DEFAULT_WINDOW_DAYS} days)",
    )
    parser.add_argument("--end-date", type=str, default=DATASET_END_DATE.isoformat())
    parser.add_argument("--tables", type=str, default=None, help="Comma-separated table names")
    parser.add_argument("--warehouse", type=str, default=str(DEFAULT_WAREHOUSE_PATH))
    parser.add_argument("--manifest", type=str, default=str(DEFAULT_MANIFEST_PATH))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    tables = args.tables.split(",") if args.tables else None
    manifest = run(
        warehouse_path=Path(args.warehouse),
        manifest_path=Path(args.manifest),
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        tables=tables,
    )
    total_rows = sum(t["row_count"] for t in manifest["tables"])
    logger.info("Extraction complete: %d table(s), %d total rows", len(manifest["tables"]), total_rows)
    for t in manifest["tables"]:
        if t["row_count"] == 0:
            logger.error("Table %s has ZERO rows — this should never happen.", t["table"])
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
