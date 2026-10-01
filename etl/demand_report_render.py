"""Renders the demand report JSON (built by `etl/analyze_demand.py`) as
markdown and two PNG charts. Pure functions of the report dict: nothing here
reads the warehouse, so the rendered files can only say what the JSON says.

matplotlib is imported inside `render_charts` only. It lives in
`requirements-analysis.txt`, never in the runtime image.
"""

from __future__ import annotations

from pathlib import Path

from etl.demand_labels import KIND_MEANINGS, Kind, match_noun, reason_row
from etl.demand_snapshot import REFRESH_COMMAND

CALL_REASONS_CHART = "call_reasons.png"
FIRST_RESPONSE_CHART = "first_response_cargo_no_reconocido.png"
CHART_SIZE_INCHES = (10, 5)
CHART_DPI = 100
CHART_RC = {"font.family": "DejaVu Sans"}
TITLE_FONT_SIZE = 11
ANNOTATION_FONT_SIZE = 9
BAR_LABEL_HEADROOM = 1.6
MARKER_LINE_WIDTH = 1.5
PRIMARY_COLOR = "#4C72B0"
NEUTRAL_COLOR = "#8C8C8C"
P90_COLOR = "#DD8452"
WINDOW_COLOR = "#C44E52"

NOT_AVAILABLE = "n/a"
ISO_DATE_LENGTH = len("YYYY-MM-DD")


def _pct(share: float | None) -> str:
    return NOT_AVAILABLE if share is None else f"{share * 100:.1f}%"


def _pct_range(shares: list[float | None]) -> str:
    present = [s for s in shares if s is not None]
    return f"{_pct(min(present, default=None))} to {_pct(max(present, default=None))}"


def _day(timestamp: str | None) -> str:
    return NOT_AVAILABLE if timestamp is None else timestamp[:ISO_DATE_LENGTH]


def _num(value: int | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value:,}"


def _hours(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value:.1f} h"


def _seconds(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value:.0f} s"


