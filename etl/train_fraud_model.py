"""Chronological split, baselines, candidate models and validation-only tuning
for the per-charge fraud-risk model.

## Pre-registered protocol (fixed before any test-fold number was looked at)

- **Data:** `transactions` 2024-06-17..2026-06-17 (`data/fraud_warehouse.duckdb`,
  built by `python -m etl.extract --tables transactions --start-date 2024-06-17
  --warehouse data/fraud_warehouse.duckdb --manifest data/fraud_extraction_manifest.json`).
- **Split by time:** warm-up < 2024-08-16 (used ONLY as customer history, never
  as a training row: those rows have artificially short histories);
  train [2024-08-16, 2025-09-01); validation [2025-09-01, 2026-02-01);
  test [2026-02-01, 2026-06-18). Never random.
- **Comparison population:** rows with a non-null `fraud_score` (about 80%),
  the only rows where the bank's score exists to compare against. Rows without
  a score are reported separately in the evaluation.
- **Baselines:** (a) `fraud_score` alone, (b) a hand-written red-flag count
  (`rules_score`), no fitting.
- **Candidates:** logistic regression and `HistGradientBoostingClassifier`,
  each on our features only ("ours") and on our features + `fraud_score`
  ("stacked"). Grids below; class imbalance handled with `class_weight`
  (None vs "balanced" is itself a tuned option).
- **Selection:** validation average precision (PR-AUC) on the comparison
  population. The test fold is scored once, by `etl/evaluate_fraud_model.py`.
- **Calibration:** Platt scaling fitted on the validation fold for the
  selected model of each variant (class weights distort raw probabilities).

Training rows: every positive plus a fixed 10% sample of negatives, each kept
negative weighted x10 so the weighted loss is an unbiased estimate of the
full-data loss. This keeps a 16-fit grid within minutes on ~2M rows; it does
not touch validation or test, which are always scored in full.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from etl.fraud_features import (
    BASELINE_SCORE_COLUMN,
    CATEGORICAL_FEATURES,
    DEFAULT_CUSTOMERS_WAREHOUSE_PATH,
    DEFAULT_FRAUD_WAREHOUSE_PATH,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
    STACKED_FEATURE_COLUMNS,
    TARGET_COLUMN,
    build_features,
    load_features,
    profile_warehouse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.train_fraud_model")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = REPO_ROOT / "data" / "fraud_model.joblib"
DEFAULT_PREDICTIONS_PATH = REPO_ROOT / "data" / "fraud_predictions.joblib"
DEFAULT_EXPERIMENTS_PATH = REPO_ROOT / "docs" / "ml" / "experiments.jsonl"

DATA_WINDOW = ("2024-06-17", "2026-06-17")
WARMUP_END = pd.Timestamp("2024-08-16")
TRAIN_END = pd.Timestamp("2025-09-01")
VAL_END = pd.Timestamp("2026-02-01")

RANDOM_STATE = 42
NEGATIVE_SAMPLE_RATE = 0.10

VARIANTS = {"ours": FEATURE_COLUMNS, "stacked": STACKED_FEATURE_COLUMNS}

LR_GRID = [
    {"C": c, "class_weight": cw} for c in (0.01, 0.1, 1.0) for cw in (None, "balanced")
]
HGB_GRID = [
    {"learning_rate": lr, "max_leaf_nodes": leaves, "class_weight": cw}
    for lr in (0.05, 0.1)
    for leaves in (15, 31)
    for cw in (None, "balanced")
]

# Red flags of the rules baseline: each adds 1 to the score.
RULES = {
    "new_merchant": lambda d: d["is_new_merchant"].fillna(0) == 1,
    "new_country": lambda d: d["is_new_country"].fillna(0) == 1,
    "foreign_country": lambda d: d["is_foreign_country"].fillna(0) == 1,
    "amount_zscore_gt_2": lambda d: d["amount_zscore"].fillna(0) > 2,
    "speed_gt_500_kmh": lambda d: d["kmh_from_prev_geo"].fillna(0) > 500,
    "burst_1h": lambda d: d["txns_last_1h"] >= 1,
}


@dataclass
class Split:
    warmup_end: str
    train_end: str
    val_end: str
    train_rows: int
    train_positives: int
    val_rows: int
    val_positives: int
    test_rows: int
    test_positives: int


def chronological_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Split]:
    if not df["transaction_date"].is_monotonic_increasing:
        raise ValueError("df must be sorted by transaction_date ascending (build_features does this)")
    dates = df["transaction_date"]
    train = df[(dates >= WARMUP_END) & (dates < TRAIN_END)]
    val = df[(dates >= TRAIN_END) & (dates < VAL_END)]
    test = df[dates >= VAL_END]
    split = Split(
        warmup_end=str(WARMUP_END.date()), train_end=str(TRAIN_END.date()), val_end=str(VAL_END.date()),
        train_rows=len(train), train_positives=int(train[TARGET_COLUMN].sum()),
        val_rows=len(val), val_positives=int(val[TARGET_COLUMN].sum()),
        test_rows=len(test), test_positives=int(test[TARGET_COLUMN].sum()),
    )
    return train, val, test, split


def rules_score(df: pd.DataFrame) -> np.ndarray:
    return sum(rule(df).astype(int) for rule in RULES.values()).to_numpy(dtype=float)


def _numeric(columns: tuple[str, ...]) -> list[str]:
    return [c for c in columns if c not in CATEGORICAL_FEATURES]


def _categorical(columns: tuple[str, ...]) -> list[str]:
    return [c for c in columns if c in CATEGORICAL_FEATURES]


def build_logistic(columns: tuple[str, ...], C: float, class_weight: str | None) -> Pipeline:
    pre = ColumnTransformer([
        ("num", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]), _numeric(columns)),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=50), _categorical(columns)),
    ])
    clf = LogisticRegression(C=C, class_weight=class_weight, max_iter=2000, random_state=RANDOM_STATE)
    return Pipeline([("preprocess", pre), ("classify", clf)])


def build_hgb(
    columns: tuple[str, ...], learning_rate: float, max_leaf_nodes: int, class_weight: str | None
) -> Pipeline:
    numeric, categorical = _numeric(columns), _categorical(columns)
    pre = ColumnTransformer(
        [
            ("num", "passthrough", numeric),
            ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan,
                                   encoded_missing_value=np.nan), categorical),
        ],
        verbose_feature_names_out=False,
    )
    clf = HistGradientBoostingClassifier(
        learning_rate=learning_rate, max_leaf_nodes=max_leaf_nodes, max_iter=200,
        min_samples_leaf=200, l2_regularization=1.0, class_weight=class_weight,
        categorical_features=[False] * len(numeric) + [True] * len(categorical),
        early_stopping=False, random_state=RANDOM_STATE,
    )
    return Pipeline([("preprocess", pre), ("classify", clf)])


BUILDERS = {"logistic": build_logistic, "hgb": build_hgb}


class PlattCalibrator:
    """Monotone map from a model's raw probability to a calibrated one,
    fitted on the validation fold (logistic regression on the raw logit)."""

    def __init__(self) -> None:
        self._lr = LogisticRegression(C=1e6, max_iter=1000)

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, raw: np.ndarray, y: np.ndarray) -> PlattCalibrator:
        self._lr.fit(self._logit(raw), y)
        return self

    def transform(self, raw: np.ndarray) -> np.ndarray:
        return self._lr.predict_proba(self._logit(raw))[:, 1]


def _training_sample(train: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(RANDOM_STATE)
    y = train[TARGET_COLUMN].to_numpy()
    keep = y | (rng.random(len(train)) < NEGATIVE_SAMPLE_RATE)
    sample = train[keep]
    weights = np.where(sample[TARGET_COLUMN].to_numpy(), 1.0, 1.0 / NEGATIVE_SAMPLE_RATE)
    return sample, weights


def _scored(df: pd.DataFrame) -> pd.Series:
    return df[BASELINE_SCORE_COLUMN].notna()


def _val_metrics(y: np.ndarray, score: np.ndarray) -> dict[str, float]:
    return {
        "val_pr_auc": round(float(average_precision_score(y, score)), 4),
        "val_roc_auc": round(float(roc_auc_score(y, score)), 4),
    }


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def append_experiment(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _log_experiment(experiments_path: Path | None, common: dict, record: dict) -> None:
    if experiments_path:
        append_experiment(experiments_path, {**common, "timestamp": datetime.now(UTC).isoformat(), **record})


def _evaluate_baselines(
    val_scored: pd.DataFrame, y_val: np.ndarray, experiments_path: Path | None, common: dict
) -> dict[str, dict[str, float]]:
    baselines = {
        "fraud_score": _val_metrics(y_val, val_scored[BASELINE_SCORE_COLUMN].to_numpy()),
        "rules": _val_metrics(y_val, rules_score(val_scored)),
    }
    for name, metrics in baselines.items():
        logger.info("Baseline %s: %s", name, metrics)
        _log_experiment(experiments_path, common, {
            "model": f"baseline_{name}", "variant": "baseline",
            "params": {"rules": list(RULES)} if name == "rules" else {},
            "features": [BASELINE_SCORE_COLUMN] if name == "fraud_score" else list(RULES),
            "metrics": metrics,
        })
    return baselines


def _grid_search(
    sample: pd.DataFrame, weights: np.ndarray, val_scored: pd.DataFrame, y_val: np.ndarray,
    experiments_path: Path | None, common: dict,
) -> dict[tuple[str, str], dict]:
    """Fits every grid point of every (family, variant) and keeps the best one
    of each on validation PR-AUC."""
    y_sample = sample[TARGET_COLUMN].to_numpy()
    best: dict[tuple[str, str], dict] = {}
    for variant, columns in VARIANTS.items():
        for family, builder in BUILDERS.items():
            grid = LR_GRID if family == "logistic" else HGB_GRID
            for params in grid:
                pipe = builder(columns, **params)
                pipe.fit(sample[list(columns)], y_sample, classify__sample_weight=weights)
                raw_val = pipe.predict_proba(val_scored[list(columns)])[:, 1]
                metrics = _val_metrics(y_val, raw_val)
                logger.info("%s/%s %s -> %s", family, variant, params, metrics)
                _log_experiment(experiments_path, common, {
                    "model": family, "variant": variant, "params": params, "features": list(columns),
                    "metrics": metrics,
                })
                current = best.get((family, variant))
                if current is None or metrics["val_pr_auc"] > current["metrics"]["val_pr_auc"]:
                    best[(family, variant)] = {"pipeline": pipe, "params": params, "metrics": metrics}
    return best


def _calibrated_models(
    best: dict[tuple[str, str], dict], val_scored: pd.DataFrame, y_val: np.ndarray
) -> dict[str, dict]:
    models = {}
    for (family, variant), entry in best.items():
        columns = VARIANTS[variant]
        raw_val = entry["pipeline"].predict_proba(val_scored[list(columns)])[:, 1]
        calibrator = PlattCalibrator().fit(raw_val, y_val)
        models[f"{family}_{variant}"] = {
            "family": family, "variant": variant, "features": list(columns),
            "params": entry["params"], "val_metrics": entry["metrics"],
            "pipeline": entry["pipeline"], "calibrator": calibrator,
        }
    return models


def _fold_predictions(models: dict[str, dict], folds: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Every fold scored in full by the rules baseline and every calibrated model."""
    keep_cols = [
        "transaction_id", "transaction_date", "customer_id", TARGET_COLUMN, BASELINE_SCORE_COLUMN,
        "amount_usd_filled", "transaction_status", *FEATURE_COLUMNS,
    ]
    predictions = {}
    for fold_name, fold in folds.items():
        out = fold[[c for c in keep_cols if c in fold.columns]].copy()
        out["rules_score"] = rules_score(fold)
        for name, model in models.items():
            raw = model["pipeline"].predict_proba(fold[model["features"]])[:, 1]
            out[f"p_{name}"] = model["calibrator"].transform(raw)
        predictions[fold_name] = out.reset_index(drop=True)
    return predictions


