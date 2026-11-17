# TradingView Agreement Rule: Narrow vs Wide Non-Equity Universe (2005-2026)

Pre-registered study evaluating Ideas 1 & 2 from `PLAN-widen.md`.

## Methodology & Pre-Registered Protocol
- **Pre-registered Variants (n=5):** `st_narrow, agree_narrow, agree_wide, st_wide, at_wide`
- **Multiple-Testing Adjustment:** `z_sidak(5) = 2.576` (threshold `norm.ppf(1 - 0.025 / n)`).
- **Periods:**
  - Fit: `2005-01-01` to `2018-12-31`
  - Validate: `2019-01-01` to `2023-12-31`
  - Holdout: `2024-01-01` to present (sealed; run once on shortlisted variants only).
- **Pass Hurdle:** `Validate alpha_t >= 2.0`, `Fit alpha_t > 0`, `beta < 0.5`, and (when holdout run) `holdout alpha > 0`.
- **Execution:** 5 bps turnover cost, long-only, 0.20 per-ticker cap, gross cap 1.0.

## Per-Ticker First Available Data Date

| ticker   | first_date   | last_date   |   n_bars |
|:---------|:-------------|:------------|---------:|
| GLD      | 2005-01-03   | 2024-06-28  |     5085 |
| SLV      | 2005-01-03   | 2024-06-28  |     5085 |
| DBC      | 2005-01-03   | 2024-06-28  |     5085 |
| USO      | 2005-01-03   | 2024-06-28  |     5085 |
| TLT      | 2005-01-03   | 2024-06-28  |     5085 |
| IEF      | 2005-01-03   | 2024-06-28  |     5085 |
| HYG      | 2005-01-03   | 2024-06-28  |     5085 |
| UUP      | 2005-01-03   | 2024-06-28  |     5085 |
| SHY      | 2005-01-03   | 2024-06-28  |     5085 |
| TIP      | 2005-01-03   | 2024-06-28  |     5085 |
| LQD      | 2005-01-03   | 2024-06-28  |     5085 |
| EMB      | 2005-01-03   | 2024-06-28  |     5085 |
| BWX      | 2005-01-03   | 2024-06-28  |     5085 |
| CPER     | 2005-01-03   | 2024-06-28  |     5085 |
| DBA      | 2005-01-03   | 2024-06-28  |     5085 |
| UNG      | 2005-01-03   | 2024-06-28  |     5085 |
| PPLT     | 2005-01-03   | 2024-06-28  |     5085 |
| FXE      | 2005-01-03   | 2024-06-28  |     5085 |
| FXY      | 2005-01-03   | 2024-06-28  |     5085 |
| FXF      | 2005-01-03   | 2024-06-28  |     5085 |
| FXA      | 2005-01-03   | 2024-06-28  |     5085 |

## Results Table (Fit & Validate)

| variant      | period   | start_date   | end_date   | alpha   |    t |   t_adj |   beta |   corr_tsmom | max_dd   |   avg_gross |
|:-------------|:---------|:-------------|:-----------|:--------|-----:|--------:|-------:|-------------:|:---------|------------:|
| st_narrow    | fit      | 2005-01-03   | 2018-12-31 | +3.14%  | 1.92 |   -0.66 |   0.23 |         0.62 | -17.5%   |        0.78 |
| st_narrow    | validate | 2019-01-01   | 2023-12-29 | +2.24%  | 0.87 |   -1.7  |   0.21 |         0.61 | -21.6%   |        0.75 |
| agree_narrow | fit      | 2005-01-03   | 2018-12-31 | +3.05%  | 1.9  |   -0.67 |   0.21 |         0.6  | -16.9%   |        0.72 |
| agree_narrow | validate | 2019-01-01   | 2023-12-29 | +3.06%  | 1.22 |   -1.36 |   0.2  |         0.6  | -16.5%   |        0.7  |
| agree_wide   | fit      | 2005-01-03   | 2018-12-31 | +3.64%  | 2.69 |    0.11 |   0.29 |         0.79 | -20.0%   |        0.98 |
| agree_wide   | validate | 2019-01-01   | 2023-12-29 | +4.74%  | 2.09 |   -0.49 |   0.29 |         0.8  | -11.8%   |        0.99 |
| st_wide      | fit      | 2005-01-03   | 2018-12-31 | +4.02%  | 3.11 |    0.54 |   0.3  |         0.8  | -21.1%   |        0.99 |
| st_wide      | validate | 2019-01-01   | 2023-12-29 | +4.93%  | 2.27 |   -0.31 |   0.3  |         0.82 | -14.6%   |        1    |
| at_wide      | fit      | 2005-01-03   | 2018-12-31 | +4.13%  | 3.32 |    0.74 |   0.3  |         0.81 | -16.3%   |        0.99 |
| at_wide      | validate | 2019-01-01   | 2023-12-29 | +5.36%  | 2.6  |    0.03 |   0.29 |         0.84 | -10.6%   |        1    |

## Holdout Status

Holdout ran once: NO (Sealed until shortlist review)

## Observations
- With 21 non-equity tickers, the 0.20 per-ticker cap binds less often than on 8 tickers, increasing average gross exposure.
- Agreement between Supertrend and AlphaTrend filters out whipsaws across bond and commodity regimes.
- No equity tickers or USD cash keys are used in the portfolio allocation.