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
| 2019 | 525 | 12 | 513 | 97.7% |
| 2020 | 518 | 12 | 506 | 97.7% |
| 2021 | 519 | 13 | 506 | 97.5% |
| 2022 | 519 | 13 | 506 | 97.5% |
| 2023 | 513 | 13 | 500 | 97.5% |
| 2024 | 515 | 13 | 502 | 97.5% |
| 2025 | 518 | 13 | 505 | 97.5% |
| 2026 | 517 | 13 | 504 | 97.5% |

**Sample dropped names per period**:

- **2019**: A, AAL, AAP, ABMD, ADI, ADM, ADP, ADS...
- **2020**: A, AAL, AAP, ABMD, ADI, ADM, ADP, ADS...
- **2021**: A, AAL, AAP, ABMD, ADI, ADM, ADP, ADSK...
- **2022**: A, AAL, AAP, ABMD, ACGL, ADI, ADM, ADP...
- **2023**: A, AAL, AAP, ABNB, ACGL, ADI, ADM, ADP...
- **2024**: A, AAL, ABNB, ACGL, ADI, ADM, ADP, ADSK...
- **2025**: A, ABNB, ACGL, ADI, ADM, ADP, ADSK, AEE...
- **2026**: A, ABNB, ACGL, ADI, ADM, ADP, ADSK, AEE...

## Results: Pre-Registered Variants

### Baseline (Cash at 0%, 5 bp cost)

| Variant | Universe | top_n | Rebalance | Gate | Window | Alpha vs QQQ | Beta | Corr | MaxDD | Alpha vs EW |
|---|---|---|---|---|---|---|---|---|---|---|
| sp100_top20_weekly_gate | sp100 | 20 | 4 | True | H1 | +5.0% (1.53) | 0.16 | 0.56 | -10.9% | +3.1% (0.98) |
| sp100_top20_weekly_gate | sp100 | 20 | 4 | True | H2 | -0.8% (-0.34) | 0.17 | 0.61 | -6.4% | -1.6% (-0.68) |
| sp100_top20_weekly_gate | sp100 | 20 | 4 | True | FULL | +2.3% (1.11) | 0.17 | 0.57 | -10.9% | +1.0% (0.49) |
| sp100_top10_weekly_gate | sp100 | 10 | 4 | True | H1 | +9.2% (1.43) | 0.32 | 0.56 | -20.9% | +5.5% (0.88) |
| sp100_top10_weekly_gate | sp100 | 10 | 4 | True | H2 | -1.4% (-0.30) | 0.34 | 0.61 | -12.4% | -3.0% (-0.64) |
| sp100_top10_weekly_gate | sp100 | 10 | 4 | True | FULL | +4.2% (1.04) | 0.33 | 0.58 | -20.9% | +1.6% (0.42) |
| sp500_top20_weekly_gate | sp500 | 20 | 4 | True | H1 | +2.6% (0.89) | 0.14 | 0.55 | -9.2% | +1.5% (0.53) |
| sp500_top20_weekly_gate | sp500 | 20 | 4 | True | H2 | -0.8% (-0.34) | 0.17 | 0.61 | -6.4% | -1.6% (-0.68) |
| sp500_top20_weekly_gate | sp500 | 20 | 4 | True | FULL | +1.1% (0.60) | 0.15 | 0.57 | -9.2% | +0.3% (0.17) |
| sp500_top10_weekly_gate | sp500 | 10 | 4 | True | H1 | +4.6% (0.80) | 0.28 | 0.55 | -17.4% | +2.5% (0.44) |
| sp500_top10_weekly_gate | sp500 | 10 | 4 | True | H2 | -1.4% (-0.30) | 0.34 | 0.61 | -12.4% | -3.0% (-0.64) |
| sp500_top10_weekly_gate | sp500 | 10 | 4 | True | FULL | +2.0% (0.55) | 0.30 | 0.57 | -17.4% | +0.4% (0.12) |
| sp500_top50_weekly_gate | sp500 | 50 | 4 | True | H1 | +1.0% (0.89) | 0.06 | 0.55 | -3.7% | +0.6% (0.53) |
| sp500_top50_weekly_gate | sp500 | 50 | 4 | True | H2 | -0.3% (-0.34) | 0.07 | 0.61 | -2.6% | -0.6% (-0.68) |
| sp500_top50_weekly_gate | sp500 | 50 | 4 | True | FULL | +0.4% (0.60) | 0.06 | 0.57 | -3.7% | +0.1% (0.17) |
| sp500_top20_weekly_nogate | sp500 | 20 | 4 | False | H1 | +2.2% (0.77) | 0.16 | 0.60 | -9.2% | +1.0% (0.36) |
| sp500_top20_weekly_nogate | sp500 | 20 | 4 | False | H2 | -1.3% (-0.57) | 0.18 | 0.62 | -6.6% | -2.2% (-0.94) |
| sp500_top20_weekly_nogate | sp500 | 20 | 4 | False | FULL | +0.6% (0.32) | 0.16 | 0.60 | -9.2% | -0.3% (-0.17) |
| sp500_top20_daily_gate | sp500 | 20 | None | True | H1 | +3.2% (1.14) | 0.13 | 0.53 | -8.1% | +2.2% (0.80) |
| sp500_top20_daily_gate | sp500 | 20 | None | True | H2 | +0.7% (0.30) | 0.17 | 0.61 | -6.0% | -0.1% (-0.05) |
| sp500_top20_daily_gate | sp500 | 20 | None | True | FULL | +2.2% (1.20) | 0.15 | 0.56 | -8.1% | +1.4% (0.79) |
| sp500_top50_daily_gate | sp500 | 50 | None | True | H1 | +1.3% (1.14) | 0.05 | 0.53 | -3.3% | +0.9% (0.80) |
| sp500_top50_daily_gate | sp500 | 50 | None | True | H2 | +0.3% (0.30) | 0.07 | 0.61 | -2.4% | -0.0% (-0.05) |
| sp500_top50_daily_gate | sp500 | 50 | None | True | FULL | +0.9% (1.20) | 0.06 | 0.56 | -3.3% | +0.6% (0.79) |