def train(df: pd.DataFrame, experiments_path: Path | None = None) -> dict:
    train_df, val_df, test_df, split = chronological_split(df)
    logger.info("Split: %s", split)
    sample, weights = _training_sample(train_df)
    val_scored = val_df[_scored(val_df)]
    y_val = val_scored[TARGET_COLUMN].to_numpy()

    run_group = uuid.uuid4().hex[:8]
    common = {
        "run_group": run_group, "git_sha": _git_sha(), "data_window": DATA_WINDOW,
        "split": asdict(split), "negative_sample_rate": NEGATIVE_SAMPLE_RATE,
        "selection_metric": "val_pr_auc on rows with fraud_score",
    }

    baselines = _evaluate_baselines(val_scored, y_val, experiments_path, common)
    best = _grid_search(sample, weights, val_scored, y_val, experiments_path, common)
    models = _calibrated_models(best, val_scored, y_val)

    # The integration model: the best variant on validation PR-AUC.
    selected = max(models, key=lambda k: models[k]["val_metrics"]["val_pr_auc"])
    logger.info("Selected on validation: %s", selected)
    predictions = _fold_predictions(models, {"val": val_df, "test": test_df})

    return {
        "split": asdict(split), "run_group": run_group, "git_sha": common["git_sha"],
        "data_window": DATA_WINDOW, "baselines_val": baselines, "models": models,
        "selected": selected, "predictions": predictions,
    }


