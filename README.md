# Factored AI & Data Hackathon 2026 — Transaction Dispute Resolution Agent

AI-first banking customer service system for LATAM Bank, focused on **transaction dispute intake and resolution**. Built for the [Factored AI & Data Hackathon 2026](docs/challenge/challenge-brief.md).

## Status

Planning phase. See `docs/challenge/challenge-brief.md` for the full requirements digest and `openspec/` (once generated) for the implementation plan.

## Challenge scope

- **Workflow**: transaction-dispute intake, investigation support, and resolution/escalation.
- **Languages**: Spanish and Portuguese customer interactions (dataset is Spanish-only; Portuguese coverage is a stated limitation).
- **Data**: LATAM Bank synthetic dataset (Mexico, Colombia, Argentina), ~19M rows across 13 tables, hosted on S3.
- **Deadline**: Submissions close October 5, 2026.

## Repo layout

```
docs/challenge/     Challenge requirements, data dictionary digest, rubric
raw-docs/           Original PDFs from Factored (gitignored, not redistributed)
data/               Local data samples/cache (gitignored, never commit raw dataset)
.env.example         Environment variable template (copy to .env, fill in secrets)
```

## Setup

1. Copy `.env.example` to `.env` and fill in AWS + LLM credentials (see `docs/challenge/challenge-brief.md` for where the AWS credentials come from — **never commit `.env`**).
2. (Implementation details TBD once the plan is finalized.)

## Submission checklist (per challenge rules)

- [ ] Public GitHub repo named `factored-hackathon-2026-jagusgelos`
- [ ] Deployed tool link
- [ ] 4-6 slide presentation
- [ ] Short mandatory video pitch demonstrating the working solution
