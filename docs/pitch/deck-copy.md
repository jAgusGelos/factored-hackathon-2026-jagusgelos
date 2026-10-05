# Pitch deck copy (6 slides, 16:9)

Audience: a bank investor, and Factored judges reading the deck as a standalone PDF.
Split: 60% product, 40% technical; every technical slide ends on the value it delivers.
Every number below has a `Source:` line and an honesty label:
MEASURED (computed from the dataset, or real Claude Haiku 4.5 runs), SIMULATED (offline eval with a
mocked model), DESIGN ARGUMENT (a reasoned choice, not a measurement).
Body text on the slides is 22px or larger; source footnotes, labels and the top bar are smaller
metadata. Lines marked "(not on the slide)" were cut for space and stay here as backup.

---

## Slide 1 · Why

**Kicker:** The customer's pain

**Headline:** You see a charge you never made. Then you typically wait 37 hours to hear back.

**Body:** A customer who reports a charge they do not recognize waits a median of 37 hours for
the bank's first response. One in ten waits 58 hours or more. Until then, the charge sits on their
account with no answer.

**Big number:** 37 h · median wait for a first response · MEASURED

**Supporting chips:**
- p90 58 h · MEASURED
- n = 7,567 of 12,297 "Cargo no reconocido" complaints with a recorded first response · MEASURED

Source: `docs/analysis/demand-report.md`, finding 3 and the "Human first response p50" line
(median 37.0 h, p90 58.0 h, n = 7,567 of 12,297, coverage 61.5%); also README "Demand analysis",
item 3. Measured on the challenge's LATAM Bank dataset, which is synthetic (README "Known
limitations"), not on a live bank.

**Footer (why disputes):** We picked disputes because a dispute can be checked in code against the
customer's own ledger and decided by an explicit policy. DESIGN ARGUMENT.
Source: README "Demand analysis", closing paragraph.

---

## Slide 2 · What

**Headline:** A dispute agent that knows when to act, when to ask, and when to step aside.

**Three moments (one real app screenshot each, Spanish UI with an English caption):**

1. **Resolves, with proof.** The customer explains an Uber charge they never made. The agent checks
   the record (online purchase, no other charge at that merchant) and grants a provisional credit
   with a reference, in the same chat. Screen: the case panel (resolved, three verified steps,
   reference).
   Caption: "The record backs the claim, so a provisional credit lands in seconds, in the same chat."
2. **Asks when it is unclear.** "Me cobraron dos veces un taxi": the agent shows only the
   customer's own matching charges as cards and lets them pick. It never guesses.
   Caption: "Two matching charges: the customer picks from their own. It never guesses."
3. **Protects, hands off.** The customer denies an online charge with a high fraud score: the
   agent blocks the card (simulated) before the handoff, and a person gets the customer's
   statement, the verified facts and the open questions already in the file. Screen: the
   advisor's case file, cropped to its "Acciones realizadas" section. That section lists
   "Tarjeta bloqueada preventivamente por el agente (simulado, sin movimiento de dinero)".
   The same file's "Hechos verificados" come from the record.
   Caption: "Likely fraud: the card is blocked first, then a person gets a complete file of verified facts."

**Strip:** Spanish and Portuguese · Credits and card blocks are simulated (the live URL is on slide 6)

Source: README "The required scenarios, on one customer" (rows: Automated resolution (typed),
Ambiguous: duplicated charge, Human escalation (policy)); the card block is AD-14
(`docs/architecture-decisions.md`, `CONFORMANCE.md` row 23). Screenshots re-captured on 2026-10-05
from the running app on main's fixture (the handoff screen shows the block notice).

---

## Slide 3 · How (trust by design)

**Top bar:** Trust by design (no kicker on slides 3 to 5; the headline carries the slide).

**Headline:** The model reads. The code decides.

**Diagram (left to right):** Customer (ES · PT) → THE MODEL READS: Claude Haiku 4.5, "Extracts the
facts. Never decides." → THE CODE DECIDES: State machine + policy, "Pays only on evidence. The
classifier can only escalate." (reads the "Customer's own ledger only") → ✓ Resolve · ? Ask · →
Hand off. Dashed return path: "Replies use only facts the code allows" (closed allowlist).

**Three rules (each with its value):**
- **The LLM only extracts and phrases.** It never decides to pay. → No refund comes from the model.
- **Policy and permissions live in code.** Lookups take no customer id from the caller; the
  session decides whose ledger is read. → No lookup can reach another customer's ledger.
