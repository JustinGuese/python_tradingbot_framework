# FOMC Event Volatility Study (SPY, 2006–2026)

**Verdict: inconclusive, leaning long since 2019; selling FOMC vol loses on every cut.**
- **Short** (straddle, fly, strangle) loses in every sample, and significantly on 2019+ real quotes
  (short straddle -26.7% per trade, t -2.1, n = 21; short fly 5% wings -8.4%, t -1.95).
- **Long iron fly (5% wings, the shipped structure)** is positive since 2019 (real quotes +3.6%
  per trade, t 1.30, n = 21; modelled 2019+ +2.2%, t 1.40, n = 40) but **not significant**
  (every bootstrap CI includes zero), and **negative on the 2006-2019 synthetic half** (t -0.34, n = 110).
  So "replicates across H1 and H2" does not hold; it holds only since 2019.
- **The pre-2019 half has no event premium in its prices** (Black-Scholes at ^VIX, no FOMC-day bump),
  and 2019+ meetings outside the 21 real-quote ones are priced off a surface interpolated from
  sampled expiries (nearest ~14 days) with the same blind spot. A model that omits event variance
  makes every long-event trade look good and every short one look bad. The 21 real-quote
  meetings are the only unbiased evidence, and 21 is too few to ship an edge claim.
- Consistent with the literature (OptionMetrics: FOMC straddles positive, other announcements
  negative; CPI and NFP long flies here are also negative: -1.2% t -1.9, -0.9% t -1.3).

Real vs modelled split (long and short, no delta hedge; per-trade return on max risk):

| Side | Structure | Real quotes (n=21) | Modelled 2019+ (n=40) | Synthetic <2019 (n=110) |
|---|---|---|---|---|
| long | iron fly 5% | +3.55%, t 1.30, CI [-1.7, 9.0] | +2.18%, t 1.40 | -0.32%, t -0.34 |
| long | straddle | +13.58%, t 1.56, CI [-2.4, 30.6] | +6.45%, t 1.07 | -4.19%, t -1.26 |
| short | iron fly 5% | -8.37%, t -1.95, CI [-17.0, -0.8] | -4.91%, t -2.99 | -2.74%, t -2.76 |
| short | straddle | -26.74%, t -2.11, CI [-51.7, -4.4] | -14.19%, t -2.20 | -3.63%, t -1.04 |

The bot ships live on paper with defined risk (long fly, <= 5% of the book per event, about 8 events
a year) so the real record accumulates; each meeting adds one observation.

## Overview & Methodology

- **Strategy:** Trade SPY index option structures into scheduled macro announcements.
- **Entry:** Prior trading session (T-1) close (within last 120 minutes of regular trading).
- **Exit:** Announcement session (T0) close (after the 14:00 New York statement release).
- **History:** 2006-01-01 to 2026-11-09 (171 scheduled FOMC meetings).
  - **H1 (2006–2019-05):** Synthetic Black-Scholes pricing calibrated from ^VIX implied vol and index smile.
  - **H2 (2019-05–2026-11):** Real option chains from `option_quotes` interpolated via `DaySurface` (61 meetings on real prices).
- **Sizing / Risk Budget:** Defined-risk maximum loss capped at 5% of portfolio equity.

> [!IMPORTANT]
> **Small-Sample Caveat:** Across the entire 20-year window there are only ~70 FOMC meetings
> (and ~40 on real option quotes since 2019). Due to the modest degrees of freedom (n ≈ 40 in H2),
> standard t-statistics must be interpreted cautiously alongside bootstrap confidence intervals.
> Alpha vs QQQ must not be claimed without acknowledging these sample size limits.

## Walk-Forward Tuning Grid Results