def predictions_bundle(result: dict, predictions: dict) -> dict:
    """What `etl/evaluate_fraud_model.py` reads: the val/test predictions plus
    the run metadata, with the fitted pipelines and calibrators left out."""
    return {
        **predictions, "selected": result["selected"], "split": result["split"],
        "run_group": result["run_group"], "git_sha": result["git_sha"],
        "baselines_val": result["baselines_val"], "data_profile": result.get("data_profile"),
        "models": {k: {kk: vv for kk, vv in v.items() if kk not in ("pipeline", "calibrator")}
                   for k, v in result["models"].items()},
    }


def score_transactions(df: pd.DataFrame, model_path: Path = DEFAULT_MODEL_PATH, model: str | None = None) -> np.ndarray:
    """Calibrated fraud probability for every row of `df` (offline precompute,
    e.g. from `etl/build_fixture.py`; never in the request path).

    `df` holds raw `transactions` rows (the columns of
    `etl.fraud_features.RAW_COLUMNS` plus `home_country` and
    `registration_date`). Include each customer's EARLIER transactions too:
    the history features only see what is in `df`. Returns probabilities
    aligned with `df`'s row order. `model` picks a stored model by name
    (default: the one selected on validation).
    """
    bundle = joblib.load(model_path)
    entry = bundle["models"][model or bundle["selected"]]
    # Same one-row-per-transaction_id rule as `load_transactions` (training):
    # a duplicated row would otherwise count twice in every later row's history.
    feats = build_features(df.drop_duplicates("transaction_id"))
    raw = entry["pipeline"].predict_proba(feats[entry["features"]])[:, 1]
    proba = pd.Series(entry["calibrator"].transform(raw), index=feats["transaction_id"].to_numpy())
    return proba.reindex(df["transaction_id"].to_numpy()).to_numpy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_FRAUD_WAREHOUSE_PATH)
    parser.add_argument("--customers-warehouse", type=Path, default=DEFAULT_CUSTOMERS_WAREHOUSE_PATH)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--experiments", type=Path, default=DEFAULT_EXPERIMENTS_PATH)
    args = parser.parse_args(argv)

    df = load_features(args.warehouse, args.customers_warehouse)
    logger.info("Loaded %d labeled transactions (%d frauds)", len(df), int(df[TARGET_COLUMN].sum()))
    result = train(df, experiments_path=args.experiments)
    predictions = result.pop("predictions")
    result["data_profile"] = profile_warehouse(args.warehouse)

    args.model.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {**result, "feature_columns": list(FEATURE_COLUMNS), "numeric_features": list(NUMERIC_FEATURES),
         "categorical_features": list(CATEGORICAL_FEATURES)},
        args.model,
    )
    joblib.dump(predictions_bundle(result, predictions), args.predictions)
    logger.info("Wrote %s and %s", args.model, args.predictions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
