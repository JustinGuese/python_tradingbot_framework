# Form 8-K Buyback Announcements: Sector-Hedged Sleeve Backtest Study

**Verdict:** `unproven (fails bar)`. Offsetting active buyback drift longs with equal-weighted sector ETF shorts
isolates idiosyncratic corporate alpha from sector momentum and market beta.

## Overview

Tests whether hedging S&P 100 buyback drift long positions with sector ETFs (SECTORS9 via `sector_map.py`)
improves risk-adjusted return, residual volatility, and walk-forward stability vs holding benchmark QQQ.

- **Universe:** S&P 100 constituents (long positions) hedged with `SECTORS9` (`XLB`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLU`, `XLV`, `XLY`).
- **Signal:** Form 8-K buyback authorizations (Item 8.01 / 7.01), 10-session holding window, equal-weight capped at 0.5 each.
- **Idle Cash:** Uninvested capacity parked in `SHV` (cash proxy) via `park_idle_cash`, earning T-bill yield without leverage.
- **Short Carry:** Flat 1.0%/yr borrow fee on short ETF market value.
- **Baselines:** Benchmark QQQ and equal-weight hold of SP100.
- **Walk-Forward:** H1 in-sample (2010-2018) vs H2 out-of-sample (2019-2026).
- **Execution Costs:** 5 bp and 20 bp per side.

## Pre-Registered Variants (Anti-p-Hacking Grid)

| Variant | Description | Short Hedge Target |
|---|---|---|
| `qqq_hedge` | Baseline QQQ short hedge (`BuybackHedgedBot`) | QQQ |
| `sector_hedge` | Exact sector ETF short hedge (`BuybackSectorHedgedBot`) | SECTORS9 (`sector_map.sector_etf`) |
| `blend_50_50` | 50% QQQ + 50% sector ETF hedge | QQQ (50%) + SECTORS9 (50%) |

## Performance & Alpha Metrics vs QQQ (at 5 bp / side)

| Variant | CAGR (0% cash) | CAGR (T-bill) | Alpha vs QQQ | t-stat | Beta | Corr | Resid Vol | H1 Alpha (t) | H2 Alpha (t) | Pass? |
|---|---|---|---|---|---|---|---|---|---|---|
| `qqq_hedge` | -2.55% | -1.46% | +2.34% | 0.82 | -0.16 | -0.28 | 11.71% | -4.91% (-1.88) | +11.25% (2.15) | FAIL |
| `sector_hedge` | -2.01% | -0.91% | +1.12% | 0.46 | -0.08 | -0.17 | 9.98% | -5.40% (-2.19) | +9.19% (2.16) | FAIL |
| `blend_50_50` | -2.22% | -1.13% | +1.73% | 0.68 | -0.12 | -0.24 | 10.38% | -5.15% (-2.12) | +10.22% (2.25) | FAIL |

## Comparison at Higher Execution Costs (20 bp / side)

| Variant | CAGR (T-bill) | Alpha vs QQQ | t-stat | H1 Alpha (t) | H2 Alpha (t) | Alpha vs EW SP100 (t) | Pass? |
|---|---|---|---|---|---|---|---|
| `qqq_hedge` | -6.80% | -3.22% | -1.12 | -10.45% (-3.98) | +5.68% (1.08) | -6.41% (-2.15) | FAIL |
| `sector_hedge` | -7.05% | -5.25% | -2.14 | -11.75% (-4.68) | +2.79% (0.65) | -7.04% (-2.83) | FAIL |
| `blend_50_50` | -6.87% | -4.23% | -1.66 | -11.10% (-4.51) | +4.23% (0.93) | -6.72% (-2.56) | FAIL |

## Key Findings & Decisions

1. **Walk-Forward Winner:** `qqq_hedge` achieved H2 alpha of +11.25% (t=2.15).
2. **Residual Volatility:** Sector hedging produced residual volatility of 9.98% vs 11.71% for the broad QQQ hedge.
3. **Market Beta:** Beta to QQQ remained low across all variants (-0.08 for sector hedge).
4. **Cash Parking Benefit:** Parking idle cash into SHV added +1.09% CAGR over 0% cash for `qqq_hedge` across the study period.

## Implementation Notes

- Live bot implemented in `tradingbot/buybacksectorhedgedbot.py` with `ALLOW_SHORT=True`, `MAX_GROSS=2.0`, and `SHORTABLE=tuple(SECTORS9)`.
- Uses `tradingbot.utils.sector_map.sector_etf` for mapping equity tickers to their corresponding sector ETFs.
- Uses `tradingbot.utils.cash_park.park_idle_cash` to park unallocated capacity into SHV.
