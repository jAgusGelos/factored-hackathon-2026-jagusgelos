# The fraud gate: cost-justified, and what the fraud-risk model is allowed to do

**Short version.** The policy sends a disputed charge to a person when its vendor `fraud_score`
is **above 30** (it used to be "at or above 30", an unjustified hackathon default). The
fraud-risk model built in `docs/ml/fraud-model.md` did not beat `fraud_score`, so it is **not a
policy input**: its estimate is precomputed offline into the demo fixture and shown to the
advisor in the handoff, labelled as a model estimate, and never to the customer. Decision record:
AD-15 in `docs/architecture-decisions.md`.

Labels: **MEASURED** (computed on the dataset), **SIMULATED** (the demo fixture, synthetic charges
included), **ASSUMED** (an input we chose), **DESIGN ARGUMENT** (reasoning, not a measurement).

## Why "above 30"

Source: `data/fraud_eval_report.json` (`python -m etl.evaluate_fraud_model`), also reported by the
eval as `fraud_gate.measured` in `data/eval_report.json`. Population: a transaction-level proxy for
the charges the gate decides on (score present, Approved, amount at most USD 200), chronological
test fold 2026-02-01 to 2026-06-17, 84,269 charges, 71 frauds (MEASURED).

| Gate | Escalated | Frauds caught | Frauds credited automatically | Precision of escalations | Cost per 1,000 charges |
|---|---|---|---|---|---|
| **shipped: `fraud_score > 30`** | **48** | **48** | **23** | **100%** | **USD 38.13** |
| previous default: `fraud_score >= 30` | 66 | 48 | 23 | 72.7% | USD 38.38 |
| model gate (not shipped): stacked logistic risk >= 0.003527 | 166 | 48 | 23 | 28.9% | USD 39.78 |

- Cost model: an escalation costs USD 1.18 (425 s median handle time, MEASURED but not
  dispute-specific, at USD 10/h, ASSUMED); a fraud credited automatically costs its amount
  (MEASURED per charge) plus USD 25 of operations (ASSUMED).
- The threshold was chosen on the validation fold (optimum in (30.0, 30.06], every value in that
  interval is the same rule) and is the same in all nine cost-sensitivity settings (hourly rate
  USD 5 / 10 / 20, handle time 202 / 425 / 1,800 s, operations cost USD 0 / 25 / 100). MEASURED.
- "Above 30" catches every fraud "at or above 30" catches and sends 18 fewer legitimate charges
  (all scored exactly 30.0) to a person. The model's own gate reaches the same 48 frauds only by
  escalating 118 more legitimate charges.
- What no threshold fixes: 23 of the 71 test frauds score below 30 and look like normal traffic on
  every feature we have; the post-credit back-office review (AD-13) is what catches those.

## What the model's estimate does, and what it cannot do

- **Offline only.** `etl/build_fixture.py` scores every fixture charge with
  `etl.train_fraud_model.score_transactions`, using the customer's earlier transactions from the
  two-year fraud warehouse as history, and stores `fraud_risk`, `fraud_risk_threshold` and
  `fraud_model_version` (`logistic_stacked-d7c46aeb`). The app never loads the model.
- **Synthetic charges are scored, not assigned.** They carry only their scenario's attributes
  (amount, channel, merchant, time, the vendor score). SIMULATED result: the online fraud
  scenario `SYN-DEMO-ONLINE` gets 0.9994, above the 0.0035 reference threshold, and every charge
  built to auto-resolve, the duplicate pair and the repeated fare get at most 0.00004. Every
  README scenario keeps its outcome; no synthetic charge had to change.
- **Advisor only.** The handoff's verified facts carry `fraud_score` with the policy threshold, and
  the model estimate as "estimación del modelo, no un hecho verificado; apoyo a la decisión, la
  política no la usa", its reference threshold and the model version. The customer's own
  `/api/case` view drops these five fields (`app/handoffs.py::INTERNAL_FACTS`), and the eval marks
  any case unsafe if a key or stored text of them reaches it. No screen renders the stored
  handoff for an advisor yet: the demo's on-screen handoff panel is the customer's own view, so
  the estimate is visible only in the stored handoff (app db) and the eval report.
- **Never a decision.** No function in `app/policy.py` names the estimate (an AST test), only
  the fixture reads (`app/fixture_db.py`, `app/transactions.py`) and `app/handoffs.py` touch it, and `tests/test_policy_not_overridden.py`
  checks over every dispute reason and every combination of the other gates that the estimate
  never changes a verdict and that a score above 30 only adds one reason to escalate.

## The priority classifier

The same feature measured the intake classifier's signal ceiling: macro-F1 0.2448 against 0.2434
for 100 shuffled-label refits, permutation p = 0.45 (MEASURED, `etl/priority_signal_ceiling.py`).
It stays in the policy as an escalation-only signal, its reason now saying "no measured lift": it
can never credit a charge and predicts Critical for few cases (Critical recall 0.058, MEASURED).
Retiring it would change AD-6, the image and the live features on submission day for no safety
gain (DESIGN ARGUMENT); that is the documented next step.

## Limits

- Synthetic data: no legitimate charge in the dataset scores above 30, which is why "above 30"
  is so clean. With a real vendor score the bands overlap; the cost method transfers, the number
  30 does not.
- The proxy population is every approved charge, not disputed ones (no historical dispute links to
  a charge). Fraud is likely more common among disputed charges, which would push the optimum
  toward escalating more, never less (DESIGN ARGUMENT).
- On the demo fixture all three gates flag the same single charge, so the conversation eval
  cannot tell them apart (SIMULATED); the measured fold is the evidence.
