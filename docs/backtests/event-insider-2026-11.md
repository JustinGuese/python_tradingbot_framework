# Insider Cluster Buys (Form 4) — Backtest Study

**Verdict:** `no edge`

## Overview

Tests whether open-market insider cluster purchases (SEC Form 4, transaction code `P`) across S&P 100 companies generate statistically significant abnormal returns or alpha vs holding QQQ.

- **Data Source:** SEC EDGAR Form 4 filings & DERA quarterly insider transactions datasets (2006 to 2026).
- **Clusters Found:** Found 267 base insider clusters (>= 2 distinct insiders buying within 10 days, >= $50k) across S&P 100 constituents.
- **Universe:** S&P 100 constituents (`tradingbot.utils.universes.SP100`).
- **Benchmark:** QQQ (daily close-to-close).
- **Look-Ahead Guard:** Entry is strictly the first daily close after the filing is public (next session close if timestamped at filing date).
- **Costs Applied:** 2 bp/side (4 bp round trip) and 5 bp/side (10 bp round trip).
- **Evaluation Bar (PLAN.md):** `t >= 2.0`, positive alpha in **both** halves (`H1 > 0`, `H2 > 0`), and `beta < 0.5`. Edge requires surviving a 2000-draw placebo test (`p < 0.05`).

## Pre-registered Variants Table (12 variants)

| Variant | Hold | Min Size | Min $ | N Clusters | AR Mean | AR t | Win% | Gross Alpha (t) | Net 2bp Alpha (t) | Net 5bp Alpha (t) | Beta | H1 Alpha (t) | H2 Alpha (t) | Pass? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hold_10_size_2_usd_50k | 10d | 2 | $50k | 267 | +0.17% | 0.25 | 46.4% | -7.77% (-1.66) | -8.10% (-1.73) | -8.59% (-1.84) | 0.49 | -9.23% (-1.23) | -6.27% (-1.16) | FAIL |
| hold_10_size_2_usd_250k | 10d | 2 | $250k | 233 | +0.15% | 0.20 | 45.9% | -7.72% (-1.74) | -8.02% (-1.81) | -8.47% (-1.91) | 0.43 | -7.24% (-1.04) | -8.35% (-1.56) | FAIL |
| hold_10_size_3_usd_50k | 10d | 3 | $50k | 88 | +1.47% | 0.85 | 44.3% | -1.58% (-0.33) | -1.72% (-0.36) | -1.92% (-0.40) | 0.31 | -2.46% (-0.28) | +0.03% (0.01) | FAIL |
| hold_10_size_3_usd_250k | 10d | 3 | $250k | 84 | +1.59% | 0.89 | 44.0% | -0.78% (-0.17) | -0.91% (-0.20) | -1.10% (-0.24) | 0.25 | -1.13% (-0.14) | +0.03% (0.01) | FAIL |
| hold_20_size_2_usd_50k | 20d | 2 | $50k | 267 | -0.05% | -0.06 | 51.7% | -0.20% (-0.04) | -0.47% (-0.10) | -0.86% (-0.18) | 0.60 | -5.26% (-0.74) | +5.09% (0.84) | FAIL |
| hold_20_size_2_usd_250k | 20d | 2 | $250k | 233 | -0.12% | -0.14 | 51.5% | +0.84% (0.18) | +0.60% (0.13) | +0.23% (0.05) | 0.55 | -2.86% (-0.41) | +4.67% (0.77) | FAIL |
| hold_20_size_3_usd_50k | 20d | 3 | $50k | 88 | +1.10% | 0.60 | 48.9% | +0.33% (0.07) | +0.21% (0.04) | +0.03% (0.01) | 0.40 | -2.44% (-0.28) | +4.08% (0.98) | FAIL |
| hold_20_size_3_usd_250k | 20d | 3 | $250k | 84 | +1.12% | 0.60 | 50.0% | +1.32% (0.28) | +1.21% (0.26) | +1.03% (0.22) | 0.31 | -0.84% (-0.10) | +4.08% (0.98) | FAIL |
| hold_60_size_2_usd_50k | 60d | 2 | $50k | 267 | +2.15% | 1.52 | 54.3% | +3.66% (0.76) | +3.52% (0.73) | +3.30% (0.68) | 0.79 | -0.24% (-0.04) | +8.10% (1.20) | FAIL |
| hold_60_size_2_usd_250k | 60d | 2 | $250k | 233 | +2.11% | 1.34 | 55.4% | +1.48% (0.30) | +1.34% (0.27) | +1.13% (0.23) | 0.78 | -2.62% (-0.39) | +6.11% (0.86) | FAIL |
| hold_60_size_3_usd_50k | 60d | 3 | $50k | 88 | +6.03% | 1.96 | 52.3% | +4.85% (0.88) | +4.76% (0.86) | +4.62% (0.83) | 0.58 | +2.05% (0.23) | +8.90% (1.47) | FAIL |
| hold_60_size_3_usd_250k | 60d | 3 | $250k | 84 | +6.25% | 1.95 | 53.6% | +4.40% (0.78) | +4.31% (0.77) | +4.17% (0.74) | 0.57 | +1.14% (0.13) | +8.90% (1.47) | FAIL |

## Key Findings

1. **Cluster Frequency in Large-Cap Equities:** Insider open-market purchases (Code `P`) in S&P 100 companies are relatively rare compared to stock awards or option exercises. Over the 20-year sample (2006-2026), 267 multi-insider cluster events were identified.
2. **Best variant by net-2bp t:** hold_60_size_3_usd_50k (alpha +4.76%/yr, t 0.86).
3. **Variants with positive alpha in both halves:** 2 of 12.
4. **Verdict Assessment:** Final study verdict is `no edge` against the pre-registered acceptance criteria.