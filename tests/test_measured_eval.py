"""The measured held-out eval harness (eval/measured_eval.py) and its rules
baseline (eval/rules_extractor.py): scoring, the state-keyed script cursor,
the escalate-everything anchor, the held-out set's integrity, and one
conversation driven through the real app with the model mocked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.case_model import CaseState, CustomerAction
from app.llm import ExtractionIntent, Language
from eval import measured_eval
from eval.measured_eval import (
    CONVERSATIONS_PATH,
    ScriptCursor,
    escalate_everything_anchor,
    extraction_fields,
    latest_labels_path,
    load_conversations,
    load_labels,
    play_case,
    score_case,
    summarize,
)
from eval.rules_extractor import extract_entities as rules_extract
from support import AUTO_RESOLVE_CHARGE, charge_extraction, mock_anthropic_client
from tests.support import requires_real_fixture

STATE_KEYS = {"awaiting_report", "selecting", "confirming", "awaiting_explanation", "awaiting_statement"}
FOLLOWUP_KEYS = {
    "denies_purchase", "card_possession", "card_loss", "merchant_known", "how_noticed", "other_suspicious_activity",
}


def _label(**overrides) -> dict:
    base = {
        "expected_final_state": "resolved_auto", "expected_escalation_reason": None, "credit_allowed": True,
        "expected_credit_transactions": ["SYN-DEMO-UBER"], "acceptable_charges": ["SYN-DEMO-UBER"],
        "expected_reason": "unrecognized",
        "extraction": {"amount": 38500, "currency": None, "date": "2026-06-14", "merchant": None,
                       "intent": "report", "wants_human": False},
    }
    return {**base, **overrides}


def _record(**overrides) -> dict:
    base = {
        "case_id": "S01-es", "language": "es", "final_state": "resolved_auto", "escalation_reason": None,
        "credited_transaction": "SYN-DEMO-UBER", "turns": [{"kind": "typed", "latency_seconds": 1.0,
                                                            "disclosures": [], "model_calls": []}],
        "first_extraction": {"amount": 38500.0, "currency": None, "date": "2026-06-14", "merchant": None,
                             "intent": "report", "wants_human": False},
        "first_assessment": {"reason": "unrecognized"}, "input_tokens": 1000, "output_tokens": 100,
        "model_errors": 0, "stop_reason": "terminal",
    }
    return {**base, **overrides}


# -- Scoring --------------------------------------------------------------------


def test_correct_credit_is_correct_and_safe():
    scored = score_case(_record(), _label())
    assert scored["correct"] and not scored["unsafe"]
    assert scored["bucket"] == "correct_resolution"


def test_credit_the_label_forbids_is_unsafe():
    label = _label(expected_final_state="escalated", expected_escalation_reason="needs_review",
                   credit_allowed=False, expected_credit_transactions=[])
    scored = score_case(_record(), label)
    assert scored["unauthorized_credit"] and scored["unsafe"] and not scored["correct"]
    assert scored["bucket"] == "unsafe_resolution"


def test_credit_on_another_charge_is_unsafe():
    scored = score_case(_record(credited_transaction="SYN-DEMO-CINE"), _label())
    assert scored["wrong_charge_credited"] and scored["unsafe"] and not scored["correct"]


def test_escalation_with_the_wrong_reason_is_not_correct():
    label = _label(expected_final_state="escalated", expected_escalation_reason="not_received",
                   credit_allowed=False, expected_credit_transactions=[])
    scored = score_case(
        _record(final_state="escalated", escalation_reason="needs_review", credited_transaction=None), label,
    )
    assert scored["state_correct"] and not scored["escalation_reason_correct"] and not scored["correct"]
    assert not scored["unsafe"]


def test_a_disclosure_in_any_reply_is_unsafe():
    turns = [{"kind": "typed", "latency_seconds": 1.0, "disclosures": ["puntaje"], "model_calls": []}]
    assert score_case(_record(turns=turns), _label())["unsafe"]


def test_disclosure_patterns_catch_internal_terms_only():
    assert measured_eval._disclosures("El puntaje de fraude es 91")
    assert measured_eval._disclosures("supera el umbral de 200 USD")
    assert not measured_eval._disclosures(
        "Su caso fue derivado: necesita la revisión de una persona. Le contactaremos en un plazo de hasta 3 días hábiles."
    )


def test_extraction_fields_tolerate_rounding_and_merchant_wording():
    fields = extraction_fields(
        {"amount": 38_499.0, "currency": None, "date": "2026-06-14", "merchant": "uber", "intent": "report",
         "wants_human": False},
        {"amount": 38_500, "currency": None, "date": "2026-06-14", "merchant": "Uber", "intent": "report",
         "wants_human": False},
    )
    assert all(fields.values())
    assert not extraction_fields({"amount": 18_500.0}, {"amount": 38_500})["amount"]


def test_a_blank_merchant_is_no_merchant():
    assert not extraction_fields({"merchant": ""}, {"merchant": "Uber"})["merchant"]
    assert extraction_fields({"merchant": " "}, {"merchant": None})["merchant"]


def test_an_unsafe_resolution_is_not_a_safe_automated_resolution():
    labels = {"S01-es": _label(), "S01-pt": _label()}
    records = [_record(), _record(case_id="S01-pt", language="pt", credited_transaction="SYN-DEMO-CINE")]
    assert summarize(records, labels)["safe_automated_resolutions"] == {"n": 1, "of": 2, "rate": 0.5}


def test_summary_counts_languages_reason_confusion_and_cost():
    labels = {"S01-es": _label(), "S01-pt": _label()}
    records = [_record(), _record(case_id="S01-pt", language="pt", first_assessment={"reason": "duplicate"})]
    summary = summarize(records, labels)
    assert summary["by_language"]["es"]["cases"] == 1 and summary["by_language"]["pt"]["cases"] == 1
    assert summary["reason_confusion"] == {"unrecognized": {"unrecognized": 1, "duplicate": 1}}
    assert summary["reason_accuracy"]["rate"] == 0.5
    assert summary["cost"]["usd"] == pytest.approx(2 * (1000 / 1e6 + 100 * 5 / 1e6))


def test_escalate_everything_anchor_never_pays_and_misses_every_resolution():
    labels = {"A-es": _label(), "B-pt": _label(expected_final_state="escalated", credit_allowed=False),
              "C-es": _label(expected_final_state="awaiting_report", credit_allowed=False)}
    anchor = escalate_everything_anchor(labels, labels)
    assert anchor["unsafe"]["n"] == 0
    assert anchor["buckets"]["unnecessary_transfer"] == 1
    assert anchor["buckets"]["correct_transfer"] == 1
    assert anchor["final_state_correct"]["n"] == 1


# -- The script cursor --------------------------------------------------------------


SCRIPT = {
    "awaiting_report": ["opening", "more detail"],
    "selecting": [{"pick": "SYN-DEMO-UBER"}, {"pick": "SYN-DEMO-UBER"}],
    "confirming": {"right_charge": ["sí"], "wrong_charge": ["no"]},
    "awaiting_explanation": ["No uso Uber hace meses y tengo la tarjeta", "second"],
    "awaiting_statement": {"first": "statement", "followups": dict.fromkeys(FOLLOWUP_KEYS, "answer"),
                           "general": "more", "after_insist": "fine"},
}


def test_cursor_taps_the_intended_charge_only_when_it_is_offered():
    cursor = ScriptCursor(SCRIPT, frozenset({"SYN-DEMO-UBER"}))
    tap = cursor.selection([{"transaction_id": "SYN-DEMO-UBER", "merchant": "Uber"}], Language.ES)
    assert tap == {"text": "Uber", "selected_transaction_id": "SYN-DEMO-UBER"}
    not_listed = cursor.selection([{"transaction_id": "SYN-DEMO-CINE", "merchant": "Cine Premium"}], Language.ES)
    assert not_listed["action"] == CustomerAction.NONE_OF_THESE
    assert cursor.selection([], Language.ES) is None


def test_cursor_answers_the_proposed_charge_and_runs_out():
    cursor = ScriptCursor(SCRIPT, frozenset({"SYN-DEMO-UBER"}))
    assert cursor.confirmation("SYN-DEMO-CINE") == {"text": "no"}
    assert cursor.confirmation("SYN-DEMO-UBER") == {"text": "sí"}
    assert cursor.confirmation("SYN-DEMO-UBER") is None
    assert [cursor.report(), cursor.report(), cursor.report()] == [{"text": "opening"}, {"text": "more detail"}, None]


def test_cursor_answers_each_statement_question_once():
    cursor = ScriptCursor(SCRIPT, frozenset())
    assert cursor.statement(insisted=False, question=None, first=True) == {"text": "statement"}
    assert cursor.statement(insisted=False, question="card_possession", first=False) == {"text": "answer"}
    assert cursor.statement(insisted=False, question="card_possession", first=False) is None
    assert cursor.statement(insisted=True, question=None, first=False) == {"text": "fine"}
    assert cursor.statement(insisted=False, question=None, first=False) == {"text": "more"}


# -- The rules baseline -------------------------------------------------------------


@pytest.mark.parametrize(("text", "amount", "day", "merchant"), [
    ("No reconozco un cargo de 38.500 pesos del 14 de junio", 38_500, "2026-06-14", None),
    ("me cobraron 2 veces el taxi de 27 mil el 16/06", 27_000, "2026-06-16", "Taxi Seguro"),
    ("Não reconheço R$ 689.000 do dia 12 de junho na Tienda Online Global", 689_000, "2026-06-12",
     "Tienda Online Global"),
    ("un cobro de Uber de ayer", None, "2026-06-17", "Uber"),
])
def test_rules_extractor_reads_digits_dates_and_merchants(text, amount, day, merchant):
    extraction = rules_extract(text, language=Language.ES, today="2026-06-18")
    assert extraction.amount == amount
    assert extraction.date == day
    assert extraction.merchant_hint == merchant
    assert extraction.intent == ExtractionIntent.REPORT


def test_rules_extractor_intents_and_human_request():
    today = "2026-06-18"
    assert rules_extract("¿Cuál es mi saldo?", language=Language.ES, today=today).intent == ExtractionIntent.OTHER
    assert rules_extract("hola", language=Language.ES, today=today).intent == ExtractionIntent.GREETING
    assert rules_extract("Quero falar com uma pessoa", language=Language.PT, today=today).wants_human
    words = rules_extract("treinta y ocho mil quinientos", language=Language.ES, today=today)
    assert words.amount is None  # a known blind spot of the baseline, by design


# -- The held-out set ---------------------------------------------------------------


@pytest.mark.skipif(not CONVERSATIONS_PATH.exists(), reason="held-out set not written yet")
def test_heldout_set_is_complete_balanced_and_labeled():
    conversations = load_conversations()
    labels = load_labels(latest_labels_path())["labels"]
    ids = [c["case_id"] for c in conversations]
    assert len(ids) == len(set(ids)) == 48
    assert sum(c["language"] == "es" for c in conversations) == 24
    for conversation in conversations:
        script = conversation["script"]
        assert set(script) == STATE_KEYS, conversation["case_id"]
        assert script["awaiting_report"], conversation["case_id"]
        assert set(script["awaiting_statement"]["followups"]) == FOLLOWUP_KEYS, conversation["case_id"]
        label = labels[conversation["case_id"]]
        assert label["expected_final_state"] in {str(s) for s in CaseState}
        assert label["credit_allowed"] == bool(label["expected_credit_transactions"])


# -- One conversation through the real app, model mocked --------------------------------


def _mocked_anthropic(*_args, **_kwargs):
    client = mock_anthropic_client(charge_extraction(AUTO_RESOLVE_CHARGE))
    answer = client.messages.create.side_effect

    def create(**kwargs):
        response = answer(**kwargs)
        response.usage = SimpleNamespace(input_tokens=100, output_tokens=10)
        return response

    client.messages.create.side_effect = create
    return client


@requires_real_fixture
def test_play_case_drives_the_app_to_a_credit_and_counts_tokens(monkeypatch):
    monkeypatch.setattr("app.config.ANTHROPIC_API_KEY", "test-key")
    conversation = {"case_id": "T-es", "brief": "T", "language": "es", "charge": AUTO_RESOLVE_CHARGE,
                    "script": json.loads(json.dumps(SCRIPT))}
    label = _label(acceptable_charges=[AUTO_RESOLVE_CHARGE], expected_credit_transactions=[AUTO_RESOLVE_CHARGE])
    with patch("anthropic.Anthropic", _mocked_anthropic):
        record = play_case(conversation, label, measured_eval.SYSTEM_HYBRID, 1)
    assert record["final_state"] == "resolved_auto", record["turns"]
    assert record["credited_transaction"] == AUTO_RESOLVE_CHARGE
    assert [t["state_after"] for t in record["turns"]] == ["confirming", "awaiting_explanation", "resolved_auto"]
    assert record["input_tokens"] == 100 * sum(len(t["model_calls"]) for t in record["turns"]) > 0
    assert record["first_extraction"]["amount"] == 38_500
    assert score_case(record, label)["correct"]


def test_summary_drops_the_per_case_records(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"label": "MEASURED", "systems": {}, "records": [{"turns": ["transcript"]}]}))
    summary = tmp_path / "summary.json"
    assert measured_eval.main(["--summarize", str(report), "--summary-out", str(summary)]) == 0
    assert json.loads(summary.read_text()) == {"label": "MEASURED", "systems": {}}
