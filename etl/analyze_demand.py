"""Offline demand analysis: reads the local DuckDB warehouse and writes a
committed, reproducible demand report under `docs/analysis/`.

The JSON report is the source of truth (AD-2 in the demand-analysis plan);
the markdown and the charts are rendered only from it. Every headline number
is an object `{value, n, kind, ...}` whose `kind` says what it is: `measured`
(computed here from the warehouse), `assumed` (a stated input with no data
behind it), `simulated` (from the offline eval, quoted from the committed
snapshot), `projection` (arithmetic over the others) or `design-argument`.
Every percentile carries its `n` and `coverage`.

This module never imports `app` or `eval` (AD-1): `app.config` loads `.env`
and `eval.run_eval` pulls in the Anthropic client, neither of which belongs
in an offline analysis. The eval's numbers come from
`docs/analysis/inputs/eval_cost_snapshot.json`, refreshed from the gitignored
`data/eval_report.json` with `--refresh-eval-snapshot` (AD-6).

Usage:
    python -m etl.analyze_demand
    python -m etl.analyze_demand --refresh-eval-snapshot
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
from datetime import date, datetime
from pathlib import Path

import duckdb

from etl.demand_labels import Kind
from etl.demand_report_render import reason_row, render_charts, render_markdown
from etl.extract import DATA_DIR, DEFAULT_WAREHOUSE_PATH, REPO_ROOT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.analyze_demand")

DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "analysis"
DEFAULT_SNAPSHOT_PATH = DEFAULT_OUT_DIR / "inputs" / "eval_cost_snapshot.json"
DEFAULT_EVAL_REPORT_PATH = DATA_DIR / "eval_report.json"
REPORT_JSON_NAME = "demand-report.json"
REPORT_MD_NAME = "demand-report.md"

# Paths as they appear inside the report: repo-relative, never the machine's.
WAREHOUSE_SOURCE = "data/warehouse.duckdb"
SNAPSHOT_SOURCE = "docs/analysis/inputs/eval_cost_snapshot.json"
EVAL_REPORT_SOURCE = "data/eval_report.json"

SCHEMA_VERSION = 1



SECONDS_PER_HOUR = 3600
HOURS_UNIT = "calendar hours from creation_date"

FOCUS_SUBCATEGORY = "Cargo no reconocido"
# Calendar hours. app.policy.ESCALATION_CONTACT_BUSINESS_DAYS = 3 promises
# business days, and 3 business days always span at least 72 calendar hours,
# so a share measured against 72 h is a lower bound for that promise.
CONTACT_WINDOW_HOURS = 72
CLOSED_STATUSES = ("Resolved", "Closed")

# A category's weekly demand is "flat" when its share of all complaints is in
# this band (5 categories: 20% each) and its weekly coefficient of variation is
# within this multiple of the Poisson expectation 1/sqrt(mean weekly count).
FLAT_SHARE_BAND = (0.19, 0.21)
FLAT_CV_TOLERANCE = 1.25

ANCHOR_CONTACT_REASON = "Transaccional"
UPPER_ANCHOR_CONTACT_REASON = "Queja"

# Projection inputs (AD-5). Rates have no cited source; they are labeled assumed.
HOURLY_RATES_USD = (5, 10, 20)
ASSUMED_AUTOMATION_SHARE = 0.5
SCENARIO_SUITE_LABEL = "scenario-suite outcome, not a population estimate"
PROJECTION_PREREQUISITE = (
    "Real complaint-to-transaction linkage or a redesigned intake is required before any "
    "automation share can be claimed."
)

# AD-6 allowlist: the only keys the committed snapshot may hold. `latency_seconds`
# is added to the plan's list because cost block A quotes the agent's pipeline time.
# The eval's free-text notes are left out: the report never quotes them.
SNAPSHOT_KEYS = {
    "source": None,
    "disclosure": None,
    "sample_size": None,
    "safe_automated_resolution_rate": ("count", "of_attempted", "rate"),
    "estimated_cost_usd": (
        "per_attempted_case_mean", "per_successful_resolution", "pricing_source", "method",
    ),
    "latency_seconds": ("p50", "p95"),
    "real_data_match_rate_finding": ("sample_size", "real_matches_found", "source"),
}
OPTIONAL_SNAPSHOT_KEY = "real_data_match_rate_finding"


def _share(part: int | float, whole: int | float) -> float | None:
    return round(part / whole, 4) if whole else None


def _hours(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _usd(value: float) -> float:
    return round(value, 4)


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _hours_since_creation_sql(end_column: str) -> str:
    return f"date_diff('second', creation_date, {end_column}) / {SECONDS_PER_HOUR}.0"


def _distribution(con: duckdb.DuckDBPyConnection, key_sql: str, from_sql: str) -> dict:
    """Count and share per key, ordered by count descending then key (NULL last)."""
    rows = con.execute(
        f"SELECT {key_sql} AS key, count(*) AS c FROM {from_sql} "
        "GROUP BY 1 ORDER BY c DESC, key NULLS LAST"
    ).fetchall()
    total = sum(c for _, c in rows)
    return {
        "kind": Kind.MEASURED,
        "n": total,
        "rows": [{"key": key, "count": c, "share": _share(c, total)} for key, c in rows],
    }


def demand_by_category(con: duckdb.DuckDBPyConnection) -> dict:
    return _distribution(con, "category", "complaints")


def demand_by_subcategory(con: duckdb.DuckDBPyConnection) -> dict:
    """Subcategory counts within each category; a NULL subcategory is its own key."""
    rows = con.execute(
        "SELECT category, subcategory, count(*) AS c, "
        "sum(count(*)) OVER (PARTITION BY category) AS category_total "
        "FROM complaints GROUP BY 1, 2 "
        "ORDER BY c DESC, category NULLS LAST, subcategory NULLS LAST"
    ).fetchall()
    return {
        "kind": Kind.MEASURED,
        "n": sum(r[2] for r in rows),
        "rows": [
            {
                "category": category,
                "subcategory": subcategory,
                "count": c,
                "share_of_category": _share(c, int(category_total)),
            }
            for category, subcategory, c, category_total in rows
        ],
    }


def demand_by_channel(con: duckdb.DuckDBPyConnection) -> dict:
    return _distribution(con, "reception_channel", "complaints")


def demand_by_country(con: duckdb.DuckDBPyConnection) -> dict:
    """Country of the complaining customer; complaints whose customer is not in
    `customers` stay as a NULL key and lower the coverage.
    """
    result = _distribution(
        con, "cu.country",
        "complaints co LEFT JOIN customers cu ON co.customer_id = cu.customer_id",
    )
    matched = con.execute(
        "SELECT count(*) FROM complaints co "
        "JOIN customers cu ON co.customer_id = cu.customer_id"
    ).fetchone()[0]
    result["coverage"] = _share(matched, result["n"])
    return result


def weekly_demand(con: duckdb.DuckDBPyConnection) -> dict:
    """Monday-based weekly counts per category. The first and last weeks are
    flagged partial when the data starts after their Monday or ends before
    their Sunday, and excluded from the coefficient of variation.
    """
    rows = con.execute(
        "SELECT date_trunc('week', creation_date)::DATE AS week, category, count(*) "
        "FROM complaints WHERE creation_date IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY 1, 2 NULLS LAST"
    ).fetchall()
    first_day, last_day = con.execute(
        "SELECT min(creation_date)::DATE, max(creation_date)::DATE FROM complaints"
    ).fetchone()
    categories = sorted({c for _, c, _ in rows if c is not None})
    counts: dict[date, dict[str | None, int]] = {}
    for week, category, c in rows:
        counts.setdefault(week, {})[category] = c
    week_starts = sorted(counts)

    weeks = []
    for i, week in enumerate(week_starts):
        partial = (i == 0 and first_day > week) or (
            i == len(week_starts) - 1 and (last_day - week).days < 6
        )
        weeks.append({
            "week_start": week.isoformat(),
            "partial": partial,
            "total": sum(counts[week].values()),
            "by_category": {c: counts[week].get(c, 0) for c in categories},
        })

    total = sum(w["total"] for w in weeks)
    full = [w for w in weeks if not w["partial"]]
    return {
        "kind": Kind.MEASURED,
        "n": total,
        "week_start_day": "Monday",
        "full_weeks": len(full),
        "partial_weeks": len(weeks) - len(full),
        "flat_rule": {
            "share_band": list(FLAT_SHARE_BAND),
            "cv_tolerance_vs_poisson": FLAT_CV_TOLERANCE,
        },
        "variability": [_category_variability(category, weeks, total) for category in categories],
        "weeks": weeks,
    }


def _category_variability(category: str, weeks: list[dict], total: int) -> dict:
    """Share over all weeks; coefficient of variation over full weeks only."""
    series = [w["by_category"][category] for w in weeks if not w["partial"]]
    share = _share(sum(w["by_category"][category] for w in weeks), total)
    mean = statistics.fmean(series) if series else 0.0
    cv = statistics.pstdev(series) / mean if mean else None
    expected = 1 / math.sqrt(mean) if mean else None
    flat = (
        cv is not None
        and share is not None
        and FLAT_SHARE_BAND[0] <= share <= FLAT_SHARE_BAND[1]
        and cv <= FLAT_CV_TOLERANCE * expected
    )
    return {
        "category": category,
        "share": share,
        "full_weeks": len(series),
        "mean_weekly": round(mean, 1),
        "cv": None if cv is None else round(cv, 4),
        "poisson_expected_cv": None if expected is None else round(expected, 4),
        "flat": flat,
    }


def _elapsed_hours_by_category(con: duckdb.DuckDBPyConnection, end_column: str) -> dict:
    """p50/p90 of hours from creation to `end_column` per category, over the
    rows where `end_column` is set; coverage is that n over the category total.
    """
    rows = con.execute(
        "SELECT category, count(*) AS total, count(h) AS n, "
        "quantile_cont(h, 0.5), quantile_cont(h, 0.9) FROM ("
        f"  SELECT category, {_hours_since_creation_sql(end_column)} AS h FROM complaints"
        ") GROUP BY 1 ORDER BY 1 NULLS LAST"
    ).fetchall()
    return {
        "kind": Kind.MEASURED,
        "unit": HOURS_UNIT,
        "rows": [
            {
                "category": category,
                "kind": Kind.MEASURED,
                "total": total,
                "n": n,
                "coverage": _share(n, total),
                "p50": _hours(p50),
                "p90": _hours(p90),
            }
            for category, total, n, p50, p90 in rows
        ],
    }


def response_times(con: duckdb.DuckDBPyConnection) -> dict:
    return {
        "first_response_by_category": _elapsed_hours_by_category(con, "first_response_date"),
        "resolution_by_category": _elapsed_hours_by_category(con, "resolution_date"),
        "focus_first_response": focus_first_response(con),
    }


def focus_first_response(con: duckdb.DuckDBPyConnection) -> dict:
    """First response for the dispute subcategory this system handles, with the
    censored cases (no first response yet) counted by status, not dropped.
    """
    hours_sql = (
        f"SELECT status, {_hours_since_creation_sql('first_response_date')} AS h "
        "FROM complaints WHERE subcategory = ?"
    )
    total, n, p50, p90, max_h, within = con.execute(
        "SELECT count(*), count(h), quantile_cont(h, 0.5), quantile_cont(h, 0.9), max(h), "
        f"count(*) FILTER (WHERE h <= {CONTACT_WINDOW_HOURS}) FROM ({hours_sql})",
        [FOCUS_SUBCATEGORY],
    ).fetchone()
    missing_by_status = con.execute(
        f"SELECT status, count(*) AS c FROM ({hours_sql}) WHERE h IS NULL "
        "GROUP BY 1 ORDER BY c DESC, status NULLS LAST",
        [FOCUS_SUBCATEGORY],
    ).fetchall()
    histogram = con.execute(
        f"SELECT floor(h)::INTEGER AS hour, count(*) FROM ({hours_sql}) WHERE h IS NOT NULL "
        "GROUP BY 1 ORDER BY 1",
        [FOCUS_SUBCATEGORY],
    ).fetchall()
    return {
        "subcategory": FOCUS_SUBCATEGORY,
        "kind": Kind.MEASURED,
        "unit": HOURS_UNIT,
        "total": total,
        "n": n,
        "coverage": _share(n, total),
        "p50": _hours(p50),
        "p90": _hours(p90),
        "max_hours": {"value": _hours(max_h), "n": n, "kind": Kind.MEASURED},
        "missing_first_response": {
            "value": total - n,
            "n": total,
            "kind": Kind.MEASURED,
            "by_status": [{"status": s, "count": c} for s, c in missing_by_status],
        },
        "within_contact_window": {
            "value": _share(within, n),
            "n": n,
            "kind": Kind.MEASURED,
            "window_hours": CONTACT_WINDOW_HOURS,
            "note": (
                f"Share of RECORDED first responses within {CONTACT_WINDOW_HOURS} calendar "
                "hours. Cases with no first response are censored and excluded, so this share "
                "says nothing about them."
            ),
        },
        "histogram_1h": [{"hour": hour, "count": c} for hour, c in histogram],
    }


def call_center(con: duckdb.DuckDBPyConnection) -> dict:
    """Contacts, handle time and resolution on contact per contact reason, over
    the windowed `call_center_interactions` extract.
    """
    rows = con.execute(
        "SELECT contact_reason, reason_category, count(*) AS c, count(duration_seconds), "
        "median(duration_seconds), coalesce(sum(duration_seconds), 0), count(was_resolved), "
        "count(*) FILTER (WHERE was_resolved) "
        "FROM call_center_interactions GROUP BY 1, 2 "
        "ORDER BY c DESC, contact_reason NULLS LAST, reason_category NULLS LAST"
    ).fetchall()
    start, end = con.execute(
        "SELECT min(interaction_date), max(interaction_date) FROM call_center_interactions"
    ).fetchone()
    total = sum(r[2] for r in rows)
    handle_total = sum(r[5] for r in rows)
    reasons = [
        {
            "contact_reason": reason,
            "reason_category": reason_category,
            "contacts": c,
            "share": _share(c, total),
            "handle_time_n": handle_n,
            "median_handle_seconds": None if median is None else round(median, 1),
            "handle_seconds_share": _share(handle_seconds, handle_total),
            "resolved_n": resolved_n,
            "resolved_on_contact_share": _share(resolved, resolved_n),
        }
        for (
            reason, reason_category, c, handle_n, median, handle_seconds, resolved_n, resolved,
        ) in rows
    ]
    shares = [r["share"] for r in reasons]
    return {
        "kind": Kind.MEASURED,
        "n": total,
        "window": {"start": _iso(start), "end": _iso(end)},
        "note": (
            "First-contact handle time across all contact reasons. Not dispute-specific: "
            "there is no join key from complaints to interactions."
        ),
        "share_spread": {"min": min(shares, default=None), "max": max(shares, default=None)},
        "reasons": reasons,
    }


def data_quality(con: duckdb.DuckDBPyConnection) -> dict:
    both_dates, before, closed, closed_no_date, linked, total, null_currency = con.execute(
        "SELECT "
        "count(*) FILTER (WHERE first_response_date IS NOT NULL AND resolution_date IS NOT NULL), "
        "count(*) FILTER (WHERE resolution_date < first_response_date), "
        "count(*) FILTER (WHERE status IN ?), "
        "count(*) FILTER (WHERE status IN ? AND resolution_date IS NULL), "
        "count(origin_interaction_id), count(*), count(*) FILTER (WHERE currency IS NULL) "
        "FROM complaints",
        [list(CLOSED_STATUSES), list(CLOSED_STATUSES)],
    ).fetchone()
    by_currency = con.execute(
        "SELECT currency, count(*), count(claimed_amount), median(claimed_amount) "
        "FROM complaints WHERE currency IS NOT NULL GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return {
        "resolution_before_first_response": {
            "value": before, "n": both_dates, "kind": Kind.MEASURED,
            "note": "Over complaints that have both a first response and a resolution date.",
        },
        "closed_without_resolution_date": {
            "value": closed_no_date, "n": closed, "kind": Kind.MEASURED,
            "statuses": list(CLOSED_STATUSES),
        },
        "claimed_amount_by_currency": {
            "kind": Kind.MEASURED,
            "n": total,
            "null_currency": {"value": null_currency, "n": total, "kind": Kind.MEASURED},
            "note": (
                "Medians per currency, never summed across currencies. Medians of similar size "
                "in currencies with very different FX rates are not consistent with real "
                "amounts in those currencies."
            ),
            "rows": [
                {
                    "currency": currency,
                    "complaints": c,
                    "amount_n": amount_n,
                    "median_amount": None if median is None else round(median, 2),
                }
                for currency, c, amount_n, median in by_currency
            ],
        },
        "complaint_interaction_link": {
            "value": _share(linked, total), "n": total, "kind": Kind.MEASURED, "linked": linked,
            "note": "Share of complaints with origin_interaction_id populated.",
        },
    }


def _table_rows(con: duckdb.DuckDBPyConnection) -> dict:
    return {
        table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("complaints", "customers", "call_center_interactions")
    }


def cost_blocks(snapshot: dict, focus: dict, call_center_block: dict) -> dict:
    """AD-5: four separately labeled blocks. No ratio between them, no savings
    figure and no total over a period: the blocks measure different things.
    """
    sample_size = snapshot["sample_size"]
    resolution = snapshot["safe_automated_resolution_rate"]
    cost = snapshot["estimated_cost_usd"]
    anchor = reason_row(call_center_block, ANCHOR_CONTACT_REASON)
    upper = reason_row(call_center_block, UPPER_ANCHOR_CONTACT_REASON)
    anchor_seconds = anchor["median_handle_seconds"]
    if anchor_seconds is None:
        raise ValueError(f"contact reason {ANCHOR_CONTACT_REASON!r} has no recorded handle time")

    per_success = cost["per_successful_resolution"]
    per_success_value = per_success if isinstance(per_success, int | float) else None

    return {
        "time_to_first_action": {
            "agent_pipeline_p50_seconds": {
                "value": snapshot["latency_seconds"]["p50"], "n": sample_size,
                "kind": Kind.SIMULATED,
                "note": "Offline eval pipeline time with a mocked LLM; excludes network time.",
            },
            "human_first_response_p50_hours": {
                "value": focus["p50"], "n": focus["n"], "coverage": focus["coverage"],
                "kind": Kind.MEASURED, "subcategory": FOCUS_SUBCATEGORY,
            },
        },
        "human_first_contact_handle_time": {
            "anchor_seconds": {
                "value": anchor_seconds, "n": anchor["handle_time_n"], "kind": Kind.MEASURED,
                "contact_reason": ANCHOR_CONTACT_REASON,
            },
            "upper_anchor_seconds": {
                "value": upper["median_handle_seconds"], "n": upper["handle_time_n"],
                "kind": Kind.MEASURED, "contact_reason": UPPER_ANCHOR_CONTACT_REASON,
            },
            "note": call_center_block["note"],
        },
        "agent_llm_cost_usd": {
            "per_attempted_case": {
                "value": cost["per_attempted_case_mean"], "n": sample_size,
                "kind": Kind.SIMULATED,
            },
            "per_successful_resolution": {
                "value": per_success_value, "n": resolution["count"], "kind": Kind.SIMULATED,
            },
            "method": cost["method"],
            "pricing_source": cost["pricing_source"],
            "disclosure": snapshot["disclosure"],
        },
        "projection": _projection(anchor_seconds, resolution),
    }


def _projection(anchor_seconds: float, resolution: dict) -> dict:
    """Block D: hourly rate x automation share sensitivity over the anchor handle time."""
    shares = [
        {
            "value": 0.0, "n": None, "kind": Kind.ASSUMED, "label": "baseline",
            "note": "Evidence-based baseline: real complaint-to-transaction linkage is near zero.",
        },
        {
            "value": resolution["rate"], "n": resolution["of_attempted"],
            "kind": Kind.SIMULATED, "label": SCENARIO_SUITE_LABEL,
            "note": (
                f"{resolution['count']} of {resolution['of_attempted']} constructed eval "
                "scenarios ended in a safe automated resolution."
            ),
        },
        {
            "value": ASSUMED_AUTOMATION_SHARE, "n": None, "kind": Kind.ASSUMED,
            "label": "illustrative",
        },
    ]
    rates = [{"value": rate, "n": None, "kind": Kind.ASSUMED} for rate in HOURLY_RATES_USD]
    anchor_hours = anchor_seconds / SECONDS_PER_HOUR
    cells = [
        {
            "hourly_rate_usd": rate["value"],
            "automation_share": share["value"],
            "value": _usd((1 - share["value"]) * anchor_hours * rate["value"]),
            "n": None,
            "kind": Kind.PROJECTION,
        }
        for rate in rates
        for share in shares
    ]
    return {
        "kind": Kind.PROJECTION,
        "formula": (
            "expected human handle cost per case (USD) = (1 - automation share) x "
            "anchor handle hours x hourly rate"
        ),
        "anchor_seconds": anchor_seconds,
        "hourly_rates_usd": rates,
        "automation_shares": shares,
        "cells": cells,
        "prerequisite": PROJECTION_PREREQUISITE,
    }


def build_report(con: duckdb.DuckDBPyConnection, snapshot: dict) -> dict:
    data_as_of = con.execute("SELECT max(creation_date) FROM complaints").fetchone()[0]
    responses = response_times(con)
    call_center_block = call_center(con)
    finding = snapshot.get("real_data_match_rate_finding")
    return {
        "schema_version": SCHEMA_VERSION,
        "data_as_of": _iso(data_as_of),
        "generator": "python -m etl.analyze_demand",
        "sources": {
            "warehouse": WAREHOUSE_SOURCE,
            "eval_snapshot": SNAPSHOT_SOURCE,
            "table_rows": _table_rows(con),
        },
        "demand": {
            "by_category": demand_by_category(con),
            "by_subcategory": demand_by_subcategory(con),
            "by_channel": demand_by_channel(con),
            "by_country": demand_by_country(con),
            "weekly": weekly_demand(con),
        },
        "response_times": responses,
        "call_center": call_center_block,
        "data_quality": data_quality(con),
        "eval": {
            "disclosure": snapshot["disclosure"],
            "real_data_match": None if finding is None else {
                "value": finding["real_matches_found"],
                "n": finding["sample_size"],
                "kind": Kind.MEASURED,
                "source": finding["source"],
                "note": "Quoted from the eval snapshot, not recomputed here.",
            },
        },
        "cost": cost_blocks(snapshot, responses["focus_first_response"], call_center_block),
    }


def read_snapshot(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Eval cost snapshot not found at {path}. Run `python -m eval.run_eval` and then "
            "`python -m etl.analyze_demand --refresh-eval-snapshot`."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_snapshot(eval_report_path: Path, snapshot_path: Path) -> dict:
    """Copies only the AD-6 allowlist from the eval report, so no case id,
    customer data or prompt can reach the committed snapshot.
    """
    if not eval_report_path.exists():
        raise FileNotFoundError(
            f"Eval report not found at {eval_report_path}. Run `python -m eval.run_eval` first."
        )
    report = json.loads(eval_report_path.read_text(encoding="utf-8"))
    source = {
        **report,
        OPTIONAL_SNAPSHOT_KEY: (report.get("escalation_quality") or {}).get(OPTIONAL_SNAPSHOT_KEY),
    }
    missing = [
        key for key in SNAPSHOT_KEYS
        if key not in ("source", OPTIONAL_SNAPSHOT_KEY) and source.get(key) is None
    ]
    if missing:
        raise ValueError(f"Eval report at {eval_report_path} lacks required keys: {missing}")
    snapshot: dict = {"source": EVAL_REPORT_SOURCE}
    for key, fields in SNAPSHOT_KEYS.items():
        if key == "source" or source.get(key) is None:
            continue
        value = source[key]
        snapshot[key] = value if fields is None else {f: value[f] for f in fields if f in value}
    _write_json(snapshot, snapshot_path)
    return snapshot


def _write_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    logger.info("Wrote %s", path)


def _write_json(payload: dict, path: Path) -> None:
    _write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", path)


def run(
    warehouse_path: Path = DEFAULT_WAREHOUSE_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
) -> dict:
    snapshot = read_snapshot(snapshot_path)
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        report = build_report(con, snapshot)
    finally:
        con.close()
    _write_json(report, out_dir / REPORT_JSON_NAME)
    _write_text(render_markdown(report), out_dir / REPORT_MD_NAME)
    _write_charts(report, out_dir)
    return report


def _write_charts(report: dict, out_dir: Path) -> None:
    try:
        paths = render_charts(report, out_dir)
    except ModuleNotFoundError as exc:
        if exc.name != "matplotlib":
            raise
        logger.warning(
            "matplotlib is not installed; charts skipped. "
            "Install it with `pip install -r requirements-analysis.txt`."
        )
        return
    for path in paths:
        logger.info("Wrote %s", path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--refresh-eval-snapshot", action="store_true",
        help=f"rewrite the eval cost snapshot from {EVAL_REPORT_SOURCE} before the report",
    )
    args = parser.parse_args(argv)
    try:
        if args.refresh_eval_snapshot:
            refresh_snapshot(DEFAULT_EVAL_REPORT_PATH, DEFAULT_SNAPSHOT_PATH)
        run(snapshot_path=DEFAULT_SNAPSHOT_PATH)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
