# Factored AI & Data Hackathon 2026 — Transaction Dispute Resolution Agent

AI-first banking customer service system for LATAM Bank: a customer reports an unrecognized
transaction in a chat, the system verifies it against their own transaction history, resolves
eligible cases automatically under an explicit policy, asks clarifying questions or abstains on
ambiguous cases, and hands off complex/high-risk cases to a human agent with a structured,
verified case file. Built for the [Factored AI & Data Hackathon 2026](docs/challenge/challenge-brief.md).

Full planning record (architecture decisions, research, design rationale): `.workspace/features/dispute-agent/`
(`plan.md`, `todo.md`, `findings.md`, `DESIGN.md`). This README summarizes what's relevant to run,
evaluate, and understand the shipped system.

## Architecture at a glance

A hybrid design: a trained classifier (decision support only) + a deterministic state machine
(all permission/policy decisions) + an LLM scoped strictly to natural-language understanding and
generation. Single FastAPI process, offline-first data layer, vanilla HTML/CSS/JS frontend (no
build step).

```
Customer message
      |
      v
app/llm.py::extract_entities()        <- LLM (Claude Haiku 4.5), NLU only, JSON-schema output
      |
      v
app/state_machine.py::evaluate_case() <- deterministic guard function
      |         |
      |         +--> app/transactions.py (session-scoped fuzzy match, no customer_id param)
      |         +--> app/policy.py (AD-11 policy table: match tolerance, auto-resolve
      |         |     eligibility, forced-escalation triggers)
      |         +--> app/classifier.py (priority prediction — decision SUPPORT only,
      |               can only ADD an escalation reason, never auto-resolve or override)
      |
      v
resolved_auto | clarifying | escalated
      |
      v
app/llm.py::generate_response()       <- LLM, NLG only, grounded in build_prompt_context()'s
                                          closed allowlist (never raw DB rows/PII)
```

**Why this split:** the challenge requires permissions/policy enforced *in code*, not in a model
prompt. The LLM never decides whether to auto-resolve or escalate — it only extracts structured
entities from free text and phrases the (code-decided) outcome in natural language. See
`.workspace/features/dispute-agent/plan.md` (Architecture Decisions AD-1 through AD-11) for the
full rationale, alternatives considered, and the three-experts/Codex adversarial review record.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION / DATA_BUCKET
# (dataset access — see docs/challenge/challenge-brief.md) and ANTHROPIC_API_KEY
# (LLM calls — see "Known limitations" below if you don't have one yet).
```

## Running it end to end (reproducible setup)

```bash
# 1. Offline ETL: pull the dataset from S3 into a local DuckDB warehouse (one-time, ~10-15 min;
#    re-runnable; never touches the deployed runtime — see AD-2)
python -m etl.extract
python -m etl.quality_checks       # data-quality + lineage report -> data/lineage_manifest.json

# 2. Build the sanitized demo fixture (3 personas selected programmatically from real complaints
#    data) and demo credentials — this is the ONLY data the app ever reads at runtime
python -m etl.build_fixture        # -> data/fixture.duckdb, data/demo_users.json

# 3. Train + evaluate the priority classifier (decision-support signal, Milestone 3)
python -m etl.train_classifier     # -> data/classifier.joblib, data/classifier_split.json
python -m etl.evaluate_classifier  # -> data/classifier_eval_report.json

# 4. Run the app
uvicorn app.main:app --reload --port 8000
# Open http://127.0.0.1:8000 — log in with one of the 3 demo personas (see data/demo_users.json
# after step 2; usernames are cliente.claro / cliente.ambiguo / cliente.escalado)

