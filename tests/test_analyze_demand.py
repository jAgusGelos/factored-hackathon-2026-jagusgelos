"""Demand analysis report: compute layer, snapshot handling, JSON contract."""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from app import policy
from etl import analyze_demand as ad
from etl import demand_labels as labels
from etl import demand_report_render as render
from etl import demand_snapshot as snap
from etl.demand_labels import KINDS

H = 3600
HOURS_PER_DAY = 24

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
            assert "n" in node and node.get("kind") in KINDS, node
        if "p50" in node or "p90" in node:
            assert "n" in node and "coverage" in node, node


@pytest.mark.parametrize("module", [ad, render, labels, snap])
def test_module_imports_neither_app_nor_eval(module):
    source = Path(module.__file__).read_text()
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
    # Share 62.5% is outside the 49-51% band (2 categories), so not flat despite a CV of 0.
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
        {"hour_end": 10, "count": 1}, {"hour_end": 20, "count": 1}, {"hour_end": 80, "count": 1},
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
    assert block["share_spread"] == {
        "min": 0.2, "max": 0.6, "even_share": 0.3333, "tolerance": 0.01, "uniform": False,
    }


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
    # Medians 50, 60 and 200 are not within 10% of each other.
    assert amounts["median_similarity"] == {
        "compared_currencies": 3, "tolerance": 0.1, "similar": False,
    }
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
        ad.run(warehouse_path=warehouse, out_dir=tmp_path / "out", snapshot_path=tmp_path / "missing.json")


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
        ad.refresh_snapshot(eval_report_path=source, snapshot_path=tmp_path / "snapshot.json")
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
    snapshot = ad.refresh_snapshot(eval_report_path=source, snapshot_path=tmp_path / "snapshot.json")
    assert snapshot == SNAPSHOT
    assert json.loads((tmp_path / "snapshot.json").read_text()) == SNAPSHOT


def test_committed_snapshot_keys_are_the_allowlist():
    snapshot = json.loads(ad.DEFAULT_SNAPSHOT_PATH.read_text())
    assert set(snapshot) == set(snap.SNAPSHOT_SCHEMA)
    for key, spec in snap.SNAPSHOT_SCHEMA.items():
        if isinstance(spec, dict):
            assert set(snapshot[key]) == set(spec), key


def test_refresh_rejects_eval_report_missing_a_nested_field(tmp_path):
    latency = {"p50": SNAPSHOT["latency_seconds"]["p50"]}
    source = tmp_path / "eval_report.json"
    source.write_text(json.dumps({**SNAPSHOT, "latency_seconds": latency}))
    with pytest.raises(ValueError, match=r"latency_seconds\.p95"):
        ad.refresh_snapshot(eval_report_path=source, snapshot_path=tmp_path / "snapshot.json")
    assert not (tmp_path / "snapshot.json").exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "not valid JSON"),
        (json.dumps({k: v for k, v in SNAPSHOT.items() if k != "latency_seconds"}),
         "latency_seconds"),
        ("[]", "not a JSON object"),
        (json.dumps({**SNAPSHOT, "safe_automated_resolution_rate": {
            **SNAPSHOT["safe_automated_resolution_rate"], "rate": None}}),
         r"safe_automated_resolution_rate\.rate"),
        (json.dumps({**SNAPSHOT, "safe_automated_resolution_rate": {
            **SNAPSHOT["safe_automated_resolution_rate"], "rate": 1.5}}),
         r"safe_automated_resolution_rate\.rate"),
        (json.dumps({**SNAPSHOT, "sample_size": 29.5}), "sample_size"),
        (json.dumps({**SNAPSHOT, "latency_seconds": {"p50": float("nan"), "p95": 0.4}}),
         r"latency_seconds\.p50"),
        (json.dumps({**SNAPSHOT, "latency_seconds": {"p50": "bad", "p95": 0.4}}),
         r"latency_seconds\.p50"),
        (json.dumps({**SNAPSHOT, "unsafe_outcomes": {"cases": ["case-key"]}}),
         "unsafe_outcomes"),
        (json.dumps({**SNAPSHOT, "latency_seconds": {"p50": 0.2, "p95": 0.4, "cases": []}}),
         r"latency_seconds\.cases"),
        (json.dumps({**SNAPSHOT, "safe_automated_resolution_rate": {
            "count": 30, "of_attempted": 29, "rate": 1.0}}),
         "count <= of_attempted"),
        (json.dumps({**SNAPSHOT, "safe_automated_resolution_rate": {
            "count": 6, "of_attempted": 29, "rate": 0.5}}),
         "rate == count / of_attempted"),
        (json.dumps({**SNAPSHOT, "real_data_match_rate_finding": {
            "sample_size": 2000, "real_matches_found": 2001, "source": "test"}}),
         "real_matches_found <= sample_size"),
        (json.dumps({**SNAPSHOT, "sample_size": 0, "safe_automated_resolution_rate": {
            "count": 0, "of_attempted": 0, "rate": 0.5}}),
         "of_attempted > 0"),
    ],
)
def test_invalid_snapshot_is_rejected_with_its_path(tmp_path, content, message):
    path = tmp_path / "snapshot.json"
    path.write_text(content)
    with pytest.raises(ValueError, match=message) as excinfo:
        snap.read_snapshot(path)
    assert str(path) in str(excinfo.value)


