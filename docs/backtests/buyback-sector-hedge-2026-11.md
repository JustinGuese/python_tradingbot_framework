# Form 8-K Buyback Announcements: Sector-Hedged Sleeve Backtest Study

**Verdict:** `unproven`. Offsetting active buyback drift longs with equal-weighted sector ETF shorts
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
| `qqq_hedge` | +5.49% | +6.67% | +9.28% | 2.68 | -0.10 | -0.14 | 14.21% | +1.05% (0.30) | +18.80% (3.03) | **PASS** |
| `sector_hedge` | +6.48% | +7.67% | +8.45% | 2.75 | -0.01 | -0.02 | 12.59% | +0.77% (0.24) | +17.30% (3.17) | **PASS** |
| `blend_50_50` | +6.04% | +7.23% | +8.86% | 2.79 | -0.05 | -0.09 | 13.02% | +0.91% (0.28) | +18.05% (3.19) | **PASS** |

## Comparison at Higher Execution Costs (20 bp / side)

| Variant | CAGR (T-bill) | Alpha vs QQQ | t-stat | H1 Alpha (t) | H2 Alpha (t) | Alpha vs EW SP100 (t) | Pass? |
|---|---|---|---|---|---|---|---|
| `qqq_hedge` | +0.89% | +3.73% | 1.08 | -4.49% (-1.26) | +13.22% (2.13) | +1.94% (0.55) | FAIL |
| `sector_hedge` | +1.02% | +2.08% | 0.68 | -5.58% (-1.73) | +10.90% (2.00) | +1.54% (0.50) | FAIL |
| `blend_50_50` | +1.01% | +2.90% | 0.91 | -5.04% (-1.52) | +12.06% (2.14) | +1.74% (0.55) | FAIL |

## Key Findings & Decisions

1. **Walk-Forward Winner:** `qqq_hedge` achieved H2 alpha of +18.80% (t=3.03).
2. **Residual Volatility:** Sector hedging produced residual volatility of 12.59% vs 14.21% for the broad QQQ hedge.
3. **Market Beta:** Beta to QQQ remained low across all variants (-0.01 for sector hedge).
4. **Cash Parking Benefit:** Parking idle cash into SHV added substantial risk-free yield over 0% cash across the 16-year period.

## Implementation Notes

- Live bot implemented in `tradingbot/buybacksectorhedgedbot.py` with `ALLOW_SHORT=True`, `MAX_GROSS=2.0`, and `SHORTABLE=tuple(SECTORS9)`.
- Uses `tradingbot.utils.sector_map.sector_etf` for mapping equity tickers to their corresponding sector ETFs.
- Uses `tradingbot.utils.cash_park.park_idle_cash` to park unallocated capacity into SHV.
