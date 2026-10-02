import sqlite3
from contextlib import closing

import pytest

from etl.schema_contract import COMPLAINTS, SchemaValidationError, validate_header


def test_validate_header_accepts_exact_match():
    extra = validate_header(COMPLAINTS, list(COMPLAINTS.expected_columns))
    assert extra == []


def test_validate_header_tolerates_extra_columns():
    columns = list(COMPLAINTS.expected_columns) + ["process_date", "year", "month", "day"]
    extra = validate_header(COMPLAINTS, columns)
    assert set(extra) == {"process_date", "year", "month", "day"}


def test_validate_header_strips_bom_prefix():
    columns = list(COMPLAINTS.expected_columns)
    columns[0] = "﻿" + columns[0]
    extra = validate_header(COMPLAINTS, columns)
    assert extra == []


def test_validate_header_fails_loudly_on_missing_column():
    columns = [c for c in COMPLAINTS.expected_columns if c != "claimed_amount"]
    with pytest.raises(SchemaValidationError, match="claimed_amount"):
        validate_header(COMPLAINTS, columns)


def test_validate_header_fails_on_renamed_column():
    """Simulates the dataset's documented 'schema evolution over time' risk:
    a column that exists but under a different name must be treated as
    missing, not silently matched.
    """
    columns = [
        "complaint_id_renamed" if c == "complaint_id" else c for c in COMPLAINTS.expected_columns
    ]
    with pytest.raises(SchemaValidationError, match="complaint_id"):
        validate_header(COMPLAINTS, columns)


# -- App db: the statement step's columns (statement-before-handoff AD-1) ------

_STATEMENT_COLUMNS = (
    "pending_escalation_json", "statement_text", "statement_facts_json", "statement_followups",
    "statement_declines",
)


def test_an_app_db_from_before_the_statement_step_gets_its_columns(tmp_path):
    from app import cases, db

    app_db = tmp_path / "old.db"
    old_schema = "\n".join(
        line for line in db.SCHEMA.splitlines() if line.strip().split(" ")[0] not in _STATEMENT_COLUMNS
    )
    with closing(sqlite3.connect(app_db)) as con:
        con.executescript(old_schema)
        assert not set(_STATEMENT_COLUMNS) & {r[1] for r in con.execute("PRAGMA table_info(cases)")}

    db.init_db(app_db)

    with closing(sqlite3.connect(app_db)) as con:
        assert set(_STATEMENT_COLUMNS) <= {r[1] for r in con.execute("PRAGMA table_info(cases)")}
    case = cases.create_case("C1", "es", db_path=app_db)
    stored = cases.get_case(case.case_id, db_path=app_db)
    assert (stored.pending_escalation, stored.statement_text, stored.statement_facts) == (None, None, None)
    assert (stored.statement_followups, stored.statement_declines) == (0, 0)


def test_update_case_round_trips_every_statement_field(tmp_path):
    from app import cases, db

    app_db = tmp_path / "app.db"
    db.init_db(app_db)
    case = cases.create_case("C1", "es", db_path=app_db)
    pending = {"reason": "needs_review", "handoff": {"open_questions": []}, "charge": None}

    assert cases.update_case(
        case.case_id, state="awaiting_statement", pending_escalation=pending, append_statement="primero",
        statement_facts={"card_possession": "yes"}, add_statement_followup=True, add_statement_decline=True,
        db_path=app_db,
    )
    assert cases.update_case(case.case_id, state="awaiting_statement", append_statement="segundo", db_path=app_db)

    stored = cases.get_case(case.case_id, db_path=app_db)
    assert stored.pending_escalation == pending
    assert stored.statement_text == "primero\nsegundo"
    assert stored.statement_facts == {"card_possession": "yes"}
    assert (stored.statement_followups, stored.statement_declines) == (1, 1)
