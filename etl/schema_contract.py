"""Data contract for the LATAM Bank dataset tables used by the dispute-agent ETL.

Each table's expected columns are the ones the pipeline actually depends on
(sourced from `docs/challenge/challenge-brief.md` and verified directly against
real S3 CSV headers on 2026-09-28 — not assumed from the organizer's summary PDF
alone). Validation is intentionally column-presence based, not column-count based:

- A MISSING expected column fails loudly (this is the dataset's documented
  "schema evolution" risk materializing — a column we depend on was renamed,
  dropped, or never present in a given partition).
- An EXTRA/unexpected column is tolerated and logged, not a failure. Direct
  sampling found a real example of this: every fact table carries a
  `process_date` column that is not mentioned anywhere in the organizer's data
  dictionary digest. Failing on unexpected columns would make the pipeline
  brittle against exactly the kind of harmless schema addition real data
  warehouses accumulate over time.

This module has zero I/O — it is pure data + a validation function — so it can
be imported by both `etl/extract.py` and `tests/` without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TableKind(StrEnum):
    # Hive-partitioned fact tables live under data/<name>/year=YYYY/month=MM/day=DD/
    # Flat dimension tables live at data/<name>.csv
    DIMENSION = "dimension"
    FACT = "fact"


@dataclass(frozen=True)
class TableContract:
    name: str
    kind: TableKind
    primary_key: str | None
    expected_columns: tuple[str, ...]
    documented_row_count: int  # from challenge-brief.md, for row-count sanity checks

    @property
    def partitioned(self) -> bool:
        return self.kind is TableKind.FACT


CUSTOMERS = TableContract(
    name="customers",
    kind=TableKind.DIMENSION,
    primary_key="customer_id",
    expected_columns=(
        "customer_id",
        "document_number",
        "document_type",
        "first_name",
        "last_name",
        "date_of_birth",
        "gender",
        "email",
        "mobile_phone",
        "landline_phone",
        "address",
        "city",
        "state",
        "country",
        "postal_code",
        "detected_accent",
        "segment",
        "credit_score",
        "estimated_monthly_income",
        "occupation",
        "marital_status",
        "education_level",
        "registration_date",
        "registration_branch_id",
        "customer_status",
        "last_updated",
        "accepts_marketing",
    ),
    documented_row_count=150_000,
)

PRODUCTS = TableContract(
    name="products",
    kind=TableKind.DIMENSION,
    primary_key="product_id",
    expected_columns=(
        "product_id",
        "customer_id",
        "product_type",
        "product_number",
        "currency",
        "current_balance",
        "credit_limit",
        "interest_rate",
        "opening_date",
        "expiration_date",
        "opening_branch_id",
        "product_status",
        "opening_channel",
        "has_linked_app",
        "days_past_due",
        "last_transaction_date",
        "last_updated",
    ),
    documented_row_count=400_000,
)

BRANCHES = TableContract(
    name="branches",
    kind=TableKind.DIMENSION,
    primary_key="branch_id",
    expected_columns=(
        "branch_id",
        "branch_code",
        "branch_name",
        "branch_type",
        "address",
        "city",
        "state",
        "country",
        "postal_code",
        "geographic_zone",
        "phone",
        "email",
        "opening_time",
        "closing_time",
        "has_atms",
        "atm_count",
        "has_teller_windows",
        "teller_window_count",
        "latitude",
        "longitude",
        "branch_opening_date",
        "branch_status",
    ),
    documented_row_count=350,
)

SERVICE_AGENTS = TableContract(
    name="service_agents",
    kind=TableKind.DIMENSION,
    primary_key="agent_id",
    expected_columns=(
        "agent_id",
        "employee_code",
        "first_name",
        "last_name",
        "email",
        "phone",
        "native_accent",
        "country_of_origin",
        "assigned_branch_id",
        "agent_type",
        "experience_level",
        "languages",
        "specialty",
        "hire_date",
        "avg_csat",
        "total_monthly_interactions",
        "agent_status",
        "work_shift",
    ),
    documented_row_count=1_200,
)

DAILY_EXCHANGE_RATES = TableContract(
    name="daily_exchange_rates",
    kind=TableKind.DIMENSION,
    primary_key=None,
    expected_columns=(
        "date",
        "source_currency",
        "target_currency",
        "exchange_rate",
        "buy_rate",
        "sell_rate",
        "source",
    ),
    documented_row_count=3_000,
)

COMPLAINTS = TableContract(
    name="complaints",
    kind=TableKind.FACT,
    primary_key="complaint_id",
    expected_columns=(
        "complaint_id",
        "creation_date",
        "customer_id",
        "case_type",
        "category",
        "subcategory",
        "reception_channel",
        "affected_product_id",
        "related_branch_id",
        "origin_interaction_id",
        "description",
        "claimed_amount",
        "currency",
        "priority",
        "status",
        "assigned_agent_id",
        "assignment_date",
        "first_response_date",
        "resolution_date",
        "closing_date",
        "sla_breached",
        "resolution_days",
        "resolution",
        "compensation_granted",
        "resolution_satisfaction",
        "is_repeat_complainer",
    ),
    documented_row_count=80_000,
)

TRANSACTIONS = TableContract(
    name="transactions",
    kind=TableKind.FACT,
    primary_key="transaction_id",
    expected_columns=(
        "transaction_id",
        "transaction_date",
        "product_id",
        "customer_id",
        "transaction_type",
        "transaction_category",
        "amount",
        "currency",
        "amount_usd",
        "channel",
        "branch_id",
        "merchant_name",
        "merchant_category",
        "transaction_country",
        "transaction_city",
        "transaction_status",
        "response_code",
        "is_fraud",
        "fraud_score",
        "latitude",
        "longitude",
    ),
    documented_row_count=5_000_000,
)

CALL_CENTER_INTERACTIONS = TableContract(
    name="call_center_interactions",
    kind=TableKind.FACT,
    primary_key="interaction_id",
    expected_columns=(
        "interaction_id",
        "interaction_date",
        "customer_id",
        "agent_id",
        "interaction_type",
        "channel",
        "contact_reason",
        "reason_category",
        "duration_seconds",
        "wait_time_seconds",
        "was_resolved",
        "requires_followup",
        "detected_sentiment",
        "sentiment_score",
        "customer_detected_accent",
        "agent_used_accent",
        "was_escalated",
        "mentioned_products",
        "has_transcript",
        "has_recording",
    ),
    documented_row_count=800_000,
)

CALL_TRANSCRIPTS = TableContract(
    name="call_transcripts",
    kind=TableKind.FACT,
    primary_key="transcript_id",
    expected_columns=(
        "transcript_id",
        "interaction_id",
        "customer_id",
        "agent_id",
        "full_text",
        "customer_text",
        "agent_text",
        "detected_language",
        "detected_accent",
        "accent_confidence",
        "detected_keywords",
        "mentioned_entities",
        "detected_intents",
        "main_topics",
        "transcription_model",
        "audio_quality",
        "duration_seconds",
    ),
    documented_row_count=200_000,
)

ALL_TABLES: tuple[TableContract, ...] = (
    CUSTOMERS,
    PRODUCTS,
    BRANCHES,
    SERVICE_AGENTS,
    DAILY_EXCHANGE_RATES,
    COMPLAINTS,
    TRANSACTIONS,
    CALL_CENTER_INTERACTIONS,
    CALL_TRANSCRIPTS,
)

TABLES_BY_NAME: dict[str, TableContract] = {t.name: t for t in ALL_TABLES}


class SchemaValidationError(ValueError):
    """Raised when a CSV header is missing one or more expected columns."""


def validate_header(table: TableContract, actual_columns: list[str]) -> list[str]:
    """Validate `actual_columns` (as read from a real CSV header) against `table`.

    Returns the list of unexpected/extra columns found (informational, not fatal).
    Raises `SchemaValidationError` if any expected column is missing.
    """
    # Defend against a BOM-prefixed first header cell (observed in real S3 files,
    # e.g. "﻿complaint_id") — this is an encoding artifact, not a schema change.
    normalized = [c.lstrip("﻿").strip() for c in actual_columns]
    actual_set = set(normalized)
    expected_set = set(table.expected_columns)

    missing = sorted(expected_set - actual_set)
    if missing:
        raise SchemaValidationError(
            f"Table '{table.name}': missing expected column(s) {missing}. "
            f"Actual header: {normalized}"
        )

    return sorted(actual_set - expected_set)
