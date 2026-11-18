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
| 2010 | 507 | 480 | 27 | 5.3% |
| 2011 | 505 | 480 | 25 | 5.0% |
| 2012 | 505 | 480 | 25 | 5.0% |
| 2013 | 504 | 480 | 24 | 4.8% |
| 2014 | 503 | 480 | 23 | 4.6% |
| 2015 | 505 | 480 | 25 | 5.0% |
| 2016 | 505 | 480 | 25 | 5.0% |
| 2017 | 504 | 480 | 24 | 4.8% |
| 2018 | 503 | 480 | 23 | 4.6% |
| 2019 | 502 | 480 | 22 | 4.4% |
| 2020 | 502 | 480 | 22 | 4.4% |
| 2021 | 502 | 480 | 22 | 4.4% |
| 2022 | 499 | 480 | 19 | 3.8% |
| 2023 | 499 | 480 | 19 | 3.8% |
| 2024 | 499 | 480 | 19 | 3.8% |
| 2025 | 499 | 480 | 19 | 3.8% |
| 2026 | 498 | 480 | 18 | 3.6% |

## Results: Pre-Registered Grid (12 Variants)

### Baseline (5 bp Cost, 1.0%/yr Borrow Drag, Cash at 0%)

| Variant | Reversal / Momentum | k (per side) | Window | Alpha vs QQQ | Beta | Corr | MaxDD | Alpha vs EW | Turnover/mo |
|---|---|---|---|---|---|---|---|---|---|
| rev0_mom100_k15 | 0/100 | 15 | H1 | +7.7% (0.89) | 0.02 | 0.01 | -42.0% | n/a | 63.2% |
| rev0_mom100_k15 | 0/100 | 15 | H2 | +1.3% (0.18) | -0.04 | -0.05 | -51.9% | n/a | 63.2% |
| rev0_mom100_k15 | 0/100 | 15 | FULL | +4.7% (0.84) | -0.03 | -0.03 | -51.9% | n/a | 63.2% |
| rev0_mom100_k25 | 0/100 | 25 | H1 | +7.8% (0.91) | -0.02 | -0.01 | -42.0% | n/a | 59.4% |
| rev0_mom100_k25 | 0/100 | 25 | H2 | -0.8% (-0.13) | -0.02 | -0.03 | -47.3% | n/a | 59.4% |
| rev0_mom100_k25 | 0/100 | 25 | FULL | +3.8% (0.70) | -0.02 | -0.02 | -47.3% | n/a | 59.4% |
| rev0_mom100_k40 | 0/100 | 40 | H1 | +7.6% (0.88) | -0.03 | -0.01 | -42.0% | n/a | 56.0% |
| rev0_mom100_k40 | 0/100 | 40 | H2 | -3.5% (-0.62) | -0.04 | -0.05 | -51.7% | n/a | 56.0% |
| rev0_mom100_k40 | 0/100 | 40 | FULL | +2.4% (0.46) | -0.04 | -0.03 | -51.7% | n/a | 56.0% |
| rev30_mom70_k15 | 30/70 | 15 | H1 | +7.9% (1.01) | -0.05 | -0.02 | -37.0% | n/a | 102.0% |
| rev30_mom70_k15 | 30/70 | 15 | H2 | -3.8% (-0.61) | 0.02 | 0.02 | -47.5% | n/a | 102.0% |
| rev30_mom70_k15 | 30/70 | 15 | FULL | +2.4% (0.48) | 0.01 | 0.01 | -54.3% | n/a | 102.0% |
| rev30_mom70_k25 | 30/70 | 25 | H1 | +8.0% (1.03) | -0.05 | -0.02 | -37.0% | n/a | 97.7% |
| rev30_mom70_k25 | 30/70 | 25 | H2 | -4.5% (-0.82) | 0.01 | 0.02 | -50.0% | n/a | 97.7% |
| rev30_mom70_k25 | 30/70 | 25 | FULL | +2.2% (0.44) | 0.01 | 0.01 | -56.1% | n/a | 97.7% |
| rev30_mom70_k40 | 30/70 | 40 | H1 | +7.8% (1.01) | -0.03 | -0.01 | -37.0% | n/a | 92.6% |
| rev30_mom70_k40 | 30/70 | 40 | H2 | -2.3% (-0.47) | 0.01 | 0.02 | -42.9% | n/a | 92.6% |
| rev30_mom70_k40 | 30/70 | 40 | FULL | +3.1% (0.65) | 0.01 | 0.01 | -51.5% | n/a | 92.6% |
| rev50_mom50_k15 | 50/50 | 15 | H1 | +14.9% (1.90) | -0.05 | -0.02 | -34.9% | n/a | 121.5% |
| rev50_mom50_k15 | 50/50 | 15 | H2 | -5.8% (-0.93) | 0.05 | 0.06 | -56.1% | n/a | 121.5% |
| rev50_mom50_k15 | 50/50 | 15 | FULL | +5.3% (1.03) | 0.03 | 0.03 | -56.1% | n/a | 121.5% |
| rev50_mom50_k25 | 50/50 | 25 | H1 | +15.3% (1.96) | -0.03 | -0.01 | -34.9% | n/a | 118.4% |
| rev50_mom50_k25 | 50/50 | 25 | H2 | -6.6% (-1.24) | 0.04 | 0.07 | -57.5% | n/a | 118.4% |
| rev50_mom50_k25 | 50/50 | 25 | FULL | +5.1% (1.05) | 0.03 | 0.03 | -57.5% | n/a | 118.4% |
| rev50_mom50_k40 | 50/50 | 40 | H1 | +15.1% (1.95) | -0.04 | -0.02 | -34.9% | n/a | 114.9% |
| rev50_mom50_k40 | 50/50 | 40 | H2 | -4.6% (-0.98) | 0.05 | 0.09 | -48.1% | n/a | 114.9% |
| rev50_mom50_k40 | 50/50 | 40 | FULL | +5.9% (1.26) | 0.04 | 0.03 | -48.9% | n/a | 114.9% |
| rev100_mom0_k15 | 100/0 | 15 | H1 | +9.2% (1.16) | 0.00 | 0.00 | -39.0% | n/a | 137.5% |
| rev100_mom0_k15 | 100/0 | 15 | H2 | -6.8% (-0.89) | 0.10 | 0.11 | -65.0% | n/a | 137.5% |
| rev100_mom0_k15 | 100/0 | 15 | FULL | +1.7% (0.31) | 0.08 | 0.06 | -65.0% | n/a | 137.5% |
| rev100_mom0_k25 | 100/0 | 25 | H1 | +9.3% (1.19) | -0.03 | -0.01 | -39.0% | n/a | 135.2% |
| rev100_mom0_k25 | 100/0 | 25 | H2 | -4.7% (-0.72) | 0.10 | 0.13 | -55.5% | n/a | 135.2% |
| rev100_mom0_k25 | 100/0 | 25 | FULL | +2.7% (0.53) | 0.08 | 0.07 | -55.5% | n/a | 135.2% |
| rev100_mom0_k40 | 100/0 | 40 | H1 | +9.3% (1.19) | -0.02 | -0.01 | -39.0% | n/a | 131.7% |
| rev100_mom0_k40 | 100/0 | 40 | H2 | -5.0% (-0.86) | 0.11 | 0.16 | -52.5% | n/a | 131.7% |
| rev100_mom0_k40 | 100/0 | 40 | FULL | +2.6% (0.53) | 0.09 | 0.08 | -52.5% | n/a | 131.7% |

