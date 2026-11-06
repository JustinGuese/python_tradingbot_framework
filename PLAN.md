# PLAN: backtest the four next event ideas (2026-11-06)

Goal: for each idea, find out from history whether a **long-only** version has alpha vs QQQ
(t >= 2, alpha > 0 in BOTH halves, beta < 0.5), net of 2 bp/side and also reported at 5 bp.
Reuse `tradingbot/utils/event_study.py` (`forward_returns`, `abnormal_returns`, `event_strategy_returns`,
`judge`). Do not modify it. No bots, no helm, no configs. Output per step: one script, one test file,
one doc with the verdict, in the style of `docs/backtests/event-macro-2026-11.md`.

Rules for every step (anti-p-hacking):
- **List all variants before running**, cap at 12 per step, report every one including failures.
- Look-ahead guard: entry is the first daily close strictly after the event is public (use filing
  acceptance time / release time; if only a date is known, enter the next session close).
- Verdict words: `edge` only if a variant passes AND survives a placebo (random same-count dates, 2000 draws,
  p < 0.05) AND the bar above; else `unproven` (some pass) or `no edge`.
- Prices: yfinance daily closes, cache pickles under `.cache/`. EDGAR: send a generic User-Agent
  (`tradingbot-research`, no email), max 5 requests/s, cache raw responses on disk.
- Costs: apply 2 bp and 5 bp per side to each round trip.
- Tests: 3-5 pytest cases on the pure functions only (window math, look-ahead guard, scoring). No network in tests.

## Batch 1 (4 steps, parallel, disjoint files)

### B1 — buyback announcements (8-K)
Files: `scripts/onetime_event_buyback_study.py`, `tests/test_event_buyback_study.py`, `docs/backtests/event-buyback-2026-11.md`
- Universe: S&P 100 (`tradingbot/utils/universes.py` if present, else a hard-coded list in the script).
- Events: 8-Ks (Item 8.01 or 7.01) whose text matches a buyback/repurchase authorization regex, 2005 to now,
  from EDGAR (company submissions JSON + the filing's primary document). Dedupe per company within 30 days.
- Variants: hold 5/10/20 sessions x equal-weight vs size-bucket (above/below median market cap) x with/without
  abnormal-return-vs-QQQ filter = at most 12.
- Acceptance: script runs end to end on a `--cap N` smoke run, writes the doc table, tests pass.

### B2 — hard events with LLM labels
Files: `scripts/onetime_event_hard_study.py`, `tests/test_event_hard_study.py`, `docs/backtests/event-hard-2026-11.md`
- Events: `stock_news` + `telegram_messages` excluded; use stored `world_events` if non-empty, otherwise
  `stock_news` rows. Label each headline with `run_ai_simple` into one of: refinery/pipeline outage,
  hurricane/storm, sanctions, strike/shutdown, chip/export control, other. Batch the labelling: send 25 headlines per `run_ai_simple` call and
  ask for a JSON list of labels in input order (validate length and fall back to single calls on mismatch). Cache labels
  to disk; cap total LLM calls at 150; stop and report if fewer than 20 events exist per class.
- Per class pick ONE pre-written mapping (e.g. outage -> XLE/USO, hurricane -> insurers KIE, sanctions -> GLD/XLE).
  Test [0,+1], [0,+3], [0,+5] sessions after the event day.
- Acceptance: classes with < 20 events are listed as `too few`, not scored. Tests cover label parsing and window math.

### B3 — insider cluster buys (Form 4)
Files: `scripts/onetime_event_insider_study.py`, `tests/test_event_insider_study.py`, `docs/backtests/event-insider-2026-11.md`
- Data: EDGAR Form 4 for the S&P 100, 2006 to now, open-market purchases only (transaction code `P`).
- Cluster = >= 2 distinct insiders buying within 10 days; event day = filing date of the second.
- Variants: hold 10/20/60 sessions x min cluster size 2/3 x min total $ (50k, 250k) = at most 12.
- Acceptance: parser unit-tested on a saved Form 4 XML fixture; the doc states how many clusters were found.

### B4 — extra scheduled-calendar legs
Files: `scripts/onetime_event_calendar_study.py`, `tests/test_event_calendar_study.py`, `docs/backtests/event-calendar-2026-11.md`
- Legs (fixed, 6 total): EIA crude inventory Wednesdays -> XLE hold [0,+1]; turn-of-month (last session to
  3rd session) -> SPY; pre-holiday session -> SPY; NFP day [0,+1] -> GLD; quarter-end week -> TLT; first
  session after FOMC -> SHV is NOT included. EIA dates: every Wednesday (holiday shift to Thursday).
- Pool the passing legs with the existing sleeve legs (`tradingbot/utils/macro_sleeve_rules.py`) and report the combined alpha.
- Acceptance: pooled table with and without the existing four legs; tests cover the calendar helpers.

## Final review (me)
Run the four test files once, read each doc's verdict against the numbers, check no config or other test
was touched, write `docs/backtests/event-next-summary-2026-11.md` (one table, one paragraph each, recommendation),
merge to main.

## Risks to approve
- B2 spends paid LLM calls (batched 25 headlines per call, cap 150, cheap model) and may end as `too few`.
- B1/B3 hit SEC EDGAR from the workers (rate-limited, free); B3 is the biggest build.
- Workers died twice last time when run as background children; I will launch them detached with `setsid nohup`.
