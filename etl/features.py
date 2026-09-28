"""Leakage-safe feature extraction for the priority-at-intake classifier (AD-6).

Predicts `priority` (Low/Medium/High/Critical) using ONLY structured features
knowable at intake time — never anything set later in the complaint's
lifecycle. `complaints.description`/`call_transcripts.full_text` are verified
templated boilerplate (findings.md), not real free text, so this is a
structured-feature classifier, not an NLP one.

## Feature-availability table (each feature justified as known-at-intake)

| Feature | Known at intake? | Source |
|---|---|---|
| category | Yes — set when the complaint is filed | `complaints.category` |
| subcategory | Yes | `complaints.subcategory` |
| claimed_amount | Yes — the customer reports it | `complaints.claimed_amount` |
| currency | Yes | `complaints.currency` |
| reception_channel | Yes — how the complaint arrived | `complaints.reception_channel` |
| customer_segment | Yes — pre-existing customer attribute | `customers.segment` |
| customer_credit_score | Yes — pre-existing customer attribute | `customers.credit_score` |
| product_type | Yes — pre-existing product attribute | `products.product_type` |
| prior_complaint_count | Yes IF RECOMPUTED strictly from complaints with `creation_date <` this complaint's `creation_date` (this module does that, via a correlated subquery — never the dataset's own `is_repeat_complainer` column, which is not documented as having the same strict temporal boundary and is NOT used here) | `complaints` (self-referencing) |

Target: `priority`.

## Explicitly EXCLUDED as post-outcome leakage
`status`, `assigned_agent_id`, `assignment_date`, `first_response_date`,
`resolution_date`, `closing_date`, `sla_breached`, `resolution_days`,
`resolution`, `compensation_granted`, `resolution_satisfaction`,
`is_repeat_complainer` (the raw column — see table above). None of these are
knowable when the complaint is first filed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"

FEATURE_COLUMNS = (
    "category",
    "subcategory",
    "claimed_amount",
    "currency",
    "reception_channel",
    "customer_segment",
    "customer_credit_score",
    "product_type",
    "prior_complaint_count",
)
TARGET_COLUMN = "priority"

_QUERY = """
    SELECT
        c.complaint_id,
        c.creation_date,
        c.category,
        c.subcategory,
        c.claimed_amount,
        c.currency,
        c.reception_channel,
        cu.segment AS customer_segment,
        cu.credit_score AS customer_credit_score,
        p.product_type AS product_type,
        (
            SELECT COUNT(*) FROM complaints c2
            WHERE c2.customer_id = c.customer_id AND c2.creation_date < c.creation_date
        ) AS prior_complaint_count,
        c.priority
    FROM complaints c
    JOIN customers cu ON cu.customer_id = c.customer_id
    LEFT JOIN products p ON p.product_id = c.affected_product_id
    WHERE c.category = 'Transactions'
    ORDER BY c.creation_date ASC
"""


def load_features(warehouse_path: Path = DEFAULT_WAREHOUSE_PATH) -> pd.DataFrame:
    """Returns a pandas DataFrame, one row per "Transactions"-category
    complaint, ordered chronologically by `creation_date` (required for
    `etl/train_classifier.py`'s chronological split — never shuffle this).
    """
    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        df = con.execute(_QUERY).df()
    finally:
        con.close()
    return df
