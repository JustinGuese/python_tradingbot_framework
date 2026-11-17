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
| GLD      | 2005-01-03   | 2026-11-16  |     5503 |
| SLV      | 2006-04-28   | 2026-11-16  |     5171 |
| DBC      | 2006-02-06   | 2026-11-16  |     5228 |
| USO      | 2006-04-10   | 2026-11-16  |     5184 |
| TLT      | 2005-01-03   | 2026-11-16  |     5503 |
| IEF      | 2005-01-03   | 2026-11-16  |     5503 |
| HYG      | 2007-04-11   | 2026-11-16  |     4933 |
| UUP      | 2007-03-01   | 2026-11-16  |     4961 |
| SHY      | 2005-01-03   | 2026-11-16  |     5503 |
| TIP      | 2005-01-03   | 2026-11-16  |     5503 |
| LQD      | 2005-01-03   | 2026-11-16  |     5503 |
| EMB      | 2007-12-19   | 2026-11-16  |     4757 |
| BWX      | 2007-10-11   | 2026-11-16  |     4805 |
| CPER     | 2011-11-15   | 2026-11-16  |     3772 |
| DBA      | 2007-01-05   | 2026-11-16  |     4998 |
| UNG      | 2007-04-18   | 2026-11-16  |     4928 |
| PPLT     | 2010-01-08   | 2026-11-16  |     4240 |
| FXE      | 2005-12-12   | 2026-11-16  |     5265 |
| FXY      | 2007-02-13   | 2026-11-16  |     4972 |
| FXF      | 2006-06-26   | 2026-11-16  |     5131 |
| FXA      | 2006-06-26   | 2026-11-16  |     5131 |

## Results Table (Fit & Validate)

| variant      | period   | start_date   | end_date   | alpha   |     t |   t_adj |   beta |   corr_tsmom | max_dd   |   avg_gross |
|:-------------|:---------|:-------------|:-----------|:--------|------:|--------:|-------:|-------------:|:---------|------------:|
| st_narrow    | fit      | 2005-01-03   | 2018-12-31 | +3.66%  |  1.46 |   -1.11 |   0.02 |         0.48 | -16.9%   |        0.73 |
| st_narrow    | validate | 2019-01-02   | 2023-12-29 | +5.62%  |  1.32 |   -1.25 |   0.05 |         0.44 | -11.0%   |        0.77 |
| agree_narrow | fit      | 2005-01-03   | 2018-12-31 | +4.79%  |  2.01 |   -0.56 |   0.01 |         0.48 | -14.0%   |        0.67 |
| agree_narrow | validate | 2019-01-02   | 2023-12-29 | +7.64%  |  1.85 |   -0.72 |   0.04 |         0.44 | -12.0%   |        0.72 |
| agree_wide   | fit      | 2005-01-03   | 2018-12-31 | +1.23%  |  0.58 |   -2    |  -0    |         0.55 | -29.4%   |        0.92 |
| agree_wide   | validate | 2019-01-02   | 2023-12-29 | -1.61%  | -0.37 |   -2.95 |   0.08 |         0.58 | -24.1%   |        0.94 |
| st_wide      | fit      | 2005-01-03   | 2018-12-31 | +0.58%  |  0.27 |   -2.3  |   0.01 |         0.56 | -31.7%   |        0.95 |
| st_wide      | validate | 2019-01-02   | 2023-12-29 | -2.80%  | -0.65 |   -3.23 |   0.09 |         0.59 | -23.4%   |        0.96 |
| at_wide      | fit      | 2005-01-03   | 2018-12-31 | +1.62%  |  0.77 |   -1.81 |   0.01 |         0.6  | -26.0%   |        0.94 |
| at_wide      | validate | 2019-01-02   | 2023-12-29 | +0.28%  |  0.06 |   -2.51 |   0.07 |         0.64 | -22.6%   |        0.97 |

## Holdout Status

Holdout ran once: NO (Sealed until shortlist review)

## Verdict (real data; holdout stays sealed)

No variant passes the pre-registered rule (Validate alpha t >= 2.0, Fit t > 0, beta < 0.5). Nothing is shortlisted, so
the 2024-2026 holdout was NOT run and nothing ships.

- **Widening hurts.** Every `*_wide` variant has alpha ~0 or negative on Validate (agree_wide -1.6%, st_wide -2.8%, at_wide +0.3%).
  The extra FX ETFs (FXE/FXY/FXF/FXA), UNG, CPER, PPLT, DBA and EMB/BWX carry little trend alpha, push average gross up
  to ~0.95 and raise TSMOM correlation to 0.55-0.64. The "more independent bets" argument fails because these
  markets are not trending in a way these signals capture; they just add noise and turnover.
- **The agreement rule on the narrow 8-ticker universe is the best variant** (agree_narrow: Fit +4.8% t 2.01, Validate +7.6% t 1.85, beta ~0.0-0.04),
  consistently above `st_narrow` (t 1.46 / 1.32). That supports the earlier finding that ST AND AT cuts whipsaw, but
  Validate t 1.85 is below 2.0, and far below the Sidak-adjusted bar (~2.58 for 5 variants).
- Do NOT add a wide-universe bot. The existing narrow bots (`supertrendexequitybot`, `alphatrendexequitybot`, ST AND AT in
  `tvtrenddailybot`) remain the live candidates; judge them on live alphareport data.

Note: an earlier draft of this doc was produced from silently-generated synthetic data (the study scripts fell back to
synthetic data when the cache was missing). The fallback was removed; all numbers above come from real yfinance data.
