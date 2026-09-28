"""Chronological train/test split + baseline + proposed classifier (AD-6).

## Pre-registered evaluation protocol (fixed before training, per plan.md AD-6)

- **Split:** chronological by `creation_date` — the most recent 15% of the
  "Transactions"-category complaints is the held-out test set; the earlier
  85% trains. Never random (a random split would let future complaints leak
  into training relative to test-set complaints from earlier in time, and
  would hide any real temporal drift).
- **Baseline (the one, fixed baseline):** a majority-class predictor —
  always predicts the training split's single most frequent `priority`.
- **Proposed model:** `RandomForestClassifier` (scikit-learn), fixed
  `random_state` for reproducibility.
- **Metric:** macro-F1 as the primary metric (class-imbalance-robust across
  Low/Medium/High/Critical), with per-class recall reported alongside.

## Real-data finding (verified, not assumed)

`claimed_amount` (and `currency`) is NULL in ~67% of "Transactions"-category
complaints — far above the ~5% documented for "nullable fields" generally.
Verified directly via `etl/features.py`'s query; not a bug, a genuine
characteristic of the dataset. Handled via `SimpleImputer` (median for the
numeric `claimed_amount`/`customer_credit_score`, a literal "missing"
category for categoricals) with `add_indicator=True` on the numeric imputer
so "was this amount missing" is itself an available signal, not silently
discarded.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from etl.features import FEATURE_COLUMNS, TARGET_COLUMN, load_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.train_classifier")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPLIT_PATH = REPO_ROOT / "data" / "classifier_split.json"
DEFAULT_MODEL_PATH = REPO_ROOT / "data" / "classifier.joblib"

TEST_FRACTION = 0.15
RANDOM_STATE = 42

NUMERIC_FEATURES = ("claimed_amount", "customer_credit_score", "prior_complaint_count")
CATEGORICAL_FEATURES = tuple(f for f in FEATURE_COLUMNS if f not in NUMERIC_FEATURES)


@dataclass
class SplitResult:
    split_date: str
    train_size: int
    test_size: int


def chronological_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, SplitResult]:
    """`df` MUST already be sorted by `creation_date` ascending (as
    `etl/features.py::load_features` guarantees) — this function does not
    re-sort, so a caller passing an unsorted frame would silently get a
    wrong split, which is exactly the leakage risk this function exists to
    prevent, so it is asserted rather than assumed.
    """
    if not df["creation_date"].is_monotonic_increasing:
        raise ValueError("df must be sorted by creation_date ascending — see load_features()")

    split_idx = int(len(df) * (1 - TEST_FRACTION))
    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]
    split_date = test_df["creation_date"].iloc[0]
    return train_df, test_df, SplitResult(
        split_date=str(split_date), train_size=len(train_df), test_size=len(test_df)
    )


def build_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                SimpleImputer(strategy="median", add_indicator=True),
                list(NUMERIC_FEATURES),
            ),
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="constant", fill_value="missing")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                list(CATEGORICAL_FEATURES),
            ),
        ]
    )
    classifier = RandomForestClassifier(
        n_estimators=200, max_depth=12, class_weight="balanced", random_state=RANDOM_STATE
    )
    return Pipeline([("preprocess", preprocessor), ("classify", classifier)])


def build_baseline() -> DummyClassifier:
    return DummyClassifier(strategy="most_frequent", random_state=RANDOM_STATE)


def train(warehouse_path: Path | None = None) -> dict:
    df = load_features(warehouse_path) if warehouse_path else load_features()
    train_df, test_df, split = chronological_split(df)
    logger.info(
        "Chronological split: train=%d test=%d split_date=%s",
        split.train_size, split.test_size, split.split_date,
    )

    x_train, y_train = train_df[list(FEATURE_COLUMNS)], train_df[TARGET_COLUMN]
    x_test, y_test = test_df[list(FEATURE_COLUMNS)], test_df[TARGET_COLUMN]

    baseline = build_baseline()
    baseline.fit(x_train, y_train)
    baseline_pred = baseline.predict(x_test)

    pipeline = build_pipeline()
    pipeline.fit(x_train, y_train)
    proposed_pred = pipeline.predict(x_test)

    return {
        "split": asdict(split),
        "feature_columns": list(FEATURE_COLUMNS),
        "y_test": y_test.tolist(),
        "baseline_pred": np.asarray(baseline_pred).tolist(),
        "proposed_pred": np.asarray(proposed_pred).tolist(),
        "train_class_distribution": y_train.value_counts().to_dict(),
        "fitted_pipeline": pipeline,
    }


def main() -> int:
    result = train()
    pipeline = result.pop("fitted_pipeline")

    DEFAULT_SPLIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_SPLIT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info("Wrote split/predictions to %s", DEFAULT_SPLIT_PATH)

    # Persists the EXACT pipeline that was evaluated (trained on the 85%
    # chronological-split train set) — never a separately-retrained "final"
    # model, so the shipped decision-support signal is provably the same one
    # etl/evaluate_classifier.py's report describes, not a different model.
    joblib.dump(pipeline, DEFAULT_MODEL_PATH)
    logger.info("Wrote trained model to %s", DEFAULT_MODEL_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
