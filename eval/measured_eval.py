"""Measured held-out evaluation against the real model (MEASURED, not SIMULATED).

`eval/run_eval.py` plays 40 constructed cases against a mocked client. This
harness plays the pre-registered held-out set in `eval/heldout/` against the
real app and the real Anthropic API:

- `conversations.json`: 48 customer scripts (24 situations x es/pt) written by
  a subagent that saw only the customer's charge list and a scenario brief.
  Each script is keyed by the STATE the agent leaves the case in, so the run
  does not depend on the agent's exact wording.
- `labels_v<N>.json`: the expected outcome of each case, derived from the
  written policy before any run (the commit that adds a label version is its
  pre-registration).

Every case runs in its own app database, through the FastAPI app
(`TestClient`, the real `/auth/login` and `/api/chat`), with the production
model and prompts. Model calls go through a recording wrapper around the
Anthropic client, so cost is computed from the API's own token usage.

Systems: `hybrid` (the shipped app) and `rules_extractor` (baseline b: the
model's entity extraction replaced by `eval/rules_extractor.py`, every other
model call unchanged). Baseline a, `escalate_everything`, hands every case to
a person at the first message; it needs no model call, so it is computed from
the labels rather than run.

Run: `python -m eval.measured_eval --runs 3` (see `--help`). Reports go under
`data/` (gitignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import patch

from app.case_model import TERMINAL_STATES, CaseState, CustomerAction
from app.llm import ASSESSMENT_MARKER, CONFIRMATION_MARKER, STATEMENT_MARKER, Language
from eval.run_eval import (
    BUCKETS,
    HAIKU_INPUT_USD_PER_M_TOKENS,
    HAIKU_OUTPUT_USD_PER_M_TOKENS,
    NOT_IN_LIST,
    classify_outcome,
)

logger = logging.getLogger("eval.measured_eval")

REPO_ROOT = Path(__file__).resolve().parent.parent
HELDOUT_DIR = REPO_ROOT / "eval" / "heldout"
CONVERSATIONS_PATH = HELDOUT_DIR / "conversations.json"
DEFAULT_REPORT_PATH = REPO_ROOT / "data" / "measured_eval_report.json"

SYSTEM_HYBRID = "hybrid"
SYSTEM_RULES_EXTRACTOR = "rules_extractor"
SYSTEMS = (SYSTEM_HYBRID, SYSTEM_RULES_EXTRACTOR)
ANCHOR_ESCALATE_EVERYTHING = "escalate_everything"

MAX_TURNS = 16
AMOUNT_REL_TOLERANCE = 0.005

# Customer-facing text must never carry an internal score, threshold, rule or
# model name (README "Escalation notice"). Checked on every agent reply.
DISCLOSURE_PATTERNS = (
    r"fraud[_ ]?score", r"\bscore\b", r"puntaje", r"pontua[cç][aã]o", r"umbral", r"limiar", r"threshold",
    r"clasificador", r"classificador", r"classifier", r"\b91\b", r"\bUSD\b", r"d[oó]lares", r"evidence check",
    r"auto[- ]?resolve", r"AD-1[13]",
)


# -- Inputs ---------------------------------------------------------------------


def load_conversations(path: Path = CONVERSATIONS_PATH) -> list[dict]:
    return json.loads(path.read_text())["conversations"]


def latest_labels_path() -> Path:
    versions = sorted(HELDOUT_DIR.glob("labels_v*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem)))
    if not versions:
        raise FileNotFoundError(f"No labels_v<N>.json in {HELDOUT_DIR}")
    return versions[-1]


def load_labels(path: Path) -> dict:
    return json.loads(path.read_text())


# -- Recording the real model calls ----------------------------------------------


@dataclass
class ModelCall:
    kind: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    error: str | None = None


_CALLS: list[ModelCall] = []
_EXTRACTIONS: list[dict] = []
_ASSESSMENTS: list[dict] = []


def _call_kind(system: str | None) -> str:
    system = system or ""
    if CONFIRMATION_MARKER in system:
        return "confirmation"
    if ASSESSMENT_MARKER in system:
        return "explanation_assessment"
    if STATEMENT_MARKER in system:
        return "statement_assessment"
    if "JSON" in system:
        return "extraction"
    return "nlg"


class _RecordingMessages:
    def __init__(self, messages) -> None:
        self._messages = messages

    def create(self, **kwargs):
        start = time.perf_counter()
        try:
            response = self._messages.create(**kwargs)
        except Exception as exc:
            _CALLS.append(ModelCall(_call_kind(kwargs.get("system")), 0, 0, time.perf_counter() - start, type(exc).__name__))
            raise
        usage = response.usage
        _CALLS.append(ModelCall(
            _call_kind(kwargs.get("system")), usage.input_tokens, usage.output_tokens, time.perf_counter() - start,
        ))
        return response


def _recording_anthropic(real_class):
    def factory(*args, **kwargs):
        client = real_class(*args, **kwargs)
        client.messages = _RecordingMessages(client.messages)
        return client
    return factory


def _recording(fn, sink: list[dict], to_dict):
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        sink.append(to_dict(result))
        return result
    return wrapper


def _extraction_dict(extraction) -> dict:
    return {
        "amount": extraction.amount, "currency": extraction.currency, "date": extraction.date,
        "merchant": extraction.merchant_hint, "intent": str(extraction.intent), "wants_human": extraction.wants_human,
        "parse_failed": extraction.parse_failed,
    }


def _assessment_dict(assessment) -> dict:
    if assessment is None:
        return {"reason": None, "specific": None, "consistent": None}
    return {"reason": str(assessment.reason), "specific": assessment.specific, "consistent": assessment.consistent}


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1e6 * HAIKU_INPUT_USD_PER_M_TOKENS + output_tokens / 1e6 * HAIKU_OUTPUT_USD_PER_M_TOKENS


# -- Playing one script ------------------------------------------------------------


@dataclass
class ScriptCursor:
    """What the customer says next, given the state the agent left the case in."""

    script: dict
    intended: frozenset[str]
    used: Counter = field(default_factory=Counter)
    statement_used: set[str] = field(default_factory=set)

    def _next(self, key: str, items: list) -> object | None:
        index = self.used[key]
        if index >= len(items):
            return None
        self.used[key] += 1
        return items[index]

    def report(self) -> dict | None:
        text = self._next("awaiting_report", self.script["awaiting_report"])
        return {"text": text} if text is not None else None

    def selection(self, options: list[dict], language: Language) -> dict | None:
        item = self._next("selecting", self.script["selecting"])
        if item is None:
            return None
        if "text" in item:
            return {"text": item["text"]}
        pick = item.get("pick")
        offered = {o["transaction_id"]: o for o in options}
        if pick in offered:
            return {"text": offered[pick].get("merchant") or pick, "selected_transaction_id": pick}
        return {"text": NOT_IN_LIST[language], "action": CustomerAction.NONE_OF_THESE}

    def confirmation(self, proposed: str | None) -> dict | None:
        key = "right_charge" if proposed in self.intended else "wrong_charge"
        text = self._next(f"confirming.{key}", self.script["confirming"][key])
        return {"text": text} if text is not None else None

    def explanation(self) -> dict | None:
        text = self._next("awaiting_explanation", self.script["awaiting_explanation"])
        return {"text": text} if text is not None else None

    def statement(self, *, insisted: bool, question: str | None, first: bool) -> dict | None:
        block = self.script["awaiting_statement"]
        if insisted:
            key, text = "after_insist", block["after_insist"]
        elif question is not None:
            key, text = f"followups.{question}", block["followups"].get(question)
        elif first:
            key, text = "first", block["first"]
        else:
            key, text = "general", block["general"]
        if text is None or key in self.statement_used:
            return None
        self.statement_used.add(key)
        return {"text": text}


def _disclosures(text: str) -> list[str]:
    return [p for p in DISCLOSURE_PATTERNS if re.search(p, text, flags=re.IGNORECASE)]


def _next_move(cursor: ScriptCursor, reply: dict, case, language: Language) -> dict | None:
    state = reply["state"]
    if state in (CaseState.AWAITING_REPORT, CaseState.CLARIFYING):
        return cursor.report()
    if state == CaseState.SELECTING:
        return cursor.selection(reply.get("options") or [], language)
    if state == CaseState.CONFIRMING:
        return cursor.confirmation(case.matched_transaction_id if case else None)
    if state == CaseState.AWAITING_EXPLANATION:
        return cursor.explanation()
    if state == CaseState.AWAITING_STATEMENT:
        from app import replies

        facts = (case.statement_facts or {}) if case else {}
        return cursor.statement(
            insisted=reply["reply"].strip().startswith(replies.STATEMENT_INSIST[language][:40]),
            question=facts.get("question"),
            first=case is None or (case.statement_followups == 0 and case.statement_declines == 0
                                    and not case.statement_text),
        )
    return None


def play_case(conversation: dict, label: dict, system: str, run_index: int) -> dict:
    """One conversation against the real app in a fresh app db (run in a
    worker process). Returns the per-case record the report is built from.
    """
    import anthropic
    from fastapi.testclient import TestClient

    from app import cases, config, llm
    from app.main import app
    from etl.build_fixture import DEMO_USERNAME
    from eval import rules_extractor

    language = Language(conversation["language"])
    cursor = ScriptCursor(conversation["script"], frozenset(label.get("acceptable_charges") or []))
    _CALLS.clear()
    _EXTRACTIONS.clear()
    _ASSESSMENTS.clear()
    turns: list[dict] = []
    stop_reason = "max_turns"
    case_id = None
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        db_path = Path(tmp) / "app.db"
        stack.enter_context(patch.object(config, "APP_DB_PATH", db_path))
        stack.enter_context(patch("app.llm.anthropic.Anthropic", _recording_anthropic(anthropic.Anthropic)))
        extractor = rules_extractor.extract_entities if system == SYSTEM_RULES_EXTRACTOR else llm.extract_entities
        stack.enter_context(patch("app.llm.extract_entities", _recording(extractor, _EXTRACTIONS, _extraction_dict)))
        stack.enter_context(patch(
            "app.llm.assess_explanation", _recording(llm.assess_explanation, _ASSESSMENTS, _assessment_dict),
        ))
        client = stack.enter_context(TestClient(app, base_url="https://testserver"))
        password = json.loads(config.DEMO_USERS_PATH.read_text())[DEMO_USERNAME]["password"]
        login = client.post("/auth/login", json={"username": DEMO_USERNAME, "password": password})
        login.raise_for_status()

        reply: dict = {"state": CaseState.AWAITING_REPORT, "reply": "", "options": []}
        for _ in range(MAX_TURNS):
            case = cases.get_case(case_id, db_path=db_path) if case_id else None
            move = _next_move(cursor, reply, case, language)
            if move is None:
                stop_reason = "script_exhausted"
                break
            calls_before = len(_CALLS)
            extractions_before = len(_EXTRACTIONS)
            payload = {
                "case_id": case_id, "message": move["text"], "language": str(language), "turn_id": str(uuid.uuid4()),
                "selected_transaction_id": move.get("selected_transaction_id"),
                "action": str(move["action"]) if move.get("action") else None,
            }
            start = time.perf_counter()
            response = client.post("/api/chat", json=payload)
            latency = time.perf_counter() - start
            response.raise_for_status()
            state_before = reply["state"]
            reply = response.json()
            case_id = reply["case_id"]
            calls = _CALLS[calls_before:]
            turns.append({
                "state_before": str(state_before), "state_after": str(reply["state"]),
                "customer": move["text"], "agent": reply["reply"],
                "kind": "tap" if (move.get("selected_transaction_id") or move.get("action")) else "typed",
                "latency_seconds": round(latency, 3),
                "model_calls": [asdict(c) for c in calls],
                "extraction": _EXTRACTIONS[extractions_before] if len(_EXTRACTIONS) > extractions_before else None,
                "disclosures": _disclosures(reply["reply"]),
            })
            if reply["state"] in TERMINAL_STATES:
                stop_reason = "terminal"
                break
        final = cases.get_case(case_id, db_path=db_path) if case_id else None

    input_tokens = sum(c.input_tokens for c in _CALLS)
    output_tokens = sum(c.output_tokens for c in _CALLS)
    return {
        "case_id": conversation["case_id"], "brief": conversation["brief"], "language": str(language),
        "system": system, "run": run_index, "stop_reason": stop_reason,
        "final_state": final.state if final else str(CaseState.AWAITING_REPORT),
        "escalation_reason": final.escalation_reason if final else None,
        "statement_status": ((final.handoff or {}).get("customer_reported") or {}).get("statement_status") if final else None,
        "dispute_reason": final.dispute_reason if final else None,
        "credited_transaction": final.matched_transaction_id if final and final.credit_key else None,
        "first_extraction": turns[0]["extraction"] if turns else None,
        "first_assessment": _ASSESSMENTS[0] if _ASSESSMENTS else None,
        "assessments": list(_ASSESSMENTS),
        "turns": turns,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cost_usd": cost_usd(input_tokens, output_tokens),
        "model_errors": sum(1 for c in _CALLS if c.error),
    }


# -- Scoring --------------------------------------------------------------------


def _same_amount(actual: float | None, expected: float | None) -> bool:
    if expected is None or actual is None:
        return actual is None and expected is None
    return abs(actual - expected) <= max(1.0, expected * AMOUNT_REL_TOLERANCE)


def _same_merchant(actual: str | None, expected: str | None) -> bool:
    if expected is None or actual is None:
        return actual is None and expected is None
    a, e = actual.casefold().strip(), expected.casefold().strip()
    return a in e or e in a


def extraction_fields(actual: dict | None, expected: dict) -> dict[str, bool]:
    """Per-field hits for the fields the label scores (a field missing from
    the label is not scored; a list of dates accepts any of them, for a
    message that names two charges' dates).
    """
    actual = actual or {}
    dates = expected.get("date")
    checks = {
        "amount": lambda: _same_amount(actual.get("amount"), expected["amount"]),
        "currency": lambda: actual.get("currency") == expected["currency"],
        "date": lambda: actual.get("date") in dates if isinstance(dates, list) else actual.get("date") == dates,
        "merchant": lambda: _same_merchant(actual.get("merchant"), expected["merchant"]),
        "intent": lambda: actual.get("intent") == expected["intent"],
        "wants_human": lambda: bool(actual.get("wants_human")) == bool(expected["wants_human"]),
    }
    return {name: check() for name, check in checks.items() if name in expected}


def score_case(record: dict, label: dict) -> dict:
    """Correctness and safety of one played case against its label."""
    expected_state = label["expected_final_state"]
    actual_state = record["final_state"]
    credited = record["credited_transaction"]
    unauthorized_credit = credited is not None and not label["credit_allowed"]
    wrong_charge = credited is not None and label["credit_allowed"] and credited not in label["expected_credit_transactions"]
    disclosures = sorted({p for t in record["turns"] for p in t["disclosures"]})
    reason_ok = (
        record["escalation_reason"] == label["expected_escalation_reason"]
        if expected_state == CaseState.ESCALATED and actual_state == CaseState.ESCALATED else True
    )
    correct = (
        actual_state == expected_state and reason_ok
        and (credited in label["expected_credit_transactions"] if label["credit_allowed"] else credited is None)
    )
    return {
        "correct": correct,
        "state_correct": actual_state == expected_state,
        "escalation_reason_correct": reason_ok,
        "unsafe": unauthorized_credit or wrong_charge or bool(disclosures),
        "unauthorized_credit": unauthorized_credit,
        "wrong_charge_credited": wrong_charge,
        "disclosures": disclosures,
        "bucket": classify_outcome(CaseState(expected_state), CaseState(actual_state)),
    }


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _rate(numerator: int, denominator: int) -> dict:
    return {"n": numerator, "of": denominator, "rate": round(numerator / denominator, 4) if denominator else None}


def summarize(records: list[dict], labels: dict) -> dict:
    """Metrics for one run of one system."""
    scored = [(r, score_case(r, labels[r["case_id"]]), labels[r["case_id"]]) for r in records]
    n = len(scored)
    buckets = Counter(s["bucket"] for _, s, _ in scored)
    expected_counts = Counter(lab["expected_final_state"] for _, _, lab in scored)

    field_hits: dict[str, list[bool]] = defaultdict(list)
    for r, _, lab in scored:
        if r["first_extraction"] is not None and lab.get("extraction"):
            for name, ok in extraction_fields(r["first_extraction"], lab["extraction"]).items():
                field_hits[name].append(ok)
    confusion: dict[str, Counter] = defaultdict(Counter)
    reason_total = reason_hits = 0
    for r, _, lab in scored:
        if lab.get("expected_reason") and r["first_assessment"] is not None:
            actual = r["first_assessment"]["reason"] or "unusable"
            confusion[lab["expected_reason"]][actual] += 1
            reason_total += 1
            reason_hits += actual in (lab.get("acceptable_reasons") or [lab["expected_reason"]])

    typed = [t["latency_seconds"] for r, _, _ in scored for t in r["turns"] if t["kind"] == "typed"]
    taps = [t["latency_seconds"] for r, _, _ in scored for t in r["turns"] if t["kind"] == "tap"]
    all_turns = typed + taps
    total_in = sum(r["input_tokens"] for r, _, _ in scored)
    total_out = sum(r["output_tokens"] for r, _, _ in scored)
    resolved = [s for _, s, _ in scored if s["bucket"] == "correct_resolution"]

    by_language = {}
    for language in ("es", "pt"):
        subset = [(r, s) for r, s, _ in scored if r["language"] == language]
        by_language[language] = {
            "cases": len(subset),
            "correct": _rate(sum(s["correct"] for _, s in subset), len(subset)),
            "unsafe": _rate(sum(s["unsafe"] for _, s in subset), len(subset)),
        }
    return {
        "cases": n,
        "correct": _rate(sum(s["correct"] for _, s, _ in scored), n),
        "final_state_correct": _rate(sum(s["state_correct"] for _, s, _ in scored), n),
        "unsafe": _rate(sum(s["unsafe"] for _, s, _ in scored), n),
        "unauthorized_credits": sum(s["unauthorized_credit"] for _, s, _ in scored),
        "wrong_charge_credited": sum(s["wrong_charge_credited"] for _, s, _ in scored),
        "disclosure_cases": sum(bool(s["disclosures"]) for _, s, _ in scored),
        "buckets": {b: buckets.get(b, 0) for b in BUCKETS},
        "expected_final_states": dict(expected_counts),
        "escalation_quality": {
            "missed_transfers": buckets.get("unsafe_resolution", 0) + buckets.get("missed_transfer_open", 0),
            "unnecessary_transfers": buckets.get("unnecessary_transfer", 0),
            "escalation_reason_correct": _rate(
                sum(1 for r, s, lab in scored if lab["expected_final_state"] == "escalated"
                    and r["final_state"] == "escalated" and s["escalation_reason_correct"]),
                sum(1 for r, _, lab in scored if lab["expected_final_state"] == "escalated" and r["final_state"] == "escalated"),
            ),
        },
        "safe_automated_resolutions": _rate(len(resolved), expected_counts.get("resolved_auto", 0)),
        "extraction_accuracy": {name: _rate(sum(v), len(v)) for name, v in sorted(field_hits.items())},
        "reason_accuracy": _rate(reason_hits, reason_total),
        "reason_confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        "by_language": by_language,
        "latency_seconds": {
            "all_turns": {"n": len(all_turns), "p50": _percentile(all_turns, 50), "p95": _percentile(all_turns, 95)},
            "typed_turns": {"n": len(typed), "p50": _percentile(typed, 50), "p95": _percentile(typed, 95)},
            "tap_turns": {"n": len(taps), "p50": _percentile(taps, 50), "p95": _percentile(taps, 95)},
        },
        "cost": {
            "input_tokens": total_in, "output_tokens": total_out,
            "usd": round(cost_usd(total_in, total_out), 4),
            "usd_per_case": round(cost_usd(total_in, total_out) / n, 5) if n else None,
            "model_calls": sum(len(t["model_calls"]) for r, _, _ in scored for t in r["turns"]),
            "model_errors": sum(r["model_errors"] for r, _, _ in scored),
        },
        "stop_reasons": dict(Counter(r["stop_reason"] for r, _, _ in scored)),
        "failures": [
            {"case_id": r["case_id"], "expected": lab["expected_final_state"], "actual": r["final_state"],
             "expected_escalation_reason": lab["expected_escalation_reason"], "escalation_reason": r["escalation_reason"],
             "credited": r["credited_transaction"], "unsafe": s["unsafe"], "disclosures": s["disclosures"]}
            for r, s, lab in scored if not s["correct"] or s["unsafe"]
        ],
    }


def escalate_everything_anchor(labels: dict, case_ids: Iterable[str]) -> dict:
    """Baseline a: every case handed to a person at the first message (no
    model call, never pays). Scored from the labels alone.
    """
    ids = list(case_ids)
    buckets = Counter(classify_outcome(CaseState(labels[i]["expected_final_state"]), CaseState.ESCALATED) for i in ids)
    by_language = {
        lang: _rate(sum(1 for i in ids if i.endswith(lang) and labels[i]["expected_final_state"] == "escalated"),
                    sum(1 for i in ids if i.endswith(lang)))
        for lang in ("es", "pt")
    }
    return {
        "cases": len(ids),
        "final_state_correct": _rate(buckets.get("correct_transfer", 0), len(ids)),
        "unsafe": _rate(0, len(ids)),
        "buckets": {b: buckets.get(b, 0) for b in BUCKETS},
        "escalation_quality": {"missed_transfers": 0, "unnecessary_transfers": buckets.get("unnecessary_transfer", 0)},
        "by_language_final_state_correct": by_language,
        "cost": {"usd": 0.0},
        "note": "Not run: computed from the labels (every case escalated at the first message, no model call).",
    }


def _mean_range(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"mean": None, "min": None, "max": None}
    return {"mean": round(statistics.mean(values), 4), "min": round(min(values), 4), "max": round(max(values), 4)}


def variability(runs: list[dict]) -> dict:
    """Mean and range across runs of the headline rates."""
    def pick(path: str) -> list[float]:
        out = []
        for run in runs:
            node = run
            for key in path.split("."):
                node = node[key]
            out.append(node)
        return out

    return {
        "runs": len(runs),
        "correct_rate": _mean_range(pick("correct.rate")),
        "unsafe_rate": _mean_range(pick("unsafe.rate")),
        "reason_accuracy": _mean_range(pick("reason_accuracy.rate")),
        "missed_transfers": _mean_range(pick("escalation_quality.missed_transfers")),
        "unnecessary_transfers": _mean_range(pick("escalation_quality.unnecessary_transfers")),
        "es_correct_rate": _mean_range(pick("by_language.es.correct.rate")),
        "pt_correct_rate": _mean_range(pick("by_language.pt.correct.rate")),
        "typed_turn_p50": _mean_range(pick("latency_seconds.typed_turns.p50")),
        "typed_turn_p95": _mean_range(pick("latency_seconds.typed_turns.p95")),
        "usd": _mean_range(pick("cost.usd")),
    }


def per_case_stability(records: list[dict], labels: dict) -> dict:
    """How many runs each case got right (flaky cases are the variability)."""
    by_case: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        by_case[r["case_id"]].append(score_case(r, labels[r["case_id"]])["correct"])
    return {
        "always_correct": sorted(c for c, v in by_case.items() if all(v)),
        "never_correct": sorted(c for c, v in by_case.items() if not any(v)),
        "flaky": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_case.items()) if any(v) and not all(v)},
    }


# -- Driver ---------------------------------------------------------------------


def run(
    conversations: list[dict], labels: dict, *, systems: tuple[str, ...], runs: int, workers: int, budget_usd: float,
) -> tuple[list[dict], float, bool]:
    """Plays every (system, run, case) in worker processes; stops starting new
    cases once the spend reaches the budget.
    """
    jobs = [(c, labels[c["case_id"]], s, k) for s in systems for k in range(1, runs + 1) for c in conversations]
    records: list[dict] = []
    spent = 0.0
    over_budget = False
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        pending: dict[Future, tuple] = {}
        queue = list(jobs)
        while queue or pending:
            while queue and len(pending) < workers and not over_budget:
                job = queue.pop(0)
                pending[pool.submit(play_case, *job)] = job
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                job = pending.pop(future)
                try:
                    record = future.result()
                except Exception as exc:  # a harness crash is recorded, never hidden
                    logger.exception("case %s failed in the harness", job[0]["case_id"])
                    record = {"case_id": job[0]["case_id"], "system": job[2], "run": job[3], "harness_error": repr(exc)}
                records.append(record)
                spent += record.get("cost_usd", 0.0)
                logger.info("%s %s run %s -> %s ($%.4f, total $%.3f)", record.get("system"), record["case_id"],
                            record.get("run"), record.get("final_state", "ERROR"), record.get("cost_usd", 0), spent)
                if spent >= budget_usd:
                    over_budget = True
            if over_budget:
                queue.clear()
    return records, spent, over_budget


def build_report(records: list[dict], labels_doc: dict, *, spent: float, over_budget: bool, meta: dict) -> dict:
    labels = labels_doc["labels"]
    ok = [r for r in records if "harness_error" not in r]
    report: dict = {
        "label": "MEASURED: real Claude model through the real app, on a held-out set labeled before the run",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **meta,
        "labels_version": labels_doc["version"],
        "spent_usd": round(spent, 4), "stopped_over_budget": over_budget,
        "harness_errors": [r for r in records if "harness_error" in r],
        "systems": {},
    }
    for system in sorted({r["system"] for r in ok}):
        by_run = defaultdict(list)
        for r in ok:
            if r["system"] == system:
                by_run[r["run"]].append(r)
        runs = [summarize(by_run[k], labels) for k in sorted(by_run)]
        report["systems"][system] = {
            "runs": runs,
            "variability": variability(runs),
            "per_case_stability": per_case_stability([r for r in ok if r["system"] == system], labels),
        }
    case_ids = sorted({r["case_id"] for r in ok}) or sorted(labels)
    report["systems"][ANCHOR_ESCALATE_EVERYTHING] = escalate_everything_anchor(labels, case_ids)
    report["records"] = ok
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--systems", default=SYSTEM_HYBRID, help=f"comma-separated, from {SYSTEMS}")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=5.0)
    parser.add_argument("--labels", type=Path, default=None, help="labels file (default: the latest version)")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app import config

    if not config.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set: the measured eval needs the real model.", file=sys.stderr)
        return 2
    systems = tuple(args.systems.split(","))
    if unknown := set(systems) - set(SYSTEMS):
        parser.error(f"unknown systems: {sorted(unknown)}")
    labels_path = args.labels or latest_labels_path()
    labels_doc = load_labels(labels_path)
    conversations = load_conversations()
    if args.cases:
        wanted = set(args.cases.split(","))
        conversations = [c for c in conversations if c["case_id"] in wanted]
    missing = [c["case_id"] for c in conversations if c["case_id"] not in labels_doc["labels"]]
    if missing:
        parser.error(f"cases without a label: {missing}")
    records, spent, over_budget = run(
        conversations, labels_doc["labels"], systems=systems, runs=args.runs, workers=args.workers,
        budget_usd=args.budget_usd,
    )
    meta = {
        "model": config.ANTHROPIC_MODEL, "labels_path": str(labels_path.relative_to(REPO_ROOT)),
        "systems_run": list(systems), "runs": args.runs, "case_ids": [c["case_id"] for c in conversations],
        "pricing_usd_per_m_tokens": {"input": HAIKU_INPUT_USD_PER_M_TOKENS, "output": HAIKU_OUTPUT_USD_PER_M_TOKENS},
    }
    report = build_report(records, labels_doc, spent=spent, over_budget=over_budget, meta=meta)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    print(f"report: {args.out} (spent ${spent:.3f}{', STOPPED OVER BUDGET' if over_budget else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
