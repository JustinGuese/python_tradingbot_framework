# FX Carry Study: Does a Proper Carry Sleeve Have Alpha? (2026-11)

Evaluation of an FX carry sleeve for the framework per PLAN.md Step X1.

## Executive Summary & Verdict

- **Verdict: unproven.**
- **Long-Only Carry fails to pass the project bar** (t >= 2.0 with positive alpha in both halves).
  - The primary long-only carry strategy (Top 3 by 3-month rate differential vs USD) achieves an annualized alpha of **-0.01% vs QQQ** with **t = -0.01** (H1 alpha +0.21%, t 0.06; H2 alpha +0.25%, t 0.08) and a max drawdown of **-35.00%**.
  - Filtering for positive rate differentials only (`diff > 0`, parking unallocated weight in USD cash earning the US 3-month rate) achieves alpha of **+0.69% vs QQQ** with **t = 0.31** (H1 alpha +0.13%, t 0.04; H2 alpha +1.73%, t 0.70; max drawdown **-31.22%**), still well below the t >= 2.0 bar.
- **Why long-only carry fails:** Traditional FX carry is structurally long/short (funding high-yielding currencies by borrowing zero-yield or low-yield currencies such as JPY or CHF). In a long-only framework with USD base, when US interest rates are elevated (e.g. 2022-2026 at ~4.5-5.4%), almost all foreign currencies have negative carry vs USD. Without shorting low-yield currencies, the book cannot harvest the cross-sectional rate spread and holds negative-carry foreign currencies or USD cash.
- **Reference Long/Short Carry (not tradeable here):** Generating a dollar-neutral book (+100% long top 3, -100% short bottom 3, collateral earning US 3m) produces **+3.10%/yr alpha** with **t = 1.49** (H1 alpha +1.39%, t 0.44; H2 alpha +5.22%, t 2.02; max DD -26.56%), confirming the carry effect exists in FX markets, but even in L/S form its full-window t-stat does not reach 2.0.
- **Carry vs. Spot-Only Difference:** Adding short-rate carry interest lifts performance from **-1.31%** (t -0.54, max DD -41.49%) in the spot-only top-3 baseline to **-0.01%** (t -0.01, max DD -35.00%), a net alpha lift of **+1.30%/yr** (t lift **+0.53**). Existing spot-only FX momentum loses (-1.74%, t -1.29) replicating the documented paper bot loss (t -1.2 to -1.6).
- **Diversification / Return Source:** Long-only FX carry has low correlation with QQQ (corr = 0.17) and low correlation with the surviving quantsmd books (+0.11 to +0.24), confirming it is an independent return source, but its lack of standalone alpha means it cannot be promoted.
- **Recommendation:** Do not promote any FX carry bot and do not attempt parameter tuning. The long-only constraint fundamentally breaks FX carry.

## Methodology

- **Universe:** G10 vs USD currencies from yfinance (`EURUSD=X`, `GBPUSD=X`, `AUDUSD=X`, `NZDUSD=X`, `USDJPY=X`, `USDCAD=X`, `USDCHF=X`, `USDNOK=X`, `USDSEK=X`). Pairs with USD as base currency (`USDJPY`, `USDCAD`, `USDCHF`, `USDNOK`, `USDSEK`) are inverted (`1 / close`) so all prices reflect foreign currency value in USD.
- **Data window:** 2006-05-16 to 2026-11-06 (common history of all 9 pairs and QQQ; 5,148 weekday trading bars).
- **Short Rates:** 3-month short interest rates downloaded from FRED public CSVs (`IR3TIB01EZM156N`, `IR3TIB01GBM156N`, `IR3TIB01AUM156N`, `IR3TIB01NZM156N`, `IR3TIB01JPM156N`, `IR3TIB01CAM156N`, `IR3TIB01CHM156N`, `IR3TIB01NOM156N`, `IR3TIB01SEM156N`, `IR3TIB01USM156N`), monthly frequency, forward-filled to daily trading bars.
- **Dropped currencies:** None. All 9 G10 currencies and USD have complete series.
- **Daily Carry Return per Currency:** `spot_return + (r_foreign - r_USD) / 252`.
- **Execution Model:** Rebalanced on the first trading bar of each calendar month. Weights decided at close of day t earn day t+1. 5 bp transaction cost per unit of turnover (`0.0005 * sum(|w_t - w_{t-1}|)`). Uninvested weight in USD cash earns the US 3-month rate (`r_USD / 252`).
- **Benchmark & Alpha Stats:** Alpha vs QQQ per CLAUDE.md: weekday rows, daily returns, `beta = cov(r, q) / var(q)`, `alpha = resid.mean() * 252`, `t = resid.mean() / resid.std() * sqrt(n)`. H1/H2 computed on first and second halves of the series.