def test_missing_warehouse_raises_with_extract_hint(snapshot_path, tmp_path):
    with pytest.raises(FileNotFoundError, match="python -m etl.extract"):
        ad.run(warehouse_path=tmp_path / "missing.duckdb", out_dir=tmp_path / "out", snapshot_path=snapshot_path)


def test_run_is_byte_identical(warehouse, snapshot_path, tmp_path):
    out = tmp_path / "out"
    ad.run(warehouse_path=warehouse, out_dir=out, snapshot_path=snapshot_path)
    first = {name: (out / name).read_bytes() for name in (ad.REPORT_JSON_NAME, ad.REPORT_MD_NAME)}
    ad.run(warehouse_path=warehouse, out_dir=out, snapshot_path=snapshot_path)
    for name, content in first.items():
        assert (out / name).read_bytes() == content, name
        assert content.endswith(b"\n")
    _assert_contract(json.loads(first[ad.REPORT_JSON_NAME]))


def _tldr(markdown: str) -> str:
    return markdown.split("## TL;DR")[1].split("\n## ")[0]


def test_markdown_sections_and_labels(report):
    markdown = render.render_markdown(report)
    for heading in (
        "## TL;DR: findings", "## Why disputes", "## What this data does not tell us",
        "## Demand", "## Response times", "## Call center", "## Cost",
        "### D. PROJECTION", "## Data quality", "## Method and reproducibility",
    ):
        assert heading in markdown, heading
    assert "PROJECTION" in markdown and ad.SCENARIO_SUITE_LABEL in markdown
    assert "design-argument" in markdown and ad.PROJECTION_PREREQUISITE in markdown
    assert "\u2014" not in markdown
    assert not re.search(r"(?i)\d+(\.\d+)?\s*x\s+cheaper", markdown)
    tldr = _tldr(markdown)
    assert "USD" not in tldr
    assert re.findall(r"^\d+\. ", tldr, re.MULTILINE) == ["1. ", "2. ", "3. ", "4. ", "5. "]
    assert "n = 3 of 4" in tldr
    # The fixture's two categories are not an even split, so nothing may call demand flat.
    assert "**Complaint demand is not flat by category**" in tldr
    assert "Volume does not single disputes out" not in markdown


def test_flat_headline_and_currency_claim_follow_the_json(report):
    for variability in report["demand"]["weekly"]["variability"]:
        variability["flat"] = True
    report["demand"]["weekly"]["all_flat"] = True
    report["data_quality"]["claimed_amount_by_currency"]["median_similarity"]["similar"] = True
    markdown = render.render_markdown(report)
    assert "**Complaint demand is flat by category**" in _tldr(markdown)
    assert "Volume does not single disputes out" in markdown
    assert "within 10.0% of each other" in _tldr(markdown)


def _weekly_for(tmp_path, timestamps: list[str]) -> dict:
    con = duckdb.connect(str(tmp_path / "weekly.duckdb"))
    con.execute("CREATE TABLE complaints (creation_date TIMESTAMP, category VARCHAR)")
    con.executemany("INSERT INTO complaints VALUES (?, 'Fees')", [(t,) for t in timestamps])
    try:
        return ad.weekly_demand(con)
    finally:
        con.close()


def test_weekly_gap_weeks_count_as_zero(tmp_path):
    weekly = _weekly_for(tmp_path, ["2024-01-01 10:00:00", "2024-01-21 10:00:00"])
    assert [(w["week_start"], w["total"]) for w in weekly["weeks"]] == [
        ("2024-01-01", 1), ("2024-01-08", 0), ("2024-01-15", 1),
    ]
    fees = weekly["variability"][0]
    assert fees["full_weeks"] == 3 and fees["cv"] is not None


