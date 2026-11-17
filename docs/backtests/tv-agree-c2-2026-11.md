# TradingView Agreement Filter on TSMOM Sleeve (2005-2026)

Pre-registered study evaluating Idea 6 from `PLAN-widen.md` (Step S2).

## Methodology & Pre-Registered Protocol
- **Pre-registered Variants (n=3):** `tsmom_base, tsmom_and_st, tsmom_and_st_at`
  - `tsmom_base`: 12-month trailing total return momentum sign rule across UNIVERSE_WIDE (documented proxy).
  - `tsmom_and_st`: hold TSMOM-selected ticker only when Supertrend is also long.
  - `tsmom_and_st_at`: hold TSMOM-selected ticker only when both Supertrend AND AlphaTrend are long.
- **Multiple-Testing Adjustment:** `z_sidak(3) = 2.394` (`norm.ppf(1 - 0.025 / 3)`).
- **Execution Model:** 5 bps turnover cost, long-only, 0.20 per-ticker cap, gross cap 1.0.
- **Periods:**
  - Fit: `2005-01-01` to `2018-12-31`
  - Validate: `2019-01-01` to `2023-12-31`
  - Holdout: `2024-01-01` to present (sealed; run once on shortlisted variants only).
- **Pass Hurdle:** `Validate alpha_t >= 2.0`, `Fit alpha_t > 0`, `beta < 0.5`, and (when holdout run) `holdout alpha > 0`.
- **C2 Blend Integration:**
  - Evaluates replacing the baseline TSMOM sleeve in the live C2 blend (0.39 TARegime / 0.37 GoldenButterfly / 0.24 TSMOM).
  - TARegime and GoldenButterfly sleeves are proxied causally over their respective multi-asset constituents within UNIVERSE_WIDE.

## TSMOM Sleeve Performance (Fit & Validate)

| variant         | period   | start_date   | end_date   | alpha   |     t |   t_adj |   beta |   corr_qqq | max_dd   |   annual_turnover |
|:----------------|:---------|:-------------|:-----------|:--------|------:|--------:|-------:|-----------:|:---------|------------------:|
| tsmom_base      | fit      | 2005-01-03   | 2018-12-31 | +1.15%  |  0.5  |   -1.89 |   0.3  |       0.58 | -26.9%   |              13.4 |
| tsmom_and_st    | fit      | 2005-01-03   | 2018-12-31 | +1.43%  |  0.65 |   -1.74 |   0.18 |       0.4  | -15.7%   |              26.5 |
| tsmom_and_st_at | fit      | 2005-01-03   | 2018-12-31 | +1.89%  |  0.87 |   -1.53 |   0.16 |       0.36 | -14.4%   |              26.3 |
| tsmom_base      | validate | 2019-01-02   | 2023-12-29 | -0.53%  | -0.13 |   -2.53 |   0.35 |       0.71 | -21.7%   |              20.8 |
| tsmom_and_st    | validate | 2019-01-02   | 2023-12-29 | +3.90%  |  0.93 |   -1.47 |   0.2  |       0.48 | -14.7%   |              30.3 |
| tsmom_and_st_at | validate | 2019-01-02   | 2023-12-29 | +6.42%  |  1.52 |   -0.88 |   0.16 |       0.39 | -16.0%   |              29.7 |

## C2 Blend Impact (Fit & Validate)

| variant (TSMOM replacement)   | period   | blend_alpha   |   blend_t |   blend_t_adj |   blend_beta | blend_max_dd   | delta_alpha_vs_base   |   delta_t_vs_base |
|:------------------------------|:---------|:--------------|----------:|--------------:|-------------:|:---------------|:----------------------|------------------:|
| tsmom_base                    | fit      | +2.36%        |      1.52 |         -0.88 |         0.32 | -23.1%         | +0.00%                |              0    |
| tsmom_and_st                  | fit      | +2.42%        |      1.61 |         -0.78 |         0.29 | -20.6%         | +0.07%                |              0.1  |
| tsmom_and_st_at               | fit      | +2.54%        |      1.69 |         -0.7  |         0.29 | -19.3%         | +0.18%                |              0.17 |
| tsmom_base                    | validate | +0.48%        |      0.17 |         -2.22 |         0.3  | -17.3%         | +0.00%                |              0    |
| tsmom_and_st                  | validate | +1.54%        |      0.56 |         -1.83 |         0.27 | -14.8%         | +1.06%                |              0.39 |
| tsmom_and_st_at               | validate | +2.14%        |      0.79 |         -1.61 |         0.26 | -13.8%         | +1.67%                |              0.62 |

## Holdout Status

Holdout ran once: NO (Sealed until shortlist review)

## Verdict (real data; holdout stays sealed)

No variant passes (Validate alpha t >= 2.0). `tsmom_and_st_at` has the best Validate sleeve alpha (+6.4%, t 1.52, beta 0.16 vs 0.35 for the base sleeve),
and the base sleeve itself is negative on Validate (-0.5%, t -0.13), so the filter is a real improvement over the base sleeve, but not a pass.

- Turnover roughly doubles (13-21x to 26-30x per year); results are after 5 bps costs.
- Blend effect (TSMOM sleeve replaced in the 0.39/0.37/0.24 blend): Validate blend alpha +0.5% -> +2.1% (t 0.17 -> 0.79); Fit +2.4% -> +2.5%
  (t 1.52 -> 1.69). Improvement is concentrated in Validate (2019-2023).
- **Caveat:** the TARegime and GoldenButterfly sleeves are equal-weight proxies, not the live bots' returns, and the TSMOM sleeve is a 12-month sign-rule proxy.
  Treat the blend numbers as indicative only. Next step if pursued: rebuild with `scripts.onetime_c2_blend_backtest.sleeve_returns`.
- The effect is consistent with ST AND AT also helping a second strategy, but t is below 2 in both periods. Not shipped.
