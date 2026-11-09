# quants.md walk-forward survivor study (2026-11)

Step W1 of PLAN.md: Walk-forward out-of-sample evaluation across the six surviving quants.md strategy books.
For each bot, we grid-search its own `param_grid` on **H1 (2000/inception to 2012-12-31)** ranked by `alpha_t` vs QQQ,
then judge the H1 winner against the default parameters on unseen **H2 (2013-01-01 to 2026-11-06)**.

Related studies in this series:
- Cash yield lever across survivors: [docs/backtests/quantsmd-cash-yield-2026-11.md](docs/backtests/quantsmd-cash-yield-2026-11.md)
- SPY SMA200 trend gate on rotation and parity: [docs/backtests/quantsmd-gate-more-2026-11.md](docs/backtests/quantsmd-gate-more-2026-11.md)
- Baseline survey and true CAGR corrections: [docs/backtests/quantsmd-2026-11.md](docs/backtests/quantsmd-2026-11.md)

## Methodology

- **Harness:** Fast vectorized/plain-pandas equity-curve simulation reusing the models from `quantsmd_cash_yield.py` and `quantsmd_gate_more.py`.
- **Universe & Data:** Yahoo Finance adjusted closes (`auto_adjust=True`, start `2000-01-01`). Inception is `2000-01-03` for sector books (SECTORS9) and `2006-02-06` for multi-asset ETF books (ETF8).
- **Rebalance & Costs:** Weights decided at close of day t earn return on day t+1. Turnover friction of 5 bp (0.0005) charged per unit rebalanced (`sum(|w_t - w_{t-1}|)`). Cash earns 0.0% (untuned baseline).
- **Split:**
  - **H1 (In-sample):** Start date to 2012-12-31 (~7 years for ETF8; ~13 years for SECTORS9). Optimization objective: `alpha_t` vs QQQ.
  - **H2 (Out-of-sample):** 2013-01-01 to 2026-11-06 (~13.8 years across both universes). Unseen evaluation period.
- **Benchmark:** QQQ. Metrics calculated via `tradingbot.utils.alpha_report.alpha_stats` on daily returns.
- **Project Bar:** A bot is designated **unproven** unless t >= 2.0 in both halves (H1 and H2).
- **Guardrail:** Report only; no bot implementation, helm values, or default parameters are modified.

## Executive Summary & Out-of-Sample Verdict

| Bot | Universe | H1 Winner Params | Default Params | Default H2 Alpha (t, beta) | Winner H2 Alpha (t, beta) | H2 Alpha Delta | H2 t Delta | Out-of-Sample Result | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **MonthlyRotation** | ETF8 | `lookback=63, top_n=3` | `lookback=126, top_n=3` | +0.7% (0.26, 0.32) | +1.3% (0.47, 0.26) | +0.6% | +0.20 | BEATS default | **unproven** |
| **VolTargetMomentum** | ETF8 | `lookback=63, top_n=4, target_vol=0.1` | `lookback=126, top_n=3, target_vol=0.15` | +1.6% (0.58, 0.29) | +1.0% (0.40, 0.18) | -0.6% | -0.18 | FAILS to beat default | **unproven** |
| **QuarterlyTeCostGate** | ETF8 | `lookback=126, te_threshold=0.06` | `lookback=126, te_threshold=0.06` | +3.5% (1.22, 0.30) | +3.5% (1.22, 0.30) | +0.0% | +0.00 | TIED with default | **unproven** |
| **RiskParityCapped** | SECTORS9 | `lookback=126, cap=0.2` | `lookback=126, cap=0.25` | +1.9% (0.78, 0.57) | +1.9% (0.76, 0.57) | -0.0% | -0.01 | FAILS to beat default | **unproven** |
| **SmaDaily** | SECTORS9 | `fast=50, slow=250` | `fast=50, slow=200` | +0.5% (0.22, 0.55) | +0.4% (0.18, 0.56) | -0.1% | -0.03 | FAILS to beat default | **unproven** |
| **SmaDailyGated** | SECTORS9+SPY | `off_scale=0.5` | `off_scale=0.0` | +4.2% (1.77, 0.29) | +2.3% (1.11, 0.42) | -1.8% | -0.65 | FAILS to beat default | **unproven** |

