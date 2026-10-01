# Architecture decisions

The decisions behind this repo, condensed from the planning record. The full record (research,
expert deliberations, audit transcripts) lives in a gitignored `.workspace/` folder that only
exists on the author's machine, so this file is the readable, versioned copy. Each entry ends with
a `Source:` line that points back to that local record for traceability.

Decisions were taken per feature, and each feature plan numbers its own decisions from AD-1. The
sections below keep those numbers, namespaced by feature.

## How to read AD citations in the code

- A citation that names its feature (`usability-s2 AD-1`, `statement-before-handoff AD-4`) points
  to that feature's section below.
- A bare `AD-n` belongs to the feature named in the module's docstring or in the comment around
  it. When neither names a feature, it is a **dispute-agent** decision: AD-1 to AD-13 are the core
  product decisions, and most citations in `app/` and `tests/` mean those.
- "plan.md AD-x" in a code comment refers to the local planning record of the feature that
  module belongs to; the matching entry here holds the same decision.
- The citations in code are not rewritten to the namespaced form. That keeps the code unchanged,
  at the cost of the context step above.

## dispute-agent

The core product: chat dispute flow, policy, data layer, auth, classifier, UI and deployment.
Planned 2026-09-28 (`.workspace/features/dispute-agent/plan.md`); AD-12 was added on 2026-09-28
after Milestone 6. AD-13 was decided on 2026-09-30 without a plan section (see its entry).

### AD-1: Single-process FastAPI backend serving a static HTML/JS frontend

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** One developer, about a week, and no room for failures that only show up on demo
  day.
- **Options:** a separate frontend build (React or Vue) plus an API; one FastAPI process serving
  both.
- **Decision:** one FastAPI process serves the JSON API and the static chat page. No SPA
  framework, no frontend build step, no second dev server.
- **Consequences:** static assets live under `static/` and are served by `StaticFiles`. No
  independent frontend scaling, disclosed as a limitation.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-1 (line 66).

### AD-2: Two-phase offline-first data layer; the runtime ships a sanitized fixture

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** `transactions` alone is about 808 MB and 5M rows across 1097 S3 partitions; live
  queries at request time risk network failures during a one-shot demo recording.
- **Options:** live S3 or DuckDB `httpfs` queries at runtime; deploying the full local warehouse;
  deploying a small sanitized fixture.
- **Decision:** an offline, re-runnable DuckDB ETL (`etl/extract.py`) pulls the needed partitions
  with Hive-partition pruning into a local warehouse, used for validation, classifier training and
  building a small sanitized demo fixture (`etl/build_fixture.py`). The app reads only that fixture
  and holds no AWS credentials at runtime.
- **Consequences:** refreshing the data means re-running the ETL; the fixture must stay in sync
  with the eval personas. `data/` is gitignored.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-2 (line 75).

### AD-3: Hand-rolled deterministic state machine; permissions enforced in code, never in the prompt

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** the brief requires policy enforced outside model prose. Complaints have no foreign
  key to transactions, so the disputed charge is found by matching against the customer's own
  history, a confused-deputy risk if not scoped.
- **Options:** a state-machine library (`transitions`); a hand-rolled transition table with one
  guard per transition.
- **Decision:** a small explicit state table with unit-tested guards. Functions that read a
  customer's data take no `customer_id` a caller could fill in: the verified session is the only
  source of identity. Thresholds are not decided here; guards look them up in the AD-11 policy.
- **Consequences:** `app/state_machine.py` and its tests; the policy and its enforcement are
  tested separately.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-3 (line 84).

### AD-4: Demo-credentials fixture and an opaque server-side session token

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** the organizer's rule: a national ID or customer number alone does not prove
  identity. Checking two dataset fields was still only knowledge of dataset attributes.
- **Options:** JWT sessions; a `customer_id` plus `document_number` check; a separate
  demo-credentials fixture acting as a trusted test identity service.
- **Decision:** `data/demo_users.json`, not derived from customer data, maps test accounts to
  real customer ids with a demo credential. Login checks it and mints an opaque
  `secrets.token_urlsafe(32)` token; SQLite stores only its hash, the customer id and the expiry.
  The cookie is `HttpOnly`, `Secure` and `SameSite=Lax`.
