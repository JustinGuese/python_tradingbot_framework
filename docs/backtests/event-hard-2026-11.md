# Hard Events with LLM Labels — Backtest Study

**Verdict:** `no edge`

## Overview

Evaluates whether trading news-driven hard geopolitical and physical disruption events
(refinery/pipeline outages, severe storms/hurricanes, sanctions, strikes/shutdowns,
and semiconductor export controls) labelled via LLM classification generates persistent long-only
alpha against the QQQ benchmark.

- **Data Source:** `stock_news` headlines labelled with OpenRouter LLM.
- **Benchmark:** QQQ (daily close-to-close).
- **Windows:** `[0,+1]`, `[0,+3]`, `[0,+5]` sessions after public headline release.
- **Costs:** 2 bp/side (primary) and 5 bp/side (framework default).
- **Evaluation bar (PLAN.md):** `t >= 2.0`, `H1 alpha > 0`, `H2 alpha > 0`, `beta < 0.5`, plus placebo p < 0.05.
- **Acceptance Rule:** Classes with fewer than 20 events are classified as `too few` and not scored.

## Event Identification & Frequency Summary

| Event Class | Mapped ETF | Total Count | Classification Status |
|---|---|---|---|
| refinery/pipeline outage | XLE | 4 | **too few** (< 20 events) |
| hurricane/storm | KIE | 1 | **too few** (< 20 events) |
| sanctions | GLD | 21 | Scored |
| strike/shutdown | XLI | 5 | **too few** (< 20 events) |
| chip/export control | SMH | 295 | Scored |

## Pre-registered Variants Table (12 variants)

| Event Class | Asset | Window | N Events | Status | AR Mean | AR t | Alpha (2bp) | t (2bp) | Beta | H1 Alpha | H2 Alpha | Alpha (5bp) | t (5bp) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| refinery/pipeline outage | XLE | [0,+1] | 4 | too few | — | — | — | — | — | — | — | — | — |
| refinery/pipeline outage | XLE | [0,+3] | 4 | too few | — | — | — | — | — | — | — | — | — |
| refinery/pipeline outage | XLE | [0,+5] | 4 | too few | — | — | — | — | — | — | — | — | — |
| strike/shutdown | XLI | [0,+1] | 5 | too few | — | — | — | — | — | — | — | — | — |
| strike/shutdown | XLI | [0,+3] | 5 | too few | — | — | — | — | — | — | — | — | — |
| strike/shutdown | XLI | [0,+5] | 5 | too few | — | — | — | — | — | — | — | — | — |
| chip/export control | SMH | [0,+1] | 295 | FAIL | +0.28% | 6.65 | -0.68% | -0.42 | 0.01 | +0.00% | -1.60% | -3.27% | -1.90 |
| chip/export control | SMH | [0,+3] | 295 | FAIL | +0.07% | 0.72 | -1.29% | -0.57 | 0.02 | +0.00% | -3.05% | -3.87% | -1.67 |
| chip/export control | SMH | [0,+5] | 295 | FAIL | +0.20% | 1.85 | -1.63% | -0.69 | 0.03 | +0.00% | -3.80% | -4.22% | -1.75 |
| sanctions | GLD | [0,+1] | 21 | FAIL | -0.44% | -2.23 | -0.11% | -0.26 | 0.00 | +0.00% | -0.24% | -0.30% | -0.62 |
| sanctions | GLD | [0,+3] | 21 | FAIL | -2.59% | -6.60 | -0.93% | -1.14 | 0.00 | +0.00% | -1.87% | -1.11% | -1.32 |
| sanctions | GLD | [0,+5] | 21 | FAIL | -4.90% | -11.60 | -1.19% | -1.17 | 0.00 | +0.00% | -2.39% | -1.38% | -1.33 |

## Key Findings & Discussion

1. **Event Sparsity in News Feed:**
   Across the stored news database, specialized physical and geopolitical disruptions occur rarely:
   - `strike/shutdown`: 5 events (< 20 threshold, listed as `too few`).
   - `refinery/pipeline outage`: 4 events (< 20 threshold, listed as `too few`).
   - `hurricane/storm`: 1 events (< 20 threshold, listed as `too few`).
   Per PLAN.md acceptance rules, classes with fewer than 20 events are safely marked `too few` and skipped to prevent overfitting on tiny samples.

2. **Scored Classes:**
   - `chip/export control` (295 events) was evaluated across windows [0,+1], [0,+3], and [0,+5].
   - `sanctions` (21 events) was evaluated across windows [0,+1], [0,+3], and [0,+5].

3. **Execution & Cost Impact:**
   Transaction costs of 2 bp and 5 bp per side substantially drag short holding windows on sector ETFs,
   demonstrating that even modest abnormal reactions get eroded once bid-ask spread and turnover are applied.

4. **Verdict Conclusion:** `no edge`.
   Hard events identified by LLM headline classification do not demonstrate a robust, statistically significant
   long-only edge over QQQ that survives transaction costs and sample-half validation.
