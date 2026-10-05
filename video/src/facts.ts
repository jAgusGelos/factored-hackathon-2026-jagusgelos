// Every number the video shows, with its source and honesty label. Change them here only.

export type Honesty = 'MEASURED' | 'SIMULATED' | 'ASSUMED' | 'DESIGN ARGUMENT' | 'PLACEHOLDER';

export interface Fact {
  label: Honesty;
  source: string;
  short: string;
}

export const MODEL_NAME = 'Claude Haiku 4.5';

const EVAL_CASES = 48;
const EVAL_RUNS = 3;
const EVAL_DOC = 'docs/eval/measured-eval.md';
const EVAL_SET = `${EVAL_CASES} held-out cases × ${EVAL_RUNS} runs`;

export const FACTS = {
  firstResponse: {
    hours: 37,
    label: 'MEASURED',
    source: 'median first response, "Cargo no reconocido" complaints, n = 7,567 of 12,297 (demand report)',
    short: 'demand report, n = 7,567',
  },
  typedReply: {
    text: '3.0 s',
    label: 'MEASURED',
    source: `median typed turn with ${MODEL_NAME}, ${EVAL_SET}, p95 7.7 s (${EVAL_DOC})`,
    short: `${MODEL_NAME}, ${EVAL_SET}`,
  },
  subset: {
    rows: '130,690',
    rowCount: 130_690,
    of: '~5M',
    totalRows: 5_000_000,
    window: '2026-05-18 to 2026-06-17',
    label: 'MEASURED',
    source: 'transaction rows extracted (data/extraction_manifest.json); 5M rows / 808 MB in AD-2',
    short: 'extraction manifest',
  },
  eval: {
    unsafe: 0,
    cases: EVAL_CASES,
    resolved: 18,
    resolvable: 18,
    label: 'MEASURED',
    source: `held-out set written before the first run, real ${MODEL_NAME}, ES and PT, 0 unsafe in each of ${EVAL_RUNS} runs; small set, 95% upper bound about 7% (${EVAL_DOC})`,
    short: `${EVAL_CASES} held-out cases, real ${MODEL_NAME}`,
  },
  contactDeadline: {
    text: 'within 3 business days',
    label: 'ASSUMED',
    source: 'demo assumption (policy.ESCALATION_CONTACT_BUSINESS_DAYS)',
    short: 'contact deadline is a demo assumption',
  },
  recentLedger: {
    label: 'DESIGN ARGUMENT',
    source: 'A dispute needs only the customer’s recent ledger',
    short: 'why a subset',
  },
} as const satisfies Record<string, Fact & Record<string, unknown>>;

export const DUPLICATE_OUTCOME = { text: '1 of 2', note: 'reversed · twin verified in this case' } as const;
