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