- **Verified facts only.** A convincing explanation is never enough on its own: a credit needs
  evidence in the record. → A persuasive claim the record contradicts still goes to a person.

All three rules are DESIGN ARGUMENT (enforced in code; Slide 5 measures the outcome on a held-out set with the real model).

**Footnote (source line):** The priority classifier can only add a reason to escalate. An exhaustive sweep over
every dispute reason and all 128 combinations of the other conditions proves it never causes a
credit. Source: README "Evaluation results" (`tests/test_policy_not_overridden.py`).

**Footnote (source line):** 1,121 automated tests. Source: `pytest --collect-only` on main merged into
this branch, 2026-10-05 (1,121 collected; the README still says 990, written before the fraud and
measured-eval features).

Source for the architecture: README "Architecture at a glance" and "Dispute policy" (AD-13);
`docs/architecture-decisions.md` dispute-agent AD-3, AD-5, AD-13 to AD-15; `CONFORMANCE.md` rows 2, 3, 5, 15, 23, 24.

---

## Slide 4 · Data & ML rigor

**Headline:** Honest models: we ship only what the numbers back.

**Panel A, ETL with contracts:**
- An offline DuckDB ETL with a schema contract per table (9 tables) checks row counts, duplicate
  keys, null rates and foreign keys, and writes a lineage manifest.
- Both foreign keys checked from complaints have 0 orphaned rows. MEASURED.
Source: `etl/schema_contract.py` (9 `TableContract`s), `data/lineage_manifest.json`
(`quality_checks.foreign_keys`: complaints.customer_id → customers, 0 of 67,095;
complaints.affected_product_id → products, 0 of 44,570).

**Panel B, why a subset:**
- Transactions: 130,690 of 5,000,000 rows, plus call-center interactions and transcripts, in a
  30-day window (2026-05-18 to 2026-06-17). Complaints in full: 67,095 rows. MEASURED.
- The live app reads only a small fixture: one dataset customer (6 of their real dataset charges)
  plus 10 labeled synthetic charges. On the slide: "Live app: 1 customer, 16 charges (6 real, 10
  synthetic)", a plain count with no label.
- Why: the runtime never touches S3 (privacy, AD-2); the run is reproducible; it deploys on a free
  plan; and a dispute only needs the customer's recent ledger. DESIGN ARGUMENT.
Source: `data/extraction_manifest.json` (`windowed_date_range`, `tables[].row_count`); `etl/extract.py`
(`DEFAULT_WINDOW_DAYS = 30`);
`docs/challenge/challenge-brief.md` ("transactions: 5,000,000 rows"); `docs/architecture-decisions.md`
AD-2; `data/fixture.duckdb` re-counted 2026-10-05 (6 of the 16 charges are real dataset rows) and `docs/eval/measured-eval.md` "Limitations" (the root README still says 14 charges).

**Panel C, fraud model vs the bank's score:**
- Data: 2 years of transactions, 2,951,642 rows (2024-06-17 to 2026-06-17), split by date into train (1,528,441 rows, legitimate rows sampled at 10%), validation and test. MEASURED. (On the slide: "2,951,642 transactions over 2 years, split by date".)
- Test PR-AUC on 451,556 scored charges (391 frauds): the bank's `fraud_score` 0.720, our stacked
  model 0.707 (paired 95% CI of the difference [-0.024, -0.005]). It does not beat the bank's
  score. MEASURED.
- Shipped instead: the cost-justified gate `fraud_score > 30`. On the test fold's proxy
  population its 48 escalations are all fraud, 18 fewer legitimate charges sent to a person than
  `>= 30`, with the same 48 frauds caught. MEASURED (costs partly ASSUMED). The model's estimate
  reaches only the advisor's handoff and never decides.
