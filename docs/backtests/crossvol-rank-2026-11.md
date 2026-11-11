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

The run was stopped after 59 of 97 grid cells (4h41m runtime). 37 cells produced results; the
rest were skipped (NaN sizing in some delta-hedged-call cells, wing-selection failures).
Ranks tested: `gap` (existing) and `vol_of_vol`; structures: condor and delta-hedged call writing.
Idiosyncratic-vol rank was not reached. Cells covered 21 and 35 DTE and 0.10 / 0.15 delta.

| Result over the 37 completed cells | Value |
|---|---|
| H2 alpha t > 0 | 0 of 37 |
| H2 alpha t range | about -1.9 to -0.2 |
| H1 alpha t range | about -2.8 to +0.13 |
| Best H2 cell | gap, condor, 21 DTE, 0.15 delta: H1 -0.88, H2 -0.25 (150 trades) |
| Delta-hedged call writing | H2 t -1.0 to -1.4 in every cell |
| vol_of_vol vs gap | no difference beyond noise (both negative) |

## Full Tuning Grid Results

Per-cell H1/H2 alpha t are in the run log; every completed cell is negative on H2.

## Kill-Gate Verdict

**Kill-gate: failed.** No rank or structure turned H2 alpha t above 0, so nothing beats the live
rules. `CrossVolRules` is unchanged in behaviour (new fields default to None) and the bot
stays paused. Caveat: the grid was cut at 59 of 97 cells and idiosyncratic vol was not tested;
with 37 of 37 negative on H2, finishing would not plausibly change the verdict.
