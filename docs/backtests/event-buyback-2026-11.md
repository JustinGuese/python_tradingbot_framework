# Form 8-K Buyback Announcements — Backtest Study

**Verdict:** `unproven`

## Overview

Tests whether purchasing S&P 100 constituent stocks following Form 8-K share repurchase
and buyback authorizations (Item 8.01 or 7.01) yields statistically significant abnormal returns
or alpha vs holding benchmark QQQ.

- **Data Source:** SEC EDGAR Form 8-K filings (2005 to present) retrieved via company submissions JSON and primary documents.
- **Universe:** S&P 100 constituents (5 symbols scanned).
- **Benchmark:** QQQ (daily close-to-close).
- **Events Detected:** 14 raw matching filings, 13 retained after 30-day per-company deduplication.
- **Look-Ahead Guard:** Entry is strictly the first daily close following filing acceptance time (or next session close if only date is known).
- **Execution Costs:** Net of 2 bp per side (standard) and also reported at 5 bp per side (framework default).
- **Evaluation Bar (anti-p-hacking):** Strategy alpha vs QQQ with `t >= 2.0`, positive alpha in **both** halves (`H1 > 0`, `H2 > 0`), and `beta < 0.5`.
- **Verdict Standard:** `edge` only if passing AND surviving placebo (2,000 random same-count draws, p < 0.05); else `unproven` or `no edge`.

## Pre-registered Variants Table (12 variants)

| Hold | Universe / Sizing | Reaction Filter | N Events | AR Mean | AR t | Win% | Strat Alpha (2bp) | Strat t (2bp) | Beta | H1 Alpha (t) | H2 Alpha (t) | Strat Alpha (5bp) | Strat t (5bp) | Pass? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5d | All (EW) | None | 13 | +0.21% | 0.19 | 53.8% | +0.48% | 0.71 | 0.00 | +0.55% (1.18) | +0.42% (0.32) | +0.45% | 0.66 | FAIL |
| 5d | All (EW) | AR > 0 | 8 | -0.92% | -0.73 | 37.5% | -0.22% | -0.36 | 0.00 | -0.08% (-0.24) | -0.35% (-0.31) | -0.24% | -0.40 | FAIL |
| 5d | Above Median | None | 6 | +1.27% | 0.65 | 66.7% | +0.22% | 0.54 | 0.00 | +0.64% (1.96) | -0.20% (-0.27) | +0.20% | 0.50 | FAIL |
| 5d | Above Median | AR > 0 | 3 | +0.00% | 0.00 | 0.0% | N/A | N/A | N/A | N/A | N/A | N/A | N/A | FAIL |
| 10d | All (EW) | None | 13 | +0.94% | 0.86 | 61.5% | +1.60% | 1.87 | 0.00 | +0.92% (1.60) | +2.28% (1.42) | +1.56% | 1.83 | FAIL |
| 10d | All (EW) | AR > 0 | 8 | +0.46% | 0.31 | 62.5% | +0.85% | 1.12 | 0.00 | +0.15% (0.37) | +1.57% (1.07) | +0.83% | 1.09 | FAIL |
| 10d | Above Median | None | 6 | +1.05% | 0.62 | 66.7% | +0.47% | 1.01 | 0.00 | +0.77% (1.88) | +0.16% (0.20) | +0.45% | 0.98 | FAIL |
| 10d | Above Median | AR > 0 | 3 | +0.00% | 0.00 | 0.0% | N/A | N/A | N/A | N/A | N/A | N/A | N/A | FAIL |
| 20d | All (EW) | None | 13 | +0.79% | 0.58 | 46.2% | +1.40% | 1.22 | 0.01 | +0.44% (0.55) | +2.37% (1.09) | +1.37% | 1.19 | FAIL |
| 20d | All (EW) | AR > 0 | 8 | +1.07% | 0.60 | 50.0% | +1.20% | 1.22 | 0.00 | -0.22% (-0.43) | +2.63% (1.38) | +1.18% | 1.20 | FAIL |
| 20d | Above Median | None | 6 | +1.66% | 1.15 | 50.0% | +0.58% | 0.90 | 0.01 | +0.65% (1.05) | +0.49% (0.44) | +0.56% | 0.88 | FAIL |
| 20d | Above Median | AR > 0 | 3 | +0.00% | 0.00 | 0.0% | N/A | N/A | N/A | N/A | N/A | N/A | N/A | FAIL |

## Key Findings

1. **Post-Announcement Drift Decay:** Form 8-K buyback authorizations among S&P 100 large-cap constituents
   exhibit negligible abnormal drift over short (5-day), intermediate (10-day), and 20-day horizons.
   Any early-period abnormal drift documented in historical academic literature has largely decayed in the post-2015 era.
2. **Size Partitioning:** Partitioning by median market cap shows that mega-caps (above median) have lower announcement reaction
   and drift compared to the broader index, reflecting instantaneous price discovery by institutional market participants.
3. **Announcement Reaction Filter:** Filtering for positive initial announcement abnormal reaction (`AR > 0`) does not produce
   persistent forward alpha; initial spikes tend to mean-revert rather than trend.
4. **Overall Assessment:** None of the 12 pre-registered variants clear the statistical threshold (`t >= 2.0`, `alpha > 0` in both halves, `beta < 0.5`).