Source: `docs/ml/fraud-model.md` ("Data", "Results on the test fold", "Operating threshold by
cost"); `docs/policy/fraud-gate.md`; `docs/architecture-decisions.md` AD-15; `CONFORMANCE.md` row 24.

**Footnote, priority classifier:** macro-F1 0.2448 against 0.2434 for 100 shuffled-label refits,
permutation p = 0.45: no measured lift. It stays as an escalation-only signal and can never
credit. MEASURED. (On the slide: "no measured lift (p = 0.45) and can only escalate".) Earlier
copy compared it with the majority baseline (0.1662 vs 0.2448); the signal-ceiling test shows that
gain is what any class-balanced guesser gets, so it is no longer claimed.
Source: `docs/ml/fraud-model.md` "Priority classifier: signal ceiling"; AD-15.

(On the slide: "Shipped instead · MEASURED: Gate fraud score > 30, chosen by cost (some inputs
assumed). The model only informs the advisor.")

**Value line:** Our model lost to the bank's score, so it never decides: the rule the numbers back does.

**(not on the slide)** Full complaints and a 30-day ledger for analysis; a small fixture where
privacy matters more.

---

## Slide 5 · Proof

**Top bar:** Evaluation on the real model

**Headline:** 48 held-out conversations on the real model. Zero unsafe outcomes.

**Hero chip:** 0 / 48 unsafe, in each of 3 runs · MEASURED
Source: `docs/eval/measured-eval.md` (v2 results table); `docs/eval/measured-eval-summary.json`
(`systems.hybrid.runs[].unsafe`, `variability.unsafe_rate`). 24 situations x ES/PT, customer
messages written blind by a model that never saw the policy or prompts, labels committed before
the first run, real Claude Haiku 4.5 through the real app.

**Supporting lines (all MEASURED, same source):**
- 18 / 18 resolvable cases resolved (safe automated resolutions, every run).
- 26 / 26 card-block calls right (block or no block, on the 26 escalated cases with a block label; 14 of the 28 labeled escalations expect a block; every run).
- 3.0 s median reply (typed turn p50, range 2.68-3.44 over runs), p95 7.7 s.
- $0.004 per case ($0.0040, measured tokens at Haiku 4.5 list price).

**System comparison table (same 48 cases, MEASURED):**

| System | Correct (of 48) | Unsafe (of 48) | Unneeded transfers (of 18) |
|---|---|---|---|
| Our hybrid (Claude Haiku 4.5, 3 runs) | 43 | 0 | 0 |
| Regex extractor ("extraction swapped for rules, 1 run"; every other model call unchanged) | 43 | 0 | 0 |
| Always escalate (the safety anchor, scored from labels, as on the slide) | n/a | 0 | 18 |

"Correct" means final state, escalation reason and credit all match the label (0.896).
Source: `docs/eval/measured-eval.md`, "Results (v2)" and "Reading the comparison".

**Read:** Escalating everything is safe but helps no one. The 5 misses moved no money (failure analysis: S11 x2 and S09-es escalated with a generic reason,
S04 x2 left open by the script; none credited). Secondary line, SIMULATED (on the slide: "Mocked-model suite: without the evidence check, 6 unsafe
credits."): on the offline suite with a mocked model (48 constructed cases), dropping the evidence
check pays 6 unsafe credits; the hybrid pays 0. Source: `data/eval_report.json` `system_comparison` (regenerated with
`python -m eval.run_eval` on 2026-10-05; pinned by `tests/test_system_comparison.py`).

**(not on the slide)** The regex baseline is 21% cheaper and about 0.5-0.8 s faster at p50; at
this size the difference in correctness is within run-to-run noise.

**Caveat (always visible):** one customer's account, messages written by a model that never saw
the policy, labels fixed before the first run. 0 of 48 bounds the unsafe rate below about 7%
(Wilson 95% upper bound), not at zero.
Source: `docs/eval/measured-eval.md` "Results (v2)" (Wilson line) and "Limitations".

**Replaced:** the previous headline "40 scripted attacks and edge cases. Zero unsafe outcomes."
(SIMULATED) and the manual-run latency (3.2 s over 8 runs, 2026-09-30); the measured eval
supersedes both.

---

## Slide 6 · What's next

**Kicker:** Route to production

**Headline:** From demo to the bank's front line.

**Roadmap (four steps):**
1. **Real identity.** The bank's identity verification replaces the demo login.
2. **Managed database.** Replicated storage across several instances, not local SQLite.
3. **Monitoring.** The structured, correlated event log feeds alerting.
4. **Real traffic.** Thresholds and the live model re-validated on real customer traffic.
Source: README "Remaining production-deployment work".

**Links:**
- Live demo: https://factored-hackaton-latest.onrender.com/
- Code: https://github.com/jAgusGelos/factored-hackathon-2026-jagusgelos

**Closing line (the product promise):** An answer in seconds, not 37 hours. Every refund backed by
evidence. Every hard case in a person's hands, with the file already complete.

---

## Checks
- No number above is absent from the cited file (checked 2026-10-05 against the files listed).
- No em-dash in this file.
