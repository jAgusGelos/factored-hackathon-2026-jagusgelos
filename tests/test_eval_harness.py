"""Tests for the Milestone 5 eval harness itself (Task 5.3)."""

from __future__ import annotations

from pathlib import Path

from eval.run_eval import build_report, run
from tests.support import requires_real_fixture

pytestmark = requires_real_fixture


def test_run_produces_zero_unsafe_outcomes(tmp_path):
    report = run(tmp_path / "eval_app.db")
    assert report["unsafe_outcomes"]["count"] == 0, report["unsafe_outcomes"]["cases"]


def test_run_covers_all_3_required_cases_in_both_languages(tmp_path):
    report = run(tmp_path / "eval_app.db")
    demo_keys = {c["case_key"] for c in report["by_group"]["required_demo"]}
    for username in ("cliente.claro", "cliente.ambiguo", "cliente.escalado"):
        for language in ("es", "pt"):
            assert f"{username}[{language}]" in demo_keys


def test_run_covers_all_6_adversarial_scenarios(tmp_path):
    report = run(tmp_path / "eval_app.db")
    adversarial_keys = {c["case_key"] for c in report["by_group"]["adversarial"]}
    # Expired-session and unauthorized-access are structural (no conversational
    # outcome) and are verified in tests/test_adversarial.py, not re-run here.
    assert adversarial_keys == {
        "missing_data", "prompt_injection", "tool_failure", "multilingual_ambiguity",
    }


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

    exit_code = run_eval_module.main()

    assert exit_code == 0
    assert report_path.exists()
    assert Path(report_path).stat().st_size > 0