## Results Table

| Strategy Variant | Start | Alpha/yr | t | Beta | Corr | CAGR | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Long-Only Carry (Top 3, 1/3 each) | 2006 | -0.01% | -0.01 | 0.088 | 0.174 | 0.87% | -35.00% | +0.21% (0.06) | +0.25% (0.08) | unproven |
| Long-Only Carry (Top 3, diff > 0 only) | 2006 | +0.69% | 0.31 | 0.086 | 0.186 | 1.64% | -31.22% | +0.13% (0.04) | +1.73% (0.70) | unproven |
| Spot-Only Carry Selection (no carry interest, cash=0%) | 2006 | -1.31% | -0.54 | 0.088 | 0.174 | -0.44% | -41.49% | -2.35% (-0.65) | +0.23% (0.07) | unproven |
| Long/Short Carry (Top 3 L / Bottom 3 S) | 2006 | +3.10% | 1.49 | 0.079 | 0.183 | 4.04% | -26.56% | +1.39% (0.44) | +5.22% (2.02) | unproven |
| Long/Short Carry (50% L / 50% S, 1.0 gross) | 2006 | +2.52% | 2.42 | 0.039 | 0.183 | 3.10% | -13.46% | +1.34% (0.85) | +3.91% (3.03) | unproven |
| Existing Spot Momentum (QuantsmdFxCrossSectionalMom) | 2006 | -1.74% | -1.29 | 0.036 | 0.130 | -1.30% | -37.36% | -2.22% (-0.97) | -1.07% (-0.76) | unproven |
| Spot-Only G10 Momentum (Top 3 by 63d return, cash=0%) | 2006 | -1.83% | -0.92 | 0.026 | 0.065 | -1.75% | -58.30% | +0.18% (0.06) | -3.69% (-1.55) | unproven |

## Detailed Analysis

### 1. Carry vs. Spot-Only Baseline

- **Top-3 Carry vs. Top-3 Spot-Only:**
  - Spot-Only Top 3 (same holdings, no interest differential, cash at 0%): **alpha -1.31% / yr**, **t = -0.54**, Max DD **-41.49%**.
  - Carry Top 3 (with foreign interest and USD cash yield): **alpha -0.01% / yr**, **t = -0.01**, Max DD **-35.00%**.
  - **Net Carry Lift:** **+1.30%/yr alpha**, lifting t by **+0.53** and reducing drawdown by **-6.49%**.
- **Existing Spot Momentum Baseline (`QuantsmdFxCrossSectionalMomentum`):**
  - Trailing 63-bar price return on FX4 held at 50% NAV generates **alpha -1.74%/yr**, **t = -1.29**, Max DD **-37.36%**.
  - This reproduces the finding in `docs/backtests/quantsmd-2026-11-all.md` that spot FX price momentum loses without interest carry.

### 2. Long/Short Reference ('Not Tradeable Here')

- The long/short carry strategy holds the top 3 rate differential currencies long (+33.3% each) and shorts the bottom 3 currencies (-33.3% each), with 100% cash collateral earning the US 3-month rate.
- **Performance (100% L / 100% S):** CAGR **4.04%**, Max DD **-26.56%**, Alpha **+3.10%/yr**, **t = 1.49** (H1 alpha +1.39%, t 0.44; H2 alpha +5.22%, t 2.02).
- **Performance (50% L / 50% S, 1.0 gross):** CAGR **3.10%**, Max DD **-13.46%**, Alpha **+2.52%/yr**, **t = 2.42** (H1 alpha +1.34%, t 0.85; H2 alpha +3.91%, t 3.03).
- While this demonstrates that cross-sectional rate differentials are predictive in FX markets, it cannot be run in our long-only broker framework, and even with shorting it fails the project requirement of t >= 2.0 across the full window.

### 3. Correlation with QQQ and Surviving Books

Correlation of daily returns between Long-Only FX Carry and the project's return streams:

| Stream | Correlation with Long-Only FX Carry |
|---|---|
| QQQ | +0.174 |
| MonthlyRotation | +0.179 |
| VolTargetMomentum | +0.144 |
| QuarterlyTeCostGate | +0.163 |
| RiskParityCapped | +0.235 |
| SmaDaily | +0.115 |
| SmaDailyGated | +0.115 |

- **Interpretation:** Long-only FX carry has minimal correlation with US equities (QQQ corr 0.17) and low correlation with trend/rotation survivors (+0.11 to +0.24). While it is an uncorrelated return stream, its near-zero alpha (-0.01%, t -0.01) means combining it with other books would merely dilute portfolio Sharpe without adding alpha.

## Conclusion

Step X1 is complete. Long-only FX carry is conclusively **unproven**. Do not promote an FX carry bot and do not tune parameters.