- **Consequences:** a simulated identity service, disclosed as such; `app/auth.py` and a
  `sessions` table.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-4 (line 93).

### AD-5: Explicit LLM privacy and data-minimization boundary

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** the fixture holds PII-shaped fields (email, phone, address, document number,
  credit score) and full transaction rows; nothing said what may reach a prompt.
- **Options:** ad-hoc prompt construction; one allowlist function.
- **Decision:** every prompt is built through `app/llm.py::build_prompt_context`, which accepts
  only an allowlist of minimal facts (amount, date, merchant category and similar). No other code
  path interpolates customer data into a model call.
- **Consequences:** a conformance test asserts no PII field name appears in any prompt.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-5 (line 102).

### AD-6: Learned component: a priority-at-intake classifier with a chronological split

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** complaint descriptions and transcripts are templated, so a text model would learn
  the templates; customer and product tables are monthly snapshots, so a random split would hide
  temporal leakage.
- **Options:** a text classifier; a structured-feature classifier with a random split; a
  structured-feature classifier with a chronological split and a feature-availability contract.
- **Decision:** predict `priority` at intake from pre-intake structured features (category,
  subcategory, claimed amount, currency, channel, segment, credit score, product type, and a
  recomputed count of strictly earlier complaints), excluding every post-outcome column.
  Pre-registered protocol: chronological split with the latest 15 percent held out, a fixed
  majority-class baseline, macro-F1 as the primary metric with per-class recall, the delta reported
  whatever its sign, and a fixed `random_state`. The prediction is decision support only: it can
  add a reason to escalate, never cause a credit.
- **Consequences:** `etl/train_classifier.py`, `tests/test_classifier_leakage.py`, and an
  evaluation report with the split date and the baseline delta.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-6 (line 111).

### AD-7: Deployment target: a single Fly.io machine, Render as fallback; demo recorded on localhost

- **Status:** Accepted; not deployed yet. The deploy artifacts are ready and verified locally
  (see `DEPLOY.md`); the platform choice waits on a billing decision.
- **Date:** 2026-09-28
- **Context:** the recorded video is a one-shot artifact, and network failures while recording are
  a real risk.
- **Options:** one Fly.io machine (needs a card on file); Render (free-tier cold starts, disclosed);
  Railway.
- **Decision:** one Fly.io machine with a mounted volume for the SQLite store, validated by an
  early deploy-and-restart test; the demo video always runs against localhost.
- **Consequences:** single host and single region, stated as a limitation.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-7 (line 127).

### AD-8: Portuguese support simulated at the LLM layer, disclosed as a data-coverage limitation

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** the dataset has no Portuguese rows, and the brief requires Spanish and Portuguese
  interactions.
- **Options:** a translation layer or pretend Portuguese data; skipping Portuguese; using the
  model's general multilingual ability with an explicit disclosure.
- **Decision:** a language toggle; in Portuguese the same scoped model handles understanding and
  replies. No training or held-out claim is made for Portuguese.
- **Consequences:** the README states the limitation; the eval harness includes a Portuguese
  conversation, scored only on whether the conversation works.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-8 (line 136).

### AD-9: UI structure: verification panel, an escalation-only persona toggle and inline action cards

- **Status:** Accepted, implemented.
- **Date:** 2026-09-28
- **Context:** the chat is the whole customer-facing product and has to show verified facts and
  the structured human handoff, not only messages.
- **Options:** (A) a verification panel; (B) an inline timeline; (C) a dual persona view.
- **Decision:** a hybrid of A and C: chat plus a persistent case panel, with a customer/agent
  toggle only on escalated cases that reveals the handoff; B's inline action cards inside the
  chat for system verifications.
- **Consequences:** two panel modes to keep consistent; `static/` follows the design record's UI
  acceptance criteria.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-9 (line 145).

### AD-10: LLM provider: Anthropic Claude Haiku 4.5 by default, Sonnet as an upgrade path