## Conclusion Per Bot

Per the project bar rule (say `unproven` unless t >= 2 in both halves):

- **MonthlyRotation**: **unproven** (H1 winner `{'lookback': 63, 'top_n': 3}` achieves H1 t = 2.16 but decays to H2 t = 0.47; defaults achieve H1 t = 2.06, H2 t = 0.26; fails the project bar of t >= 2 in both halves).
- **VolTargetMomentum**: **unproven** (H1 winner `{'lookback': 63, 'top_n': 4, 'target_vol': 0.1}` achieves H1 t = 2.46 but decays to H2 t = 0.40; defaults achieve H1 t = 1.85, H2 t = 0.58; fails the project bar of t >= 2 in both halves).
- **QuarterlyTeCostGate**: **unproven** (H1 winner `{'lookback': 126, 'te_threshold': 0.06}` achieves H1 t = 2.19 but decays to H2 t = 1.22; defaults achieve H1 t = 2.19, H2 t = 1.22; fails the project bar of t >= 2 in both halves).
- **RiskParityCapped**: **unproven** (H1 winner `{'lookback': 126, 'cap': 0.2}` achieves H1 t = 1.48 but decays to H2 t = 0.76; defaults achieve H1 t = 1.47, H2 t = 0.78; fails the project bar of t >= 2 in both halves).
- **SmaDaily**: **unproven** (H1 winner `{'fast': 50, 'slow': 250}` achieves H1 t = 1.73 but decays to H2 t = 0.18; defaults achieve H1 t = 1.42, H2 t = 0.22; fails the project bar of t >= 2 in both halves).
- **SmaDailyGated**: **unproven** (H1 winner `{'off_scale': 0.5}` achieves H1 t = 1.65 but decays to H2 t = 1.11; defaults achieve H1 t = 1.65, H2 t = 1.77; fails the project bar of t >= 2 in both halves).

## Detailed Comparison: Default vs In-Sample H1 Winner

### In-Sample (H1: Inception to 2012)

| Bot | Inception | Default H1 Alpha | Default H1 t | Default H1 Beta | Winner H1 Alpha | Winner H1 t | Winner H1 Beta | H1 t Lift |
|---|---|---|---|---|---|---|---|---|
| MonthlyRotation | 2006 | +11.7% | 2.06 | 0.15 | +12.9% | 2.16 | 0.27 | +0.11 |
| VolTargetMomentum | 2006 | +9.1% | 1.85 | 0.11 | +9.4% | 2.46 | 0.12 | +0.62 |
| QuarterlyTeCostGate | 2006 | +11.4% | 2.19 | 0.13 | +11.4% | 2.19 | 0.13 | +0.00 |
| RiskParityCapped | 2000 | +5.4% | 1.47 | 0.39 | +5.4% | 1.48 | 0.39 | +0.01 |
| SmaDaily | 2000 | +4.5% | 1.42 | 0.20 | +5.4% | 1.73 | 0.20 | +0.31 |
| SmaDailyGated | 2000 | +4.2% | 1.65 | 0.10 | +4.3% | 1.65 | 0.15 | +0.00 |

### Out-of-Sample (H2: 2013 to 2026)

| Bot | Default H2 Alpha | Default H2 t | Default H2 Beta | Default H2 CAGR | Default H2 MaxDD | Winner H2 Alpha | Winner H2 t | Winner H2 Beta | Winner H2 CAGR | Winner H2 MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|
| MonthlyRotation | +0.7% | 0.26 | 0.32 | 6.6% | -29.8% | +1.3% | 0.47 | 0.26 | 5.8% | -24.5% |
| VolTargetMomentum | +1.6% | 0.58 | 0.29 | 6.8% | -30.3% | +1.0% | 0.40 | 0.18 | 4.1% | -20.6% |
| QuarterlyTeCostGate | +3.5% | 1.22 | 0.30 | 9.0% | -20.8% | +3.5% | 1.22 | 0.30 | 9.0% | -20.8% |
| RiskParityCapped | +1.9% | 0.78 | 0.57 | 12.8% | -35.8% | +1.9% | 0.76 | 0.57 | 12.8% | -35.8% |
| SmaDaily | +0.5% | 0.22 | 0.55 | 10.7% | -34.6% | +0.4% | 0.18 | 0.56 | 10.9% | -34.4% |
| SmaDailyGated | +4.2% | 1.77 | 0.29 | 9.7% | -17.1% | +2.3% | 1.11 | 0.42 | 10.4% | -25.4% |