# 5. Tests, lint, eval harness
pytest                              # 136 tests
ruff check .
python -m eval.run_eval             # -> data/eval_report.json (see "Evaluation results" below)
```

Steps 1-3 require AWS credentials (dataset access) and are offline/one-time. Step 4 (the deployed
app) needs **zero** AWS credentials at runtime — verified by `tests/test_main.py::test_app_serves_with_aws_env_unset`
and by unsetting `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` locally and confirming the app still serves.

## The 3 required demo cases

All 3 are backed by real complaints from the dataset (real `customer_id`, `claimed_amount`,
`currency`, `creation_date`); see `etl/build_fixture.py`'s module docstring for the exact,
documented selection criteria and a real finding from building it (complaints and transactions
turned out to be independently generated synthetic data with no deliberate cross-linkage — a
real amount+date match exists for fewer than 1 in 1,000 real complaints).

| Persona | Case | Outcome |
|---|---|---|
| `cliente.claro` | Normal resolution | Confident match, eligible under policy -> `resolved_auto`, simulated provisional credit, reference number |
| `cliente.ambiguo` | Ambiguous / abstain | No real matching transaction -> up to 2 clarifying rounds -> escalates with a structured handoff |
| `cliente.escalado` | Human escalation | Confident match, but fails eligibility (amount/fraud threshold) -> `escalated` with a structured handoff (facts/actions/evidence/open questions) |

Each case works in Spanish and Portuguese (toggle in the chat header) — see "Known limitations"
for what the Portuguese toggle does and does not validate.

## Evaluation results

`data/eval_report.json` (regenerate with `python -m eval.run_eval`) and `data/classifier_eval_report.json`
(regenerate with `python -m etl.evaluate_classifier`) hold the full reports. Headline numbers as
last generated in this environment:

**Classifier (priority-at-intake, decision support only)** — chronological split (train=11,543,
test=2,037, split date 2026-01-06), fixed majority-class baseline vs. RandomForestClassifier on
structured intake-time features:

| | macro-F1 | Low recall | Medium recall | High recall | Critical recall |
|---|---|---|---|---|---|
| Baseline (majority class) | 0.1662 | 0.00 | 1.00 | 0.00 | 0.00 |
| Proposed classifier | 0.2448 | 0.23 | 0.45 | 0.26 | 0.06 |

Delta: **+0.0786 macro-F1**, reported honestly (a modest improvement, not inflated). The
classifier is wired as decision support only — a `Critical` prediction can only ever *add* a
reason to escalate; it structurally cannot cause an auto-resolution or override any other AD-11
condition (proven by an exhaustive 16-combination test in `tests/test_policy_not_overridden.py`).

**Conversation/system eval** (`eval/run_eval.py`) — ⚠️ **explicitly OFFLINE/SIMULATED**, not a
measured-production result (see "Known limitations" — no real `ANTHROPIC_API_KEY` was available
in the build environment, so this harness runs against a deterministic mocked LLM client, not a
real one). 10 cases (3 required demo cases × 2 languages + 6 adversarial/failure-mode fixtures):

- **Unsafe outcomes: 0 / 10.**
- Safe automated resolution rate: 0.2 (2/10 — most cases are deliberately ambiguous/escalated/
  adversarial by construction, so a low rate here reflects the test mix, not system quality).
- Containment rate: 0.2222 (2/9 concluded cases).
- Pipeline latency (excludes real LLM network time): p50 0.099s, p95 0.76s.
- Estimated cost (Haiku 4.5 list pricing, not measured billing): ~$0.00054/attempted case,
  ~$0.0027/successful resolution.

## Known limitations (disclosed, not hidden)

- **`ANTHROPIC_API_KEY` was never provisioned in the build environment.** Every LLM call site,
  the 3 required conversation cases, the Portuguese toggle, and the eval harness are built and
  tested against a deterministic mocked Anthropic client (matching the model's real JSON output
  contract) — verified correct, but not the same as a real-model quality check. **Before a real
  demo/submission:** supply a real key in `.env`, manually sanity-check response quality (the
  documented Task 2.3b checkpoint — currently unresolved for exactly this reason), and re-run
  `eval/run_eval.py` for a genuinely measured report. The app already degrades gracefully without
  a key (a missing/invalid key forces escalation with a deterministic fallback message rather
  than crashing — verified live, not just in tests).
- **Demo-credentials login is simulated, not production identity verification.** `data/demo_users.json`
  provisions test accounts distinct from any dataset field (never `document_number` or similar) —
  this satisfies the organizer's "a customer number alone does not prove identity" rule as a
  *demonstration* of a trusted-identity-service boundary, not a production-grade auth system.
- **Portuguese support is simulated via the LLM's general multilingual capability.** The dataset
  contains zero Portuguese rows — no training or held-out evaluation claim is made for Portuguese
  specifically. The structured handoff record's `actions_taken`/`open_questions` text (deterministic,
  code-generated, not LLM output) stays in Spanish regardless of the toggle — an internal
  agent-facing audit artifact, not customer-facing content.
- **Single-host deployment, no load/concurrency testing.** Designed for sequential demo/judge
  traffic on one machine — stated explicitly, not silently assumed away.
- **The rolling-aggregate classifier feature (AD-6 stretch goal) was not attempted**, per the
  plan's own pre-agreed cut order for time pressure (this was the first thing marked cuttable).
- **Data retention / access controls / capacity limits** are demonstrative defaults for a
  hackathon submission with only synthetic data — not production-grade policies. Logs and the
  SQLite session/case store are local to the single deployed instance only.

## Remaining production-deployment work

This submission demonstrates the architecture and the 3 required workflows end to end; a real
production deployment of this system would still need: real identity verification (replacing the
demo-credentials fixture), a managed/replicated database instead of local SQLite, multi-instance
deployment with load balancing, a monitoring/alerting stack (this app logs structured events with
correlation IDs per `app/cases.py`'s `events` table, but nothing ships those anywhere external),
empirically-validated (not hackathon-default) policy thresholds in `app/policy.py`, and a real
measured evaluation once a production LLM key and traffic are available.

## Repo layout

```
app/            FastAPI backend — auth, state machine, policy, LLM boundary, classifier wiring
etl/            Offline ETL: extraction, quality checks, fixture generation, classifier training
eval/           Eval harness (Milestone 5)
static/         Frontend (vanilla HTML/CSS/JS, no build step — AD-1)
tests/          pytest suite (136 tests)
support.py      Shared test/eval mock helpers (no pytest dependency — used by eval/ too)
docs/           Challenge requirements digest
data/           Local ETL artifacts, fixture, trained model (gitignored — never commit raw data)
.workspace/     Full planning record: plan.md, todo.md, findings.md, DESIGN.md (gitignored)
```

## Submission checklist (per challenge rules)

- [x] Public GitHub repo named `factored-hackathon-2026-jagusgelos`
- [ ] Deployed tool link — pending a deployment-platform decision (Fly.io vs. Render; Fly.io
      requires a credit card on file — see `.workspace/features/dispute-agent/plan.md` Open Questions).
      Deploy artifacts are ready (`Dockerfile`, `docker-entrypoint.sh`, `fly.toml`, `render.yaml`)
      and locally verified (a real Docker build + a real container-restart persistence test) —
      see `DEPLOY.md` for the exact remaining commands and what's proven vs. still pending.
- [ ] 4-6 slide presentation
- [ ] Short mandatory video pitch demonstrating the working solution, recorded against `localhost`
      (never the deployed link, per AD-7 — avoids demo-day network flakiness in the recorded artifact)
