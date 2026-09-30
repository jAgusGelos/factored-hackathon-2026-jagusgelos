"""AD-5 privacy-boundary tests (Task 2.3).

`build_prompt_context()` is the only function allowed to turn customer data
into LLM-bound context — these tests prove that structurally, not just by
convention.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app import llm
from tests.support import mock_anthropic_client

FORBIDDEN_FIELD_NAMES = (
    "email",
    "phone",
    "mobile_phone",
    "landline_phone",
    "address",
    "document_number",
    "credit_score",
    "full_text",
    "customer_text",
    "agent_text",
    "transcript",
    "raw_row",
    "postal_code",
    "date_of_birth",
)

ALLOWED_FIELDS = {
    "case_state",
    "language",
    "reported_amount",
    "reported_currency",
    "reported_date",
    "candidate_amount",
    "candidate_date",
    "candidate_currency",
    "candidate_merchant_name",
    "candidate_merchant_category",
    "candidate_count",
    "clarification_rounds",
    "resolution_reference",
}


def test_build_prompt_context_signature_has_no_kwargs_passthrough():
    """A **kwargs parameter would let a caller smuggle an unlisted field
    through — the allowlist must be closed, not just documented.
    """
    sig = inspect.signature(llm.build_prompt_context)
    kinds = [p.kind for p in sig.parameters.values()]
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds


def test_build_prompt_context_signature_excludes_every_forbidden_pii_field():
    sig = inspect.signature(llm.build_prompt_context)
    param_names = set(sig.parameters)
    overlap = param_names & set(FORBIDDEN_FIELD_NAMES)
    assert not overlap, f"build_prompt_context() has PII-shaped parameter(s): {overlap}"


def test_build_prompt_context_returns_exactly_the_documented_allowlist():
    context = llm.build_prompt_context(
        case_state="matching",
        reported_amount=100.0,
        reported_currency="USD",
        reported_date="2024-03-10",
        candidate_amount=100.0,
        candidate_date="2024-03-09",
        candidate_currency="USD",
        candidate_merchant_name="Comercio Demo",
        candidate_merchant_category="Retail",
        candidate_count=1,
        clarification_rounds=0,
        resolution_reference="CASE-ABC123",
    )
    assert set(context.keys()) <= ALLOWED_FIELDS


def test_build_prompt_context_omits_none_fields_rather_than_including_them_as_null():
    context = llm.build_prompt_context(case_state="awaiting_report")
    assert context == {"case_state": "awaiting_report", "language": "es"}


def test_generate_response_rejects_a_raw_dict_that_bypassed_the_allowlist():
    with pytest.raises(TypeError):
        llm.generate_response({"email": "leak@example.test"}, language=llm.Language.ES)


def test_only_llm_module_imports_the_anthropic_sdk():
    """Every LLM call site in app/ must go through app/llm.py — proven by
    grepping for a direct provider-client import anywhere else in app/.
    """
    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in app_dir.glob("*.py"):
        if path.name == "llm.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "anthropic" for a in node.names):
                offenders.append(path.name)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "anthropic":
                offenders.append(path.name)
    assert not offenders, f"Direct anthropic import(s) outside app/llm.py: {offenders}"


SENTINEL_EMAIL = "sentinel-9f3a7c@example-leak-check.test"
SENTINEL_PHONE = "+52-SENTINEL-5551234"
SENTINEL_DOCUMENT = "SENTINEL-DOC-998877"
SENTINEL_ADDRESS = "SENTINEL AVENUE 123, FAKE CITY"


def _build_sentinel_fixture(db_path):
    import duckdb

    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE customers (customer_id VARCHAR, segment VARCHAR, credit_score VARCHAR, "
        "country VARCHAR, customer_status VARCHAR, email VARCHAR, mobile_phone VARCHAR, "
        "document_number VARCHAR, address VARCHAR)"
    )
    con.execute(
        "INSERT INTO customers VALUES ('CLI-SENTINEL', 'Plus', '650', 'México', 'Active', ?, ?, ?, ?)",
        [SENTINEL_EMAIL, SENTINEL_PHONE, SENTINEL_DOCUMENT, SENTINEL_ADDRESS],
    )
    con.execute(
        "CREATE TABLE transactions (transaction_id VARCHAR, transaction_date VARCHAR, "
        "customer_id VARCHAR, amount VARCHAR, currency VARCHAR, amount_usd VARCHAR, "
        "fraud_score VARCHAR, transaction_status VARCHAR, merchant_name VARCHAR, "
        "merchant_category VARCHAR, channel VARCHAR, _is_synthetic VARCHAR)"
    )
    con.execute(
        "INSERT INTO transactions VALUES ('TRX-1', '2024-03-09', 'CLI-SENTINEL', '100.0', "
        "'USD', '100.0', '5.0', 'Approved', 'Comercio Demo', 'Retail', 'App', 'false')"
    )
    con.execute(
        "CREATE TABLE complaints (complaint_id VARCHAR, customer_id VARCHAR, category VARCHAR, creation_date VARCHAR)"
    )
    con.execute("ALTER TABLE transactions ADD COLUMN transaction_type VARCHAR DEFAULT 'Purchase'")
    con.close()


def test_sentinel_pii_never_appears_in_any_prompt_across_a_full_conversation(tmp_path, monkeypatch):
    """Plants sentinel PII values (email/phone/document_number/address — all
    explicitly OUTSIDE the AD-5 allowlist) in the local fixture, runs a full
    conversation with the LLM client intercepted, and asserts none of the
    sentinel values appear in any prompt string sent to the provider.
    """
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from app import config, db
    from app.auth import Session

    fixture_path = tmp_path / "fixture.duckdb"
    _build_sentinel_fixture(fixture_path)
    monkeypatch.setattr(config, "FIXTURE_DB_PATH", fixture_path)

    app_db_path = tmp_path / "app.db"
    db.init_db(app_db_path)

    captured_prompts: list[str] = []
    mock_client = mock_anthropic_client(
        {"amount": 100.0, "currency": "USD", "date": "2024-03-09", "merchant_hint": None, "wants_human": False},
        "Tu caso fue resuelto.",
        captured_prompts=captured_prompts,
    )

    session = Session(customer_id="CLI-SENTINEL", expires_at=datetime.now(UTC) + timedelta(hours=1))

    with patch("app.llm.anthropic.Anthropic", return_value=mock_client):
        from app.state_machine import handle_message

        handle_message(session, None, "Tengo un cargo que no reconozco", db_path=app_db_path)

    assert captured_prompts, "No prompts were captured — the test isn't exercising the LLM path"
    full_text = "\n".join(captured_prompts)
    assert SENTINEL_EMAIL not in full_text
    assert SENTINEL_PHONE not in full_text
    assert SENTINEL_DOCUMENT not in full_text
    assert SENTINEL_ADDRESS not in full_text
