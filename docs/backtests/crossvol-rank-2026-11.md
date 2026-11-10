# CrossVol Ranking Filters & Structure Comparison (2026-11)

## Context & Hypotheses

CrossVol (`option_CrossVolBot`) previously evaluated on 25 real names scored alpha −10%/yr with t = −3.56 and was paused (`values.yaml`, `suspend: true`). As noted in PLAN.md step C1, this study is a kill-gate, not a rescue.

The study tests three candidate ranking filters across single-stock options:
1. **Implied - Realized Vol Gap (`gap`)**: The a-priori baseline ($IV - HAR_{fair}$), selling condors on the single names whose implied volatility sits furthest above their forecast volatility.
2. **Vol-of-Vol (`vol_of_vol`)**: Sample standard deviation of daily ATM IV changes ($\sigma(\Delta IV_t)$ over a rolling window).
3. **Idiosyncratic Volatility (`idio_vol`)**: Annualized standard deviation of residuals from stock returns regressed against QQQ ($R_{i,t} = \alpha + \beta R_{m,t} + \epsilon_{i,t}$).

Additionally, we evaluate:
- **Delta-Hedged Call Writing (Han et al. comparison)**: Selling call spreads while dynamically delta-hedging the underlying stock.

## Walk-Forward Protocol

- **Objective**: Walk-forward alpha t vs QQQ (not raw Sharpe or CAGR, to avoid beta-chasing).
- **Split**: 50/50 temporal split (H1 / H2).
- **Rule**: Pick best configuration on H1 alpha t; judge on H2. Ship tuned values in `CrossVolRules` only if the H1 winner turns H2 alpha t > 0 **AND** beats the live baseline on H2. Otherwise, defaults remain and the bot stays paused.

## Baseline vs Candidate Ranking Filters

<!-- SUMMARY_TABLE -->

## Full Tuning Grid Results

<!-- GRID_TABLE -->

## Kill-Gate Verdict

<!-- VERDICT -->
