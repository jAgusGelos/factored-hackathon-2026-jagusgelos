"""ETL quality checks + lineage manifest writer.

Reads the local DuckDB warehouse produced by `etl/extract.py` plus its
`data/extraction_manifest.json`, runs concrete data-quality checks against the
documented expectations (challenge-brief.md: ~2% duplicates, ~5% nulls in
nullable fields, some referential-integrity gaps), and writes the combined
result — extraction lineage + quality metrics — to `data/lineage_manifest.json`.

This never fabricates a "looks fine" pass — every check reports a real number,
and a check whose result falls outside the documented tolerance band is
flagged explicitly in the report rather than silently ignored, per the
brief's own framing: the dataset intentionally has non-zero defect rates, so
"documented tolerance, not zero-defect" is the correct pass condition.

Known, expected out-of-band results (verified against real data, not
defects): `complaints` and `transactions` both show `mean_null_rate_in_band
= False` (~20-30%, vs. the ~5% documented for "nullable fields" generally).
This is `mean_null_rate` being a blunt, table-wide average across ALL
columns, including several `complaints` lifecycle fields that are null BY
DESIGN until a case reaches that stage (`resolution_date`, `closing_date`,
`assigned_agent_id`, `resolution`, `compensation_granted`,
`resolution_satisfaction`, ...) and several `transactions` fields that only
apply to specific channels/types (`branch_id`, `merchant_name`,
`merchant_category`, `latitude`/`longitude`). The per-column
`column_null_rates` in the report is what to read for the real per-field
picture — the aggregate is intentionally conservative (flags rather than
hides), not a false alarm to "fix" by loosening the band.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from etl.extract import DATA_DIR, DEFAULT_WAREHOUSE_PATH
from etl.extract import DEFAULT_MANIFEST_PATH as DEFAULT_EXTRACTION_MANIFEST_PATH
from etl.schema_contract import TABLES_BY_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.quality_checks")

DEFAULT_LINEAGE_MANIFEST_PATH = DATA_DIR / "lineage_manifest.json"

# Documented tolerance bands from challenge-brief.md ("Intentional data quality
# issues: ~2% duplicate records, ~5% nulls in nullable fields"). These are
# generous bands around the documented figures, not exact-match assertions —
# the brief itself says "~", and a windowed (not full-range) transactions pull
# can shift the duplicate rate slightly versus the full-table figure.
DUPLICATE_RATE_BAND = (0.0, 0.05)  # 0%-5%, documented ~2%
NULL_RATE_BAND = (0.0, 0.15)  # 0%-15% mean across columns, documented ~5%

# (table, fk_column, referenced_table, referenced_column) — only checked when
# both tables are present in the warehouse.
FK_CHECKS = [
    ("complaints", "customer_id", "customers", "customer_id"),
    ("complaints", "affected_product_id", "products", "product_id"),
]


@dataclass
class ColumnNullRate:
    column: str
    null_rate: float


@dataclass
class TableQualityReport:
    table: str
    row_count: int
    documented_row_count: int | None
    row_count_ratio: float | None
    is_full_extraction: bool
    duplicate_key_rate: float | None
    duplicate_key_in_band: bool | None
    mean_null_rate: float
    mean_null_rate_in_band: bool
    column_null_rates: list[ColumnNullRate]
    non_nullable_key_violations: dict[str, int]


@dataclass
class FKQualityReport:
    table: str
    fk_column: str
    referenced_table: str
    referenced_column: str
    orphaned_rows: int
    total_non_null_rows: int
    orphaned_rate: float


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return row[0] > 0


def _in_band(value: float, band: tuple[float, float]) -> bool:
    return band[0] <= value <= band[1]


def _duplicate_key_rate(
    con: duckdb.DuckDBPyConnection, table: str, pk: str | None, row_count: int
) -> float | None:
    if pk is None or row_count == 0:
        return None
    dup_rows = con.execute(
        f"""
        SELECT COALESCE(SUM(cnt - 1), 0) FROM (
            SELECT COUNT(*) AS cnt FROM {table} GROUP BY {pk}
        )
        """
    ).fetchone()[0]
    return dup_rows / row_count


def _column_null_counts(
    con: duckdb.DuckDBPyConnection, table: str, row_count: int
) -> dict[str, int]:
    columns = [c[0] for c in con.execute(f"SELECT * FROM {table} LIMIT 0").description]
    if row_count == 0:
        return dict.fromkeys(columns, 0)
    return {
        col: con.execute(f'SELECT COUNT(*) FROM {table} WHERE "{col}" IS NULL').fetchone()[0]
        for col in columns
    }


def check_table(
    con: duckdb.DuckDBPyConnection, table: str, is_full_extraction: bool = True
) -> TableQualityReport:
    row_count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    contract = TABLES_BY_NAME.get(table)
    documented_row_count = contract.documented_row_count if contract else None
    pk = contract.primary_key if contract else None
    row_count_ratio = (
        round(row_count / documented_row_count, 4) if documented_row_count else None
    )

    duplicate_rate = _duplicate_key_rate(con, table, pk, row_count)

    null_counts = _column_null_counts(con, table, row_count)
    column_null_rates = [
        ColumnNullRate(column=col, null_rate=round(count / row_count, 4) if row_count else 0.0)
        for col, count in null_counts.items()
    ]
    mean_null_rate = (
        sum(c.null_rate for c in column_null_rates) / len(column_null_rates)
        if column_null_rates
        else 0.0
    )
    # A primary key must never be null: a hard integrity check, not a tolerance-band one.
    pk_null_count = null_counts.get(pk, 0) if pk else 0
    non_nullable_violations = {pk: pk_null_count} if pk and pk_null_count > 0 else {}

    return TableQualityReport(
        table=table,
        row_count=row_count,
        documented_row_count=documented_row_count,
        row_count_ratio=row_count_ratio,
        is_full_extraction=is_full_extraction,
        duplicate_key_rate=round(duplicate_rate, 4) if duplicate_rate is not None else None,
        duplicate_key_in_band=(
            _in_band(duplicate_rate, DUPLICATE_RATE_BAND) if duplicate_rate is not None else None
        ),
        mean_null_rate=round(mean_null_rate, 4),
        mean_null_rate_in_band=_in_band(mean_null_rate, NULL_RATE_BAND),
        column_null_rates=column_null_rates,
        non_nullable_key_violations=non_nullable_violations,
    )


def check_fk(
    con: duckdb.DuckDBPyConnection, table: str, fk_col: str, ref_table: str, ref_col: str
) -> FKQualityReport:
    total_non_null = con.execute(
        f'SELECT COUNT(*) FROM {table} WHERE "{fk_col}" IS NOT NULL'
    ).fetchone()[0]
    orphaned = con.execute(
        f"""
        SELECT COUNT(*) FROM {table} t
        WHERE t."{fk_col}" IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM {ref_table} r WHERE r."{ref_col}" = t."{fk_col}")
        """
    ).fetchone()[0]
    rate = orphaned / total_non_null if total_non_null else 0.0
    return FKQualityReport(
        table=table,
        fk_column=fk_col,
        referenced_table=ref_table,
        referenced_column=ref_col,
        orphaned_rows=orphaned,
        total_non_null_rows=total_non_null,
        orphaned_rate=round(rate, 4),
    )


def _log_table_report(report: TableQualityReport) -> None:
    table = report.table
    if report.documented_row_count and not report.is_full_extraction:
        logger.info(
            "  %s: %d rows (windowed extraction — NOT compared against documented total %d)",
            table, report.row_count, report.documented_row_count,
        )
    elif report.row_count_ratio is not None:
        logger.info(
            "  %s: %d rows vs documented %d (ratio=%s)",
            table, report.row_count, report.documented_row_count, report.row_count_ratio,
        )
    logger.info(
        "  %s: %d rows, dup_rate=%s (in band: %s), mean_null_rate=%s (in band: %s)",
        table,
        report.row_count,
        report.duplicate_key_rate,
        report.duplicate_key_in_band,
        report.mean_null_rate,
        report.mean_null_rate_in_band,
    )
    if report.non_nullable_key_violations:
        logger.error(
            "  %s: primary key column(s) have unexpected NULLs: %s",
            table,
            report.non_nullable_key_violations,
        )


def _checked_fk(
    con: duckdb.DuckDBPyConnection, table: str, fk_col: str, ref_table: str, ref_col: str
) -> FKQualityReport:
    logger.info("Checking FK: %s.%s -> %s.%s", table, fk_col, ref_table, ref_col)
    fk_report = check_fk(con, table, fk_col, ref_table, ref_col)
    logger.info(
        "  orphaned_rate=%s (%d/%d)",
        fk_report.orphaned_rate,
        fk_report.orphaned_rows,
        fk_report.total_non_null_rows,
    )
    return fk_report


def run(
    warehouse_path: Path = DEFAULT_WAREHOUSE_PATH,
    extraction_manifest_path: Path = DEFAULT_EXTRACTION_MANIFEST_PATH,
    lineage_manifest_path: Path = DEFAULT_LINEAGE_MANIFEST_PATH,
) -> dict:
    con = duckdb.connect(str(warehouse_path), read_only=True)

    extraction_manifest = json.loads(extraction_manifest_path.read_text())
    windowed_tables = set(extraction_manifest.get("windowed_tables", []))

    table_reports = []
    for entry in extraction_manifest["tables"]:
        table = entry["table"]
        if not _table_exists(con, table):
            logger.warning("Table %s in extraction manifest but not found in warehouse; skipping", table)
            continue
        logger.info("Running quality checks: %s", table)
        report = check_table(con, table, is_full_extraction=table not in windowed_tables)
        table_reports.append(report)
        _log_table_report(report)

    checked_tables = {r.table for r in table_reports}
    fk_reports = [
        _checked_fk(con, table, fk_col, ref_table, ref_col)
        for table, fk_col, ref_table, ref_col in FK_CHECKS
        if table in checked_tables and ref_table in checked_tables
    ]

    con.close()

    lineage_manifest = {
        **extraction_manifest,
        "quality_checks": {
            "tables": [asdict(r) for r in table_reports],
            "foreign_keys": [asdict(r) for r in fk_reports],
            "duplicate_rate_band": list(DUPLICATE_RATE_BAND),
            "null_rate_band": list(NULL_RATE_BAND),
        },
    }
    lineage_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lineage_manifest_path.write_text(json.dumps(lineage_manifest, indent=2, ensure_ascii=False))
    logger.info("Wrote lineage manifest to %s", lineage_manifest_path)

    return lineage_manifest


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
