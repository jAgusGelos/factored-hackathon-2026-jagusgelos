"""Demand analysis report: compute layer, snapshot handling, JSON contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pytest

from etl import analyze_demand as ad

H = 3600

# (id, created, customer, category, subcategory, channel, origin_interaction,
#  claimed_amount, currency, status, first response +h, resolution +h)
COMPLAINTS = [
    ("C01", "2024-01-03 10:00:00", "CLI-1", "Transactions", "Cargo no reconocido", "App", None,
     100.0, "USD", "Resolved", 10, 48),
    ("C02", "2024-01-08 10:00:00", "CLI-2", "Transactions", "Cargo no reconocido", "Web", None,
     300.0, "USD", "Closed", 20, None),
    ("C03", "2024-01-09 10:00:00", "CLI-1", "Transactions", "Cargo no reconocido", "App", "INT-1",
     50.0, "ARS", "Resolved", 80, 5),
    ("C04", "2024-01-15 10:00:00", "CLI-3", "Transactions", "Cargo no reconocido", "App", None,
     None, None, "Open", None, None),
    ("C05", "2024-01-16 10:00:00", "CLI-9", "Transactions", None, "Web", None,
     200.0, "USD", "In Process", 72, None),
    ("C06", "2024-01-10 10:00:00", "CLI-2", "Fees", "Cobro indebido", "Call Center", None,
     80.0, "ARS", "Escalated", None, None),
    ("C07", "2024-01-17 10:00:00", "CLI-3", "Fees", "Cobro indebido", "Call Center", None,
     20.0, "ARS", "Resolved", 40, 100),
    ("C08", "2024-01-23 10:00:00", "CLI-1", "Fees", "Cobro indebido", "App", None,
     60.0, "MXN", "Open", None, None),
]

# (id, date, reason, duration_seconds, was_resolved)
INTERACTIONS = [
    ("INT-1", "2024-01-08 09:00:00", "Transaccional", 100.0, True),
    ("INT-2", "2024-01-09 09:00:00", "Transaccional", 200.0, True),
    ("INT-3", "2024-01-10 09:00:00", "Transaccional", 300.0, False),
    ("INT-4", "2024-01-11 09:00:00", "Queja", 400.0, False),
    ("INT-5", "2024-01-12 09:00:00", "Producto", None, True),
]

SNAPSHOT = {
    "source": "data/eval_report.json",
    "disclosure": "OFFLINE/SIMULATED test disclosure.",
    "sample_size": 29,
    "safe_automated_resolution_rate": {"count": 6, "of_attempted": 29, "rate": 0.2069},
    "estimated_cost_usd": {
        "per_attempted_case_mean": 0.001235,
        "per_successful_resolution": 0.00597,
        "pricing_source": "test pricing",
        "method": "test method",
    },
    "latency_seconds": {"p50": 0.2228, "p95": 0.3989},
    "real_data_match_rate_finding": {
        "sample_size": 2000, "real_matches_found": 1, "source": "test",
    },
}

FORBIDDEN_KEY = re.compile(r"(?i)ratio|saving|cheaper|monthly|per_month|total_usd")


@pytest.fixture()
def warehouse(tmp_path):
    db_path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE complaints (complaint_id VARCHAR, creation_date TIMESTAMP, "
        "customer_id VARCHAR, category VARCHAR, subcategory VARCHAR, reception_channel VARCHAR, "
        "origin_interaction_id VARCHAR, claimed_amount DOUBLE, currency VARCHAR, status VARCHAR, "
        "first_response_date TIMESTAMP, resolution_date TIMESTAMP)"
    )
    for *head, first_h, resolution_h in COMPLAINTS:
        con.execute(
            "INSERT INTO complaints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "CAST(? AS TIMESTAMP) + to_seconds(?), CAST(? AS TIMESTAMP) + to_seconds(?))",
            [
                *head,
                head[1], None if first_h is None else first_h * H,
                head[1], None if resolution_h is None else resolution_h * H,
            ],
        )
    con.execute("CREATE TABLE customers (customer_id VARCHAR, country VARCHAR)")
    con.executemany(
        "INSERT INTO customers VALUES (?, ?)",
        [("CLI-1", "México"), ("CLI-2", "Colombia"), ("CLI-3", "México")],
    )
    con.execute(
        "CREATE TABLE call_center_interactions (interaction_id VARCHAR, "
        "interaction_date TIMESTAMP, contact_reason VARCHAR, reason_category VARCHAR, "
        "duration_seconds DOUBLE, was_resolved BOOLEAN)"
    )
    con.executemany(
        "INSERT INTO call_center_interactions VALUES (?, ?, ?, ?, ?, ?)",
        [(i, d, r, r, s, ok) for i, d, r, s, ok in INTERACTIONS],
    )
    con.close()
    return db_path


@pytest.fixture()
def con(warehouse):
    connection = duckdb.connect(str(warehouse), read_only=True)
    yield connection
    connection.close()


@pytest.fixture()
def snapshot_path(tmp_path):
    path = tmp_path / "eval_cost_snapshot.json"
    path.write_text(json.dumps(SNAPSHOT))
    return path


@pytest.fixture()
def report(con):
    return ad.build_report(con, SNAPSHOT)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _assert_contract(report: dict) -> None:
    for node in _walk(report):
        for key in node:
            assert not FORBIDDEN_KEY.search(key), key
        if "value" in node:
            assert "n" in node and node.get("kind") in ad.KINDS, node
        if "p50" in node or "p90" in node:
            assert "n" in node and "coverage" in node, node


def test_module_imports_neither_app_nor_eval():
    source = Path(ad.__file__).read_text()
    assert not re.search(r"^\s*(from|import)\s+(app|eval)\b", source, re.MULTILINE)


def test_volume_by_category_and_subcategory(con):
    by_category = ad.demand_by_category(con)
    assert by_category["n"] == 8 and by_category["kind"] == "measured"
    assert by_category["rows"] == [
        {"key": "Transactions", "count": 5, "share": 0.625},
        {"key": "Fees", "count": 3, "share": 0.375},
    ]
    sub = ad.demand_by_subcategory(con)["rows"]
    assert [(r["category"], r["subcategory"], r["count"]) for r in sub] == [
        ("Transactions", "Cargo no reconocido", 4),
        ("Fees", "Cobro indebido", 3),
        ("Transactions", None, 1),
    ]
    assert sub[0]["share_of_category"] == 0.8 and sub[2]["share_of_category"] == 0.2


def test_channel_and_country(con):
    channel = ad.demand_by_channel(con)["rows"]
    assert [(r["key"], r["count"]) for r in channel] == [("App", 4), ("Call Center", 2), ("Web", 2)]
    country = ad.demand_by_country(con)
    assert [(r["key"], r["count"]) for r in country["rows"]] == [
        ("México", 5), ("Colombia", 2), (None, 1),
    ]
    assert country["coverage"] == 0.875


def test_weekly_flags_partial_weeks_and_excludes_them_from_cv(con):
    weekly = ad.weekly_demand(con)
    assert [(w["week_start"], w["partial"], w["total"]) for w in weekly["weeks"]] == [
        ("2024-01-01", True, 1),
        ("2024-01-08", False, 3),
        ("2024-01-15", False, 3),
        ("2024-01-22", True, 1),
    ]
    assert weekly["weeks"][0]["by_category"] == {"Fees": 0, "Transactions": 1}
    assert (weekly["full_weeks"], weekly["partial_weeks"]) == (2, 2)
    transactions = next(v for v in weekly["variability"] if v["category"] == "Transactions")
    # Full weeks only: 2 and 2, so no variation; the partial weeks (1 and 0) are left out.
    assert transactions["full_weeks"] == 2 and transactions["mean_weekly"] == 2.0
    assert transactions["cv"] == 0.0 and transactions["poisson_expected_cv"] == 0.7071
    # Share 62.5% is outside the 19-21% band, so not flat despite a CV of 0.
    assert transactions["flat"] is False


def test_response_times_per_category(con):
    first = ad.response_times(con)["first_response_by_category"]["rows"]
    transactions = next(r for r in first if r["category"] == "Transactions")
    # Hours 10, 20, 72, 80: p50 = 46, p90 = 72 + 0.7 * 8.
    assert transactions == {
        "category": "Transactions", "kind": "measured", "total": 5, "n": 4, "coverage": 0.8,
        "p50": 46.0, "p90": 77.6,
    }
    resolution = ad.response_times(con)["resolution_by_category"]["rows"]
    fees = next(r for r in resolution if r["category"] == "Fees")
    assert (fees["n"], fees["coverage"], fees["p50"], fees["p90"]) == (1, 0.3333, 100.0, 100.0)


def test_focus_first_response_counts_censored_cases(con):
    focus = ad.focus_first_response(con)
    # Hours 10, 20, 80 over 4 cases; C04 (Open) has no first response.
    assert (focus["total"], focus["n"], focus["coverage"]) == (4, 3, 0.75)
    assert (focus["p50"], focus["p90"]) == (20.0, 68.0)
    assert focus["max_hours"]["value"] == 80.0
    assert focus["missing_first_response"]["value"] == 1
    assert focus["missing_first_response"]["by_status"] == [{"status": "Open", "count": 1}]
    assert focus["within_contact_window"]["value"] == 0.6667
    assert focus["histogram_1h"] == [
        {"hour": 10, "count": 1}, {"hour": 20, "count": 1}, {"hour": 80, "count": 1},
    ]


def test_call_center_reasons(con):
    block = ad.call_center(con)
    assert block["n"] == 5
    assert [r["contact_reason"] for r in block["reasons"]] == ["Transaccional", "Producto", "Queja"]
    transaccional, producto, queja = block["reasons"]
    assert transaccional["median_handle_seconds"] == 200.0
    assert transaccional["handle_seconds_share"] == 0.6
    assert transaccional["resolved_on_contact_share"] == 0.6667
    assert queja["median_handle_seconds"] == 400.0 and queja["resolved_on_contact_share"] == 0.0
    assert producto["median_handle_seconds"] is None and producto["handle_time_n"] == 0
    assert block["share_spread"] == {"min": 0.2, "max": 0.6}


def test_data_quality(con):
    quality = ad.data_quality(con)
    assert quality["resolution_before_first_response"]["value"] == 1
    assert quality["resolution_before_first_response"]["n"] == 3
    assert quality["closed_without_resolution_date"]["value"] == 1
    assert quality["closed_without_resolution_date"]["n"] == 4
    amounts = quality["claimed_amount_by_currency"]
    assert [(r["currency"], r["median_amount"]) for r in amounts["rows"]] == [
        ("ARS", 50.0), ("MXN", 60.0), ("USD", 200.0),
    ]
    assert amounts["null_currency"]["value"] == 1
    assert quality["complaint_interaction_link"]["value"] == 0.125


def test_projection_cells_and_anchors(report):
    cost = report["cost"]
    assert cost["human_first_contact_handle_time"]["anchor_seconds"]["value"] == 200.0
    assert cost["human_first_contact_handle_time"]["upper_anchor_seconds"]["value"] == 400.0
    projection = cost["projection"]
    assert [s["value"] for s in projection["automation_shares"]] == [0.0, 0.2069, 0.5]
    assert projection["automation_shares"][1]["label"] == ad.SCENARIO_SUITE_LABEL
    cells = {(c["hourly_rate_usd"], c["automation_share"]): c["value"] for c in projection["cells"]}
    assert len(cells) == 9
    assert cells[(10, 0.0)] == round(200 / 3600 * 10, 4)
    assert cells[(20, 0.5)] == round(0.5 * 200 / 3600 * 20, 4)
    assert cells[(5, 0.2069)] == round((1 - 0.2069) * 200 / 3600 * 5, 4)


def test_report_contract(report):
    _assert_contract(report)
    assert report["data_as_of"] == "2024-01-23T10:00:00"
    assert report["eval"]["real_data_match"]["value"] == 1


def test_missing_snapshot_raises_with_refresh_hint(warehouse, tmp_path):
    with pytest.raises(FileNotFoundError, match="--refresh-eval-snapshot"):
        ad.run(warehouse, tmp_path / "out", tmp_path / "missing.json")


def test_refresh_without_eval_report_exits_1(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ad, "DEFAULT_EVAL_REPORT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(ad, "DEFAULT_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    assert ad.main(["--refresh-eval-snapshot"]) == 1
    assert "python -m eval.run_eval" in caplog.text
    assert not (tmp_path / "snapshot.json").exists()


def test_main_without_snapshot_exits_1(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ad, "DEFAULT_SNAPSHOT_PATH", tmp_path / "missing.json")
    assert ad.main([]) == 1
    assert "--refresh-eval-snapshot" in caplog.text


def test_refresh_rejects_eval_report_missing_a_required_key(tmp_path):
    source = tmp_path / "eval_report.json"
    source.write_text(json.dumps({k: v for k, v in SNAPSHOT.items() if k != "estimated_cost_usd"}))
    with pytest.raises(ValueError, match="estimated_cost_usd"):
        ad.refresh_snapshot(source, tmp_path / "snapshot.json")
    assert not (tmp_path / "snapshot.json").exists()


def test_refresh_copies_only_the_allowlist(tmp_path):
    eval_report = {
        **{k: v for k, v in SNAPSHOT.items() if k not in ("source", "real_data_match_rate_finding")},
        "unsafe_outcomes": {"cases": ["case-key"]},
        "estimated_cost_usd": {**SNAPSHOT["estimated_cost_usd"], "per_case": [0.1]},
        "latency_seconds": {**SNAPSHOT["latency_seconds"], "note": "dropped"},
        "escalation_quality": {
            "escalated_count": 3,
            "real_data_match_rate_finding": {
                **SNAPSHOT["real_data_match_rate_finding"], "note": "dropped",
            },
        },
    }
    source = tmp_path / "eval_report.json"
    source.write_text(json.dumps(eval_report))
    snapshot = ad.refresh_snapshot(source, tmp_path / "snapshot.json")
    assert snapshot == SNAPSHOT
    assert json.loads((tmp_path / "snapshot.json").read_text()) == SNAPSHOT


def test_committed_snapshot_keys_are_the_allowlist():
    snapshot = json.loads(ad.DEFAULT_SNAPSHOT_PATH.read_text())
    assert set(snapshot) <= set(ad.SNAPSHOT_KEYS)
    for key, fields in ad.SNAPSHOT_KEYS.items():
        if fields is not None and key in snapshot:
            assert set(snapshot[key]) <= set(fields), key


def test_run_is_byte_identical(warehouse, snapshot_path, tmp_path):
    out = tmp_path / "out"
    ad.run(warehouse, out, snapshot_path)
    first = (out / ad.REPORT_JSON_NAME).read_bytes()
    ad.run(warehouse, out, snapshot_path)
    assert (out / ad.REPORT_JSON_NAME).read_bytes() == first
    assert first.endswith(b"\n")
    _assert_contract(json.loads(first))
