# InstitutionalFlow on the S&P 500 — Walk-Forward Backtest (2026-11)

Evaluates whether the large-cap momentum + trend filter edge discovered on the S&P 100 
generalizes to the broader S&P 500 when using point-in-time constituent membership (`sp500_members(as_of)`).

## Method and Global Rules

- **Universe**: S&P 500 point-in-time constituents reconstructed from `data/sp500_changes.csv` via `sp500_members(as_of)`. 
Baseline comparison uses `SP100` (99 traded names).
- **Windows**: Walk-forward split H1 = 2019–2022 (grid), H2 = 2023 → 2026-11 (unseen judgment). Features warm from 2017.
- **Baselines**: QQQ and an equal-weight buy-and-hold of the active universe.
- **Cash**: Idle weight parked at 0% (paper metric) and T-bill yield (^IRX / 252, SHV copier metric).
- **Costs**: 5 bp per side (standard institutional) and 20 bp per side (conservative retail).

## S&P 500 Point-in-Time Universe Coverage

Historical members lacking yfinance price data are dropped and counted per year. 
Because delisted/acquired components often lack free API history, the backtest retains a residual survivorship tilt 
for the missing fraction:

| Year | Total Members | Names with Data | Dropped for Missing History | Missing Share |
|---|---|---|---|---|
| 2019 | 525 | 462 | 63 | 12.0% |
| 2020 | 518 | 464 | 54 | 10.4% |
| 2021 | 519 | 475 | 44 | 8.5% |
| 2022 | 519 | 483 | 36 | 6.9% |
| 2023 | 513 | 489 | 24 | 4.7% |
| 2024 | 515 | 496 | 19 | 3.7% |
| 2025 | 518 | 504 | 14 | 2.7% |
| 2026 | 517 | 509 | 8 | 1.5% |

**Sample dropped names per period**:

- **2019**: ABMD, ADS, AGN, ALXN, ANSS, ARNC, ATVI, AVB...
- **2020**: ABMD, ADS, AGN, ALXN, ANSS, ARNC, ATVI, AVB...
- **2021**: ABMD, ALXN, ANSS, ATVI, AVB, CERN, CMA, CTLT...
- **2022**: ABMD, ANSS, ATVI, AVB, CERN, CMA, CTLT, CTRA...
- **2023**: ANSS, ATVI, AVB, CMA, CTLT, CTRA, DAY, DFS...
- **2024**: ANSS, AVB, CMA, CTLT, CTRA, DAY, DFS, EA...
- **2025**: ANSS, AVB, CTRA, DAY, DFS, EA, HES, HOLX...
- **2026**: AVB, CTRA, DAY, EA, HOLX, KVUE, VYLR, WBD

## Results: Pre-Registered Variants

### Baseline (Cash at 0%, 5 bp cost)