def test_cv_needs_two_full_weeks(tmp_path):
    weekly = _weekly_for(tmp_path, ["2024-01-01 10:00:00", "2024-01-07 10:00:00"])
    assert weekly["full_weeks"] == 1
    assert weekly["variability"][0]["cv"] is None and weekly["variability"][0]["flat"] is False


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_charts_are_pngs_of_at_least_800x400(report, tmp_path):
    pytest.importorskip("matplotlib")
    paths = render.render_charts(report, tmp_path)
    assert [p.name for p in paths] == [render.CALL_REASONS_CHART, render.FIRST_RESPONSE_CHART]
    for path in paths:
        width, height = _png_size(path)
        assert width >= 800 and height >= 400, path


def test_run_without_matplotlib_still_writes_json_and_markdown(
    warehouse, snapshot_path, tmp_path, monkeypatch, caplog,
):
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    out = tmp_path / "out"
    ad.run(warehouse_path=warehouse, out_dir=out, snapshot_path=snapshot_path)
    assert (out / ad.REPORT_JSON_NAME).exists() and (out / ad.REPORT_MD_NAME).exists()
    assert not (out / render.CALL_REASONS_CHART).exists()
    assert "charts skipped" in caplog.text


def _committed_report() -> dict:
    return json.loads((ad.DEFAULT_OUT_DIR / ad.REPORT_JSON_NAME).read_text())


def test_committed_report_is_rendered_from_committed_json():
    report = _committed_report()
    _assert_contract(report)
    markdown = (ad.DEFAULT_OUT_DIR / ad.REPORT_MD_NAME).read_text()
    assert markdown == render.render_markdown(report)


def _focus_max_first_response_hours(report: dict) -> float:
    return report["response_times"]["focus_first_response"]["max_hours"]["value"]


def _contact_deadline_shortfall(report: dict) -> float | None:
    """Regression guard, not a validation of the customer promise: returns the
    longest recorded "Cargo no reconocido" first response when the escalation
    deadline, read as calendar hours, is shorter than it, else None. Business
    days span at least as many calendar hours. Complaints with no first
    response (censored) are not in the maximum, so they are not checked.
    """
    max_hours = _focus_max_first_response_hours(report)
    deadline_hours = policy.ESCALATION_CONTACT_BUSINESS_DAYS * HOURS_PER_DAY
    return max_hours if deadline_hours < max_hours else None


def test_escalation_deadline_covers_the_recorded_first_responses():
    assert _contact_deadline_shortfall(_committed_report()) is None


def test_shorter_escalation_deadline_is_reported(monkeypatch):
    monkeypatch.setattr(policy, "ESCALATION_CONTACT_BUSINESS_DAYS", 2)
    report = _committed_report()
    assert _contact_deadline_shortfall(report) == _focus_max_first_response_hours(report)


def test_contact_window_is_within_the_escalation_deadline():
    assert ad.CONTACT_WINDOW_HOURS <= policy.ESCALATION_CONTACT_BUSINESS_DAYS * HOURS_PER_DAY


def test_report_without_real_data_match(con):
    snapshot = {k: v for k, v in SNAPSHOT.items() if k != snap.OPTIONAL_SNAPSHOT_KEY}
    report = ad.build_report(con, snapshot)
    _assert_contract(report)
    assert report["eval"]["real_data_match"] is None
    baseline = report["cost"]["projection"]["automation_shares"][0]
    assert baseline["note"].startswith("Baseline: no complaint-to-transaction match evidence")
    tldr = _tldr(render.render_markdown(report))
    assert re.findall(r"^\d+\. ", tldr, re.MULTILINE) == ["1. ", "2. ", "3. ", "4. "]


def test_call_center_headline_follows_the_spread(report):
    report["call_center"]["share_spread"]["uniform"] = True
    assert "close to uniform" in _tldr(render.render_markdown(report))
    # The fixture links 1 of 8 complaints to a call, so "no key joins" would be false.
    assert "only 12.5% of complaints link to a call" in _tldr(render.render_markdown(report))


def test_table_cells_cannot_break_the_row():
    table = render._table(["a"], [["x\r\ny|z\\"]])
    assert table.splitlines()[2] == "| x y\\|z\\\\ |"


