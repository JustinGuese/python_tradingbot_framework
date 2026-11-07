# Form 8-K Buyback Announcements — Backtest Study

**Verdict:** `unproven`, the best lead from the next-ideas batch. One variant clears the bar and the placebo, but it is 1 of 12 windows, with beta 0.45. Paper-trade before believing it.

## Overview

Tests whether purchasing S&P 100 constituent stocks following Form 8-K share repurchase
and buyback authorizations (Item 8.01 or 7.01) yields statistically significant abnormal returns
or alpha vs holding benchmark QQQ.

- **Data Source:** SEC EDGAR Form 8-K filings (2005 to present) retrieved via company submissions JSON and primary documents.
- **Universe:** S&P 100 constituents (99 symbols scanned).
- **Benchmark:** QQQ (daily close-to-close).
- **Events Detected:** 530 raw matching filings, 504 retained after 30-day per-company deduplication.
- **Look-Ahead Guard:** Entry is strictly the first daily close following filing acceptance time (or next session close if only date is known).
- **Execution Costs:** Net of 2 bp per side (standard) and also reported at 5 bp per side (framework default).
- **Evaluation Bar (anti-p-hacking):** Strategy alpha vs QQQ with `t >= 2.0`, positive alpha in **both** halves (`H1 > 0`, `H2 > 0`), and `beta < 0.5`.
- **Verdict Standard:** `edge` only if passing AND surviving placebo (random same-count draws, p < 0.05); else `unproven` or `no edge`.

## Pre-registered Variants Table (12 variants)

| Hold | Universe / Sizing | Reaction Filter | N Events | AR Mean | AR t | Win% | Strat Alpha (2bp) | Strat t (2bp) | Beta | H1 Alpha (t) | H2 Alpha (t) | Strat Alpha (5bp) | Strat t (5bp) | Pass? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5d | All (EW) | None | 504 | +0.10% | 0.63 | 48.5% | +6.13% | 1.70 | 0.29 | +4.69% (0.96) | +7.63% (1.44) | +5.19% | 1.44 | FAIL |
| 5d | All (EW) | AR > 0 | 306 | +0.20% | 0.97 | 48.2% | +5.24% | 1.73 | 0.20 | +3.67% (0.99) | +6.84% (1.42) | +4.62% | 1.52 | FAIL |
| 5d | Above Median | None | 245 | +0.20% | 0.97 | 49.6% | +2.91% | 1.00 | 0.20 | +1.83% (0.42) | +4.09% (1.04) | +2.38% | 0.82 | FAIL |
| 5d | Above Median | AR > 0 | 149 | +0.32% | 1.22 | 50.0% | +1.39% | 0.60 | 0.14 | +0.80% (0.25) | +2.06% (0.62) | +1.05% | 0.45 | FAIL |
| 10d | All (EW) | None | 504 | +0.24% | 1.16 | 49.8% | +10.00% | 2.56 | 0.45 | +8.33% (1.63) | +11.71% (1.98) | +9.22% | 2.36 | **PASS** |
| 10d | All (EW) | AR > 0 | 306 | +0.33% | 1.24 | 50.0% | +8.56% | 2.51 | 0.30 | +6.18% (1.41) | +11.03% (2.11) | +8.01% | 2.35 | **PASS** |
| 10d | Above Median | None | 245 | +0.26% | 0.87 | 50.4% | +4.17% | 1.19 | 0.35 | +1.67% (0.35) | +6.69% (1.30) | +3.69% | 1.05 | FAIL |
| 10d | Above Median | AR > 0 | 149 | +0.22% | 0.52 | 49.3% | +2.78% | 0.94 | 0.23 | -0.06% (-0.01) | +5.71% (1.32) | +2.46% | 0.83 | FAIL |
| 20d | All (EW) | None | 504 | +0.32% | 1.12 | 53.1% | +5.87% | 1.46 | 0.63 | +6.79% (1.18) | +5.05% (0.90) | +5.27% | 1.31 | FAIL |
| 20d | All (EW) | AR > 0 | 306 | +0.38% | 1.06 | 52.6% | +6.78% | 1.75 | 0.46 | +4.70% (0.91) | +8.90% (1.55) | +6.32% | 1.64 | FAIL |
| 20d | Above Median | None | 245 | +0.80% | 2.10 | 59.3% | +4.69% | 1.15 | 0.55 | +2.21% (0.39) | +7.21% (1.25) | +4.28% | 1.05 | FAIL |
| 20d | Above Median | AR > 0 | 149 | +0.54% | 1.10 | 58.8% | +3.86% | 1.07 | 0.37 | -0.62% (-0.13) | +8.42% (1.59) | +3.58% | 0.99 | FAIL |

## Key Findings

1. **Sample:** up to 504 events per variant.
2. **Best variant by t (2bp):** hold 10d, size=all, AR filter=False: alpha +10.00%/yr, t 2.56.
3. **Passing Variants:** 2 variant(s) passed the initial statistical criteria.

## Placebo and caveats (added by hand; the built-in placebo was too slow)

The study's own placebo rebuilds a full book per draw (~20 s each), so `scripts/onetime_event_buyback_placebo.py`
repeats it vectorised: same-count random (date, symbol) pairs over the same price matrix, 10-session holds, 2 bp/side,
t of daily alpha vs QQQ. It reproduces the observed variant at t 2.96 / +12.2%/yr / beta 0.45 (the study says t 2.56 /
+10.0%; the gap is entry-timestamp approximation).

- 5,000 random books: mean t 0.61, 95th percentile 2.15.
- p(t >= 2.96) = 0.006; p(t >= 2.56, the study's value) = 0.020. Both pass p < 0.05.
- **Why it is still only `unproven`:**
  - Only the 10-session window passes (5d t 1.70, 20d t 1.46-1.75); a real drift should be smoother across windows.
  - 12 variants were tried, so a single p of 0.02 is roughly 0.2 after correcting for 12.
  - Beta 0.45 is near the 0.5 limit: this is a stock book, so part of the "alpha" can be exposure the QQQ regression misses.
  - Above-median (mega-cap) names show nothing (t 0.6-1.2); the effect sits in the smaller S&P 100 names, where costs are higher than 2 bp.
  - Buyback authorizations were identified by regex on the 8-K text (530 matches), not hand-checked.
- Next step if pursued: a paper `targetWeights` bot that buys each new authorization at the next close and holds 10 sessions,
  then judge it with `alphareport`. Needs a live 8-K poller (EDGAR submissions API, 10 req/s limit).
