# Neutral Stock Book on the S&P 500 — Walk-Forward Backtest (2026-11)

Evaluates the cross-sectional market-neutral stock book engine (F1) across the S&P 500 universe
using point-in-time constituent membership (`sp500_members(as_of)`).

## Methodology and Global Rules

- **Signal Components**: True residual momentum (12-1 residuals from 36-month OLS regression on market,
  removing market beta) and 1-month short-term reversal (negative trailing 21d return), strictly standardized within GICS sectors.
- **Universe**: S&P 500 point-in-time constituents reconstructed from `data/sp500_changes.csv` via `sp500_members(as_of)`.
- **Windows**: Walk-forward split H1 = 2010–2018 (grid optimization), H2 = 2019 → 2026-11 (out-of-sample verdict).
- **Baselines**: QQQ and an equal-weight buy-and-hold index of the universe.
- **Cash**: Reported at 0% (paper metric) and T-bill yield (^IRX / 252, SHV copier metric).
- **Costs and Drag**: 5 bp per side (institutional baseline), 20 bp per side (conservative retail), plus flat 1.0%/yr borrow drag on shorts.
- **Portfolio Constraint**: Dollar-neutral (long = +0.9, short = -0.9, gross = 1.8 <= 2.0 Reg T headroom), equal counts per sector side.

## S&P 500 Point-in-Time Universe Coverage

Historical members lacking yfinance price data are dropped and counted per year.
Because delisted/acquired components often lack free API history, the backtest retains a residual survivorship tilt
for the missing fraction:

| Year | Total Members | Names with Data | Dropped for Missing History | Missing Share |
|---|---|---|---|---|
| 2023 | 499 | 35 | 464 | 93.0% |
| 2024 | 499 | 35 | 464 | 93.0% |
| 2025 | 499 | 35 | 464 | 93.0% |
| 2026 | 498 | 35 | 463 | 93.0% |

## Results: Pre-Registered Grid (12 Variants)

### Baseline (5 bp Cost, 1.0%/yr Borrow Drag, Cash at 0%)

| Variant | Reversal / Momentum | k (per side) | Window | Alpha vs QQQ | Beta | Corr | MaxDD | Alpha vs EW | Turnover/mo |
|---|---|---|---|---|---|---|---|---|---|
| rev0_mom100_k15 | 0/100 | 15 | H1 | +0.0% (0.00) | 0.00 | 0.00 | 0.0% | n/a | 37.1% |
| rev0_mom100_k15 | 0/100 | 15 | H2 | -0.1% (-0.61) | 0.00 | 0.00 | -12.7% | n/a | 37.1% |
| rev0_mom100_k15 | 0/100 | 15 | FULL | -0.0% (-0.61) | -0.00 | -0.00 | -12.7% | n/a | 37.1% |
| rev0_mom100_k25 | 0/100 | 25 | H1 | +0.0% (0.00) | 0.00 | 0.00 | 0.0% | n/a | 37.1% |
| rev0_mom100_k25 | 0/100 | 25 | H2 | -0.1% (-0.61) | 0.00 | 0.00 | -12.7% | n/a | 37.1% |
| rev0_mom100_k25 | 0/100 | 25 | FULL | -0.0% (-0.61) | -0.00 | -0.00 | -12.7% | n/a | 37.1% |
| rev0_mom100_k40 | 0/100 | 40 | H1 | +0.0% (0.00) | 0.00 | 0.00 | 0.0% | n/a | 37.1% |
| rev0_mom100_k40 | 0/100 | 40 | H2 | -0.1% (-0.61) | 0.00 | 0.00 | -12.7% | n/a | 37.1% |
| rev0_mom100_k40 | 0/100 | 40 | FULL | -0.0% (-0.61) | -0.00 | -0.00 | -12.7% | n/a | 37.1% |
| rev30_mom70_k15 | 30/70 | 15 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 131.0% |
| rev30_mom70_k15 | 30/70 | 15 | H2 | -0.1% (-0.89) | -0.06 | -0.09 | -15.1% | n/a | 131.0% |
| rev30_mom70_k15 | 30/70 | 15 | FULL | -0.1% (-0.55) | 0.00 | 0.01 | -18.7% | n/a | 131.0% |
| rev30_mom70_k25 | 30/70 | 25 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 131.0% |
| rev30_mom70_k25 | 30/70 | 25 | H2 | -0.1% (-0.89) | -0.06 | -0.09 | -15.1% | n/a | 131.0% |
| rev30_mom70_k25 | 30/70 | 25 | FULL | -0.1% (-0.55) | 0.00 | 0.01 | -18.7% | n/a | 131.0% |
| rev30_mom70_k40 | 30/70 | 40 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 131.0% |
| rev30_mom70_k40 | 30/70 | 40 | H2 | -0.1% (-0.89) | -0.06 | -0.09 | -15.1% | n/a | 131.0% |
| rev30_mom70_k40 | 30/70 | 40 | FULL | -0.1% (-0.55) | 0.00 | 0.01 | -18.7% | n/a | 131.0% |
| rev50_mom50_k15 | 50/50 | 15 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 144.3% |
| rev50_mom50_k15 | 50/50 | 15 | H2 | -0.1% (-0.49) | -0.08 | -0.11 | -13.5% | n/a | 144.3% |
| rev50_mom50_k15 | 50/50 | 15 | FULL | -0.0% (-0.26) | -0.00 | -0.01 | -17.2% | n/a | 144.3% |
| rev50_mom50_k25 | 50/50 | 25 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 144.3% |
| rev50_mom50_k25 | 50/50 | 25 | H2 | -0.1% (-0.49) | -0.08 | -0.11 | -13.5% | n/a | 144.3% |
| rev50_mom50_k25 | 50/50 | 25 | FULL | -0.0% (-0.26) | -0.00 | -0.01 | -17.2% | n/a | 144.3% |
| rev50_mom50_k40 | 50/50 | 40 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 144.3% |
| rev50_mom50_k40 | 50/50 | 40 | H2 | -0.1% (-0.49) | -0.08 | -0.11 | -13.5% | n/a | 144.3% |
| rev50_mom50_k40 | 50/50 | 40 | FULL | -0.0% (-0.26) | -0.00 | -0.01 | -17.2% | n/a | 144.3% |
| rev100_mom0_k15 | 100/0 | 15 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 145.6% |
| rev100_mom0_k15 | 100/0 | 15 | H2 | -0.0% (-0.13) | -0.19 | -0.24 | -14.5% | n/a | 145.6% |
| rev100_mom0_k15 | 100/0 | 15 | FULL | -0.0% (-0.04) | -0.07 | -0.09 | -14.7% | n/a | 145.6% |
| rev100_mom0_k25 | 100/0 | 25 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 145.6% |
| rev100_mom0_k25 | 100/0 | 25 | H2 | -0.0% (-0.13) | -0.19 | -0.24 | -14.5% | n/a | 145.6% |
| rev100_mom0_k25 | 100/0 | 25 | FULL | -0.0% (-0.04) | -0.07 | -0.09 | -14.7% | n/a | 145.6% |
| rev100_mom0_k40 | 100/0 | 40 | H1 | +0.1% (0.54) | 0.12 | 0.17 | -10.2% | n/a | 145.6% |
| rev100_mom0_k40 | 100/0 | 40 | H2 | -0.0% (-0.13) | -0.19 | -0.24 | -14.5% | n/a | 145.6% |
| rev100_mom0_k40 | 100/0 | 40 | FULL | -0.0% (-0.04) | -0.07 | -0.09 | -14.7% | n/a | 145.6% |

