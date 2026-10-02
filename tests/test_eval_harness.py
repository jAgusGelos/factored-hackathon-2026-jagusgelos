"""Tests for the Milestone 5 eval harness itself (Task 5.3)."""

from __future__ import annotations

import json
from pathlib import Path

from app.case_model import CaseState
from eval.run_eval import CaseOutcome, build_report, run
from tests.support import requires_real_fixture

pytestmark = requires_real_fixture


def test_run_produces_zero_unsafe_outcomes(tmp_path):
    report = run(tmp_path / "eval_app.db")
    assert report["unsafe_outcomes"]["count"] == 0, report["unsafe_outcomes"]["cases"]
    # The baselines run only when asked for (main() does); see tests/test_system_comparison.py.
    assert "system_comparison" not in report


def test_run_covers_the_required_scenarios_in_both_languages(tmp_path):
    report = run(tmp_path / "eval_app.db")
    demo_keys = {c["case_key"] for c in report["by_group"]["required_demo"]}
    scenarios = (
        "auto_resolve_reported", "auto_resolve_picked", "ambiguous_duplicate_picked",
        "ambiguous_not_in_list", "escalate_policy", "escalate_human_request",
    )
    assert demo_keys == {f"{name}[{language}]" for name in scenarios for language in ("es", "pt")}


def test_run_covers_the_adversarial_scenarios(tmp_path):
    report = run(tmp_path / "eval_app.db")
    adversarial_keys = {c["case_key"] for c in report["by_group"]["adversarial"]}
    # Expired-session and unauthorized-access are structural (no conversational
    # outcome) and are verified in tests/test_adversarial.py, not re-run here.
    assert adversarial_keys == {
        "missing_data", "prompt_injection", "tool_failure", "multilingual_ambiguity",
        "unoffered_selection", "repeat_credit", "early_human_request", "currency_parity[es]", "currency_parity[pt]",
    }


def test_the_same_report_takes_the_same_path_in_both_languages(tmp_path):
    report = run(tmp_path / "eval_app.db")
    parity = {c["case_key"]: c for c in report["by_group"]["adversarial"] if c["case_key"].startswith("currency_parity")}
    assert {c["actual_state"] for c in parity.values()} == {"confirming"}
    assert all(c["safe"] for c in parity.values())


def test_the_report_breaks_results_down_by_language(tmp_path):
    report = run(tmp_path / "eval_app.db")
    by_language = report["by_language"]
    assert set(by_language) == {"es", "pt"}
    assert sum(summary["cases"] for summary in by_language.values()) == report["sample_size"]
    assert all(summary["unsafe_cases"] == [] for summary in by_language.values())
    assert report["by_language_note"]


def test_run_covers_the_policy_abuse_scenarios(tmp_path):
    report = run(tmp_path / "eval_app.db")
    policy_keys = {c["case_key"] for c in report["by_group"]["policy_abuse"]}
    assert policy_keys == {
        "card_present_unrecognized", "merchant_history_unrecognized", "duplicate_without_twin",
        "not_received_merchant_dispute", "explanation_injection", "second_unrecognized_credit",
        "duplicate_pair_twice", "same_charge_after_escalation",
    }
    assert all(c["actual_state"] == "escalated" for c in report["by_group"]["policy_abuse"])


def test_every_escalation_needing_a_statement_carries_one(tmp_path):
    report = run(tmp_path / "eval_app.db")
    completeness = report["escalation_quality"]["statement_completeness_rate"]
    assert completeness["rate"] == 1.0, completeness["missing_case_keys"]
    assert completeness["of_escalated_needing_a_statement"] >= 15


def test_run_covers_the_statement_scenarios(tmp_path):
    report = run(tmp_path / "eval_app.db")
    statement = {c["case_key"]: c for c in report["by_group"]["statement"]}
    assert set(statement) == {
        "statement_given[es]", "statement_given[pt]", "statement_declined_twice", "statement_typed_refusal",
        "statement_one_followup", "statement_summary_timeout", "service_issue_bypasses_statement",
        "tap_to_statement_no_model", "statement_injection", "vague_explanation_then_person",
        "specific_explanation_then_person",
    }
    assert statement["vague_explanation_then_person"]["statement_status"] == "given"
    assert statement["specific_explanation_then_person"]["account_given"] is True
    assert statement["statement_summary_timeout"]["escalation_reason"] == "needs_review"
    assert statement["service_issue_bypasses_statement"]["statement_status"] is None


def test_completeness_leaves_out_service_issues_and_explained_escalations():
    def outcome(key, **kwargs):
        return CaseOutcome(
            case_key=key, group="g", expected_state=CaseState.ESCALATED, actual_state=CaseState.ESCALATED,
            safe=True, latency_seconds=0.0, estimated_prompt_chars=0, estimated_completion_chars=0, case_id=key,
            **kwargs,
        )

    report = build_report([
        outcome("given", escalation_reason="needs_review", statement_status="given"),
        outcome("skipped", escalation_reason="human_requested", statement_status=None),
        outcome("service", escalation_reason="service_issue"),
        outcome("explained", escalation_reason="needs_review", account_given=True),
    ])

    completeness = report["escalation_quality"]["statement_completeness_rate"]
    assert (completeness["count"], completeness["of_escalated_needing_a_statement"]) == (1, 2)
    assert completeness["missing_case_keys"] == ["skipped"]


def test_report_has_all_required_metrics():
    report = build_report([])
    assert "safe_automated_resolution_rate" in report
    assert "containment_rate" in report
    assert "escalation_quality" in report
    assert "unsafe_outcomes" in report
    assert "latency_seconds" in report
    assert "estimated_cost_usd" in report
    assert report["disclosure"]  # never silently omitted


def test_report_clearly_labels_offline_simulated_not_measured():
    report = build_report([])
    disclosure = report["disclosure"].lower()
    assert "offline" in disclosure or "simulated" in disclosure
    assert "estimated" in report["estimated_cost_usd"]["method"].lower()


def test_run_writes_a_report_file(tmp_path, monkeypatch):
    import eval.run_eval as run_eval_module

    report_path = tmp_path / "report.json"
    monkeypatch.setattr(run_eval_module, "DEFAULT_REPORT_PATH", report_path)
    # The baselines' content is covered by tests/test_system_comparison.py; here
    # main() only has to produce the comparison block, so it runs without them.
    monkeypatch.setattr(run_eval_module, "BASELINE_VARIANTS", {})

    exit_code = run_eval_module.main()

    assert exit_code == 0
    assert report_path.exists()
    assert Path(report_path).stat().st_size > 0
    assert "system_comparison" in json.loads(report_path.read_text())
