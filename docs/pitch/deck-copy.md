# Pitch deck copy (6 slides, 16:9)

Audience: a bank investor, and Factored judges reading the deck as a standalone PDF.
Split: 60% product, 40% technical; every technical slide ends on the value it delivers.
Every number below has a `Source:` line and an honesty label:
MEASURED (computed from the dataset or timed on real runs), SIMULATED (offline eval with a
mocked model), ASSUMED (a demo assumption), DESIGN ARGUMENT (a reasoned choice, not a measurement).

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

**Kicker:** The product

**Headline:** A dispute agent that knows when to act, when to ask, and when to step aside.

**Three moments (one real app screenshot each, Spanish UI with an English caption):**

1. **Resolves, with proof.** The customer explains an Uber charge they never made. The agent checks
   the record (online purchase, no other charge at that merchant) and grants a provisional credit
   with a reference, in the same chat. Screen: the case panel (resolved, three verified steps,
   reference).
   Caption: "The record backs the claim, so a provisional credit lands in the same chat."
2. **Asks when it is unclear.** "Me cobraron dos veces un taxi": the agent shows only the
   customer's own matching charges as cards and lets them pick. It never guesses.
   Caption: "Two matching charges: the customer picks from their own. It never guesses."
3. **Steps aside, file ready.** A charge that fails the policy goes to a person, with the
   customer's statement, the verified facts and the open questions already in the file. Screen:
   the advisor's case file ("Hechos verificados", from the record).
   Caption: "Fails the policy: a person gets verified facts, the customer's account and open questions."

**Strip:** Spanish and Portuguese · credits and card blocks are simulated · live at
factored-hackaton-latest.onrender.com

Source: README "The required scenarios, on one customer" (rows: Automated resolution (typed),
Ambiguous: duplicated charge, Human escalation (policy)); screenshots captured from the running app
(milestone 2).

---

## Slide 3 · How (trust by design)

**Top bar:** Trust by design (no kicker on slides 3 to 5; the headline carries the slide).

**Headline:** The model reads. The code decides.

