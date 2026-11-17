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
| TLT      | 2005-01-03   | 2026-11-16  |     5503 |
| IEF      | 2005-01-03   | 2026-11-16  |     5503 |
| SHY      | 2005-01-03   | 2026-11-16  |     5503 |
| TIP      | 2005-01-03   | 2026-11-16  |     5503 |
| LQD      | 2005-01-03   | 2026-11-16  |     5503 |
| ^TNX     | 2005-01-03   | 2026-11-16  |     5498 |
| ^IRX     | 2005-01-03   | 2026-11-16  |     5498 |

## Results Table (Fit & Validate)

| variant                 | period   | start_date   | end_date   | alpha   |     t |   t_adj |   beta |   corr_tsmom | max_dd   |   avg_gross |
|:------------------------|:---------|:-------------|:-----------|:--------|------:|--------:|-------:|-------------:|:---------|------------:|
| trend_agree_bonds       | fit      | 2005-01-03   | 2018-12-31 | +2.48%  |  2.51 |    0.27 |  -0.06 |         0.73 | -7.9%    |        0.55 |
| trend_agree_bonds       | validate | 2019-01-02   | 2023-12-29 | +2.00%  |  1    |   -1.25 |  -0.01 |         0.51 | -11.0%   |        0.52 |
| trend_agree_carry_bonds | fit      | 2005-01-03   | 2018-12-31 | +2.33%  |  2.4  |    0.16 |  -0.06 |         0.72 | -7.9%    |        0.5  |
| trend_agree_carry_bonds | validate | 2019-01-02   | 2023-12-29 | -0.14%  | -0.1  |   -2.34 |  -0    |         0.48 | -8.4%    |        0.33 |

## Holdout Status

Holdout ran once: NO (Sealed until shortlist review)

## Verdict (real data; holdout stays sealed)

No variant passes. Bond trend agreement (ST AND AT on TLT/IEF/SHY/TIP/LQD) has Fit +2.5% t 2.51 (beta -0.06) but Validate only +2.0% t 1.0.
Adding the yield-curve slope condition (^TNX - ^IRX > 0) does not help: Fit +2.3% t 2.40, Validate -0.1% t -0.10 (it removes exposure during 2019-2023 inversion periods that were profitable for trend).
Carry is NOT a useful agreement signal for bonds with this proxy. Commodity and FX carry are not testable with available data (see above).

Note: an earlier draft showed trend_agree_carry_bonds with zero alpha because yield series are stamped 06:00 vs ETF 05:00; indexes are now normalised to dates.
