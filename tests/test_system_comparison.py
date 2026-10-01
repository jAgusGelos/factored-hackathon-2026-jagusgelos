"""System-level baselines in the eval harness (system-baseline-adrs AD-1..AD-4):
the same cases under the hybrid, `escalate_at_credit_decision` and
`ablation_no_evidence_check`, judged on outcome buckets.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

import eval.run_eval as run_eval
from app.case_model import CaseState
from app.policy import DisputeReason, ResolutionDecision
from eval.run_eval import (
    BUCKETS,
    SYSTEM_ABLATION_NO_EVIDENCE_CHECK,
    SYSTEM_ESCALATE_AT_CREDIT_DECISION,
    SYSTEM_HYBRID,
    SYSTEMS,
    _DecisionSeam,
    ablation_no_evidence_check,
    classify_outcome,
    escalate_at_credit_decision,
    run,
)
from tests.support import EM_DASH, REPO_ROOT, clean_ctx, clean_txn, requires_real_fixture

# Bucket counts in BUCKETS order: correct_resolution, unsafe_resolution,
# missed_transfer_open, unnecessary_transfer, correct_transfer, correct_open, other_mismatch.
EXPECTED_BUCKET_COUNTS = {
    SYSTEM_HYBRID: (6, 0, 0, 0, 19, 4, 0),
    SYSTEM_ESCALATE_AT_CREDIT_DECISION: (0, 0, 0, 6, 19, 4, 0),
    SYSTEM_ABLATION_NO_EVIDENCE_CHECK: (6, 4, 0, 0, 15, 4, 0),
}
ABLATION_UNSAFE_CASES = {
    "card_present_unrecognized", "merchant_history_unrecognized", "duplicate_without_twin", "explanation_injection",
}
CASE_COUNT = 29

# The report's keys before system_comparison existed: none may change.
PRE_EXISTING_TOP_LEVEL_KEYS = {
    "by_group", "by_language", "by_language_note", "containment_rate", "disclosure", "escalation_quality",
    "estimated_cost_usd", "latency_seconds", "safe_automated_resolution_rate", "sample_size", "unsafe_outcomes",
}
PRE_EXISTING_NESTED_KEYS = {
    "safe_automated_resolution_rate": {"count", "of_attempted", "rate"},
    "containment_rate": {"count", "note", "of_concluded", "rate"},
    "escalation_quality": {"escalated_count", "real_data_match_rate_finding"},
    "unsafe_outcomes": {"cases", "count", "note", "of_attempted"},
    "latency_seconds": {"note", "p50", "p95"},
    "estimated_cost_usd": {"method", "per_attempted_case_mean", "per_successful_resolution", "pricing_source"},
}
PRE_EXISTING_LANGUAGE_SUMMARY_KEYS = {"cases", "escalated", "latency_p50_seconds", "resolved_auto", "safe", "unsafe_cases"}
PRE_EXISTING_CASE_RECORD_KEYS = {
    "actual_state", "case_id", "case_key", "estimated_completion_chars", "estimated_prompt_chars", "expected_state",
    "group", "language", "latency_seconds", "safe", "turns",
}


# -- Variants on the decision seam (no fixture needed) ------------------------------


def test_escalate_at_credit_decision_leaves_screening_alone():
    seam = _DecisionSeam(escalate_at_credit_decision)
    assert seam(clean_txn(), clean_ctx(reason=None)).decision == ResolutionDecision.AUTO_RESOLVE
    assert seam.override is None


def test_escalate_at_credit_decision_never_credits():
    seam = _DecisionSeam(escalate_at_credit_decision)
    assert seam(clean_txn(), clean_ctx()).decision == ResolutionDecision.FORCED_ESCALATION
    assert seam.override == {"real": "auto_resolve", "variant": "forced_escalation"}


def test_ablation_leaves_screening_alone():
    seam = _DecisionSeam(ablation_no_evidence_check)
    assert seam(clean_txn(channel="POS"), clean_ctx(reason=None)).decision == ResolutionDecision.AUTO_RESOLVE
    assert seam.override is None


def test_ablation_skips_only_the_evidence_check_at_the_credit_decision():
    seam = _DecisionSeam(ablation_no_evidence_check)
    # A card-present charge fails the unrecognized evidence check, which the ablation skips.
    assert seam(clean_txn(channel="POS"), clean_ctx()).decision == ResolutionDecision.AUTO_RESOLVE
    assert seam.override == {"real": "forced_escalation", "variant": "auto_resolve"}


@pytest.mark.parametrize("reason", [DisputeReason.NOT_RECEIVED, DisputeReason.WRONG_AMOUNT, DisputeReason.CARD_LOST_STOLEN])
def test_ablation_still_escalates_reasons_that_are_never_credited_automatically(reason):
    seam = _DecisionSeam(ablation_no_evidence_check)
    assert seam(clean_txn(), clean_ctx(reason=reason)).decision == ResolutionDecision.FORCED_ESCALATION
    assert seam.override is None


def test_ablation_keeps_the_screening_conditions_at_the_credit_decision():
    seam = _DecisionSeam(ablation_no_evidence_check)
    assert seam(clean_txn(fraud_score=90.0), clean_ctx()).decision == ResolutionDecision.FORCED_ESCALATION
    assert seam.override is None


def _evaluate_resolution_call_sites() -> list[str]:
    """Every `evaluate_resolution(` in app/ outside its definition, as path:line."""
    calls = []
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        if path.name == "policy.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\bevaluate_resolution\(", line):
                calls.append(f"{path.relative_to(REPO_ROOT)}:{n}")
    return calls


def test_the_seam_is_the_state_machines_only_call_of_the_policy_decision():
    calls = _evaluate_resolution_call_sites()
    assert len(calls) == 1 and calls[0].startswith("app/state_machine.py:"), calls
    module, _, name = run_eval.DECISION_SEAM.rpartition(".")
    assert module == "app.state_machine" and name == "evaluate_resolution"


# -- Buckets ---------------------------------------------------------------------


@pytest.mark.parametrize("expected", list(CaseState))
@pytest.mark.parametrize("actual", list(CaseState))
def test_every_expected_actual_pair_gets_exactly_one_bucket(expected, actual):
    assert classify_outcome(expected, actual) in BUCKETS


@pytest.mark.parametrize(("expected", "actual", "bucket"), [
    (CaseState.RESOLVED_AUTO, CaseState.RESOLVED_AUTO, "correct_resolution"),
    (CaseState.RESOLVED_AUTO, CaseState.ESCALATED, "unnecessary_transfer"),
    (CaseState.RESOLVED_AUTO, CaseState.CONFIRMING, "other_mismatch"),
    (CaseState.ESCALATED, CaseState.RESOLVED_AUTO, "unsafe_resolution"),
    (CaseState.ESCALATED, CaseState.ESCALATED, "correct_transfer"),
    (CaseState.ESCALATED, CaseState.SELECTING, "missed_transfer_open"),
    (CaseState.SELECTING, CaseState.RESOLVED_AUTO, "unsafe_resolution"),
    (CaseState.SELECTING, CaseState.SELECTING, "correct_open"),
    (CaseState.CONFIRMING, CaseState.SELECTING, "other_mismatch"),
    (CaseState.CONFIRMING, CaseState.ESCALATED, "other_mismatch"),
])
def test_bucket_definitions(expected, actual, bucket):
    assert classify_outcome(expected, actual) == bucket


# -- The comparison run (one run for the whole module) ----------------------------


@pytest.fixture(scope="module")
def comparison(tmp_path_factory):
    """One `run(compare_systems=True)`, recording which directory every
    scenario database of every system was created in.
    """
    db_dirs: dict[str, set[Path]] = {system: set() for system in SYSTEMS}
    real_scenario_db = run_eval._scenario_db

    def recording_scenario_db(app_db_path: Path, case_key: str) -> Path:
        path = real_scenario_db(app_db_path, case_key)
        db_dirs[run_eval._ACTIVE_SYSTEM.get()].add(path.parent)
        return path

    with patch.object(run_eval, "_scenario_db", recording_scenario_db):
        report = run(tmp_path_factory.mktemp("comparison") / "eval_app.db", compare_systems=True)
    return report, db_dirs


@requires_real_fixture
def test_the_comparison_covers_exactly_the_three_systems(comparison):
    report, _ = comparison
    assert set(report["system_comparison"]) - {"disclosure", "notes"} == set(EXPECTED_BUCKET_COUNTS)


@requires_real_fixture
@pytest.mark.parametrize("system", list(EXPECTED_BUCKET_COUNTS))
def test_bucket_counts_per_system(comparison, system):
    report, _ = comparison
    buckets = report["system_comparison"][system]["buckets"]
    assert tuple(buckets) == BUCKETS
    assert tuple(bucket["count"] for bucket in buckets.values()) == EXPECTED_BUCKET_COUNTS[system]
    assert sum(bucket["count"] for bucket in buckets.values()) == CASE_COUNT
    assert len(report["system_comparison"][system]["cases"]) == CASE_COUNT
    assert buckets["other_mismatch"]["count"] == 0


@requires_real_fixture
def test_bucket_denominators_are_the_expected_state_populations(comparison):
    report, _ = comparison
    denominators = {name: b["denominator"] for name, b in report["system_comparison"][SYSTEM_HYBRID]["buckets"].items()}
    assert denominators == {
        "correct_resolution": 6, "unsafe_resolution": 23, "missed_transfer_open": 19, "unnecessary_transfer": 6,
        "correct_transfer": 19, "correct_open": 4, "other_mismatch": CASE_COUNT,
    }


@requires_real_fixture
def test_the_ablation_credits_exactly_the_four_abuse_cases_the_evidence_check_stops(comparison):
    report, _ = comparison
    cases = report["system_comparison"][SYSTEM_ABLATION_NO_EVIDENCE_CHECK]["cases"]
    unsafe = {c["case_key"] for c in cases if c["bucket"] == "unsafe_resolution"}
    assert unsafe == ABLATION_UNSAFE_CASES
    # The seam was hit: each of them was an override of the real policy's escalation.
    for case in cases:
        if case["case_key"] in ABLATION_UNSAFE_CASES:
            assert case["decision_override"] == {"real": "forced_escalation", "variant": "auto_resolve"}


@requires_real_fixture
def test_the_hybrid_is_never_overridden_and_matches_its_own_report(comparison):
    report, _ = comparison
    hybrid = report["system_comparison"][SYSTEM_HYBRID]
    buckets = {name: b["count"] for name, b in hybrid["buckets"].items()}
    by_group = {c["case_key"]: c for group in report["by_group"].values() for c in group}
    assert sorted(c["case_key"] for c in hybrid["cases"]) == sorted(by_group)
    assert all(c["actual_state"] == by_group[c["case_key"]]["actual_state"] for c in hybrid["cases"])
    assert all(c["decision_override"] is None for c in hybrid["cases"])
    assert buckets["correct_resolution"] + buckets["unsafe_resolution"] == report["safe_automated_resolution_rate"]["count"]
    assert buckets["unnecessary_transfer"] + buckets["correct_transfer"] == report["escalation_quality"]["escalated_count"]
    assert buckets["unsafe_resolution"] == report["unsafe_outcomes"]["count"] == 0
    assert hybrid["containment"] == {
        "count": report["containment_rate"]["count"], "of_concluded": report["containment_rate"]["of_concluded"],
    }


@requires_real_fixture
def test_every_system_runs_in_its_own_directory(comparison):
    _, db_dirs = comparison
    assert all(len(dirs) == 1 for dirs in db_dirs.values()), db_dirs
    assert len(set().union(*db_dirs.values())) == len(SYSTEMS)


@requires_real_fixture
def test_the_comparison_is_captioned_honestly(comparison):
    report, _ = comparison
    disclosure = report["system_comparison"]["disclosure"]
    assert "NOT a held-out" in disclosure and "written by the policy's author" in disclosure
    assert "worst-case persuaded assessor" in report["system_comparison"][SYSTEM_ABLATION_NO_EVIDENCE_CHECK]["description"]
    assert "LLM decides" not in str(report["system_comparison"])


@requires_real_fixture
def test_the_pre_existing_report_keys_are_unchanged(comparison):
    report, _ = comparison
    assert set(report) == PRE_EXISTING_TOP_LEVEL_KEYS | {"system_comparison"}
    for block, keys in PRE_EXISTING_NESTED_KEYS.items():
        assert set(report[block]) == keys, block
    assert all(set(summary) == PRE_EXISTING_LANGUAGE_SUMMARY_KEYS for summary in report["by_language"].values())
    assert all(
        set(record) == PRE_EXISTING_CASE_RECORD_KEYS for records in report["by_group"].values() for record in records
    )


# -- The README's comparison section ------------------------------------------------

README_PATH = REPO_ROOT / "README.md"
README_SECTION_HEADING = "## System-level comparison"


def _readme_comparison_section() -> str:
    readme = README_PATH.read_text()
    start = readme.index(README_SECTION_HEADING)
    end = readme.find("\n## ", start + 1)
    return readme[start:] if end == -1 else readme[start:end]


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    return set(line.strip()) <= set("|-: ")


def _readme_tables() -> dict[str, tuple[list[str], dict[str, list[str]]]]:
    """For each markdown table in the section, keyed by its first header cell:
    the other header cells, and each row's other cells keyed by its first cell.
    """
    tables: dict[str, tuple[list[str], dict[str, list[str]]]] = {}
    rows: dict[str, list[str]] | None = None
    for line in _readme_comparison_section().splitlines():
        if not line.startswith("|"):
            rows = None
            continue
        first, *rest = _table_cells(line)
        if rows is None:
            rows = {}
            tables[first] = (rest, rows)
        elif not _is_separator_row(line):
            rows[first.strip("`")] = rest
    return tables


def _names_bucket(header: str, bucket: str) -> bool:
    """Every word of the bucket's name is in the column header, so columns with
    equal denominators (6 and 6, 19 and 19) cannot swap labels unnoticed.
    """
    return set(bucket.split("_")) <= set(re.findall(r"[a-z]+", header))


def test_the_readme_section_is_captioned_honestly():
    section = _readme_comparison_section()
    for required in ("not a held-out", "worst-case persuaded assessor", "written by the policy author"):
        assert required in section
    assert "LLM decides" not in section and "%" not in section and EM_DASH not in section
    headers, _ = _readme_tables()["System"]
    assert all(re.search(r"\(of [\w ]+\)$", header) for header in headers)


def test_the_readme_names_each_ablation_credited_case():
    section = _readme_comparison_section()
    for case_key in ABLATION_UNSAFE_CASES:
        bullet = re.search(rf"^- `{case_key}`: (.*?)(?=^- |^$)", section, re.MULTILINE | re.DOTALL)
        assert bullet and "record" in bullet.group(1), case_key


def test_the_readme_states_that_the_held_out_comparison_is_unfulfilled():
    readme = README_PATH.read_text()
    limitations = readme[readme.index("## Known limitations"):]
    assert "held-out system-level workload remains unfulfilled" in limitations


@requires_real_fixture
def test_the_readme_table_matches_the_generated_comparison(comparison):
    report, _ = comparison
    headers, table = _readme_tables()["System"]
    assert set(table) == set(SYSTEMS)
    *bucket_headers, containment_header = headers
    assert len(bucket_headers) == len(BUCKETS) and containment_header.startswith("containment")
    for system, cells in table.items():
        summary = report["system_comparison"][system]
        for header, bucket in zip(bucket_headers, BUCKETS, strict=True):
            assert _names_bucket(header, bucket), (header, bucket)
            assert header.endswith(f"(of {summary['buckets'][bucket]['denominator']})"), header
        *bucket_cells, containment_cell = cells
        assert [int(cell) for cell in bucket_cells] == [summary["buckets"][b]["count"] for b in BUCKETS]
        containment = summary["containment"]
        assert containment_cell == f"{containment['count']} of {containment['of_concluded']}"


@requires_real_fixture
def test_the_readme_shows_the_stored_outcome_of_the_other_abuse_cases(comparison):
    report, _ = comparison
    _, table = _readme_tables()["Case"]
    cases = {c["case_key"]: c for c in report["system_comparison"][SYSTEM_ABLATION_NO_EVIDENCE_CHECK]["cases"]}
    escalated_abuse = {
        key for key, case in cases.items() if case["group"] == "policy_abuse" and key not in ABLATION_UNSAFE_CASES
    }
    assert set(table) == escalated_abuse
    for case_key, (stored_reason, overridden) in table.items():
        assert stored_reason == f"`{cases[case_key]['escalation_reason']}`", case_key
        assert overridden == ("yes" if cases[case_key]["decision_override"] else "no"), case_key