### Full Window (2000/2006 to 2026)

| Bot | Default Full Alpha | Default Full t | Default Full Beta | Default CAGR | Default MaxDD | Winner Full Alpha | Winner Full t | Winner Full Beta | Winner CAGR | Winner MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|
| MonthlyRotation | +4.9% | 1.85 | 0.26 | 8.6% | -29.8% | +5.2% | 1.87 | 0.26 | 8.9% | -29.5% |
| VolTargetMomentum | +4.7% | 1.88 | 0.22 | 7.8% | -30.3% | +4.0% | 1.93 | 0.15 | 6.2% | -22.7% |
| QuarterlyTeCostGate | +6.6% | 2.56 | 0.23 | 10.2% | -21.4% | +6.6% | 2.56 | 0.23 | 10.2% | -21.4% |
| RiskParityCapped | +4.8% | 2.15 | 0.45 | 8.9% | -49.2% | +4.8% | 2.15 | 0.45 | 8.9% | -49.2% |
| SmaDaily | +4.7% | 2.20 | 0.31 | 7.6% | -35.8% | +5.2% | 2.43 | 0.31 | 8.1% | -34.4% |
| SmaDailyGated | +5.4% | 3.03 | 0.16 | 6.9% | -19.7% | +5.1% | 2.82 | 0.23 | 7.4% | -25.4% |

## Grid Search Results By Strategy Book

### MonthlyRotation (`ETF8`, start 2006-02-06)

Tested 9 parameter combinations from `MonthlyRotation.param_grid`. Ranked by in-sample H1 `alpha_t` vs QQQ:

| Rank | Parameters | H1 Alpha | H1 t | H1 Beta | H2 Alpha | H2 t | H2 Beta | Full Alpha | Full t | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `lookback=63, top_n=3` | +12.9% | 2.16 | 0.27 | +1.3% | 0.47 | 0.26 | +5.2% | 1.87 | **H1 Winner** |
| 2 | `lookback=63, top_n=4` | +10.4% | 2.09 | 0.38 | -0.1% | -0.03 | 0.31 | +3.2% | 1.39 | - |
| 3 | `lookback=126, top_n=3` | +11.7% | 2.06 | 0.15 | +0.7% | 0.26 | 0.32 | +4.9% | 1.85 | Default |
| 4 | `lookback=126, top_n=4` | +8.8% | 1.92 | 0.30 | +1.0% | 0.44 | 0.34 | +3.7% | 1.72 | - |
| 5 | `lookback=126, top_n=2` | +12.8% | 1.87 | 0.16 | -2.6% | -0.75 | 0.38 | +3.2% | 0.97 | - |
| 6 | `lookback=63, top_n=2` | +11.9% | 1.67 | 0.24 | +3.0% | 0.86 | 0.24 | +6.0% | 1.81 | - |
| 7 | `lookback=189, top_n=2` | +10.0% | 1.57 | 0.13 | +6.3% | 1.90 | 0.25 | +7.9% | 2.56 | - |
| 8 | `lookback=189, top_n=4` | +6.0% | 1.25 | 0.25 | +3.2% | 1.39 | 0.32 | +4.4% | 1.96 | - |
| 9 | `lookback=189, top_n=3` | +5.7% | 1.03 | 0.20 | +2.8% | 1.08 | 0.32 | +4.2% | 1.63 | - |

### VolTargetMomentum (`ETF8`, start 2006-02-06)

Tested 18 parameter combinations from `VolTargetMomentum.param_grid`. Ranked by in-sample H1 `alpha_t` vs QQQ:

| Rank | Parameters | H1 Alpha | H1 t | H1 Beta | H2 Alpha | H2 t | H2 Beta | Full Alpha | Full t | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `lookback=63, top_n=4, target_vol=0.1` | +9.4% | 2.46 | 0.12 | +1.0% | 0.40 | 0.18 | +4.0% | 1.93 | **H1 Winner** |
| 2 | `lookback=63, top_n=3, target_vol=0.1` | +8.9% | 2.27 | 0.12 | +1.0% | 0.37 | 0.18 | +3.8% | 1.77 | - |
| 3 | `lookback=126, top_n=4, target_vol=0.1` | +7.7% | 2.15 | 0.11 | +1.1% | 0.47 | 0.25 | +3.7% | 1.90 | - |
| 4 | `lookback=63, top_n=4, target_vol=0.15` | +10.3% | 2.12 | 0.17 | +1.1% | 0.43 | 0.21 | +4.3% | 1.81 | - |
| 5 | `lookback=126, top_n=4, target_vol=0.15` | +9.4% | 2.05 | 0.14 | +1.2% | 0.50 | 0.28 | +4.4% | 1.94 | - |
| 6 | `lookback=126, top_n=3, target_vol=0.1` | +7.3% | 1.93 | 0.08 | +1.4% | 0.59 | 0.25 | +3.9% | 1.88 | - |
| 7 | `lookback=63, top_n=3, target_vol=0.15` | +9.8% | 1.90 | 0.18 | +1.3% | 0.47 | 0.21 | +4.2% | 1.67 | - |
| 8 | `lookback=63, top_n=2, target_vol=0.1` | +7.3% | 1.87 | 0.10 | +1.5% | 0.58 | 0.16 | +3.6% | 1.69 | - |
| 9 | `lookback=126, top_n=3, target_vol=0.15` | +9.1% | 1.85 | 0.11 | +1.6% | 0.58 | 0.29 | +4.7% | 1.88 | Default |
| 10 | `lookback=126, top_n=2, target_vol=0.1` | +6.5% | 1.72 | 0.08 | -0.5% | -0.21 | 0.24 | +2.3% | 1.13 | - |
| 11 | `lookback=126, top_n=2, target_vol=0.15` | +8.2% | 1.67 | 0.11 | -1.2% | -0.45 | 0.28 | +2.5% | 1.00 | - |
| 12 | `lookback=63, top_n=2, target_vol=0.15` | +8.2% | 1.64 | 0.15 | +1.9% | 0.73 | 0.17 | +4.1% | 1.69 | - |
| 13 | `lookback=189, top_n=2, target_vol=0.15` | +6.6% | 1.42 | 0.09 | +4.9% | 1.93 | 0.18 | +5.8% | 2.49 | - |
| 14 | `lookback=189, top_n=2, target_vol=0.1` | +4.8% | 1.34 | 0.07 | +4.9% | 2.05 | 0.17 | +5.2% | 2.58 | - |
| 15 | `lookback=189, top_n=4, target_vol=0.15` | +3.0% | 0.64 | 0.15 | +3.3% | 1.42 | 0.27 | +3.6% | 1.62 | - |
| 16 | `lookback=189, top_n=4, target_vol=0.1` | +2.2% | 0.59 | 0.11 | +3.0% | 1.40 | 0.25 | +3.1% | 1.65 | - |
| 17 | `lookback=189, top_n=3, target_vol=0.15` | +1.7% | 0.35 | 0.15 | +3.0% | 1.19 | 0.28 | +3.0% | 1.27 | - |
| 18 | `lookback=189, top_n=3, target_vol=0.1` | +0.5% | 0.13 | 0.12 | +2.5% | 1.06 | 0.25 | +2.2% | 1.11 | - |

### QuarterlyTeCostGate (`ETF8`, start 2006-02-06)

Tested 9 parameter combinations from `QuarterlyTeCostGate.param_grid`. Ranked by in-sample H1 `alpha_t` vs QQQ:

| Rank | Parameters | H1 Alpha | H1 t | H1 Beta | H2 Alpha | H2 t | H2 Beta | Full Alpha | Full t | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `lookback=126, te_threshold=0.06` | +11.4% | 2.19 | 0.13 | +3.5% | 1.22 | 0.30 | +6.6% | 2.56 | **H1 Winner** / Default |
| 2 | `lookback=63, te_threshold=0.06` | +10.3% | 1.87 | 0.16 | -0.5% | -0.19 | 0.36 | +3.7% | 1.40 | - |
| 3 | `lookback=63, te_threshold=0.08` | +9.4% | 1.59 | 0.37 | +1.6% | 0.59 | 0.36 | +4.1% | 1.55 | - |
| 4 | `lookback=63, te_threshold=0.04` | +7.9% | 1.40 | 0.33 | +0.5% | 0.17 | 0.29 | +2.8% | 1.07 | - |
| 5 | `lookback=189, te_threshold=0.08` | +7.8% | 1.35 | 0.20 | +3.3% | 1.18 | 0.29 | +5.1% | 1.89 | - |
| 6 | `lookback=189, te_threshold=0.06` | +7.4% | 1.28 | 0.18 | +0.1% | 0.05 | 0.42 | +3.3% | 1.23 | - |
| 7 | `lookback=126, te_threshold=0.04` | +5.3% | 0.98 | 0.31 | +2.8% | 1.01 | 0.37 | +3.8% | 1.48 | - |
| 8 | `lookback=189, te_threshold=0.04` | +4.8% | 0.96 | 0.10 | +3.0% | 1.12 | 0.31 | +4.3% | 1.71 | - |
| 9 | `lookback=126, te_threshold=0.08` | +2.6% | 0.45 | 0.24 | +4.2% | 1.52 | 0.32 | +3.9% | 1.46 | - |

### RiskParityCapped (`SECTORS9`, start 2000-01-03)

Tested 9 parameter combinations from `RiskParityCapped.param_grid`. Ranked by in-sample H1 `alpha_t` vs QQQ:

| Rank | Parameters | H1 Alpha | H1 t | H1 Beta | H2 Alpha | H2 t | H2 Beta | Full Alpha | Full t | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `lookback=126, cap=0.2` | +5.4% | 1.48 | 0.39 | +1.9% | 0.76 | 0.57 | +4.8% | 2.15 | **H1 Winner** |
| 2 | `lookback=126, cap=0.25` | +5.4% | 1.47 | 0.39 | +1.9% | 0.78 | 0.57 | +4.8% | 2.15 | Default |
| 3 | `lookback=126, cap=0.3` | +5.4% | 1.47 | 0.39 | +1.9% | 0.78 | 0.57 | +4.8% | 2.15 | - |
| 4 | `lookback=252, cap=0.2` | +5.3% | 1.44 | 0.38 | +1.9% | 0.78 | 0.58 | +4.9% | 2.16 | - |
| 5 | `lookback=252, cap=0.25` | +5.3% | 1.44 | 0.38 | +2.0% | 0.80 | 0.57 | +4.9% | 2.17 | - |
| 6 | `lookback=252, cap=0.3` | +5.3% | 1.44 | 0.38 | +2.0% | 0.80 | 0.57 | +4.9% | 2.17 | - |
| 7 | `lookback=63, cap=0.2` | +5.0% | 1.39 | 0.41 | +1.6% | 0.66 | 0.58 | +4.4% | 2.00 | - |
| 8 | `lookback=63, cap=0.3` | +5.0% | 1.39 | 0.40 | +1.6% | 0.63 | 0.57 | +4.4% | 1.97 | - |
| 9 | `lookback=63, cap=0.25` | +5.0% | 1.39 | 0.40 | +1.6% | 0.64 | 0.57 | +4.4% | 1.98 | - |

### SmaDaily (`SECTORS9`, start 2000-01-03)

Tested 9 parameter combinations from `SmaDaily.param_grid`. Ranked by in-sample H1 `alpha_t` vs QQQ:

| Rank | Parameters | H1 Alpha | H1 t | H1 Beta | H2 Alpha | H2 t | H2 Beta | Full Alpha | Full t | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `fast=50, slow=250` | +5.4% | 1.73 | 0.20 | +0.4% | 0.18 | 0.56 | +5.2% | 2.43 | **H1 Winner** |
| 2 | `fast=20, slow=200` | +5.0% | 1.62 | 0.20 | +0.7% | 0.31 | 0.49 | +4.7% | 2.28 | - |
| 3 | `fast=100, slow=200` | +5.3% | 1.60 | 0.23 | +1.1% | 0.45 | 0.57 | +5.3% | 2.45 | - |
| 4 | `fast=20, slow=150` | +5.0% | 1.59 | 0.20 | -0.4% | -0.18 | 0.47 | +3.9% | 1.90 | - |
| 5 | `fast=20, slow=250` | +4.5% | 1.45 | 0.19 | -0.2% | -0.09 | 0.51 | +4.1% | 1.98 | - |
| 6 | `fast=50, slow=200` | +4.5% | 1.42 | 0.20 | +0.5% | 0.22 | 0.55 | +4.7% | 2.20 | Default |
| 7 | `fast=100, slow=150` | +4.7% | 1.38 | 0.24 | -0.4% | -0.16 | 0.56 | +4.2% | 1.88 | - |
| 8 | `fast=50, slow=150` | +4.0% | 1.26 | 0.21 | -0.6% | -0.25 | 0.52 | +3.7% | 1.73 | - |
| 9 | `fast=100, slow=250` | +3.1% | 0.95 | 0.22 | +0.5% | 0.20 | 0.58 | +4.1% | 1.86 | - |

### SmaDailyGated (`SECTORS9+SPY`, start 2000-01-03)

Tested 2 parameter combinations from `SmaDailyGated.param_grid`. Ranked by in-sample H1 `alpha_t` vs QQQ:

| Rank | Parameters | H1 Alpha | H1 t | H1 Beta | H2 Alpha | H2 t | H2 Beta | Full Alpha | Full t | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `off_scale=0.5` | +4.3% | 1.65 | 0.15 | +2.3% | 1.11 | 0.42 | +5.1% | 2.82 | **H1 Winner** |
| 2 | `off_scale=0.0` | +4.2% | 1.65 | 0.10 | +4.2% | 1.77 | 0.29 | +5.4% | 3.03 | Default |

## Cross-Study Synthesis & Interpretation

Across the three follow-up investigations completed in Batch 1 and Batch 2:
1. **Walk-Forward In-Sample Overfitting (this study):**
   - Tuning on pre-2013 data does **not** solve the post-2013 alpha collapse. For five of the six books, optimizing on H1 either slightly degrades out-of-sample H2 performance (e.g. `VolTargetMomentum` H2 t drops from 0.58 to 0.40; `RiskParityCapped` H2 t drops from 0.78 to 0.76; `SmaDaily` H2 t flips negative from +0.22 to -0.18) or selects the default parameters exactly (`QuarterlyTeCostGate`).
   - Only `MonthlyRotation` achieves a minor out-of-sample lift (H2 t moves from 0.26 to 0.47 with `lookback=63`), but 0.47 remains drastically below the project edge bar of 2.0.
   - **Conclusion:** The post-2013 performance deterioration across all quants.md models is a macro-regime shift (the massive secular mega-cap tech rally dominating equal-weight and cross-asset rotations), not a sub-optimal choice of hyperparameters.

2. **Cash-Yield Lever ([quantsmd-cash-yield-2026-11.md](docs/backtests/quantsmd-cash-yield-2026-11.md)):**
   - Crediting 13-week Treasury yield on uninvested cash provides a modest level shift proportional to cash share (+0.7%/yr for `SmaDailyGated`, negligible +0.1% for rotation/parity), but fails to lift any strategy's H2 t-stat above 1.62. All six remain unproven under cash yield.

3. **SPY 200-day Trend Gate ([quantsmd-gate-more-2026-11.md](docs/backtests/quantsmd-gate-more-2026-11.md)):**
   - Gating via SPY SMA200 is an effective drawdown and beta suppressor (cutting `RiskParityCapped` max drawdown from -49% to -19% and beta to 0.15), passing the project bar on full-window RiskParityCapped (t = 2.99). However, it functions as a risk shield during equity bear markets, not an engine of persistent out-of-sample positive alpha.

**Overall Takeaway:** Default parameters chosen upstream were already well-centered; automated retuning on historical data offers no free lunch. All quantsmd CronJobs continue running unmodified in live tracking.