- **Status:** Accepted, implemented (Haiku 4.5 ships).
- **Date:** 2026-09-28
- **Context:** cost per case and p50/p95 latency must be reported, and the model's tasks are
  narrow (extraction, classification, phrasing).
- **Options:** Sonnet throughout; Haiku throughout; a second provider.
- **Decision:** Haiku 4.5 for every call by default, with Sonnet for generation only if manual
  testing shows the quality is not enough; the model name is one config constant.
- **Consequences:** `ANTHROPIC_API_KEY` in `.env`; a possible upgrade is budgeted, not a surprise.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-10 (line 154).

### AD-11: Dispute-resolution policy: explicit, falsifiable thresholds, not model judgment

- **Status:** Accepted; its credit-on-claim rows are superseded by AD-13. Matching, ambiguity
  and escalation rows still apply.
- **Date:** 2026-09-28
- **Context:** "eligible", "ambiguous" and "high-risk" had no falsifiable definition, which left
  the boundary of controlled automation undefined.
- **Options:** deciding thresholds during implementation; a pre-registered policy table.
- **Decision:** a policy table in `app/policy.py`, consulted by every guard. A match is the
  customer's own charge within max(5 percent, 2 USD) of the amount and 3 days of the date; exactly
  one match is confident, otherwise up to 2 clarification rounds and then escalation. Automatic
  resolution needed a confident match, at most USD 200, `fraud_score` < 30, status `Approved` and
  fewer than 3 disputes in the same category in 90 days. Escalation is forced by any failed
  condition, a `Critical` classifier prediction, a request for a person, or a model failure after
  its retries. The action is a simulated provisional credit, never a real transfer.
- **Consequences:** `tests/test_policy.py` tests every row; the thresholds are hackathon defaults,
  disclosed as configuration, not tuned risk parameters.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-11 (line 163).

### AD-12: Confirm-before-resolve turn (`confirming` state), extending AD-11's flow

- **Status:** Accepted, implemented (commit 5e45bd7). Since AD-13, a confirmed match moves on to
  the customer's explanation instead of a credit.
- **Date:** 2026-09-28
- **Context:** product feedback from the walkthrough: a dispute resolved in one message, with no
  confirmation of which charge it was, read as a black box.
- **Options:** prompt-only tweaks to the one-shot reply; a bounded `confirming` state between
  matching and resolution.
- **Decision:** an eligible match moves the case to `confirming` and names merchant, amount and
  date. The reply is classified into exactly `yes`, `no`, `human` or `unclear`; only `yes`
  proceeds, and the policy is re-run for the same transaction before any credit. Anything else, a
  failed re-check or a model outage escalates. One round only. A confirmation question without the
  merchant and the amount, or a resolution reply without the reference, is replaced by a
  deterministic template.
- **Consequences:** one more model call and one more customer turn per resolved case;
  `tests/test_confirmation_flow.py`; `CaseState.CONFIRMING`.
- **Source:** `.workspace/features/dispute-agent/plan.md`, AD-12 (line 180).

### AD-13: Evidence-based credit policy: the evidence decides, not the claim

- **Status:** Accepted, implemented. Decided 2026-09-30 (21794ae), recorded after the fact;
  supersedes AD-11's credit-on-claim rows. No plan section was written at decision time: this
  entry was written on 2026-10-01 from the shipped code, the README and the conformance record.
- **Date:** 2026-09-30
- **Context:** under AD-11 a clean "I don't recognize it" on an eligible charge was credited. An
  agent that pays because the customer says so is a refund button.
- **Options:** credit on the claim within AD-11's limits (the previous behavior); let the model
  judge whether the explanation is believable; ask the customer why and credit only what the
  record supports, per reason.
- **Decision:** after a confident, confirmed match, the customer explains what happened. The
  model assesses the explanation (its reason, whether it is specific and consistent with the
  charge); code turns that into a follow-up, an escalation or a check, and the assessment alone
  never authorizes a credit. Screening applies to every reason first: status `Approved`,
  `fraud_score` < 30, at most USD 200, no older than 60 days, customer `Active`, fewer than 3
  dataset disputes in the same category in 90 days, classifier not `Critical`, and this system's
  automatic credits to the customer in the last 90 days plus this one within USD 200. Then a
  per-reason evidence check:
  - **Duplicate:** reversed only with a verifiable twin (same merchant, exact amount, currency and
    type, at most 1 day apart); a pair is reversed once.
  - **Unrecognized:** a provisional credit only for a card-not-present purchase (Web or App) at a
    merchant the customer has no other charge with, at most 1 such credit per customer every 90
    days, with a simulated card block and a back-office review.
  - **Not received, wrong amount, lost or stolen card, unclear:** never credited automatically.
