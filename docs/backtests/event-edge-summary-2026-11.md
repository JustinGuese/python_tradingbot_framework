# Do existing event data hold an edge vs QQQ? (2026-11-06)

**Answer: no tradeable edge found. Do not build an event bot or enable the eventlogger on this evidence.**

Bar (CLAUDE.md): alpha vs QQQ with t >= 2, positive in both halves, beta < 0.5. Every variant was
listed before the run and all are reported. Shared maths: `tradingbot/utils/event_study.py`.

| Study | Data | Variants | Pass | Verdict |
|---|---|---|---|---|
| E1 macro (FOMC/CPI/NFP) | `macro_events`, 2000+ | 40 | 2 | unproven |
| E2 stock_news headlines | 11.5k headlines, 84 symbols | 12 strategies + 18 event buckets | 0 | no edge |
| E3 Signals Bank channel | 168 mappable signals of 1.4k | 9 | 5 (all need shorts) | no edge for long-only |

Details: `event-macro-2026-11.md`, `event-news-2026-11.md`, `event-signals-2026-11.md`.

## Why each fails

- **E1:** the passes are FOMC with UUP over [0,+2] (t 2.99) and CPI with TLT over [0,+1] (t 2.01, second
  half weak). Two of 40 is chance level, and neither is an equity trade.
- **E2:** no strategy clears t 1.1. The headline-looking event t-stats are a regime artifact (the
  neutral bucket shows the same pattern). The one sentiment-specific effect is a reversal that flips
  sign between halves and needs shorting.
- **E3:** the passing variants are signed (long and short). The long-only subset fails (t <= 1.05,
  first half negative). Only 168 signals map to a ticker, so the sample is small anyway.
- **Excluded up front:** 99% of `telegram_messages` is pump.fun memecoin dev-sell spam, not tradeable
  by any broker here. PEAD was already rejected (`pead-2026-09.md`).

## Limits of this test

Daily bars only, because the stored 1-minute history is too sparse. So these results say nothing
about fast reactions (seconds to minutes), which is also where a polling logger could not compete.
yfinance news only reaches back recent weeks, so E2's early half is thin.

## Recommendation

1. Leave `eventLogger.enabled: false`. Nothing here justifies a live recorder.
2. If the idea is revisited, the only untested class is slow second-order reactions to hard events
   (hurricane, sanctions, outage) and it needs labelled events first, not more latency data.
3. Do not follow the Signals Bank channel with the long-only framework.