| Variant | Universe | top_n | Rebalance | Gate | Window | Alpha vs QQQ | Beta | Corr | MaxDD | Alpha vs EW |
|---|---|---|---|---|---|---|---|---|---|---|
| sp100_top20_weekly_gate | sp100 | 20 | 4 | True | H1 | +8.0% (1.34) | 0.30 | 0.56 | -21.9% | +6.5% (1.10) |
| sp100_top20_weekly_gate | sp100 | 20 | 4 | True | H2 | +10.7% (1.68) | 0.48 | 0.61 | -14.9% | +7.5% (1.12) |
| sp100_top20_weekly_gate | sp100 | 20 | 4 | True | FULL | +10.3% (2.33) | 0.36 | 0.58 | -21.9% | +9.1% (1.99) |
| sp100_top10_weekly_gate | sp100 | 10 | 4 | True | H1 | +7.2% (1.05) | 0.35 | 0.56 | -26.6% | +5.5% (0.80) |
| sp100_top10_weekly_gate | sp100 | 10 | 4 | True | H2 | +13.8% (1.68) | 0.55 | 0.57 | -19.1% | +11.4% (1.31) |
| sp100_top10_weekly_gate | sp100 | 10 | 4 | True | FULL | +11.6% (2.14) | 0.42 | 0.55 | -26.6% | +10.4% (1.86) |
| sp500_top20_weekly_gate | sp500 | 20 | 4 | True | H1 | -0.6% (-0.10) | 0.28 | 0.51 | -22.0% | -0.8% (-0.12) |
| sp500_top20_weekly_gate | sp500 | 20 | 4 | True | H2 | +1.2% (0.18) | 0.45 | 0.55 | -20.6% | +5.6% (0.74) |
| sp500_top20_weekly_gate | sp500 | 20 | 4 | True | FULL | +1.2% (0.25) | 0.34 | 0.52 | -22.0% | +3.1% (0.63) |
| sp500_top10_weekly_gate | sp500 | 10 | 4 | True | H1 | +1.7% (0.24) | 0.29 | 0.48 | -21.3% | +1.6% (0.23) |
| sp500_top10_weekly_gate | sp500 | 10 | 4 | True | H2 | +5.0% (0.56) | 0.49 | 0.49 | -18.0% | +10.5% (1.11) |
| sp500_top10_weekly_gate | sp500 | 10 | 4 | True | FULL | +4.4% (0.78) | 0.36 | 0.47 | -21.3% | +6.7% (1.14) |
| sp500_top50_weekly_gate | sp500 | 50 | 4 | True | H1 | +2.2% (0.36) | 0.28 | 0.52 | -24.4% | +1.7% (0.28) |
| sp500_top50_weekly_gate | sp500 | 50 | 4 | True | H2 | +1.2% (0.22) | 0.39 | 0.58 | -15.8% | +3.9% (0.68) |
| sp500_top50_weekly_gate | sp500 | 50 | 4 | True | FULL | +2.3% (0.56) | 0.32 | 0.54 | -24.4% | +3.5% (0.84) |
| sp500_top20_weekly_nogate | sp500 | 20 | 4 | False | H1 | -6.2% (-0.85) | 0.47 | 0.65 | -30.7% | -6.6% (-0.89) |
| sp500_top20_weekly_nogate | sp500 | 20 | 4 | False | H2 | +1.7% (0.25) | 0.53 | 0.61 | -20.6% | +6.0% (0.81) |
| sp500_top20_weekly_nogate | sp500 | 20 | 4 | False | FULL | -2.0% (-0.39) | 0.49 | 0.63 | -30.7% | +0.2% (0.04) |
| sp500_top20_daily_gate | sp500 | 20 | None | True | H1 | +1.8% (0.28) | 0.24 | 0.45 | -16.1% | +1.7% (0.26) |
| sp500_top20_daily_gate | sp500 | 20 | None | True | H2 | +3.9% (0.54) | 0.45 | 0.54 | -18.5% | +8.0% (1.05) |
| sp500_top20_daily_gate | sp500 | 20 | None | True | FULL | +4.0% (0.81) | 0.32 | 0.48 | -18.5% | +5.8% (1.15) |
| sp500_top50_daily_gate | sp500 | 50 | None | True | H1 | +5.4% (0.92) | 0.23 | 0.47 | -17.5% | +5.0% (0.86) |
| sp500_top50_daily_gate | sp500 | 50 | None | True | H2 | +0.1% (0.02) | 0.39 | 0.58 | -15.2% | +2.4% (0.43) |
| sp500_top50_daily_gate | sp500 | 50 | None | True | FULL | +3.7% (0.89) | 0.28 | 0.51 | -17.5% | +4.8% (1.15) |

### Leveraged Cash (Idle Cash in SHV / ^IRX / 252)

| Variant | Window | CAGR (0% cash) | CAGR (T-bill) | CAGR (20 bp cost) | H1 Alpha (t) | H2 Alpha (t) |
|---|---|---|---|---|---|---|
| sp100_top20_weekly_gate | FULL | +19.0% | +19.6% | +16.2% | +8.0% (1.34) | +10.7% (1.68) |
| sp100_top10_weekly_gate | FULL | +21.5% | +22.1% | +17.7% | +7.2% (1.05) | +13.8% (1.68) |
| sp500_top20_weekly_gate | FULL | +7.9% | +8.4% | +4.0% | -0.6% (-0.10) | +1.2% (0.18) |
| sp500_top10_weekly_gate | FULL | +11.5% | +12.0% | +6.9% | +1.7% (0.24) | +5.0% (0.56) |
| sp500_top50_weekly_gate | FULL | +8.9% | +9.4% | +5.7% | +2.2% (0.36) | +1.2% (0.22) |
| sp500_top20_weekly_nogate | FULL | +7.6% | +7.7% | +3.0% | -6.2% (-0.85) | +1.7% (0.25) |
| sp500_top20_daily_gate | FULL | +10.4% | +10.9% | +0.8% | +1.8% (0.28) | +3.9% (0.54) |
| sp500_top50_daily_gate | FULL | +9.7% | +10.2% | +2.2% | +5.4% (0.92) | +0.1% (0.02) |

## Verdict

- **Bar**: alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5. Say `unproven`, never round up.

**Cleared the full-window bar**: 2 variant(s):

- **sp100_top20_weekly_gate**: full t=2.33, H1 t=1.34, H2 t=1.68, beta=0.36: `unproven` (out-of-sample half below t 2)
- **sp100_top10_weekly_gate**: full t=2.14, H1 t=1.05, H2 t=1.68, beta=0.42: `unproven` (out-of-sample half below t 2)

The full-window t includes the half the defaults were chosen on, so it is not evidence on its own; the H2 column above is the out-of-sample reading.

**Universe check**: best S&P 100 variant is `sp100_top20_weekly_gate` (full t=2.33); best point-in-time S&P 500 variant is `sp500_top50_daily_gate` (full t=0.89). The S&P 100 result does not carry to the point-in-time S&P 500 universe, which points to a survivorship or universe effect in the S&P 100 list (today's constituents).