- **Consequences:** the per-customer limits are checked again inside the SQL `UPDATE` that grants
  the credit, and a unique `credit_key` index stops a duplicate pair from being reversed twice.
  The eval's `policy_abuse` group runs each abuse path with the assessment mocked as convinced,
  and the system-level comparison's `ablation_no_evidence_check` shows what this check stops on
  those cases.
- **Source:** commit 21794ae (2026-09-30); README.md "Dispute policy: the evidence decides, not the claim (AD-13)"; CONFORMANCE.md row 15; the thresholds in `app/policy.py`.

## usability-s1-flujo

Conversation flow fixes from the real-model usability run: a second claim, retries, deadlines,
stale actions, latency. Planned 2026-09-30; implemented and merged into main (PR #2).

### AD-1: The second claim is opened by the client; the server keeps its terminal guard

- **Decision:** after a terminal reply, "Report another charge" or a fresh typed message resets
  the client's case state and starts a new case (`case_id=null`); the server's rule that a
  terminal case never changes stays as it is.
- **Why:** the server-side alternative created ghost cases from stale actions.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-1 (line 49).

### AD-2: "Same charge already with a person after an explanation" guard

- **Decision:** if the charge named in a new case already has an escalated case for the same
  customer whose explanation was assessed, the new case escalates citing it, without asking for
  another explanation. One more `policy_abuse` eval case covers it.
- **Why:** keeps AD-13 from being worn down by retrying the same charge with a better story.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-2 (line 70).

### AD-3: The explanation assessment accepts "I deny the merchant and have my card" and says what is missing

- **Decision:** the prompt treats denying the merchant while holding the card as concrete for
  `unrecognized`; the assessment may return a closed `missing_detail` value that only shapes the
  one follow-up question. The policy and the handoff never read it.
- **Why:** the evidence check still decides the credit; the follow-up asks only for what is
  actually missing.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-3 (line 88).

### AD-4: Idempotent turns by the client's `turn_id`

- **Decision:** an optional UUID `turn_id` per message, stored per customer in `chat_turns` as
  pending or complete. A completed key replays its reply, a recent pending key answers 409, an old
  pending key is never reprocessed.
- **Why:** a retry is safe by construction, including the first message.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-4 (line 112).

### AD-5: A per-turn deadline on the server and no hidden SDK retries

- **Decision:** the client is built with `max_retries=0`, and all model calls of one request share
  a 20 s budget (`config.TURN_DEADLINE_SECONDS`); when it runs out the existing fallbacks apply.
- **Why:** the server answers before the client gives up at 25 s.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-5 (line 144).

### AD-6: Stale actions have no effect

- **Decision:** confirm buttons outside `confirming` and "not in the list" outside `selecting`
  get a no-effect reply that re-sends the current options, logged as `action_rejected`.
- **Why:** together with AD-4, no old tap can change a case.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-6 (line 167).

### AD-7: Typing indicator, double-send guard and retry in the client

- **Decision:** a typing bubble with a step caption, a read-only input while busy, a 25 s abort
  with a Retry card that resends the same `turn_id`, and one announcement mechanism per kind of
  content for screen readers.
- **Why:** the conversation stays the focus and announcements are not duplicated.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-7 (line 188).

### AD-8: Cheap and safe latency

- **Decision:** no SDK retries (AD-5), the resolution turn uses the deterministic
  `replies.resolved` template instead of a generation call, and the assessment's `max_tokens` is
  capped at 300.
- **Why:** the generated resolution text was often discarded anyway when it missed the reference.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-8 (line 215).

### AD-9: Menu and buttons without a model call

- **Decision:** a turn that comes from a button or a menu (an action or a tapped charge) returns
  the deterministic text without calling the model; "Ver mis últimos cargos" becomes a
  `SHOW_CHARGES` action. Requested by the user on 2026-09-30.
- **Why:** one change point, and the fallback texts already exist; the policy was already decided
  in code, so AD-13 is not at risk.
- **Source:** `.workspace/features/usability-s1-flujo/plan.md`, AD-9 (line 238).

## usability-s2-tono-escalamiento

Tone and escalation notice. Planned 2026-09-30; implemented and merged into main (PR #2).

### AD-1: "usted" in every Spanish surface; Portuguese "você" without slang

- **Decision:** usted everywhere in Spanish (fixed texts, UI strings, login, HTML and the Spanish
  prompts); Portuguese stays "você" in a professional register.
- **Why:** usted is the written register of Colombian banks' service channels and the safe one
  for money disputes.
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-1 (line 33).

### AD-2: One register module, a static guard test and a runtime guard on model replies

- **Decision:** `app/register.py` holds closed, whole-word lists per language: a broad list that
  `tests/test_register.py` checks against every fixed customer text, and a precise runtime subset
  that replaces and logs a model reply that uses one.
- **Why:** closed lists avoid the false positives a suffix regex has on words like "más" or
  "está".
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-2 (line 43).

### AD-3: Deterministic escalation notice from the case record

- **Decision:** every escalation uses a fixed template with no model call: the charge when known,
  the customer reason, the case number, "hasta 3 días hábiles" and that this chat no longer adds
  information to the case.
- **Why:** promise-bearing facts must come from code; the model had promised things that were not
  true.
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-3 (line 53).

### AD-4: Closed customer-reason enum, chosen at the escalation site, stored in the escalating compare-and-set

- **Decision:** `EscalationReason` in `app/case_model.py` (nine values), set where the cause is
  known and written in the same compare-and-set that escalates the case.
- **Why:** later replies can state the reason without re-deriving it, and the write stays atomic.
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-4 (line 63).

### AD-5: The charge is named only when the customer identified it

- **Decision:** the notice names a charge only if it was picked, named, reported with amount and
  date, or confirmed; otherwise the charge sentence is left out.
- **Why:** never show the customer an unconfirmed charge.
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-5 (line 94).

### AD-6: Contact deadline: one constant, labeled as a demo assumption

- **Decision:** `ESCALATION_CONTACT_BUSINESS_DAYS = 3` in `app/policy.py`, worded as contact, not
  resolution.
- **Why:** in the dataset's "Cargo no reconocido" complaints (n = 12,297) the first response took
  a median of 37 h and a p90 of 58 h.
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-6 (line 104).

### AD-7: `ChatReply.escalation` feeds the message, the card and the client panel

- **Decision:** one server object (case number, charge, reason, contact days) on every reply about
  an escalated case; the frontend renders the card and the panel from it.
- **Why:** the card and the message cannot disagree.
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-7 (line 114).

### AD-8: One "let me try first" per request for a person; the second request always escalates

- **Decision:** the first request for a person unlocks the handoff in the same compare-and-set as
  the turn's transition and offers the person; the second request escalates in any state.
- **Why:** the smallest change that guarantees the second request escalates everywhere.
- **Source:** `.workspace/features/usability-s2-tono-escalamiento/plan.md`, AD-8 (line 124).

## usability-s3-handoff-idiomas

Handoff shape and language parity. Planned 2026-09-30; implemented and merged into main (PR #2).

### AD-1: A currency is "the customer's" only if the customer said it; the guard is deterministic

- **Decision:** `llm.stated_currency` keeps the extracted currency only when the text has an
  explicit sign of it (ISO code, "US$", "dólares", "pesos colombianos" and so on), in Spanish and
  Portuguese alike; otherwise it is `None`.
- **Why:** the prompt alone cannot guarantee parity between languages; dropping the currency
  filter would change AD-11 matching.
- **Source:** `.workspace/features/usability-s3-handoff-idiomas/plan.md`, AD-1 (line 33).

### AD-2: New handoff shape, built in `app/handoffs.py`

- **Decision:** the handoff record separates the request summary, the verified facts, the values
  the customer reported and the policy reasons, and each builder fills in how the charge was
  identified.
- **Why:** each builder already knows how the charge was identified and whether it was confirmed.
- **Source:** `.workspace/features/usability-s3-handoff-idiomas/plan.md`, AD-2 (line 43).

### AD-3: `/api/case` does not hand the policy reasons to the customer's session

- **Decision:** the customer-facing handoff drops `policy_reasons` and gives only their count;
  the database and the `case_escalated` event keep the full text for the advisor.
- **Why:** the customer never sees thresholds.
- **Source:** `.workspace/features/usability-s3-handoff-idiomas/plan.md`, AD-3 (line 59).

### AD-4: Per-language breakdown in the eval and a currency-parity scenario

- **Decision:** `CaseOutcome` gains `language`, the report gains `by_language`, and a
  `currency_parity[es|pt]` scenario must reach `confirming` in both languages.
- **Why:** parity becomes a measured regression instead of a convention.
- **Source:** `.workspace/features/usability-s3-handoff-idiomas/plan.md`, AD-4 (line 67).

## statement-before-handoff

Ask the customer for their account before any human handoff. Planned 2026-10-01; implemented on
the branch `feat/statement-before-handoff`, not merged into main when this file was written.

### AD-1: Persist the pending escalation and add the `awaiting_statement` state

- **Decision:** a new non-terminal `CaseState.AWAITING_STATEMENT` and columns on `cases` that
  hold the pending escalation, so the decision is stored, not recomputed.
- **Why:** recomputing could give a different reason, and the statement must never change the
  decision.
- **Source:** `.workspace/features/statement-before-handoff/plan.md`, AD-1 (line 67).

### AD-2: Intercept at the single escalation funnel

- **Decision:** `finish_escalated` defers to the statement step unless the caller says the
  customer already gave an account in this case (`account_given=True`).
- **Why:** one interception point, so a new escalation site asks by default.
- **Source:** `.workspace/features/statement-before-handoff/plan.md`, AD-2 (line 121).

### AD-3: The opening question, follow-up and insistence are fixed templates

- **Decision:** fixed Spanish and Portuguese texts in `app/replies.py`.
- **Why:** no added latency, checked by the register test, and no promise that the account
  changes the outcome.
- **Source:** `.workspace/features/statement-before-handoff/plan.md`, AD-3 (line 182).

### AD-4: One model call per typed statement turn: `assess_statement`

- **Decision:** each typed turn assesses the accumulated statement and merges validated facts; a
  known value is never overwritten by `unknown`.
- **Why:** one call extracts what the advisor needs, as closed values.
- **Source:** `.workspace/features/statement-before-handoff/plan.md`, AD-4 (line 215).

### AD-5: Code decides follow-up, insistence and finalization

- **Decision:** `app/statement.py::handle_statement` routes the state; a decline is insisted on
  once, then the case is finalized.
- **Why:** the model only reads; every branch is code, with bounded turns.
- **Source:** `.workspace/features/statement-before-handoff/plan.md`, AD-5 (line 257).

### AD-6: Handoff shape and advisor view

- **Decision:** the handoff keys stay; `customer_reported` gains the statement status, a labeled
  model summary and one field per fact.
- **Why:** the advisor reads one field per fact instead of parsing a sentence.
- **Source:** `.workspace/features/statement-before-handoff/plan.md`, AD-6 (line 299).

### AD-7: Observability and evaluation

- **Decision:** statement events carrying the case id and pending reason but no raw text, plus an
  eval metric for the rule.
- **Why:** the metric makes the rule falsifiable on every eval run.
- **Source:** `.workspace/features/statement-before-handoff/plan.md`, AD-7 (line 328).

## ad13-quality-refactor

Code-quality refactor after AD-13, with no behavior change. Planned 2026-09-30; implemented and
merged into main (PR #1).

### AD-1: Split `state_machine.py` into `case_turn`, `credit`, `explanation` and `state_machine`

- **Decision:** four modules in that import order; `case_turn` is the lowest layer and never
  imports the others.
- **Why:** only one call crosses the new boundary, and every existing test patch keeps hitting
  the single binding the real path reads.
- **Source:** `.workspace/features/ad13-quality-refactor/plan.md`, AD-1 (line 37).

### AD-2: Tests migrate to the new names, and the seams get guards

- **Decision:** tests import the new names directly, and probes fail loudly if a patch misses
  its binding; an event-sequence test pins the credit path's order.
- **Why:** a missed patch must be a readable failure, not a silently weaker test.
- **Source:** `.workspace/features/ad13-quality-refactor/plan.md`, AD-2 (line 79).

### AD-3: `AUTO_CREDITABLE_REASONS` becomes the source of truth

- **Decision:** `evaluate_resolution` runs the evidence check from `_EVIDENCE_CHECKS` for a
  creditable reason; tests assert the checks, the texts and the disclosures cover exactly that set.
- **Why:** adding a creditable reason without its evidence check fails a test instead of
  shipping.
- **Source:** `.workspace/features/ad13-quality-refactor/plan.md`, AD-3 (line 113).

### AD-4: The assessment prompt's reason list is built from `DisputeReason`

- **Decision:** the list of allowed reasons in the prompt is generated from the enum, keeping the
  prompt byte-identical.
- **Why:** it is the contract the parser enforces, so deriving it removes a silent drift point.
- **Source:** `.workspace/features/ad13-quality-refactor/plan.md`, AD-4 (line 134).

### AD-5: `evaluate_explanation` returns a frozen `ExplanationDecision`

- **Decision:** a frozen dataclass built only through `accept()`, `needs_detail()` and
  `escalate(reason)`, which rejects an escalation without a reason.
- **Why:** "an escalation always says why" moves from convention to the type.
- **Source:** `.workspace/features/ad13-quality-refactor/plan.md`, AD-5 (line 151).

### AD-6: `DisputeReason` on the two `reason` fields; drop `Case.credited_amount_usd`

- **Decision:** both reason fields are typed as the enum, and the unused dataclass field is
  removed while the column stays.
- **Why:** a StrEnum serializes as before, and removing a dead field beats inventing a reader for
  it.
- **Source:** `.workspace/features/ad13-quality-refactor/plan.md`, AD-6 (line 169).

### AD-7: `cases.update_case` builds its guards in private helpers

- **Decision:** the expectation guards and the credit-limit guards come from two private helpers;
  the SQL text and parameter order stay identical.
- **Why:** the guards are the concurrency-critical part, so they should be readable on their own.
- **Source:** `.workspace/features/ad13-quality-refactor/plan.md`, AD-7 (line 195).

## demand-analysis

Offline analysis of complaint demand and costs from the warehouse. Planned 2026-10-01;
implemented and merged into main (the report is `docs/analysis/demand-report.md`).

### AD-1: One module, three layers, no app or eval imports

- **Decision:** `etl/analyze_demand.py` with pure compute functions, a pure markdown renderer and
  a chart renderer that is the only matplotlib import.
- **Why:** importing the app would load the API key into an offline process; no etl module imports
  the app.
- **Source:** `.workspace/features/demand-analysis/plan.md`, AD-1 (line 39).

### AD-2: JSON is the source of truth, and every number is labeled

- **Decision:** a committed `docs/analysis/demand-report.json`; every headline metric carries its
  n, its denominator or coverage and its kind (measured, assumed, simulated, projection or design
  argument), with no wall-clock timestamps.
- **Why:** gives the README and policy numbers a checkable source and makes regeneration testable.
- **Source:** `.workspace/features/demand-analysis/plan.md`, AD-2 (line 56).

### AD-3: Findings first, uniformity stated, disputes justified by design plus measured hours

- **Decision:** the report opens with numbered findings, each with n, including the negative ones
  (complaint demand is flat by category within Poisson noise).
- **Why:** an honest negative finding plus a design argument survives a judge's question; an
  inflated story does not.
- **Source:** `.workspace/features/demand-analysis/plan.md`, AD-3 (line 70).

### AD-4: Scope coverage kept in tables, two charts only

- **Decision:** every dimension in JSON and markdown tables; exactly two charts (handle time by
  call reason, and first-response times for "Cargo no reconocido").
- **Why:** a weekly chart would make Poisson noise look like signal.
- **Source:** `.workspace/features/demand-analysis/plan.md`, AD-4 (line 96).

### AD-5: Cost section in separate labeled blocks, no ratio and no monthly money total

- **Decision:** four blocks (time to first action, measured human handle time, simulated model
  cost, and a labeled projection table), never combined into one ratio.
- **Why:** the eval suite is constructed, so a headline ratio would promise what the system
  cannot deliver.
- **Source:** `.workspace/features/demand-analysis/plan.md`, AD-5 (line 110).

### AD-6: Reproducible inputs: a committed eval cost snapshot

- **Decision:** `docs/analysis/inputs/eval_cost_snapshot.json` is committed and refreshed only on
  request; a normal run reads only the snapshot.
- **Why:** a clean checkout plus the warehouse must regenerate the same committed report.
- **Source:** `.workspace/features/demand-analysis/plan.md`, AD-6 (line 134).

### AD-7: Escalation deadline check, without equating business days to hours

- **Decision:** the report gives the share of recorded first responses within 72 calendar hours,
  and a regression test keeps the 3-business-day promise at or above the observed maximum.
- **Why:** checking business days exactly needs holiday data the bank does not provide.
- **Source:** `.workspace/features/demand-analysis/plan.md`, AD-7 (line 156).

## system-baseline-adrs

System-level baselines in the eval and this decisions file. Planned 2026-10-01 and implemented on
the branch `feat/system-baseline-adrs`.

### AD-1: Variants as one patch seam in the harness, no app changes

- **Decision:** each baseline is applied by patching `app.state_machine.evaluate_resolution`, the
  state machine's only call to the policy decision, inside `eval/run_eval.py`.
- **Why:** one variable per baseline without eval-only hooks in the app; patching
  `app.policy.evaluate_resolution` would silently do nothing.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-1 (line 52).

### AD-2: Two single-variable baselines, named for what they are

- **Decision:** `escalate_at_credit_decision` (never credits) and `ablation_no_evidence_check`
  (skips only the AD-13 evidence check; a reason never credited automatically still escalates),
  both acting only at the final credit decision.
- **Why:** one variable per baseline makes every difference attributable to one layer.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-2 (line 66).

### AD-3: Outcome buckets independent of the hybrid's step checks

- **Decision:** one bucket per case from the expected and actual final state, reported as counts
  with denominators.
- **Why:** the hybrid's `safe` flag also checks its own per-step expectations, which would
  mislabel every baseline transfer.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-3 (line 84).

### AD-4: Additive report key, produced by `main()`, cheap default `run()`

- **Decision:** `run(compare_systems=True)` from `main()` adds `system_comparison`; each system
  runs in its own temporary directory; existing keys and the exit code are unchanged.
- **Why:** the published report always carries the comparison while the test suite stays fast.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-4 (line 108).

### AD-5: Stop reason from the app's own closed enum, not inferred causality

- **Decision:** per case, the app's stored `escalation_reason` plus the harness's own
  `decision_override`.
- **Why:** a value the app persisted from a closed enum cannot drift from the app's behavior.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-5 (line 134).

### AD-6: Honest framing of the comparison

- **Decision:** the report and the README say the suite is constructed, offline and written by
  the policy author, and that it is not a held-out workload.
- **Why:** zero failures on a small constructed set does not mean zero risk.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-6 (line 147).

### AD-7: Versioned decisions doc, namespaced, citations not rewritten

- **Decision:** this file, with one section per feature and a note on reading bare `AD-n`
  citations; the code citations stay as they are (the user's decision).
- **Why:** every citation resolves to a readable entry without touching about 150 code and test
  lines.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-7 (line 160).

### AD-8: Reference fixes in tracked docs only

- **Decision:** README, CONFORMANCE and DEPLOY point here instead of to the local planning
  record; code comments are not edited.
- **Why:** the docs are what readers open first, and this file's note covers the code comments.
- **Source:** `.workspace/features/system-baseline-adrs/plan.md`, AD-8 (line 183).
