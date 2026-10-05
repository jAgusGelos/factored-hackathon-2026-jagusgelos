// Every number the video shows, with its source and honesty label. Change them here only.
// Lines marked `pending` may move when the measured-eval / fraud-model sessions land.

export const FACTS = {
  firstResponse: {
    hours: 37,
    label: 'MEASURED' as const,
    source: 'median first response, "Cargo no reconocido" complaints, n = 7,567 of 12,297 (demand report)',
  },
  resolvingTurn: {
    text: '1.3–6.6 s',
    label: 'MEASURED' as const,
    source: 'resolving turn with Claude Haiku 4.5, manual runs (README)',
  },
  subset: {
    rows: '130,690',
    rowCount: 130_690,
    of: '~5M',
    totalRows: 5_000_000,
    window: '2026-05-18 to 2026-06-17',
    label: 'MEASURED' as const,
    source: 'transaction rows extracted (data/extraction_manifest.json); 5M rows / 808 MB in AD-2',
    pending: true,
  },
  eval: {
    unsafe: 0,
    cases: 40,
    label: 'SIMULATED' as const,
    source: 'offline harness, mocked model, constructed suite, not held-out (README)',
    pending: true,
  },
  contactDeadline: {
    text: 'within 3 business days',
    label: 'ASSUMED' as const,
    source: 'demo assumption (policy.ESCALATION_CONTACT_BUSINESS_DAYS)',
  },
} as const;
