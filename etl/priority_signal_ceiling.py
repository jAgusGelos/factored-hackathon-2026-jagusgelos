"""Signal-ceiling analysis for the existing priority-at-intake classifier (AD-6).

Asks whether `priority` carries signal that the intake-time features can
recover, without retraining or replacing the shipped model
(`data/classifier.joblib` is never written here):

1. **Permutation test of macro-F1.** The SAME pipeline and the SAME
   chronological split as `etl/train_classifier.py`, refit on training labels
   shuffled at random (`N_PERMUTATIONS` times). The p-value is the share of
   shuffled fits whose test macro-F1 reaches the real one. A model that only
   learned the class proportions scores like the shuffled fits.
2. **Mutual information** of each feature with `priority` on the training
   split, against a null from the same feature with shuffled labels (the 95th
   percentile of `N_MI_PERMUTATIONS` shuffles). MI is in nats; the entropy of
   `priority` itself is the upper bound and is reported alongside.
3. A stratified random guesser's macro-F1 (predicts by the training class
   proportions), the honest floor for a class-weighted model.

Writes `data/priority_signal_ceiling.json`.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import f1_score

from etl.features import DEFAULT_WAREHOUSE_PATH, FEATURE_COLUMNS, TARGET_COLUMN, load_features
from etl.train_classifier import NUMERIC_FEATURES, build_pipeline, chronological_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.priority_signal_ceiling")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = REPO_ROOT / "data" / "priority_signal_ceiling.json"

LABELS = ["Low", "Medium", "High", "Critical"]
N_PERMUTATIONS = 100
N_MI_PERMUTATIONS = 50
RANDOM_STATE = 42


def _macro_f1(y_true, y_pred) -> float:
    return float(f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0))


def _encoded(df: pd.DataFrame) -> tuple[pd.DataFrame, list[bool]]:
    """Features as numbers for mutual_info_classif: categoricals as codes
    (with "missing" a code of its own), numerics with the median filled in."""
    out, discrete = pd.DataFrame(index=df.index), []
    for col in FEATURE_COLUMNS:
        if col in NUMERIC_FEATURES:
            out[col] = df[col].astype(float).fillna(df[col].median())
            discrete.append(col == "prior_complaint_count")
        else:
            out[col] = pd.factorize(df[col].fillna("missing"))[0]
            discrete.append(True)
    return out, discrete


def permutation_test(train: pd.DataFrame, test: pd.DataFrame, n: int) -> dict:
    x_train, y_train = train[list(FEATURE_COLUMNS)], train[TARGET_COLUMN].to_numpy()
    x_test, y_test = test[list(FEATURE_COLUMNS)], test[TARGET_COLUMN].to_numpy()
    real = _macro_f1(y_test, build_pipeline().fit(x_train, y_train).predict(x_test))
    rng = np.random.default_rng(RANDOM_STATE)
    null = []
    for i in range(n):
        shuffled = rng.permutation(y_train)
        null.append(_macro_f1(y_test, build_pipeline().fit(x_train, shuffled).predict(x_test)))
        if (i + 1) % 20 == 0:
            logger.info("  permutation %d/%d", i + 1, n)
    null_arr = np.array(null)
    stratified = DummyClassifier(strategy="stratified", random_state=RANDOM_STATE).fit(x_train, y_train)
    return {
        "real_macro_f1": round(real, 4),
        "null_mean": round(float(null_arr.mean()), 4),
        "null_p95": round(float(np.quantile(null_arr, 0.95)), 4),
        "null_max": round(float(null_arr.max()), 4),
        # (k + 1) / (n + 1): never reports p = 0 from a finite number of shuffles.
        "p_value": round(float((np.sum(null_arr >= real) + 1) / (n + 1)), 4),
        "n_permutations": n,
        "stratified_random_macro_f1": round(_macro_f1(y_test, stratified.predict(x_test)), 4),
        "majority_class_macro_f1": round(
            _macro_f1(y_test, DummyClassifier(strategy="most_frequent").fit(x_train, y_train).predict(x_test)), 4
        ),
    }


def mutual_information(train: pd.DataFrame, n: int) -> dict:
    x, discrete = _encoded(train)
    y = train[TARGET_COLUMN].to_numpy()
    real = mutual_info_classif(x, y, discrete_features=discrete, random_state=RANDOM_STATE)
    rng = np.random.default_rng(RANDOM_STATE)
    null = np.array([
        mutual_info_classif(x, rng.permutation(y), discrete_features=discrete, random_state=RANDOM_STATE)
        for _ in range(n)
    ])
    p = pd.Series(y).value_counts(normalize=True).to_numpy()
    features = {
        col: {"mi_nats": round(float(real[i]), 5), "null_p95_nats": round(float(np.quantile(null[:, i], 0.95)), 5),
              "above_null_p95": bool(real[i] > np.quantile(null[:, i], 0.95))}
        for i, col in enumerate(FEATURE_COLUMNS)
    }
    return {"priority_entropy_nats": round(float(-(p * np.log(p)).sum()), 4), "features": features,
            "n_permutations": n, "rows": len(train)}


def run(warehouse_path: Path, n_permutations: int, n_mi_permutations: int) -> dict:
    df = load_features(warehouse_path)
    train, test, split = chronological_split(df)
    logger.info("Permutation test of macro-F1 (%d shuffles)...", n_permutations)
    perm = permutation_test(train, test, n_permutations)
    logger.info("  real %.4f vs null mean %.4f (p=%.4f)", perm["real_macro_f1"], perm["null_mean"], perm["p_value"])
    logger.info("Mutual information (%d shuffles)...", n_mi_permutations)
    mi = mutual_information(train, n_mi_permutations)
    return {"split": {"split_date": split.split_date, "train_size": split.train_size, "test_size": split.test_size},
            "permutation_test": perm, "mutual_information": mi}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--permutations", type=int, default=N_PERMUTATIONS)
    parser.add_argument("--mi-permutations", type=int, default=N_MI_PERMUTATIONS)
    args = parser.parse_args(argv)
    report = run(args.warehouse, args.permutations, args.mi_permutations)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("Wrote %s", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
