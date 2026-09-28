"""Evaluation report: baseline vs. proposed classifier on the identical
held-out set (AD-6, plan.md Conformance Criteria).

Reports macro-F1 (primary metric) and per-class recall for BOTH models,
computed identically, plus the delta — reported honestly whether positive or
negative, never selectively favorable. Every number here is measured on
`etl/train_classifier.py`'s pre-registered chronological split, read from the
predictions that script persisted alongside the model — this script never
re-trains, re-splits or re-samples.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sklearn.metrics import f1_score, recall_score

from etl.train_classifier import DEFAULT_SPLIT_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.evaluate_classifier")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = REPO_ROOT / "data" / "classifier_eval_report.json"

PRIORITY_LABELS = ("Low", "Medium", "High", "Critical")


def _per_class_recall(y_true: list[str], y_pred: list[str]) -> dict[str, float]:
    scores = recall_score(y_true, y_pred, labels=list(PRIORITY_LABELS), average=None, zero_division=0)
    return dict(zip(PRIORITY_LABELS, (round(float(s), 4) for s in scores), strict=True))


def evaluate(training_result: dict) -> dict:
    y_test = training_result["y_test"]
    baseline_pred = training_result["baseline_pred"]
    proposed_pred = training_result["proposed_pred"]

    baseline_macro_f1 = round(
        f1_score(y_test, baseline_pred, labels=list(PRIORITY_LABELS), average="macro", zero_division=0), 4
    )
    proposed_macro_f1 = round(
        f1_score(y_test, proposed_pred, labels=list(PRIORITY_LABELS), average="macro", zero_division=0), 4
    )

    return {
        "split": training_result["split"],
        "sample_size": len(y_test),
        "train_class_distribution": training_result["train_class_distribution"],
        "baseline": {
            "description": "Majority-class predictor (always predicts the training split's most frequent priority)",
            "macro_f1": baseline_macro_f1,
            "per_class_recall": _per_class_recall(y_test, baseline_pred),
        },
        "proposed": {
            "description": "RandomForestClassifier on structured intake-time features (see etl/features.py)",
            "macro_f1": proposed_macro_f1,
            "per_class_recall": _per_class_recall(y_test, proposed_pred),
        },
        "macro_f1_delta": round(proposed_macro_f1 - baseline_macro_f1, 4),
        "feature_columns": training_result["feature_columns"],
    }


def load_training_result(split_path: Path = DEFAULT_SPLIT_PATH) -> dict:
    """Reads the predictions `etl/train_classifier.py` wrote in the SAME run
    that persisted `data/classifier.joblib`, so this report describes the
    shipped model itself rather than an equivalent re-fit.
    """
    if not split_path.exists():
        raise FileNotFoundError(f"{split_path} not found — run `python -m etl.train_classifier` first")
    return json.loads(split_path.read_text())


def main() -> int:
    result = evaluate(load_training_result())
    DEFAULT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_REPORT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info("Wrote evaluation report to %s", DEFAULT_REPORT_PATH)
    logger.info(
        "macro-F1: baseline=%s proposed=%s delta=%+.4f",
        result["baseline"]["macro_f1"], result["proposed"]["macro_f1"], result["macro_f1_delta"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
