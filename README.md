# Factored AI & Data Hackathon 2026 — Transaction Dispute Resolution Agent

AI-first banking customer service system for LATAM Bank: a customer reports an unrecognized
transaction in a chat, the system verifies it against their own transaction history, resolves
eligible cases automatically under an explicit policy, shows the customer their own charges to pick
from when the report is ambiguous, declines requests outside its scope, and hands off complex/high-risk cases to a human agent with a structured,
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
confirming (AD-12) | selecting | escalated
      |   confirming -> resolved_auto only on an explicit "yes" + policy re-check
      |   selecting  -> the customer's OWN charges shown as cards (app/transactions.py::
      |                 list_own_charges); a tap is accepted only if that id was offered AND is
      |                 theirs, then AD-11 decides resolve vs escalate; "No está en la lista"
      |                 escalates with the list shown as evidence
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

# 2. Build the demo fixture from the local warehouse (no AWS needed for this step): ONE real
#    customer selected by documented criteria + labeled synthetic charges, and the demo
#    credentials: this is the ONLY data the app ever reads at runtime
python -m etl.build_fixture        # -> data/fixture.duckdb, data/demo_users.json

# 3. Train + evaluate the priority classifier (decision-support signal, Milestone 3)
python -m etl.train_classifier     # -> data/classifier.joblib, data/classifier_split.json
python -m etl.evaluate_classifier  # -> data/classifier_eval_report.json

# 4. Run the app
uvicorn app.main:app --reload --port 8000
# Open http://127.0.0.1:8000 and press "Autocompletar" (demo account cliente.demo, password in
# data/demo_users.json after step 2)

