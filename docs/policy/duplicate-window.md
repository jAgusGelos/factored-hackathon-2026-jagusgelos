# Duplicate window: why 10 minutes (AD-14)

## Question

AD-13 reversed a "duplicate" when an equal Approved charge (same merchant, exact amount, currency and
type) existed **up to 1 day** apart. Two legitimate same-price taxi rides on consecutive days met that
rule, so one of them was reversed on the customer's word. Real duplicates (a double swipe, a processor
retry) post seconds to minutes apart. The window should separate the two populations.

## Measurement (MEASURED)

Source: the local warehouse built by `etl/extract.py` (`data/warehouse.duckdb`, table `transactions`,
130,690 rows, 2026-05-18 06:00 to 2026-06-18 05:59, 77,270 customers). Only `Purchase` rows carry a
merchant name (29,887 of 31,440).

Inter-arrival of equal charges per customer, using full timestamps:

```sql
WITH t AS (
  SELECT EPOCH(transaction_date - LAG(transaction_date) OVER (
           PARTITION BY customer_id, merchant_name, amount, currency, transaction_type
           ORDER BY transaction_date)) AS gap_s
  FROM transactions
  WHERE merchant_name IS NOT NULL AND transaction_type IN ('Purchase', 'Payment'))
SELECT COUNT(*) FROM t WHERE gap_s IS NOT NULL;
```

| Grouping | Pairs with a previous equal charge |
|---|---|
| customer + merchant + exact amount + currency + type | **0** |
| customer + exact amount + currency (any merchant, any type) | **0** |
| customer + merchant (any amount), for reference | 302 (1 within 1 h, 5 in 1-6 h, 16 in 6-24 h, 101 in 1-7 days, 179 over 7 days) |

The dataset's amounts are continuous (117,943 distinct amounts in 130,690 rows) and it holds no
retried or double-posted charge at all. There is no retry cluster to measure.

## Decision (DESIGN ARGUMENT)

Because the data cannot size the window, `DUPLICATE_WINDOW_MINUTES = 10` (`app/policy.py`) is a design
argument, labeled as such:

- A double swipe or an acquirer/processor retry posts within seconds to a few minutes of the original
  authorization; 10 minutes covers that with margin.
- Any genuine repeat purchase of the same fare (a daily commute, two coffees) is hours apart; 10
  minutes keeps all of them out. The reference row above shows how rare even same-merchant repeats
  under 1 hour are (1 of 302).
- The cost is asymmetric: a real duplicate posted 11+ minutes later is not lost, it goes to a person
  with both charges as evidence; a wrongly reversed legitimate charge is money the bank pays out on a
  claim the data contradicts.

Equal charges further apart than the window are returned as `repeat_charges` and named in the
handoff's policy reason, so the advisor sees both charges. Nothing is reversed automatically.

Timestamps are read from the fixture by transaction id (`app/transactions.find_own_duplicate_evidence`),
because a charge rebuilt from a case snapshot only keeps the day.

## Demo data (SIMULATED)

- `SYN-DEMO-TAXI-1/2`: Taxi Seguro, 27,000 COP, 2026-06-15 12:00 and 12:04 (inside the window):
  reversed once, as before.
- `SYN-DEMO-RIDE-1/2`: Cabify, 18,500 COP, 2026-06-03 08:10 and 2026-06-04 08:12 (the same fare on
  the next day): never reversed as a duplicate (`tests/test_conversation_flows.py`,
  `tests/test_state_machine.py`, eval case `repeat_fare_next_day` in the `policy_abuse` group).