| Side | Structure | Wing Width | Delta Hedged | N | Mean Return | t-stat | Win Rate | 95% Bootstrap CI | H1 N (t) | H2 N (t) | Real Quotes N |
|---|---|---|---|---|---|---|---|---|---|---|---|
| long | iron_fly | 3% | No | 171 | +0.87% | +0.74 | 42.7% | [-1.2%, +3.3%] | 110 (-0.69) | 61 (+1.66) | 21 |
| long | iron_fly | 3% | Yes | 171 | +0.16% | +0.13 | 39.8% | [-2.0%, +2.6%] | 110 (-1.00) | 61 (+0.93) | 21 |
| long | iron_fly | 5% | No | 171 | +0.74% | +0.95 | 42.1% | [-0.7%, +2.4%] | 110 (-0.34) | 61 (+1.93) | 21 |
| long | iron_fly | 5% | Yes | 171 | +0.37% | +0.48 | 39.2% | [-1.0%, +2.0%] | 110 (-0.51) | 61 (+1.20) | 21 |
| long | iron_fly | 8% | No | 171 | +0.56% | +1.02 | 40.9% | [-0.5%, +1.7%] | 110 (-0.34) | 61 (+1.95) | 21 |
| long | iron_fly | 8% | Yes | 171 | +0.51% | +1.01 | 39.2% | [-0.4%, +1.6%] | 110 (-0.38) | 61 (+1.93) | 21 |
| long | straddle | - | No | 171 | +0.48% | +0.17 | 41.5% | [-4.8%, +6.3%] | 110 (-1.26) | 61 (+1.81) | 21 |
| long | straddle | - | Yes | 171 | +1.44% | +0.50 | 40.4% | [-4.1%, +7.2%] | 110 (-0.87) | 61 (+1.83) | 21 |
| long | strangle | 3% | No | 171 | -2.40% | -1.88 | 12.9% | [-4.7%, +0.3%] | 110 (-2.83) | 61 (-0.18) | 21 |
| long | strangle | 3% | Yes | 171 | -0.93% | -0.60 | 18.7% | [-3.6%, +2.5%] | 110 (-1.68) | 61 (+0.35) | 21 |
| long | strangle | 5% | No | 171 | -0.85% | -0.86 | 6.4% | [-2.3%, +1.5%] | 110 (-4.37) | 61 (+0.38) | 21 |
| long | strangle | 5% | Yes | 171 | +0.31% | +0.20 | 10.5% | [-1.7%, +3.8%] | 110 (-2.33) | 61 (+0.70) | 21 |
| long | strangle | 8% | No | 171 | -0.43% | -1.18 | 1.8% | [-0.9%, +0.4%] | 110 (-8.91) | 61 (+0.46) | 21 |
| long | strangle | 8% | Yes | 171 | -0.13% | -0.27 | 6.4% | [-0.7%, +1.0%] | 110 (-5.41) | 61 (+0.66) | 21 |
| short | iron_fly | 3% | No | 171 | -6.40% | -4.80 | 48.0% | [-9.2%, -3.9%] | 110 (-3.41) | 61 (-3.46) | 21 |
| short | iron_fly | 3% | Yes | 171 | -5.69% | -4.87 | 48.5% | [-8.1%, -3.6%] | 110 (-3.47) | 61 (-3.46) | 21 |
| short | iron_fly | 5% | No | 171 | -3.94% | -4.30 | 50.9% | [-5.9%, -2.2%] | 110 (-2.76) | 61 (-3.36) | 21 |
| short | iron_fly | 5% | Yes | 171 | -3.56% | -4.48 | 50.3% | [-5.3%, -2.1%] | 110 (-2.89) | 61 (-3.50) | 21 |
| short | iron_fly | 8% | No | 171 | -2.40% | -3.75 | 50.9% | [-3.7%, -1.2%] | 110 (-2.35) | 61 (-2.96) | 21 |
| short | iron_fly | 8% | Yes | 171 | -2.35% | -3.98 | 50.9% | [-3.5%, -1.2%] | 110 (-2.59) | 61 (-3.06) | 21 |
| short | straddle | - | No | 171 | -8.94% | -2.83 | 54.4% | [-15.5%, -2.7%] | 110 (-1.04) | 61 (-3.05) | 21 |
| short | straddle | - | Yes | 171 | -9.93% | -3.08 | 52.6% | [-16.3%, -3.6%] | 110 (-1.38) | 61 (-3.04) | 21 |
| short | strangle | 3% | No | 171 | -1.85% | -1.08 | 59.6% | [-5.8%, +1.0%] | 110 (-0.62) | 61 (-0.90) | 21 |
| short | strangle | 3% | Yes | 171 | -3.49% | -1.61 | 48.5% | [-8.4%, -0.1%] | 110 (-1.56) | 61 (-1.08) | 21 |
| short | strangle | 5% | No | 171 | -1.35% | -1.07 | 31.6% | [-4.3%, +0.4%] | 110 (-0.90) | 61 (-0.90) | 21 |
| short | strangle | 5% | Yes | 171 | -2.51% | -1.38 | 26.3% | [-6.6%, -0.2%] | 110 (-2.14) | 61 (-1.01) | 21 |
| short | strangle | 8% | No | 171 | -0.64% | -1.66 | 12.9% | [-1.5%, -0.2%] | 110 (-3.88) | 61 (-1.17) | 21 |
| short | strangle | 8% | Yes | 171 | -0.94% | -1.84 | 11.7% | [-2.1%, -0.3%] | 110 (-4.27) | 61 (-1.18) | 21 |

## Event Comparison: FOMC vs CPI vs NFP

To test whether FOMC differs from other major macro releases, the baseline structures were re-run on CPI and NFP dates:

| Structure | Event | N | Mean Return | t-stat | Win Rate | 95% Bootstrap CI |
|---|---|---|---|---|---|---|
| Long Iron Fly (5%) | FOMC | 171 | +0.74% | +0.95 | 42.1% | [-0.7%, +2.4%] |
| Long Iron Fly (5%) | CPI | 266 | -1.18% | -1.89 | 28.2% | [-2.3%, +0.1%] |
| Long Iron Fly (5%) | NFP | 256 | -0.91% | -1.26 | 36.3% | [-2.3%, +0.5%] |
| Short Straddle | FOMC | 171 | -8.94% | -2.83 | 54.4% | [-15.5%, -2.7%] |
| Short Straddle | CPI | 266 | -2.02% | -0.78 | 64.7% | [-7.2%, +2.7%] |
| Short Straddle | NFP | 256 | -7.57% | -2.38 | 58.2% | [-13.8%, -1.5%] |

## Shipped Bot Configuration

Based on the walk-forward results:
- **Bot:** `OptionFOMCVolBot` (`tradingbot/option_fomcvolbot.py`)
- **Rules:**
  - `side = 'long'`
  - `structure = 'iron_fly'`
  - `wing_width_pct = 0.05` (5% wings finance debit while providing upside leverage)
  - `strike_offset = 0.0` (ATM center)
  - `delta_hedged = False`
  - `target_dte = 1` (front expiry)
  - `max_risk_pct = 0.05`
- **Schedule:** `45 15 * * 1-5` (evaluates inside 120-min window before close on T-1, exits post-announcement on T0).
