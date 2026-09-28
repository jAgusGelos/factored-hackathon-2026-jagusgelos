"""Priority-at-intake classifier wiring (AD-6, AD-11) — DECISION SUPPORT ONLY.

Loads the model `etl/train_classifier.py` persisted offline
(`data/classifier.joblib` — the exact pipeline `etl/evaluate_classifier.py`
reports on, never a separately-retrained one) and exposes a single function,
`predict_priority()`. Its output feeds into `app/policy.py::evaluate_resolution`
as ONE OR-condition among several (AD-11 Row 5): a "Critical" prediction can
only ever ADD a reason to escalate. It can NEVER set `status=Resolved` or
authorize a simulated credit on its own — enforced by
`tests/test_policy_not_overridden.py`.

A missing model file, a feature mismatch, or any other failure here degrades
to `predict_priority() -> None` (no signal) rather than raising — mirrors the
NFR's "tool call fails -> safe fallback" philosophy: a decision-support
signal that isn't available must never block or crash the primary flow.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd

from app.policy import DISPUTE_COMPLAINT_CATEGORY
from app.transactions import CustomerProfile

logger = logging.getLogger("app.classifier")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = REPO_ROOT / "data" / "classifier.joblib"

# The chat channel this app serves is always "App"; this workflow only ever
# handles the "Cargo no reconocido" subcategory (findings.md: ~90% of real
# "Transactions"-category complaints) — these are reasonable fixed defaults
# for a feature the live conversation has no other way to observe, not a
# fabricated value standing in for something knowable.
_LIVE_RECEPTION_CHANNEL = "App"
_LIVE_SUBCATEGORY = "Cargo no reconocido"


def build_live_features(
    profile: CustomerProfile | None,
    *,
    claimed_amount: float,
    currency: str,
    prior_complaint_count: int,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "category": DISPUTE_COMPLAINT_CATEGORY,
                "subcategory": _LIVE_SUBCATEGORY,
                "claimed_amount": claimed_amount,
                "currency": currency,
                "reception_channel": _LIVE_RECEPTION_CHANNEL,
                "customer_segment": profile.segment if profile else None,
                "customer_credit_score": profile.credit_score if profile else None,
                # The chat never identifies the affected product; None maps to
                # the pipeline's "missing" category, exactly as a training row
                # with no affected_product_id does.
                "product_type": None,
                "prior_complaint_count": prior_complaint_count,
            }
        ]
    )


@lru_cache(maxsize=4)
def _load_model(model_path_str: str):
    # Cached per process: this is a live-request code path (evaluate_case()
    # runs it on every confident-match turn), and re-deserializing a ~24MB
    # joblib file from disk on every chat turn would add real, needless
    # latency to the p50/p95 numbers Milestone 5's eval harness reports.
    # Keyed by path so tests can point at a different (small) fixture model
    # without colliding with the real one's cache entry.
    return joblib.load(model_path_str)


def predict_priority(features: pd.DataFrame, *, model_path: Path = DEFAULT_MODEL_PATH) -> str | None:
    try:
        model = _load_model(str(model_path))
        return str(model.predict(features)[0])
    except Exception:
        logger.warning("Classifier prediction unavailable — proceeding without this signal", exc_info=True)
        return None