**Diagram (left to right):** Customer message → LLM (Claude Haiku 4.5): extracts facts only →
State machine + policy in code: decides → Verified ledger (the customer's own charges only) →
Outcome: resolve · ask · hand off. The LLM also writes the reply, but only from facts the code
allows (closed allowlist).

**Three rules (each with its value):**
- **The LLM only extracts and phrases.** It never decides to pay. → No refund comes from the model.
- **Policy and permissions live in code.** Lookups take no customer id from the caller; the
  session decides whose ledger is read. → No lookup can reach another customer's ledger.
- **Verified facts only.** A convincing explanation is never enough on its own: a credit needs
  evidence in the record. → A persuasive claim the record contradicts still goes to a person.

All three rules are DESIGN ARGUMENT (enforced in code; Slide 5 shows them on a simulated suite).

**Proof chip:** The priority classifier can only add a reason to escalate. An exhaustive sweep over
every dispute reason and all 128 combinations of the other conditions proves it never causes a
credit. Source: README "Evaluation results" (`tests/test_policy_not_overridden.py`).

**Chip:** 990 automated tests. Source: README "Running it end to end" step 5 and "Repo layout";
re-checked with `pytest --collect-only` on 2026-10-04 (990 collected).

Source for the architecture: README "Architecture at a glance" and "Dispute policy" (AD-13);
`docs/architecture-decisions.md` AD-3, AD-5, AD-13; `CONFORMANCE.md` rows 2, 3, 5, 15.

---

## Slide 4 · Data & ML rigor

**Headline:** Built on the full challenge dataset, with every shortcut explained.

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
  plus 8 labeled synthetic charges.
- Why: the runtime never touches S3 (privacy, AD-2); the run is reproducible; it deploys on a free
  plan; and a dispute only needs the customer's recent ledger. DESIGN ARGUMENT.
Source: `data/extraction_manifest.json` (`windowed_date_range`, `tables[].row_count`); `etl/extract.py`
(`DEFAULT_WINDOW_DAYS = 30`);
`docs/challenge/challenge-brief.md` ("transactions: 5,000,000 rows"); `docs/architecture-decisions.md`
AD-2; README "Demo data" and "Known limitations" (6 of the 14 charges are real dataset rows).

**Panel C, priority classifier vs baseline:**
- Chronological split: train 11,543, held-out test 2,037 (split date 2026-01-06). MEASURED.
- Macro-F1: majority-class baseline 0.1662 → RandomForest 0.2448 (+0.0786). MEASURED.
- A modest gain, reported as such. The classifier only supports decisions: it can add a reason to
  escalate, never approve a credit.
Source: `data/classifier_eval_report.json` (`split`, `baseline.macro_f1`, `proposed.macro_f1`,
`macro_f1_delta`); README "Evaluation results".

**Value line:** The full dataset where it matters, a small fixture where privacy and
reproducibility matter more.

---

## Slide 5 · Proof

**Headline:** 40 scripted attacks and edge cases. Zero unsafe outcomes.

**Hero chip:** 0 / 40 unsafe outcomes · SIMULATED
Source: `data/eval_report.json` (`unsafe_outcomes.count = 0`, `of_attempted = 40`); README
"Evaluation results".

**System comparison table (same 40 cases, SIMULATED):**

| System | Correct resolutions (of 6) | Unsafe credits (of 34) | Correct transfers (of 30) |
|---|---|---|---|
| Our hybrid | 6 | 0 | 30 |
| Always escalate (safety anchor) | 0 | 0 | 30 |
| No evidence check (ablation) | 6 | 4 | 26 |

Read: always escalating is safe but loses every legitimate resolution; dropping the evidence check
pays 4 credits a person should have reviewed. The hybrid keeps both.
Source: `data/eval_report.json` (`system_comparison`); README "System-level comparison".

**Other chips:**
- Statement completeness 20 / 20: every escalated case that needed the customer's statement
  records its outcome (given, declined or unavailable) · SIMULATED.
  Source: `data/eval_report.json` (`escalation_quality.statement_completeness_rate`: count 20,
  `of_escalated_needing_a_statement` 20).
- Real Claude Haiku 4.5: the explanation turn that resolves took a median 3.2 s (1.3-6.6 s,
  8 ES/PT runs) · MEASURED (manual runs, 2026-09-30).
- Button and menu taps answer in 0.05-0.14 s with no model call (a tapped charge about 1 s) ·
  MEASURED (manual runs, 2026-09-30).
  Source: README "Evaluation results", real Haiku latency bullet.

**Caveat (always visible):** A constructed offline suite with a mocked model, written by the
policy's author. It is not a held-out workload; the only held-out evaluation is the classifier's
chronological split.
Source: README "System-level comparison", "Read this with its limits"; `data/eval_report.json`
(`system_comparison.disclosure`).

---

## Slide 6 · What's next

**Kicker:** Route to production

**Headline:** From demo to the bank's front line.

**Roadmap (four steps):**
1. **Real identity.** Replace the demo login with the bank's identity verification.
2. **Managed database.** A replicated database instead of local SQLite, across several instances.
3. **Monitoring.** Ship the structured, correlated event log to alerting.
4. **Thresholds on real traffic.** Validate the policy limits and measure the live model on real,
   held-out cases.
Source: README "Remaining production-deployment work".

**Links:**
- Live demo: https://factored-hackaton-latest.onrender.com/
- Repo: https://github.com/jAgusGelos/factored-hackathon-2026-jagusgelos

**Closing line (the product promise):** An answer in seconds, not 37 hours. Every refund backed by
evidence. Every hard case in a person's hands, with the file already complete.

---

## Checks
- No number above is absent from the cited file (checked 2026-10-04 against the files listed).
- No em-dash in this file.