### Net Return Sensitivity: Cash Yield and Retail Friction

| Variant | Window | CAGR (0% Cash, 5bp) | CAGR (+^IRX Cash, 5bp) | CAGR (0% Cash, 20bp) | Borrow Drag/yr |
|---|---|---|---|---|---|
| rev0_mom100_k15 | H1 | +4.6% | +5.0% | +3.8% | 0.90% |
| rev0_mom100_k15 | H2 | -1.6% | +1.2% | -3.1% | 0.90% |
| rev0_mom100_k15 | FULL | +1.7% | +3.2% | +0.5% | 0.90% |
| rev0_mom100_k25 | H1 | +4.6% | +5.0% | +3.8% | 0.90% |
| rev0_mom100_k25 | H2 | -2.8% | -0.1% | -4.1% | 0.90% |
| rev0_mom100_k25 | FULL | +1.1% | +2.6% | +0.0% | 0.90% |
| rev0_mom100_k40 | H1 | +4.3% | +4.7% | +3.5% | 0.90% |
| rev0_mom100_k40 | H2 | -5.5% | -2.8% | -6.7% | 0.90% |
| rev0_mom100_k40 | FULL | -0.3% | +1.1% | -1.3% | 0.90% |
| rev30_mom70_k15 | H1 | +5.2% | +5.6% | +3.7% | 0.90% |
| rev30_mom70_k15 | H2 | -4.8% | -2.1% | -7.0% | 0.90% |
| rev30_mom70_k15 | FULL | +0.4% | +1.9% | -1.4% | 0.90% |
| rev30_mom70_k25 | H1 | +5.4% | +5.8% | +3.9% | 0.90% |
| rev30_mom70_k25 | H2 | -5.2% | -2.6% | -7.3% | 0.90% |
| rev30_mom70_k25 | FULL | +0.3% | +1.8% | -1.5% | 0.90% |
| rev30_mom70_k40 | H1 | +5.3% | +5.7% | +3.8% | 0.90% |
| rev30_mom70_k40 | H2 | -3.0% | -0.3% | -4.9% | 0.90% |
| rev30_mom70_k40 | FULL | +1.3% | +2.9% | -0.3% | 0.90% |
| rev50_mom50_k15 | H1 | +12.9% | +13.3% | +11.0% | 0.90% |
| rev50_mom50_k15 | H2 | -6.1% | -3.5% | -8.6% | 0.90% |
| rev50_mom50_k15 | FULL | +3.6% | +5.2% | +1.4% | 0.90% |
| rev50_mom50_k25 | H1 | +13.4% | +13.9% | +11.5% | 0.90% |
| rev50_mom50_k25 | H2 | -6.5% | -3.9% | -9.0% | 0.90% |
| rev50_mom50_k25 | FULL | +3.6% | +5.2% | +1.5% | 0.90% |
| rev50_mom50_k40 | H1 | +13.2% | +13.6% | +11.3% | 0.90% |
| rev50_mom50_k40 | H2 | -4.3% | -1.7% | -6.7% | 0.90% |
| rev50_mom50_k40 | FULL | +4.7% | +6.2% | +2.5% | 0.90% |
| rev100_mom0_k15 | H1 | +6.6% | +7.0% | +4.5% | 0.90% |
| rev100_mom0_k15 | H2 | -6.7% | -4.1% | -9.5% | 0.90% |
| rev100_mom0_k15 | FULL | +0.2% | +1.7% | -2.3% | 0.90% |
| rev100_mom0_k25 | H1 | +6.7% | +7.1% | +4.6% | 0.90% |
| rev100_mom0_k25 | H2 | -4.1% | -1.5% | -6.9% | 0.90% |
| rev100_mom0_k25 | FULL | +1.5% | +3.0% | -0.9% | 0.90% |
| rev100_mom0_k40 | H1 | +6.7% | +7.2% | +4.7% | 0.90% |
| rev100_mom0_k40 | H2 | -3.8% | -1.1% | -6.5% | 0.90% |
| rev100_mom0_k40 | FULL | +1.7% | +3.2% | -0.7% | 0.90% |

## Verdict and Promotion Assessment

- **Bar**: alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.
- **Best H2 t-stat**: 0.18 across all 12 variants.
- **Max |beta| on H2**: 0.11 across all variants.
- **Outcome**: FAIL (no variant passed the promotion bar).