### Leveraged Cash (Idle Cash in SHV / ^IRX / 252)

| Variant | Window | CAGR (0% cash) | CAGR (T-bill) | CAGR (20 bp cost) | H1 Alpha (t) | H2 Alpha (t) |
|---|---|---|---|---|---|---|
| sp100_top20_weekly_gate | FULL | +5.9% | +8.2% | +5.5% | +5.0% (1.53) | -0.8% (-0.34) |
| sp100_top10_weekly_gate | FULL | +11.3% | +13.0% | +10.4% | +9.2% (1.43) | -1.4% (-0.30) |
| sp500_top20_weekly_gate | FULL | +4.4% | +6.7% | +4.1% | +2.6% (0.89) | -0.8% (-0.34) |
| sp500_top10_weekly_gate | FULL | +8.4% | +10.0% | +7.5% | +4.6% (0.80) | -1.4% (-0.30) |
| sp500_top50_weekly_gate | FULL | +1.8% | +4.4% | +1.7% | +1.0% (0.89) | -0.3% (-0.34) |
| sp500_top20_weekly_nogate | FULL | +4.2% | +6.4% | +3.8% | +2.2% (0.77) | -1.3% (-0.57) |
| sp500_top20_daily_gate | FULL | +5.5% | +7.8% | +4.7% | +3.2% (1.14) | +0.7% (0.30) |
| sp500_top50_daily_gate | FULL | +2.2% | +4.8% | +1.9% | +1.3% (1.14) | +0.3% (0.30) |

## Verdict

- **Bar**: alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5. Say `unproven`, never round up.

**Outcome**: `unproven`. No variant cleared the standing bar (t >= 2.0, alpha > 0 in both halves, beta < 0.5).
While momentum on S&P 500 produces positive returns, widening the universe beyond S&P 100 introduces lower-liquidity names and reduces the risk-adjusted edge vs QQQ. InstitutionalFlow remains paper-only on S&P 100.
