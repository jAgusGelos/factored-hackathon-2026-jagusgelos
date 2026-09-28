"""Late-arrival / freshness test fixture (Task 1.4).

The dataset itself is static (per challenge-brief.md), so per the brief's own
guidance ("If only static data is supplied, demonstrate update correctness
with a clearly labeled test fixture"), this test simulates an out-of-order
partition drop with local CSV files and proves the extraction step re-ingests
it idempotently — no S3 access, no dependency on the real warehouse.
"""

from __future__ import annotations

import duckdb

from etl.extract import materialize_table_from_files


def _write_partition_csv(path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("id,value\n" + "\n".join(rows) + "\n")


def test_late_arriving_partition_reingest_is_idempotent(tmp_path):
    day1 = tmp_path / "data" / "testtable" / "year=2023" / "month=06" / "day=17" / "t_20230617.csv"
    day2 = tmp_path / "data" / "testtable" / "year=2023" / "month=06" / "day=18" / "t_20230618.csv"

    # Initial ETL run: day1 has 2 rows, day2's partition hasn't arrived yet
    # (a genuinely late-arriving partition — this is the documented risk, not
    # a corrupted file).
    _write_partition_csv(day1, ["1,alpha", "2,beta"])

    con = duckdb.connect()
    files = [str(day1)]
    row_count = materialize_table_from_files(con, "testtable", files)
    assert row_count == 2

    # day2's partition arrives late. Re-run extraction over the same window,
    # now including it.
    _write_partition_csv(day2, ["3,gamma"])
    files = [str(day1), str(day2)]
    row_count = materialize_table_from_files(con, "testtable", files)

    assert row_count == 3  # not 5 — a naive append would have double-counted day1
    ids = {r[0] for r in con.execute("SELECT id FROM testtable ORDER BY id").fetchall()}
    assert ids == {1, 2, 3}


def test_corrected_partition_reingest_overwrites_not_appends(tmp_path):
    """A partition that arrives, then is later corrected/reprocessed with
    different row content for the same date, must fully replace the old
    content on re-run — never leave stale rows from the earlier version
    sitting alongside the corrected ones.
    """
    day1 = tmp_path / "data" / "testtable" / "year=2023" / "month=06" / "day=17" / "t_20230617.csv"

    _write_partition_csv(day1, ["1,alpha", "2,beta"])
    con = duckdb.connect()
    row_count = materialize_table_from_files(con, "testtable", [str(day1)])
    assert row_count == 2

    # The same partition file is corrected/reprocessed with different content.
    _write_partition_csv(day1, ["1,alpha-corrected", "2,beta-corrected", "3,new-row"])
    row_count = materialize_table_from_files(con, "testtable", [str(day1)])

    assert row_count == 3
    values = {r[0] for r in con.execute("SELECT value FROM testtable").fetchall()}
    assert values == {"alpha-corrected", "beta-corrected", "new-row"}
    assert "alpha" not in values  # the stale pre-correction row must be gone, not just appended-past
