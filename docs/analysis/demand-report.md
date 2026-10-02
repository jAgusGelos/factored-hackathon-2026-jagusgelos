# Demand analysis: why the dispute workflow

Data as of 2026-06-18T07:56:52. Every number is labeled measured, assumed, simulated, projection or design-argument, and every percentile shows its n and coverage.

## TL;DR: findings

1. **Complaint demand is flat by category** (measured). Each of the 5 categories holds 19.7% to 20.2% of 67,095 complaints, and the weekly coefficient of variation over 156 full weeks is 9.5% to 10.9% against 10.7% to 10.9% expected from Poisson noise. Every category passes the flatness rule below, a heuristic against Poisson noise, not a seasonality test.
2. **Call-center contact reasons are far from uniform** (measured). Over 19,677 contacts (2026-05-18 to 2026-06-18), contact-reason shares range from 3.0% to 34.6%. "Transaccional" is 34.6% of contacts and 23.7% of handle seconds, with a median of 202 s and 91.7% resolved on contact; "Queja" takes 425 s with 43.3% resolved on contact. This sizes the opportunity; it does not show that disputes are the worst process, because no key joins a complaint to a call.
3. **"Cargo no reconocido" waits 37.0 h for a first response** (measured): median 37.0 h and p90 58.0 h over n = 7,567 of 12,297 complaints (61.5% coverage). 4,730 have no first response (3,648 Open, 618 Escalated, 241 In Process, 111 Rejected, 95 Resolved, 17 Closed). The observed maximum is 72.0 h; 100.0% of recorded first responses came within 72 calendar hours.
4. **Data quality limits what can be claimed** (measured). 492 of 14,631 complaints have a resolution before their first response; 772 of 16,121 Resolved/Closed complaints have no resolution date; 0.0% of 67,095 complaints link to a call-center interaction; and the claimed-amount medians of the 4 currencies are within 10.0% of each other, which real amounts in those currencies would not be, so amounts are never summed across currencies.
5. **Complaints in this dataset rarely match a transaction** (measured, quoted from the eval snapshot): 1 amount and date match in a sample of 2,000 complaints (source: etl/build_fixture.py development, Milestone 1, 2026-09-28). The dataset generates complaints and transactions independently, so this describes the data available here, not a real bank.

## Why disputes

*Label: design-argument.* Volume does not single disputes out (complaint demand is flat by category), so the choice of workflow rests on a design argument, not on demand.
An unrecognized-charge dispute can be checked in code against the customer's own transaction
ledger and decided by an explicit, testable policy (`app/policy.py`): the agent either acts on
evidence it can verify or hands the case to a person with that evidence attached. Branch service,
app problems or service quality need a human judgment or a fix somewhere else. The measured
support is the call-center load and the wait for a first response in the findings above.

## What this data does not tell us

- Whether disputes cost more to handle than other complaints: 0.0% of complaints link to a call, so call time is not dispute time.
- Any real automation rate: the eval scenarios are constructed on purpose, and complaints in this dataset almost never match a transaction.
- Anything about the 4,730 "Cargo no reconocido" complaints with no first response yet (censored).
- Whether the business-day contact promise is met: the data has calendar hours and recorded responses only.
- Real monetary amounts: the per-currency medians are not consistent with exchange rates.
- Seasonality: passing a flatness heuristic over this window is not evidence of "no seasonality".

## Demand

All measured over n = 67,095 complaints.

### By category

| Category | Complaints | Share |
|---|---|---|
| Transactions | 13,580 | 20.2% |
| Fees | 13,553 | 20.2% |
| Technical | 13,407 | 20.0% |
| Branch | 13,361 | 19.9% |
| Service | 13,194 | 19.7% |

### Weekly variability

Monday-based weeks; 156 full weeks, 2 partial boundary weeks excluded from the CV. A category is flat when its share is within 19.0% to 21.0% and its CV is at most 1.25x the Poisson CV 1/sqrt(mean weekly count). The full weekly series is in the JSON.

