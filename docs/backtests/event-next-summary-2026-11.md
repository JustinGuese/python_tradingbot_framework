# Next event ideas, backtested (2026-11-06)

Same bar as `event-edge-summary-2026-11.md`: alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5,
net of 2 bp/side (5 bp also reported), 12 or fewer pre-listed variants per study, placebo before `edge`.

| Idea | Data | Variants | Pass | Verdict |
|---|---|---|---|---|
| B1 buyback 8-Ks | 504 events, S&P 100, 2005-2026, EDGAR | 12 | 2 (both 10-session holds) | **unproven, best lead** |
| B2 hard events (LLM-labelled) | `stock_news`, 2 of 5 classes had >= 20 events | 12 | 0 | inconclusive |
| B3 insider cluster buys | 267 clusters, Form 4 `P` trades, 2006-2026 | 12 | 0 | no edge |
| B4 extra calendar legs | EIA Wed, turn-of-month, pre-holiday, NFP/GLD, quarter-end/TLT | 5 legs | 0 | no edge |

## B1 buybacks (the lead)

- 10-session hold on every authorization: +10.0%/yr alpha, t 2.56 (+8.6%, t 2.51 when only positive announcement reactions).
  5d and 20d windows fail (t 1.5-1.8); mega-caps show nothing (t 0.6-1.2).
- Vectorised placebo (5,000 random books): p = 0.006 to 0.02. Beta 0.45.
- Not proven: 1 window of 12, multiple-testing adjusted p is about 0.2, beta close to the limit, the effect sits in
  smaller names where costs exceed 2 bp. See `event-buyback-2026-11.md`.
- Natural next step: paper `targetWeights` bot, buy each new authorization, hold 10 sessions, needs a live 8-K poller.

## B2 hard events
Only chip/export control (295 events) and sanctions (21) had enough events; neither works. Outage, hurricane and strike
classes (4, 1, 5 events) were not scored. `stock_news` is too thin for these classes; this needs a longer news history.

## B3 insider clusters
Best variant (60 sessions, clusters of 3+) has +6% abnormal return per event (t 1.96) but alpha vs QQQ of +4.8%/yr at t 0.86.
Fixed a x100 units bug in the worker's table; verdicts were unaffected.

## B4 calendar legs
Weekly EIA/XLE bleeds costs (t -2.4 at 5 bp). Turn-of-month SPY is the closest (t 1.5 at 2 bp, first half only).
Pooling any of these with the existing four macro-sleeve legs lowers its alpha.

## Process notes
- The B1 worker committed a 5-ticker smoke-run doc (13 events) with prose written before any data; replaced by a full run.
- B1 and B3 reports had hard-coded "findings" paragraphs; both now compute them from results.

## Follow-ups, 2026-11-07

| Idea | Data | Result | Doc |
|---|---|---|---|
| Buybacks beyond the S&P 100 | S&P 400 + 600, 815 events from a 25% random sample of 88k candidate 8-Ks, 2010 on | primary (10 sessions, 10 bp) t 0.81, placebo p 0.13: **no edge**. S&P 100 reproduces (not independently) at t 2.31 / 1.82 at 2 / 10 bp | event-buyback-broad-2026-11.md |
| Trend gate on other markets | global sector ETFs, 20 country ETFs, plus a US replication | gate adds +2-3%/yr over ungated outside the US, no book clears the bar, alpha is early-sample: **unproven**; paper bot `CountryTrendGateBot` built on request (framework backtest 2003+: t 1.67, H2 t 0.24) | gate-other-markets-2026-11.md |
| Schedule 13D filings | 1,152 initial 13Ds on S&P 100/400/600, 2010 on | primary (fund-like, 20 sessions) t 1.60, p 0.0475, beta 0.68; "all filers" books (t 2.3-2.8) are carried by a few outliers, event-level median ~0: **unproven, not worth building** | event-13d-2026-11.md |

Process notes: two of the three looked better at book level than at event level, which is why the event-level table
(mean, median, winsorised mean, vs random dates on the same names) is now part of both event studies. The shared simulator
is `scripts/event_book_lib.py` (DST-aware entry close, tests in `tests/test_event_book_lib.py`); the older
`onetime_event_buyback_placebo.py` uses a fixed 21:00 UTC close, which enters a close early for 16:00-17:00 EDT filings.
