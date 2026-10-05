// Every number the video shows, with its source and honesty label. Change them here only.
// `pending` facts may move when the measured-eval / fraud-model sessions land; the chip says so.

export type Honesty = 'MEASURED' | 'SIMULATED' | 'ASSUMED' | 'DESIGN ARGUMENT' | 'PLACEHOLDER';

export interface Fact {
  label: Honesty;
  source: string;
  short: string;
  pending?: boolean;
}

export const MODEL_NAME = 'Claude Haiku 4.5';

export const FACTS = {
  firstResponse: {
    hours: 37,
    label: 'MEASURED',
    source: 'median first response, "Cargo no reconocido" complaints, n = 7,567 of 12,297 (demand report)',
    short: 'demand report, n = 7,567',
  },
  resolvingTurn: {
    text: '1.3–6.6 s',
    label: 'MEASURED',
    source: `resolving turn with ${MODEL_NAME}, manual runs (README)`,
    short: `${MODEL_NAME}, manual runs`,
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
    pending: true,
  },
  eval: {
    unsafe: 0,
    cases: 40,
    label: 'SIMULATED',
    source: 'offline harness, mocked model, constructed suite, not held-out (README)',
    short: 'offline harness, mocked model',
    pending: true,
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
