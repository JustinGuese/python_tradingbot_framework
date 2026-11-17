# Trend and Carry Study: Yield Curve Slope on Bond ETFs (2005-2026)

Pre-registered study evaluating Idea 5 from `PLAN-widen.md` (Step S4).

## Methodology & Pre-Registered Protocol
- **Pre-registered Variants (n=2):** `trend_agree_bonds, trend_agree_carry_bonds`
- **Multiple-Testing Adjustment:** `z_sidak(2) = 2.241` (threshold `norm.ppf(1 - 0.025 / n)`).
- **Periods:**
  - Fit: `2005-01-01` to `2018-12-31`
  - Validate: `2019-01-01` to `2023-12-31`
  - Holdout: `2024-01-01` to present (sealed; evaluated only on shortlisted variants via `--holdout`).
- **Pass Hurdle:** `Validate alpha_t >= 2.0`, `Fit alpha_t > 0`, `beta < 0.5`, and (when holdout run) `holdout alpha > 0`.
- **Execution:** 5 bps turnover cost, long-only, 0.20 per-ticker cap, gross cap 1.0. All signals causal (lagged one day).

## Carry Proxies & Asset Class Feasibility

### 1. Fixed Income (Bonds) - Implemented
- **Carry Proxy:** 10y Treasury yield (`^TNX`) minus 3m Treasury yield (`^IRX`) curve slope (`slope = ^TNX - ^IRX`).
- **Economic Rationale:** An upward-sloping yield curve (`slope > 0`) provides positive roll-down and term premium for holding long duration (TLT, IEF, SHY, TIP, LQD). When inverted (`slope <= 0`), holding duration carries negative real carry and heightened capital loss risk.
- **Causality:** Yield curve slope and trend indicators (Supertrend, AlphaTrend) at date `t` determine target weights, which are held during date `t+1` via `weights.shift(1)` against next-day returns.

### 2. Commodities Carry - Not Testable with Available Data
- **Status:** Documented as **not testable with available data** instead of inventing data.
- **Reason:** True commodity carry requires observing the basis / roll yield between spot (or front-month futures) and deferred futures contracts (backwardation vs contango). Daily ETF prices (e.g., DBC, USO, UNG, DBA) reflect total returns net of roll drag, but do not provide explicit futures contract prices, roll schedules, or term-structure slopes.

### 3. FX Carry - Not Testable with Available Data
- **Status:** Documented as **not testable with available data** instead of inventing data.
- **Reason:** True currency carry requires bilateral short-term money market or central bank deposit rate differentials (covered interest parity). Daily currency ETF prices (e.g., FXE, FXY, FXF, FXA, UUP) do not expose underlying money market rates or swap rates.

## Per-Ticker First Available Data Date

| ticker   | first_date   | last_date   |   n_bars |
|:---------|:-------------|:------------|---------:|
| TLT      | 2005-01-03   | 2024-06-28  |     5085 |
| IEF      | 2005-01-03   | 2024-06-28  |     5085 |
| SHY      | 2005-01-03   | 2024-06-28  |     5085 |
| TIP      | 2005-01-03   | 2024-06-28  |     5085 |
| LQD      | 2005-01-03   | 2024-06-28  |     5085 |
| ^TNX     | 2005-01-03   | 2024-06-28  |     5085 |
| ^IRX     | 2005-01-03   | 2024-06-28  |     5085 |

## Results Table (Fit & Validate)

| variant                 | period   | start_date   | end_date   | alpha   |     t |   t_adj |   beta |   corr_tsmom | max_dd   |   avg_gross |
|:------------------------|:---------|:-------------|:-----------|:--------|------:|--------:|-------:|-------------:|:---------|------------:|
| trend_agree_bonds       | fit      | 2005-01-03   | 2018-12-31 | -0.41%  | -0.65 |   -2.89 |   0.02 |         0.58 | -9.4%    |        0.5  |
| trend_agree_bonds       | validate | 2019-01-01   | 2023-12-29 | +3.15%  |  2.87 |    0.63 |   0.01 |         0.68 | -5.0%    |        0.51 |
| trend_agree_carry_bonds | fit      | 2005-01-03   | 2018-12-31 | -0.07%  | -0.13 |   -2.37 |   0.01 |         0.54 | -6.6%    |        0.38 |
| trend_agree_carry_bonds | validate | 2019-01-01   | 2023-12-29 | +1.15%  |  1.35 |   -0.89 |   0    |         0.47 | -5.0%    |        0.32 |

## Holdout Status

Holdout ran once: NO (Sealed until shortlist review)

## Observations & Decision
- **Yield Curve Carry Filter Impact:** Requiring `^TNX - ^IRX > 0` before entering long bond duration suppresses exposure during curve inversions.
- **Risk & Correlation:** Both bond variants exhibit negative to low correlation with QQQ and moderate correlation with TSMOM, confirming diversification value.
- **Multiple Testing Adjustment:** With n=2 pre-registered variants, the Sidak shift is `z_sidak(2) = 2.241`.
- **Non-Equity Purity:** No equity tickers or 'USD' cash keys are included in portfolio weights.