def _usd_value(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"USD {value}"


def _amount(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value:,.2f}"


def _raw_seconds(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value} s"


def _key(value: str | None) -> str:
    return "(missing)" if value is None else value


def _cell(text: str) -> str:
    """Keeps a data value from breaking out of its markdown table cell."""
    return " ".join(text.replace("\\", "\\\\").replace("|", "\\|").splitlines())


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _distribution_table(block: dict, label: str) -> str:
    rows = [[_key(r["key"]), _num(r["count"]), _pct(r["share"])] for r in block["rows"]]
    return _table([label, "Complaints", "Share"], rows)


def _flatness_finding(weekly: dict) -> str:
    variability = weekly["variability"]
    if not weekly["all_flat"]:
        not_flat = [v["category"] for v in variability if not v["flat"]]
        headline = "Complaint demand is not flat by category"
        verdict = "Not flat by the flatness rule below: " + (", ".join(not_flat) or NOT_AVAILABLE) + "."
    else:
        headline = "Complaint demand is flat by category"
        verdict = (
            "Every category passes the flatness rule below, a heuristic against Poisson noise, "
            "not a seasonality test."
        )
    return (
        f"**{headline}** (measured). Each of the "
        f"{len(variability)} categories holds {_pct_range([v['share'] for v in variability])} "
        f"of {_num(weekly['n'])} complaints, and the weekly coefficient of variation over "
        f"{weekly['full_weeks']} full weeks is {_pct_range([v['cv'] for v in variability])} "
        f"against {_pct_range([v['poisson_expected_cv'] for v in variability])} expected from "
        f"Poisson noise. {verdict}"
    )


def _join_key_sentence(link: dict) -> str:
    if link["linked"] == 0:
        return "because no key joins a complaint to a call."
    return (
        f"because only {_pct(link['value'])} of complaints link to a call, so call time is "
        "not dispute time."
    )


def _call_center_finding(report: dict) -> str:
    center = report["call_center"]
    handle = report["cost"]["human_first_contact_handle_time"]
    anchor = reason_row(center, handle["anchor_seconds"]["contact_reason"])
    upper = reason_row(center, handle["upper_anchor_seconds"]["contact_reason"])
    spread = center["share_spread"]
    headline = (
        "Call-center contact reasons are close to uniform"
        if spread["uniform"]
        else "Call-center contact reasons are far from uniform"
    )
    return (
        f"**{headline}** (measured). "
        f"Over {_num(center['n'])} "
        f"contacts ({_day(center['window']['start'])} to {_day(center['window']['end'])}), "
        f"contact-reason shares range from {_pct(spread['min'])} to "
        f"{_pct(spread['max'])}. \"{anchor['contact_reason']}\" is "
        f"{_pct(anchor['share'])} of contacts and {_pct(anchor['handle_seconds_share'])} of "
        f"handle seconds, with a median of {_seconds(anchor['median_handle_seconds'])} and "
        f"{_pct(anchor['resolved_on_contact_share'])} resolved on contact; "
        f"\"{upper['contact_reason']}\" takes {_seconds(upper['median_handle_seconds'])} "
        f"with {_pct(upper['resolved_on_contact_share'])} resolved on contact. This sizes "
        "the opportunity; it does not show that disputes are the worst process, "
        + _join_key_sentence(report["data_quality"]["complaint_interaction_link"])
    )


def _first_response_finding(focus: dict) -> str:
    missing = focus["missing_first_response"]
    missing_by_status = ", ".join(
        f"{s['count']:,} {_key(s['status'])}" for s in missing["by_status"]
    )
    return (
        f"**\"{focus['subcategory']}\" waits {_hours(focus['p50'])} for a first response** "
        f"(measured): median {_hours(focus['p50'])} and p90 {_hours(focus['p90'])} over "
        f"n = {_num(focus['n'])} of {_num(focus['total'])} complaints "
        f"({_pct(focus['coverage'])} coverage). {_num(missing['value'])} have no first "
        f"response ({missing_by_status}). The observed maximum is "
        f"{_hours(focus['max_hours']['value'])}; "
        f"{_pct(focus['within_contact_window']['value'])} of recorded first responses came "
        f"within {focus['within_contact_window']['window_hours']} calendar hours."
    )


def _currency_sentence(amounts: dict) -> str:
    similarity = amounts["median_similarity"]
    if not similarity["similar"]:
        return "amounts are never summed across currencies."
    return (
        f"the claimed-amount medians of the {similarity['compared_currencies']} currencies "
        f"are within {_pct(similarity['tolerance'])} of each other, which real amounts in "
        "those currencies would not be, so amounts are never summed across currencies."
    )


def _data_quality_finding(quality: dict) -> str:
    before = quality["resolution_before_first_response"]
    closed = quality["closed_without_resolution_date"]
    link = quality["complaint_interaction_link"]
    return (
        f"**Data quality limits what can be claimed** (measured). {_num(before['value'])} "
        f"of {_num(before['n'])} complaints have a resolution before their first response; "
        f"{_num(closed['value'])} of {_num(closed['n'])} {'/'.join(closed['statuses'])} "
        f"complaints have no resolution date; {_pct(link['value'])} of {_num(link['n'])} "
        "complaints link to a call-center interaction; and "
        + _currency_sentence(quality["claimed_amount_by_currency"])
    )


def _rare_match(match: dict | None) -> bool:
    return match is not None and match["rare"]


def _eval_match_finding(match: dict) -> str:
    headline = (
        "Complaints in this dataset rarely match a transaction"
        if _rare_match(match)
        else "Complaint-to-transaction match in this dataset"
    )
    return (
        f"**{headline}** (measured, quoted from the eval snapshot): {_num(match['value'])} "
        f"amount and date {match_noun(match['value'])} in a sample of "
        f"{_num(match['n'])} complaints (source: "
        f"{match['source']}). The dataset generates complaints and transactions "
        "independently, so this describes the data available here, not a real bank."
    )


def _findings(report: dict) -> str:
    items = [
        _flatness_finding(report["demand"]["weekly"]),
        _call_center_finding(report),
        _first_response_finding(report["response_times"]["focus_first_response"]),
        _data_quality_finding(report["data_quality"]),
    ]
    match = report["eval"]["real_data_match"]
    if match is not None:
        items.append(_eval_match_finding(match))
    return "\n".join(f"{i}. {text}" for i, text in enumerate(items, start=1))


WHY_DISPUTES_ARGUMENT = """\
An unrecognized-charge dispute can be checked in code against the customer's own transaction
ledger and decided by an explicit, testable policy (`app/policy.py`): the agent either acts on
evidence it can verify or hands the case to a person with that evidence attached. Branch service,
app problems or service quality need a human judgment or a fix somewhere else. The measured
support is the call-center load and the wait for a first response in the findings above."""


def _why_disputes(report: dict) -> str:
    volume = (
        "Volume does not single disputes out (complaint demand is flat by category), so the "
        "choice of workflow rests on a design argument, not on demand."
        if report["demand"]["weekly"]["all_flat"]
        else "Volume alone is not the reason for the choice of workflow; it rests on a design "
        "argument."
    )
    return f"*Label: {Kind.DESIGN_ARGUMENT}.* {volume}\n{WHY_DISPUTES_ARGUMENT}"


def _does_not_tell_us(report: dict) -> str:
    focus = report["response_times"]["focus_first_response"]
    link = report["data_quality"]["complaint_interaction_link"]
    lines = [
        "Whether disputes cost more to handle than other complaints: "
        f"{_pct(link['value'])} of complaints link to a call, so call time is not dispute time.",
        "Any real automation rate: the eval scenarios are constructed on purpose"
        + (
            ", and complaints in this dataset almost never match a transaction."
            if _rare_match(report["eval"]["real_data_match"])
            else "."
        ),
        f"Anything about the {_num(focus['missing_first_response']['value'])} "
        f"\"{focus['subcategory']}\" "
        "complaints with no first response yet (censored).",
        "Whether the business-day contact promise is met: the data has calendar hours and "
        "recorded responses only.",
        "Real monetary amounts"
        + (
            ": the per-currency medians are not consistent with exchange rates."
            if report["data_quality"]["claimed_amount_by_currency"]["median_similarity"]["similar"]
            else ": amounts are reported per currency only."
        ),
        "Seasonality: passing a flatness heuristic over this window is not evidence of \"no "
        "seasonality\".",
    ]
    return "\n".join(f"- {line}" for line in lines)


def _demand_section(report: dict) -> str:
    demand = report["demand"]
    weekly = demand["weekly"]
    variability = _table(
        ["Category", "Share", "Mean per week", "Weekly CV", "Poisson CV", "Flat"],
        [
            [
                v["category"], _pct(v["share"]), f"{v['mean_weekly']:.1f}", _pct(v["cv"]),
                _pct(v["poisson_expected_cv"]), "yes" if v["flat"] else "no",
            ]
            for v in weekly["variability"]
        ],
    )
    subcategories = _table(
        ["Category", "Subcategory", "Complaints", "Share of category"],
        [
            [_key(r["category"]), _key(r["subcategory"]), _num(r["count"]),
             _pct(r["share_of_category"])]
            for r in demand["by_subcategory"]["rows"]
        ],
    )
    band = weekly["flat_rule"]["share_band"]
    return "\n\n".join([
        "## Demand",
        f"All measured over n = {_num(demand['by_category']['n'])} complaints.",
        "### By category",
        _distribution_table(demand["by_category"], "Category"),
        "### Weekly variability",
        (
            f"{weekly['week_start_day']}-based weeks; {weekly['full_weeks']} full weeks, "
            f"{weekly['partial_weeks']} partial boundary weeks excluded from the CV. A category is "
            f"flat when its share is within {_pct(band[0])} to {_pct(band[1])} and its CV is at "
            f"most {weekly['flat_rule']['cv_tolerance_vs_poisson']}x the Poisson CV "
            "1/sqrt(mean weekly count). The full weekly series is in the JSON."
        ),
        variability,
        "### By subcategory",
        subcategories,
        "### By reception channel",
        _distribution_table(demand["by_channel"], "Channel"),
        "### By customer country",
        f"Coverage (complaints whose customer is in `customers`): "
        f"{_pct(demand['by_country']['coverage'])}.",
        _distribution_table(demand["by_country"], "Country"),
    ])


def _response_section(report: dict) -> str:
    times = report["response_times"]
    resolution = {r["category"]: r for r in times["resolution_by_category"]["rows"]}

    def row(first: dict) -> list[str]:
        res = resolution[first["category"]]
        return [
            _key(first["category"]),
            f"{_hours(first['p50'])} / {_hours(first['p90'])}", _num(first["n"]),
            _pct(first["coverage"]),
            f"{_hours(res['p50'])} / {_hours(res['p90'])}", _num(res["n"]), _pct(res["coverage"]),
        ]

    table = _table(
        ["Category", "First response p50 / p90", "n", "Coverage",
         "Resolution p50 / p90", "n", "Coverage"],
        [row(first) for first in times["first_response_by_category"]["rows"]],
    )
    focus = times["focus_first_response"]
    return "\n\n".join([
        "## Response times",
        f"Measured, in {times['first_response_by_category']['unit']}. Percentiles are over the "
        "complaints that have the date; coverage is that n over the category total.",
        table,
        f"![First response for \"{focus['subcategory']}\"]({FIRST_RESPONSE_CHART})",
        "Note: " + focus["within_contact_window"]["note"],
    ])


def _call_center_section(report: dict) -> str:
    center = report["call_center"]
    table = _table(
        ["Contact reason", "Contacts", "Share", "Median handle", "n (handle)",
         "Share of handle seconds", "Resolved on contact"],
        [
            [
                _key(r["contact_reason"]), _num(r["contacts"]), _pct(r["share"]),
                _seconds(r["median_handle_seconds"]), _num(r["handle_time_n"]),
                _pct(r["handle_seconds_share"]), _pct(r["resolved_on_contact_share"]),
            ]
            for r in center["reasons"]
        ],
    )
    return "\n\n".join([
        "## Call center",
        f"Measured over n = {_num(center['n'])} interactions, {_day(center['window']['start'])} "
        f"to {_day(center['window']['end'])}. {center['note']}",
        table,
        f"![Median handle time by contact reason]({CALL_REASONS_CHART})",
    ])


def _projection_table(projection: dict) -> str:
    shares = projection["automation_shares"]
    cells = {(c["hourly_rate_usd"], c["automation_share"]): c["value"] for c in projection["cells"]}
    header = ["Hourly rate (assumed)"] + [
        f"Share {s['value']} ({s['label']}, {s['kind']})" for s in shares
    ]
    rows = [
        [f"USD {rate['value']}"] + [
            f"USD {cells[(rate['value'], s['value'])]:.4f}" for s in shares
        ]
        for rate in projection["hourly_rates_usd"]
    ]
    return _table(header, rows)


def _time_to_first_action_block(first: dict) -> str:
    agent, human = first["agent_pipeline_p50_seconds"], first["human_first_response_p50_hours"]
    return (
        f"- Agent pipeline p50: {_raw_seconds(agent['value'])} "
        f"({agent['kind']}, n = {agent['n']} eval "
        f"scenarios). {agent['note']}\n"
        f"- Human first response p50 for \"{human['subcategory']}\": {_hours(human['value'])} "
        f"({human['kind']}, n = {_num(human['n'])}, coverage {_pct(human['coverage'])})."
    )


def _anchor_line(title: str, anchor: dict) -> str:
    return (
        f"- {title}: {_seconds(anchor['value'])} median for \"{anchor['contact_reason']}\" "
        f"({anchor['kind']}, n = {_num(anchor['n'])})."
    )


def _handle_time_block(handle: dict) -> str:
    return "\n".join([
        _anchor_line("Anchor", handle["anchor_seconds"]),
        _anchor_line("Upper anchor", handle["upper_anchor_seconds"]),
        f"- {handle['note']}",
    ])


def _llm_cost_block(llm: dict) -> str:
    attempted = llm["per_attempted_case"]
    per_success = llm["per_successful_resolution"]
    per_success_text = (
        "not defined (no successful resolution in the eval)"
        if per_success["value"] is None
        else _usd_value(per_success["value"])
    )
    return (
        f"- Per attempted case: {_usd_value(attempted['value'])} "
        f"({attempted['kind']}, n = {attempted['n']}).\n"
        f"- Per successful resolution: {per_success_text} ({per_success['kind']}, "
        f"n = {per_success['n']} resolutions).\n"
        f"- Method: {llm['method']}.\n"
        f"- Pricing: {llm['pricing_source']}.\n"
        f"- Disclosure: {llm['disclosure']}"
    )


def _share_note(share: dict) -> str:
    note = f" {share['note']}" if "note" in share else ""
    return f"- Share {share['value']}: {share['kind']}, {share['label']}.{note}"


def _projection_block(projection: dict) -> list[str]:
    return [
        "### D. PROJECTION: first-contact handle-time sensitivity",
        (
            f"*Label: {projection['kind']}.* Formula: {projection['formula']}, with the "
            f"{_seconds(projection['anchor_seconds'])} anchor from block B. Each cell is the "
            "expected human handle cost of one case, not a total over any period."
        ),
        _projection_table(projection),
        "\n".join(_share_note(s) for s in projection["automation_shares"]),
        f"**Prerequisite:** {projection['prerequisite']}",
    ]


def _cost_section(report: dict) -> str:
    cost = report["cost"]
    return "\n\n".join([
        "## Cost",
        "Four separate blocks. They measure different things, so they are never divided into a "
        "ratio or added up into a total.",
        "### A. Time to first action",
        _time_to_first_action_block(cost["time_to_first_action"]),
        "### B. Measured human first-contact handle time",
        _handle_time_block(cost["human_first_contact_handle_time"]),
        "### C. Simulated agent LLM cost",
        _llm_cost_block(cost["agent_llm_cost_usd"]),
        *_projection_block(cost["projection"]),
    ])


def _data_quality_section(report: dict) -> str:
    quality = report["data_quality"]
    amounts = quality["claimed_amount_by_currency"]
    table = _table(
        ["Currency", "Complaints", "With amount", "Median claimed amount"],
        [
            [_key(r["currency"]), _num(r["complaints"]), _num(r["amount_n"]),
             _amount(r["median_amount"])]
            for r in amounts["rows"]
        ],
    )
    before = quality["resolution_before_first_response"]
    closed = quality["closed_without_resolution_date"]
    link = quality["complaint_interaction_link"]
    return "\n\n".join([
        "## Data quality",
        (
            f"- Resolution before first response: {_num(before['value'])} of {_num(before['n'])} "
            f"({before['kind']}). {before['note']}\n"
            f"- {'/'.join(closed['statuses'])} without a resolution date: {_num(closed['value'])} "
            f"of {_num(closed['n'])} ({closed['kind']}).\n"
            f"- Complaint-to-interaction link: {_pct(link['value'])} of {_num(link['n'])} "
            f"({link['kind']}). {link['note']}\n"
            f"- Complaints with no currency: {_num(amounts['null_currency']['value'])} of "
            f"{_num(amounts['null_currency']['n'])}."
        ),
        amounts["note"],
        table,
    ])


def _kind_list() -> str:
    kinds = list(Kind)
    return ", ".join(kinds[:-1]) + " or " + kinds[-1]


def _kind_glossary() -> str:
    return ", ".join(f"{kind} ({meaning})" for kind, meaning in KIND_MEANINGS.items())


def _method_section(report: dict) -> str:
    sources = report["sources"]
    rows = ", ".join(f"`{t}` {n:,}" for t, n in sources["table_rows"].items())
    return "\n\n".join([
        "## Method and reproducibility",
        (
            f"- Generated by `{report['generator']}` from `{sources['warehouse']}` "
            f"(rows: {rows}) and `{sources['eval_snapshot']}`. Do not edit this file by hand.\n"
            f"- Data as of {report['data_as_of']} (latest complaint creation date).\n"
            "- `demand-report.json` is the source of truth; this page and both charts are "
            "rendered from it.\n"
            f"- Labels: {_kind_glossary()}.\n"
            "- Refresh the eval inputs after `python -m eval.run_eval` with "
            f"`{REFRESH_COMMAND}`."
        ),
    ])


def render_markdown(report: dict) -> str:
    sections = [
        "# Demand analysis: why the dispute workflow",
        (
            f"Data as of {report['data_as_of']}. Every number is labeled {_kind_list()}, and "
            "every percentile shows its n and coverage."
        ),
        "## TL;DR: findings",
        _findings(report),
        "## Why disputes",
        _why_disputes(report),
        "## What this data does not tell us",
        _does_not_tell_us(report),
        _demand_section(report),
        _response_section(report),
        _call_center_section(report),
        _cost_section(report),
        _data_quality_section(report),
        _method_section(report),
    ]
    return "\n\n".join(sections) + "\n"


def render_charts(report: dict, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    with plt.rc_context(CHART_RC):
        return [
            _call_reasons_chart(plt, report, out_dir / CALL_REASONS_CHART),
            _first_response_chart(plt, report, out_dir / FIRST_RESPONSE_CHART),
        ]


def _save(plt, fig, path: Path) -> Path:
    try:
        fig.savefig(path, dpi=CHART_DPI, metadata={"Software": None})
    finally:
        plt.close(fig)
    return path


def _median_handle_seconds(reason: dict) -> float:
    return reason["median_handle_seconds"]


def _call_reasons_chart(plt, report: dict, path: Path) -> Path:
    center = report["call_center"]
    reasons = sorted(
        (r for r in center["reasons"] if r["median_handle_seconds"] is not None),
        key=_median_handle_seconds,
    )
    fig, ax = plt.subplots(figsize=CHART_SIZE_INCHES)
    labels = [_key(r["contact_reason"]) for r in reasons]
    medians = [_median_handle_seconds(r) for r in reasons]
    ax.barh(labels, medians, color=PRIMARY_COLOR)
    for i, r in enumerate(reasons):
        ax.annotate(
            f"  {r['contacts']:,} contacts, "
            f"{_pct(r['resolved_on_contact_share'])} resolved on contact",
            (r["median_handle_seconds"], i), va="center", fontsize=ANNOTATION_FONT_SIZE,
        )
    ax.set_xlim(0, max(medians, default=1) * BAR_LABEL_HEADROOM)
    ax.set_xlabel("Median handle time (seconds)")
    ax.set_title(
        f"Call-center median handle time by contact reason (measured, n = {center['n']:,})\n"
        "First-contact time across all reasons; not dispute-specific",
        fontsize=TITLE_FONT_SIZE,
    )
    fig.tight_layout()
    return _save(plt, fig, path)


def _first_response_chart(plt, report: dict, path: Path) -> Path:
    focus = report["response_times"]["focus_first_response"]
    bins = focus["histogram_1h"]
    fig, ax = plt.subplots(figsize=CHART_SIZE_INCHES)
    ax.bar([b["hour_end"] - 1 for b in bins], [b["count"] for b in bins], width=1.0,
           align="edge", color=NEUTRAL_COLOR)
    window = focus["within_contact_window"]["window_hours"]
    markers = [
        (focus["p50"], f"median {_hours(focus['p50'])}", PRIMARY_COLOR),
        (focus["p90"], f"p90 {_hours(focus['p90'])}", P90_COLOR),
        (window, f"{window} calendar hours", WINDOW_COLOR),
    ]
    for x, label, color in markers:
        if x is not None:
            ax.axvline(x, color=color, linestyle="--", linewidth=MARKER_LINE_WIDTH, label=label)
    ax.set_xlabel(
        "Hours from complaint creation to first response (1-hour bins, upper edge included)"
    )
    ax.set_ylabel("Complaints")
    ax.legend()
    ax.set_title(
        f"First response for \"{focus['subcategory']}\" (measured, n = {focus['n']:,} of "
        f"{focus['total']:,})\n{focus['missing_first_response']['value']:,} complaints have no "
        "first response and are not shown",
        fontsize=TITLE_FONT_SIZE,
    )
    fig.tight_layout()
    return _save(plt, fig, path)
