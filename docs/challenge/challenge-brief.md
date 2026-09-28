# Challenge Brief — Factored AI & Data Hackathon 2026

Digest of the four organizer PDFs (`raw-docs/`, gitignored). This is the single source of truth for requirements during planning.

## Timeline

| Date | Milestone |
|---|---|
| Sep 1 | Registration opens |
| Sep 25 | Challenge launch (10-day build window starts) |
| **Oct 5** | **Submissions close** |
| Oct 15 | Finalists announced |
| Oct 16 | Award ceremony |

Prizes: 1st $6,000, 2nd $3,000, 3rd $1,000 + interview with Factored's engineering & talent team.

## Problem statement

> Build a working AI-first customer service system for a real-world banking environment. Your solution should understand complex customer interactions, use data and tools securely, complete appropriate service workflows, and involve human agents when needed.

Design goals: privacy, explainability, fairness, reliability, scalability. Make explicit trade-offs across autonomy, accuracy, latency, cost, human oversight. Justify where AI vs. deterministic logic is used, and how quality/safety are evaluated.

**This is not a chatbot demo — it's a customer-service *system*.** The system should: **Understand → Decide → Act → Verify → Escalate**.

### Scope

Pick ONE coherent workflow (examples, not tracks — implementing more does not earn a bonus):
1. Account or payment inquiries
2. Card-service support
3. **Transaction-dispute intake** ← chosen workflow
4. Credit-product information and eligibility support

Must include, within the chosen workflow:
- A normal resolution path (automated, policy-compliant)
- An ambiguous/unsupported request (clarify or abstain)
- A case requiring human intervention (safe escalation with structured handoff)

Must demonstrate interactions in **Spanish and Portuguese**, and report limitations in data/language coverage (the dataset is Spanish-only — Portuguese support is necessarily a documented gap/simulation, not something backed by real PT data).

### What the solution must demonstrate

1. **A problem supported by data** — analyze contact reasons, demand patterns, data quality, operational constraints; use this to prioritize the workflow and define outcomes.
2. **A functioning AI system** — maintain conversational context, clarify ambiguity, ground factual responses in permitted account/transaction/policy data, use tools when they serve the workflow, report only verified action outcomes.
3. **Controlled automation** — explicitly define what the system can answer, what needs confirmation, when it must abstain/transfer. Enforce permissions/policy *outside* model-generated prose (i.e., in code, not just prompts). Human handoff must include: request, verified facts, actions taken, evidence, open questions.
4. **Sound data & ML practice** — repeatable data prep with contracts, quality checks, lineage, freshness policy. Evaluate at least one learned component against a baseline. Valid labels, no leakage, justified representations/metrics/thresholds/splits.
5. **Measured quality & failure handling** — held-out evaluation including bad data, expired sessions, unauthorized access attempts, prompt injection, tool failures, multilingual ambiguity. Report success, unsafe outcomes, handoff behavior, latency, cost, with sample sizes and limitations.
6. **A credible route to operation** — tracing, bounded retries, safe fallback, reproducible setup. Explain capacity limits, monitoring, access controls, data retention, and remaining deployment work. Explanations must be grounded in sources/policy/execution records — **hidden model chain-of-thought is not an audit artifact.**

### Architecture freedom

Any approach (conventional ML, pretrained LLMs, retrieval, deterministic workflows, agents, or combos) is acceptable. NOT mandatory: training a new model, multiple agents, a tool-count target, streaming, demand forecasting, a dashboard.

Every team is assessed on **data engineering rigor** and **AI/ML rigor** regardless of architecture. For a pretrained/retrieval-based solution, demonstrate rigor through: component selection, relevance/intent labels, representations, leakage prevention, held-out evaluation, error analysis.

Use batch/incremental/streaming per the workflow's actual latency/freshness needs — incremental file delivery alone doesn't require streaming. If only static data, demonstrate update correctness with a clearly labeled test fixture.

### Data and execution boundaries

- Use only organizer-approved data + permitted external resources. Label inputs as real/de-identified/synthetic/team-generated.
- No private records, credentials, or restricted data in public submissions or external model requests. **⚠️ The AWS credentials in the data dictionary must never be committed to the public repo or sent to any external API.**
- Sandbox/mock banking tools OK if contracts/limitations are documented.
- Authentication must use a trusted test session or identity service — a national ID/customer number alone does not prove identity.
- Enforce per-customer record access and action permissions at the service/tool layer, not in the model prompt.
- **Credit workflows specifically** (not our chosen workflow, but a boundary that generalizes): separate conversation handling from predictive risk estimates from eligibility policy; the conversational model must never invent eligibility rules. No live lending decisions or money movement is authorized by this challenge — this constrains dispute resolution too: any "refund"/"chargeback" action must be simulated/mocked, never real money movement.