| Category | Share | Mean per week | Weekly CV | Poisson CV | Flat |
|---|---|---|---|---|---|
| Branch | 19.9% | 85.3 | 10.2% | 10.8% | yes |
| Fees | 20.2% | 86.4 | 10.3% | 10.8% | yes |
| Service | 19.7% | 84.2 | 10.7% | 10.9% | yes |
| Technical | 20.0% | 85.6 | 10.9% | 10.8% | yes |
| Transactions | 20.2% | 86.7 | 9.5% | 10.7% | yes |

### By subcategory

| Category | Subcategory | Complaints | Share of category |
|---|---|---|---|
| Transactions | Cargo no reconocido | 12,297 | 90.5% |
| Fees | Cobro indebido | 12,194 | 90.0% |
| Technical | Problema con app | 12,128 | 90.5% |
| Branch | Atención en sucursal | 11,892 | 89.0% |
| Service | Calidad de servicio | 11,886 | 90.1% |
| Branch | (missing) | 1,469 | 11.0% |
| Fees | (missing) | 1,359 | 10.0% |
| Service | (missing) | 1,308 | 9.9% |
| Transactions | (missing) | 1,283 | 9.4% |
| Technical | (missing) | 1,279 | 9.5% |

### By reception channel

| Channel | Complaints | Share |
|---|---|---|
| Call Center | 33,761 | 50.3% |
| Email | 13,323 | 19.9% |
| Web | 9,884 | 14.7% |
| App | 6,727 | 10.0% |
| Branch | 2,683 | 4.0% |
| Regulator | 717 | 1.1% |

### By customer country

Coverage (complaints whose customer is in `customers`): 100.0%.

| Country | Complaints | Share |
|---|---|---|
| México | 33,375 | 49.7% |
| Colombia | 20,384 | 30.4% |
| Argentina | 13,336 | 19.9% |

## Response times

Measured, in calendar hours from creation_date. Percentiles are over the complaints that have the date; coverage is that n over the category total.

| Category | First response p50 / p90 | n | Coverage | Resolution p50 / p90 | n | Coverage |
|---|---|---|---|---|---|---|
| Branch | 38.0 h / 58.0 h | 8,167 | 61.1% | 384.0 h / 672.0 h | 3,075 | 23.0% |
| Fees | 38.0 h / 58.0 h | 8,271 | 61.0% | 384.0 h / 648.0 h | 3,068 | 22.6% |
| Service | 38.0 h / 58.0 h | 7,882 | 59.7% | 384.0 h / 672.0 h | 2,998 | 22.7% |
| Technical | 38.0 h / 58.0 h | 8,184 | 61.0% | 384.0 h / 672.0 h | 3,039 | 22.7% |
| Transactions | 37.0 h / 58.0 h | 8,349 | 61.5% | 360.0 h / 648.0 h | 3,169 | 23.3% |

![First response for "Cargo no reconocido"](first_response_cargo_no_reconocido.png)

Note: Share of RECORDED first responses within 72 calendar hours. Cases with no first response are censored and excluded, so this share says nothing about them.

## Call center

Measured over n = 19,677 interactions, 2026-05-18 to 2026-06-18. First-contact handle time across all contact reasons. Not dispute-specific: there is no join key from complaints to interactions.

| Contact reason | Contacts | Share | Median handle | n (handle) | Share of handle seconds | Resolved on contact |
|---|---|---|---|---|---|---|
| Transaccional | 6,801 | 34.6% | 202 s | 5,874 | 23.7% | 91.7% |
| Producto | 4,376 | 22.2% | 265 s | 3,757 | 18.6% | 89.7% |
| Queja | 3,378 | 17.2% | 425 s | 2,901 | 23.0% | 43.3% |
| Técnico | 2,982 | 15.2% | 363 s | 2,563 | 17.0% | 69.2% |
| Comercial | 1,544 | 7.8% | 540 s | 1,337 | 13.3% | 67.1% |
| Retención | 596 | 3.0% | 482 s | 497 | 4.3% | 59.7% |

![Median handle time by contact reason](call_reasons.png)

## Cost

Four separate blocks. They measure different things, so they are never divided into a ratio or added up into a total.

### A. Time to first action

