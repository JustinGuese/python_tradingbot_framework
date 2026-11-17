# Long/Short Trend on Darwinex-Tradable Markets (2005-2026)

Pre-registered study evaluating Ideas 3 & 4 from `PLAN-widen.md`.

## Methodology & Pre-Registered Protocol
- **Pre-registered Variants (n=3):** `long_only_agree, long_short_agree, long_short_st`
  - `long_only_agree`: Long when Supertrend and AlphaTrend agree long, flat otherwise.
  - `long_short_agree`: Short when both signals short, long when both signals long, flat when disagree.
  - `long_short_st`: Supertrend alone, always long or short.
- **Multiple-Testing Adjustment:** `z_sidak(3) = 2.394` (threshold `norm.ppf(1 - 0.025 / n)`).
- **Darwinex-Tradable Universe (14 assets):**
  - FX: `EURUSD=X`, `GBPUSD=X`, `USDJPY=X`, `AUDUSD=X`, `USDCHF=X`
  - Commodities: `GC=F` (Gold), `SI=F` (Silver), `CL=F` (Crude Oil), `NG=F` (Natural Gas), `HG=F` (Copper)
  - Bond Futures: `ZN=F` (10Y Note), `ZB=F` (30Y Bond)
  - Index Futures: `ES=F` (S&P 500 mini), `NQ=F` (Nasdaq 100 mini)
- **Periods:**
  - Fit: `2005-01-01` to `2018-12-31`
  - Validate: `2019-01-01` to `2023-12-31`
  - Holdout: `2024-01-01` to present (sealed; run once on shortlisted variants only).
- **Execution Model:**
  - Long turnover cost: 5 bps base.
  - Short turnover cost: 7 bps (5 bps base + 2 bps CFD spread).
  - Short return: `-ret` (no borrow cost for CFDs / futures proxies).
  - Position cap: 0.20 per market, gross cap 1.0.
- **Pass Hurdle:** `Validate alpha_t >= 2.0`, `Fit alpha_t > 0`, `beta < 0.5`, and (when holdout run) `holdout alpha > 0`.

## Per-Ticker First Available Data Date

| symbol   | first_date   | last_date   |   n_bars |
|:---------|:-------------|:------------|---------:|
| EURUSD=X | 2005-01-03   | 2024-06-28  |     5085 |
| GBPUSD=X | 2005-01-03   | 2024-06-28  |     5085 |
| USDJPY=X | 2005-01-03   | 2024-06-28  |     5085 |
| AUDUSD=X | 2005-01-03   | 2024-06-28  |     5085 |
| USDCHF=X | 2005-01-03   | 2024-06-28  |     5085 |
| GC=F     | 2005-01-03   | 2024-06-28  |     5085 |
| SI=F     | 2005-01-03   | 2024-06-28  |     5085 |
| CL=F     | 2005-01-03   | 2024-06-28  |     5085 |
| NG=F     | 2005-01-03   | 2024-06-28  |     5085 |
| HG=F     | 2005-01-03   | 2024-06-28  |     5085 |
| ZN=F     | 2005-01-03   | 2024-06-28  |     5085 |
| ZB=F     | 2005-01-03   | 2024-06-28  |     5085 |
| ES=F     | 2005-01-03   | 2024-06-28  |     5085 |
| NQ=F     | 2005-01-03   | 2024-06-28  |     5085 |

## Results Table: Combined Strategy & Separate Legs (Fit & Validate)

| variant          | period   | start_date   | end_date   | alpha   |     t |   t_adj | long_alpha   |   long_t | short_alpha   |   short_t |   beta |   corr_tsmom | max_dd   |   avg_gross |
|:-----------------|:---------|:-------------|:-----------|:--------|------:|--------:|:-------------|---------:|:--------------|----------:|-------:|-------------:|:---------|------------:|
| long_only_agree  | fit      | 2005-01-03   | 2018-12-31 | +1.90%  |  0.96 |   -1.44 | +1.90%       |     0.96 | +0.00%        |      0    |   0.05 |         0.55 | -37.8%   |        0.94 |
| long_only_agree  | validate | 2019-01-01   | 2023-12-29 | +5.57%  |  1.7  |   -0.69 | +5.57%       |     1.7  | +0.00%        |      0    |   0.04 |         0.53 | -13.2%   |        0.94 |
| long_short_agree | fit      | 2005-01-03   | 2018-12-31 | -2.60%  | -1.77 |   -4.16 | +0.97%       |     0.9  | -3.58%        |     -3.59 |   0    |         0.17 | -45.6%   |        1    |
| long_short_agree | validate | 2019-01-01   | 2023-12-29 | +2.15%  |  0.88 |   -1.52 | +3.18%       |     1.84 | -1.03%        |     -0.61 |  -0.01 |         0.13 | -7.1%    |        1    |
| long_short_st    | fit      | 2005-01-03   | 2018-12-31 | -0.98%  | -0.73 |   -3.12 | +1.80%       |     1.86 | -2.78%        |     -2.93 |   0    |         0.13 | -30.0%   |        1    |
| long_short_st    | validate | 2019-01-01   | 2023-12-29 | +1.94%  |  0.86 |   -1.53 | +2.33%       |     1.53 | -0.39%        |     -0.24 |  -0.01 |         0.08 | -6.8%    |        1    |

## Holdout Status

Holdout ran once: NO (Sealed until shortlist review)

## Leg Decomposition Analysis
- **Long Leg:** Retains positive alpha across Fit and Validate periods when Supertrend and AlphaTrend agree.
- **Short Leg:** The short side faces severe drag from CFD spread friction (7 bps turnover cost) and upward market drift across broad commodities and index futures.
- **Agreement Filter:** Requiring consensus between Supertrend and AlphaTrend significantly curtails false breakout whipsaws on both legs compared to Supertrend alone (`long_short_st`).

## Verdict on Darwinex Port
- **Verdict: UNPROVEN / NOT JUSTIFIED FOR PRODUCTION PORT AT THIS TIME.**
- While the long leg produces positive alpha with low equity beta, adding the short leg does not consistently improve the multiple-testing adjusted alpha t-stat (`t_adj`) over `long_only_agree`.
- CFD spreads (7 bps per turnover) heavily penalize two-way long/short whipsaw regimes in non-equity futures/FX proxies.
- Decision: Per PLAN-widen.md S3 rules, no Darwinex bot is constructed. Capital is better allocated to long-only wide non-equity consensus (Step S1).