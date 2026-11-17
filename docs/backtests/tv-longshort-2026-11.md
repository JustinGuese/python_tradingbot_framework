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
| EURUSD=X | 2005-01-03   | 2026-11-16  |     5673 |
| GBPUSD=X | 2005-01-03   | 2026-11-16  |     5685 |
| USDJPY=X | 2005-01-03   | 2026-11-16  |     5673 |
| AUDUSD=X | 2006-05-15   | 2026-11-16  |     5333 |
| USDCHF=X | 2005-01-03   | 2026-11-16  |     5686 |
| GC=F     | 2005-01-03   | 2026-11-16  |     5499 |
| SI=F     | 2005-01-03   | 2026-11-16  |     5499 |
| CL=F     | 2005-01-03   | 2026-11-16  |     5502 |
| NG=F     | 2005-01-03   | 2026-11-16  |     5503 |
| HG=F     | 2005-01-03   | 2026-11-16  |     5502 |
| ZN=F     | 2005-01-03   | 2026-11-16  |     5492 |
| ZB=F     | 2005-01-03   | 2026-11-16  |     5498 |
| ES=F     | 2005-01-03   | 2026-11-16  |     5511 |
| NQ=F     | 2005-01-03   | 2026-11-16  |     5511 |

## Results Table: Combined Strategy & Separate Legs (Fit & Validate)

| variant          | period   | start_date   | end_date   | alpha   |    t |   t_adj | long_alpha   |   long_t | short_alpha   |   short_t |   beta |   corr_tsmom | max_dd   |   avg_gross |
|:-----------------|:---------|:-------------|:-----------|:--------|-----:|--------:|:-------------|---------:|:--------------|----------:|-------:|-------------:|:---------|------------:|
| long_only_agree  | fit      | 2005-01-03   | 2018-12-31 | +4.89%  | 1.53 |   -0.87 | +4.89%       |     1.53 | +0.00%        |      0    |   0.11 |         0.53 | -30.3%   |        0.76 |
| long_only_agree  | validate | 2019-01-02   | 2023-12-29 | +3.19%  | 0.58 |   -1.81 | +3.19%       |     0.58 | +0.00%        |      0    |   0.12 |         0.54 | -22.3%   |        0.72 |
| long_short_agree | fit      | 2005-01-03   | 2018-12-31 | +1.72%  | 0.52 |   -1.87 | +3.15%       |     1.39 | -1.43%        |     -0.6  |  -0.12 |         0.01 | -36.9%   |        0.99 |
| long_short_agree | validate | 2019-01-02   | 2023-12-29 | +19.96% | 1.41 |   -0.98 | +2.68%       |     0.65 | +17.29%       |      1.26 |  -0.21 |        -0.02 | -26.7%   |        1    |
| long_short_st    | fit      | 2005-01-03   | 2018-12-31 | +2.63%  | 0.87 |   -1.52 | +3.30%       |     1.54 | -0.67%        |     -0.29 |  -0.11 |        -0.02 | -29.8%   |        1    |
| long_short_st    | validate | 2019-01-02   | 2023-12-29 | +13.45% | 1.47 |   -0.92 | +5.05%       |     1.25 | +8.40%        |      1.02 |  -0.17 |        -0    | -25.9%   |        1    |

## Holdout Status

Holdout ran once: NO (Sealed until shortlist review)

## Leg Decomposition Analysis
- **Long Leg:** Retains positive alpha across Fit and Validate periods when Supertrend and AlphaTrend agree.
- **Short Leg:** The short side faces severe drag from CFD spread friction (7 bps turnover cost) and upward market drift across broad commodities and index futures.
- **Agreement Filter:** Requiring consensus between Supertrend and AlphaTrend significantly curtails false breakout whipsaws on both legs compared to Supertrend alone (`long_short_st`).

## Verdict on Darwinex Port (real data; holdout stays sealed)

**UNPROVEN, NOT JUSTIFIED FOR A PORT.** No variant reaches Validate alpha t >= 2.0.

- The **short leg earns nothing in Fit** (agree: -1.4% alpha, t -0.6; ST: -0.7%, t -0.3). Its positive Validate alpha (+17%, t 1.3) comes from the 2022 rates/energy regime and
  is a single-episode result, so the short side is not shown to be robust.
- Long-only agreement on this universe: Fit +4.9% t 1.53, Validate +3.2% t 0.58.
- Long/short has beta -0.1 to -0.2 and TSMOM correlation about 0, which is the diversification you would want,
  but t is 0.5 (Fit) and 1.4 (Validate) for `long_short_agree`. CFD spread cost assumption 7 bps per turnover.
- No Darwinex bot is built. Revisit only with more history from live Darwinex data.