- Agent pipeline p50: 0.2951 s (simulated, n = 40 eval scenarios). Offline eval pipeline time with a mocked LLM; excludes network time.
- Human first response p50 for "Cargo no reconocido": 37.0 h (measured, n = 7,567, coverage 61.5%).

### B. Measured human first-contact handle time

- Anchor: 202 s median for "Transaccional" (measured, n = 5,874).
- Upper anchor: 425 s median for "Queja" (measured, n = 2,901).
- First-contact handle time across all contact reasons. Not dispute-specific: there is no join key from complaints to interactions.

### C. Simulated agent LLM cost

- Per attempted case: USD 0.001589 (simulated, n = 40).
- Per successful resolution: USD 0.010595 (simulated, n = 6 resolutions).
- Method: estimated from constructed prompt/response character counts (~4 chars/token), not measured API billing.
- Pricing: https://www.anthropic.com/claude/haiku (Haiku 4.5, $1/$5 per M input/output tokens, verified 2026-09-28).
- Disclosure: OFFLINE/SIMULATED: the Anthropic client is mocked deterministically for reproducibility. Measures the state machine's policy pipeline and processing latency, NOT real LLM quality, network latency, or real API cost.

### D. PROJECTION: first-contact handle-time sensitivity

*Label: projection.* Formula: expected human handle cost per case (USD) = (1 - automation share) x anchor handle hours x hourly rate, with the 202 s anchor from block B. Each cell is the expected human handle cost of one case, not a total over any period.

| Hourly rate (assumed) | Share 0.0 (baseline, assumed) | Share 0.15 (scenario-suite outcome, not a population estimate, simulated) | Share 0.5 (illustrative, assumed) |
|---|---|---|---|
| USD 5 | USD 0.2806 | USD 0.2385 | USD 0.1403 |
| USD 10 | USD 0.5611 | USD 0.4769 | USD 0.2806 |
| USD 20 | USD 1.1222 | USD 0.9539 | USD 0.5611 |

- Share 0.0: assumed, baseline. Baseline: 1 amount and date match in a sample of 2,000 dataset complaints (eval snapshot). The dataset generates complaints and transactions independently, so it gives no basis for any share above 0.
- Share 0.15: simulated, scenario-suite outcome, not a population estimate. 6 of 40 constructed eval scenarios ended in a safe automated resolution.
- Share 0.5: assumed, illustrative.

**Prerequisite:** Real complaint-to-transaction linkage or a redesigned intake is required before any automation share can be claimed.

## Data quality

- Resolution before first response: 492 of 14,631 (measured). Over complaints that have both a first response and a resolution date.
- Resolved/Closed without a resolution date: 772 of 16,121 (measured).
- Negative elapsed times: 0 of 67,095 (measured). Complaints whose first response or resolution precedes their creation; excluded from every elapsed-time percentile and from the contact window.
- Complaint-to-interaction link: 0.0% of 67,095 (measured). Share of complaints with origin_interaction_id populated.
- Complaints with no currency: 45,319 of 67,095.

Medians per currency, never summed across currencies. Medians of similar size in currencies with very different FX rates are not consistent with real amounts in those currencies.

| Currency | Complaints | With amount | Median claimed amount |
|---|---|---|---|
| ARS | 5,402 | 5,132 | 2,521.17 |
| COP | 5,456 | 5,192 | 2,467.20 |
| MXN | 5,487 | 5,215 | 2,551.21 |
| USD | 5,431 | 5,172 | 2,563.86 |

## Method and reproducibility

- Generated by `python -m etl.analyze_demand` from `data/warehouse.duckdb` (rows: `complaints` 67,095, `customers` 150,000, `call_center_interactions` 19,677) and `docs/analysis/inputs/eval_cost_snapshot.json`. Do not edit this file by hand.
- Data as of 2026-06-18T07:56:52 (latest complaint creation date).
- `demand-report.json` is the source of truth; this page and both charts are rendered from it.
- Labels: measured (measured on the dataset, here or in the cited source), assumed (an input with no data behind it), simulated (from the offline eval), projection (arithmetic over the others), design-argument (reasoning, not a number).
- Refresh the eval inputs after `python -m eval.run_eval` with `python -m etl.analyze_demand --refresh-eval-snapshot`.