### Evaluation evidence

Compare baseline vs. proposed system on the same held-out workload. Report case count/mix, label quality, model/prompt versions, run-to-run variability. Include failures. If an LLM judges answers, document + validate its rubric against human/deterministic judgment.

Distinguish these outcomes explicitly:
- **Safe automated resolution** — eligible case reaches correct, policy-compliant outcome with no human intervention. Report rate over all in-scope test cases + share attempted.
- **Containment** — case ends without transfer (does NOT by itself mean the problem was solved).
- **Escalation quality** — correct transfers with useful handoff context; report missed AND unnecessary transfers where labels permit.
- **Unsafe outcomes** — unauthorized disclosure/action or materially incorrect outcome, with counts/denominators. Zero failures in a small test set ≠ zero risk.
- **Operating efficiency** — p50/p95 latency + cost per attempted case and per successful automated resolution. State workload/sample size/cost assumptions; use "not defined" when there are no successes.

Compare outcomes by language and authorized customer segment; state small-sample limitations; investigate disparities. Label offline measurements/simulations/projected savings separately from measured production results — never conflate them.

### Evaluation criteria (from kickoff deck)

"First and foremost our solution should work." Then, per discipline:
- **AI Engineering**: backend, frontend, deployment
- **Data Engineering**: extraction & transformation of the data
- **Machine Learning**: model selection, optimization, implementation, tracking
- **Data Analytics**: data quality, relevant insights from the solution
- **Overall project rationale and documentation**

No single discipline is mandatory to cover deeply, but the team should show competence across them.

### Multi-disciplinary task suggestions (not requirements)

| Discipline | Suggested focus |
|---|---|
| AI | Production backend, structured JSON handoffs |
| ML | LLM/RAG orchestration, prompt-injection defense |
| Data Engineering | ETL/ELT pipeline, customer record isolation |
| Data Analysis | Demand patterns, cost-per-resolution ROI |

## Submission requirements

1. Public GitHub repo: `factored-hackathon-2026-jagusgelos` ← this repo
2. Link to the deployed tool
3. 4-6 slide presentation with tool details
4. Short mandatory video pitch demonstrating the working solution and explaining core architectural decisions
5. **Submit no matter what** — send to `hackathon.admin@factored.ai`

No hard restriction on language/tools/cloud. Azure, Snowflake, AWS, Databricks mentioned as available resources (not required).

## Dataset: LATAM Bank (v1.0.0)

19M rows / 13 tables. Countries: Mexico, Colombia, Argentina. Date range 2023-06-17 to 2026-06-17. Currencies: MXN, COP, ARS, USD. Text data is Spanish (Mexican/Colombian/Argentine accent variants) — **no Portuguese data exists in the dataset**. Intentional data quality issues: ~2% duplicate records, ~5% nulls in nullable fields, late-arriving partitions, schema evolution over time.

Hosted read-only on S3 (`us-east-2`), bucket `<organizer-provided-bucket>`. Credentials in `.env` (gitignored) — see `.env.example`.

### Tables relevant to the chosen workflow (transaction disputes)

**`complaints` [FACT]** — 80,000 rows, partitioned daily, source PQR system. This is the core dispute-intake table.
Key columns: `complaint_id` (PK), `creation_date`, `customer_id` (FK), `case_type` (Complaint/Claim/Request/Suggestion), `category`, `subcategory`, `reception_channel`, `affected_product_id` (FK → products), `related_branch_id` (FK), `origin_interaction_id` (FK → call_center_interactions), `description` (Spanish text), `claimed_amount`, `currency`, `priority` (Low/Medium/High/Critical), `status` (Open/In Process/Escalated/Resolved/Closed/Rejected), `assigned_agent_id` (FK), `assignment_date`, `first_response_date`, `resolution_date`, `closing_date`, `sla_breached` (bool), `resolution_days`, `resolution` (Spanish text), `compensation_granted`, `resolution_satisfaction` (1-5), `is_repeat_complainer` (bool).

**`transactions` [FACT]** — 5,000,000 rows, partitioned daily. Ground truth for what's being disputed.
Key columns: `transaction_id` (PK), `transaction_date`, `product_id` (FK), `customer_id` (FK), `transaction_type` (Deposit/Withdrawal/Transfer/Payment/Purchase/...), `transaction_category`, `amount`, `currency`, `amount_usd`, `channel` (ATM/Branch/Web/App/POS/Transfer), `branch_id`, `merchant_name`, `merchant_category`, `transaction_country`, `transaction_city`, `transaction_status` (Approved/Declined/Pending/Reversed), `response_code`, `is_fraud` (bool), `fraud_score` (0-100), `latitude`/`longitude`.