def test_snapshot_with_a_null_rate_is_rejected_before_writing(tmp_path):
    rate = {**SNAPSHOT["safe_automated_resolution_rate"], "rate": None}
    source = tmp_path / "eval_report.json"
    source.write_text(json.dumps({**SNAPSHOT, "safe_automated_resolution_rate": rate}))
    with pytest.raises(ValueError, match=r"safe_automated_resolution_rate\.rate"):
        ad.refresh_snapshot(eval_report_path=source, snapshot_path=tmp_path / "snapshot.json")
    assert not (tmp_path / "snapshot.json").exists()


def test_offline_modules_do_not_pull_in_app_or_eval():
    code = (
        "import sys, etl.analyze_demand; "
        "print('\\n'.join(m for m in sys.modules "
        "if m.split('.')[0] in ('app', 'eval', 'anthropic')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=Path(ad.__file__).resolve().parent.parent,
    )
    assert result.stdout.strip() == ""


def test_match_headline_follows_the_rare_verdict(report):
    assert "rarely match a transaction" in _tldr(render.render_markdown(report))
    report["eval"]["real_data_match"]["rare"] = False
    tldr = _tldr(render.render_markdown(report))
    assert "Complaint-to-transaction match in this dataset" in tldr
    assert "rarely" not in tldr


def test_refresh_tolerates_a_malformed_escalation_block(tmp_path):
    eval_report = {k: v for k, v in SNAPSHOT.items() if k != snap.OPTIONAL_SNAPSHOT_KEY}
    source = tmp_path / "eval_report.json"
    source.write_text(json.dumps({**eval_report, "escalation_quality": ["x"]}))
    snapshot = ad.refresh_snapshot(eval_report_path=source, snapshot_path=tmp_path / "s.json")
    assert snap.OPTIONAL_SNAPSHOT_KEY not in snapshot


def test_chart_failure_leaves_the_previous_report_untouched(
    warehouse, snapshot_path, tmp_path, monkeypatch,
):
    out = tmp_path / "out"
    ad.run(warehouse_path=warehouse, out_dir=out, snapshot_path=snapshot_path)
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    (out / ad.REPORT_JSON_NAME).write_text("{}\n")
    before[ad.REPORT_JSON_NAME] = b"{}\n"

    def failing_charts(report, out_dir):
        raise RuntimeError("chart failure")

    monkeypatch.setattr(ad, "render_charts", failing_charts)
    with pytest.raises(RuntimeError, match="chart failure"):
        ad.run(warehouse_path=warehouse, out_dir=out, snapshot_path=snapshot_path)
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before


def test_rate_check_uses_the_eval_rounding(tmp_path):
    rate = {"count": 1, "of_attempted": 32, "rate": round(1 / 32, 4)}
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({**SNAPSHOT, "sample_size": 32, "safe_automated_resolution_rate": rate}))
    assert snap.read_snapshot(path)["safe_automated_resolution_rate"] == rate


def test_eval_with_no_successful_resolution_still_refreshes(con, tmp_path):
    not_defined = "not defined (0 successful resolutions)"
    eval_report = {
        **{k: v for k, v in SNAPSHOT.items() if k != snap.OPTIONAL_SNAPSHOT_KEY},
        "safe_automated_resolution_rate": {"count": 0, "of_attempted": 29, "rate": 0.0},
        "estimated_cost_usd": {
            **SNAPSHOT["estimated_cost_usd"], "per_successful_resolution": not_defined,
        },
    }
    source = tmp_path / "eval_report.json"
    source.write_text(json.dumps(eval_report))
    snapshot = ad.refresh_snapshot(eval_report_path=source, snapshot_path=tmp_path / "s.json")
    assert snapshot["estimated_cost_usd"]["per_successful_resolution"] == not_defined
    per_success = ad.build_report(con, snapshot)["cost"]["agent_llm_cost_usd"][
        "per_successful_resolution"
    ]
    assert per_success == {"value": None, "n": 0, "kind": "simulated"}


def test_weekly_keeps_a_null_category_last(tmp_path):
    con = duckdb.connect(str(tmp_path / "nulls.duckdb"))
    con.execute("CREATE TABLE complaints (creation_date TIMESTAMP, category VARCHAR)")
    con.executemany(
        "INSERT INTO complaints VALUES (?, ?)",
        [("2024-01-08 10:00:00", "Fees"), ("2024-01-09 10:00:00", None),
         ("2024-01-15 10:00:00", "Fees"), ("2024-01-16 10:00:00", None)],
    )
    weekly = ad.weekly_demand(con)
    con.close()
    assert [v["category"] for v in weekly["variability"]] == ["Fees", None]
    assert sum(v["share"] for v in weekly["variability"]) == 1.0
    assert weekly["weeks"][0]["by_category"] == {"Fees": 1, None: 1}