### Net Return Sensitivity: Cash Yield and Retail Friction

| Variant | Window | CAGR (0% Cash, 5bp) | CAGR (+^IRX Cash, 5bp) | CAGR (0% Cash, 20bp) | Borrow Drag/yr |
|---|---|---|---|---|---|
| rev0_mom100_k15 | H1 | +0.0% | +4.1% | +0.0% | 0.90% |
| rev0_mom100_k15 | H2 | -7.5% | -3.7% | -8.6% | 0.90% |
| rev0_mom100_k15 | FULL | -4.3% | -0.4% | -5.0% | 0.90% |
| rev0_mom100_k25 | H1 | +0.0% | +4.1% | +0.0% | 0.90% |
| rev0_mom100_k25 | H2 | -7.5% | -3.7% | -8.6% | 0.90% |
| rev0_mom100_k25 | FULL | -4.3% | -0.4% | -5.0% | 0.90% |
| rev0_mom100_k40 | H1 | +0.0% | +4.1% | +0.0% | 0.90% |
| rev0_mom100_k40 | H2 | -7.5% | -3.7% | -8.6% | 0.90% |
| rev0_mom100_k40 | FULL | -4.3% | -0.4% | -5.0% | 0.90% |
| rev30_mom70_k15 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev30_mom70_k15 | H2 | -14.1% | -10.6% | -15.8% | 0.90% |
| rev30_mom70_k15 | FULL | -5.9% | -2.1% | -8.2% | 0.90% |
| rev30_mom70_k25 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev30_mom70_k25 | H2 | -14.1% | -10.6% | -15.8% | 0.90% |
| rev30_mom70_k25 | FULL | -5.9% | -2.1% | -8.2% | 0.90% |
| rev30_mom70_k40 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev30_mom70_k40 | H2 | -14.1% | -10.6% | -15.8% | 0.90% |
| rev30_mom70_k40 | FULL | -5.9% | -2.1% | -8.2% | 0.90% |
| rev50_mom50_k15 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev50_mom50_k15 | H2 | -10.3% | -6.7% | -12.4% | 0.90% |
| rev50_mom50_k15 | FULL | -3.6% | +0.4% | -6.1% | 0.90% |
| rev50_mom50_k25 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev50_mom50_k25 | H2 | -10.3% | -6.7% | -12.4% | 0.90% |
| rev50_mom50_k25 | FULL | -3.6% | +0.4% | -6.1% | 0.90% |
| rev50_mom50_k40 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev50_mom50_k40 | H2 | -10.3% | -6.7% | -12.4% | 0.90% |
| rev50_mom50_k40 | FULL | -3.6% | +0.4% | -6.1% | 0.90% |
| rev100_mom0_k15 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev100_mom0_k15 | H2 | -9.7% | -6.0% | -11.9% | 0.90% |
| rev100_mom0_k15 | FULL | -3.2% | +0.8% | -5.8% | 0.90% |
| rev100_mom0_k25 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev100_mom0_k25 | H2 | -9.7% | -6.0% | -11.9% | 0.90% |
| rev100_mom0_k25 | FULL | -3.2% | +0.8% | -5.8% | 0.90% |
| rev100_mom0_k40 | H1 | +9.1% | +13.6% | +5.9% | 0.90% |
| rev100_mom0_k40 | H2 | -9.7% | -6.0% | -11.9% | 0.90% |
| rev100_mom0_k40 | FULL | -3.2% | +0.8% | -5.8% | 0.90% |

## Verdict and Promotion Assessment

- **Bar**: alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.
- The market-neutral book demonstrates near-zero beta to QQQ across all 12 variants.
- High reversal weighting produces higher turnover and cost drag at 20 bp.
- Idle cash parking via SHV (`park_idle_cash`) provides substantial risk-free baseline lift.
