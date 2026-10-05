"""Test-fold evaluation of the fraud-risk model against the bank's fraud_score.

Reads the validation/test predictions `etl/train_fraud_model.py` persisted in
the same run that saved `data/fraud_model.joblib` (never re-trains or
re-splits), and writes `data/fraud_eval_report.json` plus the charts under
`docs/ml/`.

## What is computed

- Ranking on the test fold, comparison population (rows with fraud_score):
  PR-AUC (primary), ROC-AUC, recall at FPR 0.1% and 1%, with stratified
  bootstrap 95% CIs and a paired CI of PR-AUC(model) - PR-AUC(fraud_score).
- Calibration (reliability bins + Brier) of the calibrated model probabilities.
- Cost-based operating thresholds, chosen on VALIDATION and reported on test,
  for fraud_score and for the best model (see `COST_ASSUMPTIONS`).
- Error analysis at the chosen operating points.
- Rows without fraud_score: the model's ranking there (fraud_score has none).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score, roc_curve

from etl.train_fraud_model import (
    DEFAULT_EXPERIMENTS_PATH,
    DEFAULT_PREDICTIONS_PATH,
    append_experiment,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("etl.evaluate_fraud_model")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = REPO_ROOT / "data" / "fraud_eval_report.json"
DEFAULT_DOCS_DIR = REPO_ROOT / "docs" / "ml"

TARGET = "is_fraud"
SCORE = "fraud_score"
FPR_POINTS = (0.001, 0.01)
N_BOOTSTRAP = 500
RANDOM_STATE = 42
N_CALIBRATION_BINS = 10

# The policy decision as numbers. Population: charges the rest of the AD-13
# screening would let through (Approved, amount_usd <= 200), the only ones
# where the fraud gate decides anything.
AUTO_RESOLVE_MAX_AMOUNT_USD = 200.0
COST_ASSUMPTIONS = {
    # MEASURED anchor, not dispute-specific: median first-contact handle time
    # of "Queja" contacts (425 s, n = 2,901, docs/analysis/demand-report.md),
    # the upper of the two anchors there; a dispute review is closer to a
    # complaint than to a 202 s transactional call.
    "handle_seconds": 425.0,
    # ASSUMED, the middle of the demand report's USD 5 / 10 / 20 sensitivity.
    "hourly_rate_usd": 10.0,
    # ASSUMED: back-office cost of unwinding a wrong automatic credit
    # (investigation, card reissue, chargeback paperwork) on top of the amount.
    "wrong_credit_ops_usd": 25.0,
}
SENSITIVITY = {
    "hourly_rate_usd": (5.0, 10.0, 20.0),
    "handle_seconds": (202.0, 425.0, 1800.0),
    "wrong_credit_ops_usd": (0.0, 25.0, 100.0),
}


def recall_at_fpr(y: np.ndarray, score: np.ndarray, fpr_target: float) -> float:
    fpr, tpr, _ = roc_curve(y, score)
    ok = fpr <= fpr_target
    return float(tpr[ok].max()) if ok.any() else 0.0


def ranking_metrics(y: np.ndarray, score: np.ndarray) -> dict[str, float]:
    out = {
        "pr_auc": float(average_precision_score(y, score)),
        "roc_auc": float(roc_auc_score(y, score)),
    }
    for f in FPR_POINTS:
        out[f"recall_at_fpr_{f:g}"] = recall_at_fpr(y, score, f)
    return out


def _stratified_indices(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    pos, neg = np.flatnonzero(y), np.flatnonzero(~y)
    return np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])


def bootstrap(y: np.ndarray, scores: dict[str, np.ndarray], reference: str, n: int = N_BOOTSTRAP) -> dict:
    """Stratified bootstrap (positives and negatives resampled separately, so
    every replicate keeps the fold's fraud count). The same replicate indices
    are shared by every score, which makes the differences paired."""
    rng = np.random.default_rng(RANDOM_STATE)
    samples: dict[str, list[dict[str, float]]] = {k: [] for k in scores}
    for _ in range(n):
        idx = _stratified_indices(y, rng)
        for name, s in scores.items():
            samples[name].append(ranking_metrics(y[idx], s[idx]))
    out = {}
    for name, rows in samples.items():
        frame = pd.DataFrame(rows)
        out[name] = {m: [round(float(frame[m].quantile(0.025)), 4), round(float(frame[m].quantile(0.975)), 4)]
                     for m in frame.columns}
        if name != reference:
            diff = frame["pr_auc"] - pd.DataFrame(samples[reference])["pr_auc"]
            out[name]["pr_auc_minus_reference"] = [
                round(float(diff.quantile(0.025)), 4), round(float(diff.quantile(0.975)), 4)
            ]
    return out


def reliability(y: np.ndarray, p: np.ndarray, bins: int = N_CALIBRATION_BINS) -> list[dict]:
    """Quantile bins over the predicted probability (equal-width bins would
    put almost every row in the first bin at a 0.1% base rate)."""
    order = np.argsort(p)
    rows = []
    for chunk in np.array_split(order, bins):
        rows.append({
            "n": int(len(chunk)), "mean_predicted": float(p[chunk].mean()),
            "observed_rate": float(y[chunk].mean()), "frauds": int(y[chunk].sum()),
        })
    return rows


def unit_costs(assumptions: dict) -> tuple[float, float]:
    escalation = assumptions["handle_seconds"] / 3600.0 * assumptions["hourly_rate_usd"]
    return escalation, assumptions["wrong_credit_ops_usd"]


def expected_cost(y: np.ndarray, score: np.ndarray, amount: np.ndarray, threshold: float, assumptions: dict) -> dict:
    """Charges with score >= threshold go to a person (escalation cost, fraud
    or not); a fraud below it is credited automatically (amount + ops lost)."""
    escalation, ops = unit_costs(assumptions)
    escalated = score >= threshold
    missed = (~escalated) & y
    cost = escalated.sum() * escalation + (amount[missed] + ops).sum()
    return {
        "threshold": float(threshold), "total_cost_usd": float(cost),
        "cost_per_1000_charges_usd": float(cost / len(y) * 1000),
        "escalated": int(escalated.sum()), "escalation_rate": float(escalated.mean()),
        "frauds_escalated": int((escalated & y).sum()), "frauds_auto_credited": int(missed.sum()),
        "fraud_usd_auto_credited": float(amount[missed].sum()),
        "precision_escalated": float((escalated & y).sum() / escalated.sum()) if escalated.any() else 0.0,
        "recall": float((escalated & y).sum() / y.sum()) if y.any() else 0.0,
    }


def candidate_thresholds(score: np.ndarray, max_candidates: int = 400) -> np.ndarray:
    uniq = np.unique(score[~np.isnan(score)])
    if len(uniq) > max_candidates:
        uniq = np.unique(np.quantile(uniq, np.linspace(0, 1, max_candidates)))
    return np.concatenate([uniq, [np.inf]])


def best_threshold(y, score, amount, assumptions) -> dict:
    results = [expected_cost(y, score, amount, t, assumptions) for t in candidate_thresholds(score)]
    return min(results, key=lambda r: (r["total_cost_usd"], -r["threshold"]))


def cost_population(fold: pd.DataFrame) -> pd.DataFrame:
    mask = (
        fold[SCORE].notna()
        & (fold["transaction_status"] == "Approved")
        & (fold["amount_usd_filled"] <= AUTO_RESOLVE_MAX_AMOUNT_USD)
    )
    return fold[mask]


def threshold_analysis(val: pd.DataFrame, test: pd.DataFrame, score_cols: dict[str, str]) -> dict:
    v, t = cost_population(val), cost_population(test)
    yv, yt = v[TARGET].to_numpy(), t[TARGET].to_numpy()
    out = {"population": {
        "definition": f"fraud_score present, transaction_status Approved, amount_usd <= {AUTO_RESOLVE_MAX_AMOUNT_USD}",
        "val_rows": len(v), "val_frauds": int(yv.sum()), "test_rows": len(t), "test_frauds": int(yt.sum()),
    }, "assumptions": COST_ASSUMPTIONS, "unit_costs_usd": dict(zip(
        ("escalation", "wrong_credit_ops"), unit_costs(COST_ASSUMPTIONS), strict=True)), "scores": {}}
    for name, col in score_cols.items():
        chosen = best_threshold(yv, v[col].to_numpy(), v["amount_usd_filled"].to_numpy(), COST_ASSUMPTIONS)
        thr = chosen["threshold"]
        entry = {
            "threshold_chosen_on_val": thr, "val": chosen,
            "test": expected_cost(yt, t[col].to_numpy(), t["amount_usd_filled"].to_numpy(), thr, COST_ASSUMPTIONS),
            "test_never_escalate": expected_cost(yt, t[col].to_numpy(), t["amount_usd_filled"].to_numpy(), np.inf, COST_ASSUMPTIONS),
        }
        if name == "fraud_score":
            entry["test_current_policy_30"] = expected_cost(
                yt, t[col].to_numpy(), t["amount_usd_filled"].to_numpy(), 30.0, COST_ASSUMPTIONS)
        sens = []
        for key, values in SENSITIVITY.items():
            for value in values:
                a = {**COST_ASSUMPTIONS, key: value}
                c = best_threshold(yv, v[col].to_numpy(), v["amount_usd_filled"].to_numpy(), a)
                sens.append({"vary": key, "value": value, "threshold_on_val": c["threshold"],
                             "test_cost_per_1000": expected_cost(
                                 yt, t[col].to_numpy(), t["amount_usd_filled"].to_numpy(), c["threshold"], a
                             )["cost_per_1000_charges_usd"]})
        entry["sensitivity"] = sens
        out["scores"][name] = entry
    return out


def error_analysis(test: pd.DataFrame, col: str, threshold: float) -> dict:
    pop = cost_population(test)
    flagged = pop[col] >= threshold
    fn = pop[pop[TARGET] & ~flagged]
    fp = pop[~pop[TARGET] & flagged]
    tp = pop[pop[TARGET] & flagged]

    def describe(frame: pd.DataFrame) -> dict:
        if frame.empty:
            return {"n": 0}
        return {
            "n": int(len(frame)),
            "median_amount_usd": round(float(frame["amount_usd_filled"].median()), 2),
            "median_fraud_score": round(float(frame[SCORE].median()), 2),
            "channel": frame["channel"].value_counts(normalize=True).round(3).to_dict(),
            "transaction_type": frame["transaction_type"].value_counts(normalize=True).round(3).to_dict(),
            "share_new_merchant": round(float(frame["is_new_merchant"].mean()), 3) if frame["is_new_merchant"].notna().any() else None,
            "share_foreign_country": round(float(frame["is_foreign_country"].mean()), 3),
            "median_prior_txns": float(frame["n_prior_txns"].median()),
        }

    return {"score": col, "threshold": threshold, "false_negatives": describe(fn),
            "false_positives": describe(fp), "true_positives": describe(tp),
            "all_legit_reference": describe(pop[~pop[TARGET]])}


def render_charts(test: pd.DataFrame, score_cols: dict[str, str], calib_col: str, docs_dir: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from sklearn.metrics import precision_recall_curve

    docs_dir.mkdir(parents=True, exist_ok=True)
    pop = test[test[SCORE].notna()]
    y = pop[TARGET].to_numpy()
    fig, ax = plt.subplots(figsize=(7, 5))
    for name, col in score_cols.items():
        prec, rec, _ = precision_recall_curve(y, pop[col].to_numpy())
        ax.plot(rec, prec, label=f"{name} (AP {average_precision_score(y, pop[col]):.3f})")
    ax.axhline(y.mean(), color="grey", linestyle=":", label=f"base rate {y.mean():.4%}")
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Precision-recall, test fold (rows with fraud_score)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(docs_dir / "fraud_pr_curve.png", dpi=120)
    plt.close(fig)

    bins = reliability(y, pop[calib_col].to_numpy())
    fig, ax = plt.subplots(figsize=(6, 5))
    xs = [b["mean_predicted"] for b in bins]
    ys = [b["observed_rate"] for b in bins]
    lim = max(max(xs), max(ys)) * 1.05
    ax.plot([0, lim], [0, lim], color="grey", linestyle=":", label="perfect calibration")
    ax.plot(xs, ys, marker="o", label=calib_col.removeprefix("p_"))
    ax.set_xscale("symlog", linthresh=1e-4)
    ax.set_yscale("symlog", linthresh=1e-4)
    ax.set_xlabel("Mean predicted probability (decile)")
    ax.set_ylabel("Observed fraud rate")
    ax.set_title("Reliability, test fold (deciles of predicted probability)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(docs_dir / "fraud_calibration.png", dpi=120)
    plt.close(fig)
    return ["fraud_pr_curve.png", "fraud_calibration.png"]


def _round(obj):
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v) for v in obj]
    return obj


def evaluate(pred: dict, docs_dir: Path | None = DEFAULT_DOCS_DIR) -> dict:
    val, test = pred["val"], pred["test"]
    model_cols = {name: f"p_{name}" for name in pred["models"]}
    score_cols = {"fraud_score": SCORE, "rules": "rules_score", **model_cols}

    scored = test[test[SCORE].notna()]
    y = scored[TARGET].to_numpy()
    point = {name: ranking_metrics(y, scored[col].to_numpy()) for name, col in score_cols.items()}
    cis = bootstrap(y, {name: scored[col].to_numpy() for name, col in score_cols.items()}, reference="fraud_score")

    unscored = test[test[SCORE].isna()]
    yu = unscored[TARGET].to_numpy()
    unscored_metrics = {
        "rows": len(unscored), "frauds": int(yu.sum()),
        "models": {name: ranking_metrics(yu, unscored[col].to_numpy()) for name, col in model_cols.items()
                   if pred["models"][name]["variant"] == "ours"} if yu.any() else {},
    }

    calibration = {}
    for name, col in model_cols.items():
        p = scored[col].to_numpy()
        calibration[name] = {"brier": float(brier_score_loss(y, p)),
                             "brier_base_rate": float(brier_score_loss(y, np.full(len(y), y.mean()))),
                             "reliability": reliability(y, p)}

    best_model = max(model_cols, key=lambda k: pred["models"][k]["val_metrics"]["val_pr_auc"])
    thresholds = threshold_analysis(val, test, {"fraud_score": SCORE, best_model: model_cols[best_model]})
    errors = {
        name: error_analysis(test, col, thresholds["scores"][name]["threshold_chosen_on_val"])
        for name, col in (("fraud_score", SCORE), (best_model, model_cols[best_model]))
    }
    charts = render_charts(test, score_cols, model_cols[best_model], docs_dir) if docs_dir else []

    return _round({
        "split": pred["split"], "run_group": pred["run_group"], "git_sha": pred["git_sha"],
        "selected_on_validation": pred["selected"], "best_model": best_model,
        "validation": {"baselines": pred["baselines_val"],
                       "models": {k: v["val_metrics"] | {"params": v["params"]} for k, v in pred["models"].items()}},
        "test_comparison_population": {"rows": len(scored), "frauds": int(y.sum()), "base_rate": float(y.mean())},
        "test_point_estimates": point, "test_bootstrap_95ci": cis, "n_bootstrap": N_BOOTSTRAP,
        "test_rows_without_fraud_score": unscored_metrics,
        "calibration": calibration, "thresholds": thresholds, "error_analysis": errors, "charts": charts,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--experiments", type=Path, default=DEFAULT_EXPERIMENTS_PATH)
    args = parser.parse_args(argv)

    if not args.predictions.exists():
        raise FileNotFoundError(f"{args.predictions} not found: run `python -m etl.train_fraud_model` first")
    report = evaluate(joblib.load(args.predictions), args.docs_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    append_experiment(args.experiments, {
        "run_group": report["run_group"], "git_sha": report["git_sha"], "model": "test_evaluation",
        "split": report["split"], "metrics": report["test_point_estimates"],
        "ci95": report["test_bootstrap_95ci"],
        "thresholds": {k: v["threshold_chosen_on_val"] for k, v in report["thresholds"]["scores"].items()},
    })
    for name, m in report["test_point_estimates"].items():
        logger.info("test %-16s PR-AUC %.4f %s  ROC-AUC %.4f", name, m["pr_auc"],
                    report["test_bootstrap_95ci"][name]["pr_auc"], m["roc_auc"])
    logger.info("Wrote %s", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