**`call_center_interactions` [FACT]** — 800,000 rows, daily. Where a dispute conversation may originate.
Key columns: `interaction_id` (PK), `interaction_date`, `customer_id` (FK), `agent_id` (FK), `interaction_type`, `channel` (Phone/WebChat/WhatsApp/Email/App), `contact_reason`, `reason_category` (Transactional/Product/Technical/Commercial/...), `duration_seconds`, `wait_time_seconds`, `was_resolved` (FCR bool), `requires_followup`, `detected_sentiment`, `sentiment_score`, `customer_detected_accent`, `agent_used_accent`, `was_escalated`, `mentioned_products`, `has_transcript`, `has_recording`.

**`call_transcripts` [FACT]** — 200,000 rows, daily. Full/customer/agent text, language & accent detection, intents, topics — useful for NLP grounding and intent classification training data.
Key columns: `transcript_id` (PK), `interaction_id` (FK), `agent_id`, `customer_id`, `full_text`/`customer_text`/`agent_text` (Spanish), `detected_language`, `detected_accent`, `accent_confidence`, `detected_keywords`, `mentioned_entities` (JSON), `detected_intents`, `main_topics`, `transcription_model`, `audio_quality`, `duration_seconds`.

**`satisfaction_surveys` [FACT]** — 250,000 rows. Post-interaction CSAT/NPS/CES, linkable to `interaction_id` for outcome evaluation.

**`customers` [DIMENSION]** — 150,000 rows, monthly snapshot. Identity, `detected_accent`, `segment` (Premium/Plus/Basic/Student), `credit_score`, `customer_status`. Needed for authentication/authorization simulation and segment-based evaluation.

**`products` [DIMENSION]** — 400,000 rows. `product_type`, `current_balance`, `product_status`, links transactions/complaints to specific accounts/cards.

**`service_agents` [DIMENSION]** — 1,200 rows. `native_accent`, `languages`, `specialty`, `avg_csat`, `work_shift` — useful for simulating the human-handoff target and routing logic.

**`branches` [DIMENSION]** — 350 rows. Physical locations, geographic zone.

**`daily_exchange_rates` [REFERENCE]** — 3,000 rows. MXN/COP/ARS ↔ USD conversion.

Not directly relevant to disputes but present: `marketing_campaigns`, `campaign_sends`, `digital_events`.

### Foreign key relationships (dispute-relevant subset)

```
complaints.customer_id           → customers.customer_id
complaints.affected_product_id   → products.product_id
complaints.related_branch_id     → branches.branch_id
complaints.assigned_agent_id     → service_agents.agent_id
complaints.origin_interaction_id → call_center_interactions.interaction_id
transactions.customer_id         → customers.customer_id
transactions.product_id          → products.product_id
transactions.branch_id           → branches.branch_id
call_center_interactions.customer_id → customers.customer_id
call_center_interactions.agent_id    → service_agents.agent_id
call_transcripts.interaction_id      → call_center_interactions.interaction_id
satisfaction_surveys.interaction_id  → call_center_interactions.interaction_id
```

Note: dataset has intentional referential-integrity gaps (small % orphaned records) — evaluation/pipeline must handle this, not assume clean FKs.

## Why transaction disputes (rationale for the chosen workflow)

- `complaints` + `transactions` + `call_center_interactions` + `call_transcripts` form a connected chain from *raw signal* (a transaction with a fraud_score/status) → *customer contact* (call, sentiment, intent) → *formal case* (complaint with SLA/priority/resolution) — richer than the other three workflow options for demonstrating "Understand → Decide → Act → Verify → Escalate" end to end.
- Built-in ground truth for a learned component: `is_fraud`/`fraud_score` on transactions and `sla_breached`/`status`/`priority` on complaints give labels for a baseline classifier (e.g., dispute-priority or fraud-likelihood scoring) without inventing labels.
- Natural safety boundary matching the challenge's "no live money movement" rule: dispute resolution actions (provisional credit, chargeback initiation) are inherently things a real bank would gate behind human/policy approval, making the "controlled automation" requirement concrete rather than contrived.
- Escalation quality is directly measurable via `was_escalated`, `sla_breached`, `status=Escalated`, and `is_repeat_complainer`.