# 5. Tests, lint, eval harness
pytest                              # 216 tests
ruff check .
python -m eval.run_eval             # -> data/eval_report.json (see "Evaluation results" below)
```

Steps 1-3 require AWS credentials (dataset access) and are offline/one-time. Step 4 (the deployed
app) needs **zero** AWS credentials at runtime — verified by `tests/test_main.py::test_app_serves_with_aws_env_unset`
and by unsetting `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` locally and confirming the app still serves.

## The required scenarios, on one customer

The challenge asks for three *situations* inside the workflow (automated resolution, an ambiguous
or unsupported request, a human escalation). They are not customer types, so the demo has **one**
customer and every scenario comes from what that customer says or taps.

**Demo data.** `etl/build_fixture.py` picks one real customer from the warehouse by documented
criteria (Colombia, active, COP transactions, most purchases with a named merchant, no prior
dispute complaints): Víctor, Cartagena, 6 real purchases in the extracted window. Because ~6 real
rows cannot cover a duplicated charge or the fraud-score gate, 8 **team-generated** charges are
added on the same customer and product, each labeled `_is_synthetic = true` with
`_source_file = 'synthetic'` and designed for one scenario. Colombia was chosen because it is the
market whose transactions are in the local currency: every México-customer transaction in the
dataset is in USD (there is no MXN transaction at all), a data finding in its own right.

| Scenario | How to trigger it | Outcome |
|---|---|---|
| Automated resolution (typed) | "No reconozco un cargo de 38.500 pesos del 14 de junio" | Confident match (Uber) -> the agent names merchant/amount/date and asks (`confirming`, with "Sí, es ese" / "No es ese" buttons) -> "yes" + policy re-check -> `resolved_auto`, simulated provisional credit + reference |
| Automated resolution (picked) | "Se me perdió un monto, mostrame mis cargos" -> tap a small charge | The customer's own charges as cards (`selecting`); tapping one is the customer's explicit identification (the AD-12 confirmation) -> policy -> `resolved_auto` |
| Ambiguous: duplicated charge | "Me cobraron dos veces un taxi de 27 mil" | Two matches (AD-11 Row 3) -> only those two cards are shown -> the customer picks one |
| Ambiguous: not in the list | Any list -> "No está en la lista" | `escalated` with the charges shown as evidence and an open question for the agent |
| Unsupported request | "¿Cuál es mi saldo?" | Declines and says what this channel does; no guess, no state change |
| Human escalation (policy) | "No reconozco una compra en Tienda Online Global", or tap Boutique Moda / Tienda Don José | Fails AD-11 (fraud score 91 / ~610 USD / Pending) -> `escalated` with a structured handoff (facts, actions, evidence, open questions) |
| Human escalation (request) | "Hablar con una persona" button or asking for it | `escalated` immediately |

A turn that brings a new detail (amount, date, merchant) never spends a clarification round; after
two rounds with nothing new, or more than 6 free-text reports in one case, the case escalates
(greetings and button taps do not count). A greeting gets an introduction of what the agent can do.
A transaction is credited at most once: disputing an already-credited charge again, in any case,
goes to a person with the earlier case as evidence (checked in code and enforced by a unique index). Everything works in Spanish and Portuguese (toggle in the chat header); see
"Known limitations" for what the Portuguese toggle does and does not validate.

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
measured-production result: the harness runs scripted multi-turn conversations against a
deterministic mocked LLM client so every run is reproducible. 18 cases: 6 required scenarios
(typed resolution, picked resolution, duplicated charge picked, not in list, policy escalation,
human request) × 2 languages + 6 adversarial/failure-mode fixtures (missing data, prompt injection,
LLM outage, mixed-language input, a tampered tap on a charge that was not offered, re-disputing an
already-credited charge). Each scenario runs against its own app database:

- **Unsafe outcomes: 0 / 18.**
- Safe automated resolution rate: 0.33 (6/18; the mix is mostly escalation/adversarial by design).
- Containment rate: 0.375 (6/16 concluded cases).
- Pipeline latency (excludes real LLM network time): p50 0.12s, p95 0.24s.
- Estimated cost (Haiku 4.5 list pricing, not measured billing): ~$0.0008/attempted case,
  ~$0.0025/successful resolution.

The real-model behavior is checked separately: the Playwright walkthrough and manual runs go
through Claude Haiku 4.5 end to end, and bugs they surfaced (fenced JSON, a currency lost between
turns, over-strict fact checks on natural wordings) are pinned by regression tests.

## Known limitations (disclosed, not hidden)

- **The system eval is simulated; real-model quality is checked by hand, not measured.** The app
  runs against Claude Haiku 4.5, and the walkthrough plus manual sessions exercise it end to end,
  but `eval/run_eval.py` uses a mocked client for reproducibility, so its latency/cost figures
  exclude the real model. Without a key the app still degrades gracefully: every LLM failure
  forces escalation with a deterministic fallback message (verified live, not just in tests).
- **The demo customer's history is partly synthetic.** 6 of the 14 charges are real dataset rows;
  8 are team-generated to cover every scenario and are labeled as such in the fixture
  (`_is_synthetic`, `_source_file = 'synthetic'`). The dataset window is a snapshot ending
  2026-06-17, so relative dates ("ayer") are resolved against `DATA_AS_OF` (2026-06-18), not the
  wall clock.
- **Demo-credentials login is simulated, not production identity verification.** `data/demo_users.json`
  provisions test accounts distinct from any dataset field (never `document_number` or similar) —
  this satisfies the organizer's "a customer number alone does not prove identity" rule as a
  *demonstration* of a trusted-identity-service boundary, not a production-grade auth system.
- **Login throttling is per-process demo-grade.** `/auth/login` locks a username after 5 failures
  (and an IP after 20) within 15 minutes with exponential backoff (SQLite-backed, `app/ratelimit.py`).
  The Dockerfile runs uvicorn with `--proxy-headers --forwarded-allow-ips "*"` so the per-IP bucket
  keys on the client address the Fly/Render proxy forwards (safe only because the container is
  reachable solely through that proxy; a client can still spoof the header to rotate IP buckets,
  which the per-username limit does not depend on). A per-username lockout lets an attacker
  temporarily lock a known account out (capped at 15 min).
  The login screen has an "Autocompletar" button that fills in the demo account; set
  `SHOW_DEMO_CREDENTIALS=0` to hide it on any deployment that is not a labeled demo.
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
tests/          pytest suite (216 tests)
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
