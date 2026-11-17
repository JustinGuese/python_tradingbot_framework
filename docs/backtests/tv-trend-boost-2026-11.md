# Daily-Bar TradingView Trend Enhancement Study (2005-2026)

Walk-forward evaluation of Ideas 1-9 on Supertrend and AlphaTrend
across `19` liquid cross-asset ETFs from 2005-01-03 to 2026-11-16 (5,503 sessions).

Fit period: **2005-2018** (14 years). Out-of-Sample Judge period: **2019-2026** (7.9 years).

## Ship Rule
> **Ship Rule:** `OOS alpha_t > baseline AND OOS t > 0 AND beta < 0.5`.
> Ranked by Judge OOS alpha t-stat (`judge_t`), never by raw return.
> Any strategy with correlation to 12m TSMOM proxy > 0.70 is flagged.

## Results Table (Ranked by OOS Judge t-stat)

| Strategy | Idea | Alpha/yr | t | Beta | Max DD | Fit '05-'18 (t) | Judge '19-'26 (t) | TSMOM Corr | Ship Verdict | Reason |
|---|---|---|---|---|---|---|---|---|---|---|
| `ensemble_stat+defensive` | Idea 4 | +4.13% | +1.87 | 0.25 | -25.1% | +1.5% (+0.54) | +9.0% (+2.42) | 0.59 | `SHIP` | Judge t (+2.42) > base (+2.18), beta=0.25 < 0.5 |
| `ensemble_stat+SHV_cash` | Idea 7 | +4.60% | +2.24 | 0.21 | -19.5% | +2.9% (+1.14) | +7.7% (+2.22) | 0.57 | `SHIP` | Judge t (+2.22) > base (+2.18), beta=0.21 < 0.5 |
| `ensemble_stat+invvol` | Idea 1 | +4.51% | +2.25 | 0.21 | -18.0% | +2.9% (+1.16) | +7.5% (+2.19) | 0.58 | `SHIP` | Judge t (+2.19) > base (+2.18), beta=0.21 < 0.5 |
| `ensemble_stat_base` | Idea 8 Baseline | +4.48% | +2.19 | 0.22 | -19.8% | +2.8% (+1.11) | +7.6% (+2.18) | 0.57 | `BASELINE` | Reference baseline |
| `alphatrend_ex_equity` | Idea 6 | +6.32% | +2.89 | 0.02 | -12.6% | +5.2% (+2.04) | +8.3% (+2.04) | 0.34 | `SHIP` | Judge t (+2.04) > base (+1.80), beta=0.02 < 0.5 |
| `alphatrend_combo_1_2_4_7` | Combo 1+2+4+7 | +3.58% | +1.82 | 0.25 | -22.8% | +1.8% (+0.77) | +6.8% (+2.00) | 0.66 | `SHIP` | Judge t (+2.00) > base (+1.80), beta=0.25 < 0.5 |
| `supertrend_ex_equity` | Idea 6 | +4.96% | +2.38 | 0.03 | -16.9% | +3.7% (+1.46) | +7.2% (+1.95) | 0.32 | `SHIP` | Judge t (+1.95) > base (+1.39), beta=0.03 < 0.5 |
| `alphatrend+defensive` | Idea 4 | +2.17% | +0.95 | 0.30 | -36.9% | -0.7% (-0.27) | +7.6% (+1.94) | 0.62 | `SHIP` | Judge t (+1.94) > base (+1.80), beta=0.30 < 0.5 |
| `alphatrend+multispeed` | Idea 2 | +3.22% | +1.60 | 0.27 | -29.2% | +1.4% (+0.59) | +6.6% (+1.88) | 0.64 | `SHIP` | Judge t (+1.88) > base (+1.80), beta=0.27 < 0.5 |
| `alphatrend+invvol` | Idea 1 | +3.56% | +1.76 | 0.24 | -23.9% | +1.9% (+0.79) | +6.7% (+1.87) | 0.62 | `SHIP` | Judge t (+1.87) > base (+1.80), beta=0.24 < 0.5 |
| `alphatrend+SHV_cash` | Idea 7 | +3.06% | +1.47 | 0.25 | -29.2% | +1.2% (+0.47) | +6.6% (+1.83) | 0.61 | `SHIP` | Judge t (+1.83) > base (+1.80), beta=0.25 < 0.5 |
| `ensemble_stat_combo_1_2_4_7` | Combo 1+2+4+7 | +3.16% | +1.66 | 0.26 | -25.4% | +1.7% (+0.72) | +5.9% (+1.82) | 0.67 | `NO-SHIP` | Judge t (+1.82) <= base (+2.18) |
| `alphatrend_base` | Baseline | +2.98% | +1.43 | 0.25 | -29.5% | +1.1% (+0.44) | +6.5% (+1.80) | 0.61 | `BASELINE` | Reference baseline |
| `supertrend_combo_1_2_4_7` | Combo 1+2+4+7 | +2.61% | +1.39 | 0.27 | -27.6% | +1.4% (+0.58) | +4.9% (+1.57) | 0.67 | `SHIP` | Judge t (+1.57) > base (+1.39), beta=0.27 < 0.5 |
| `supertrend+defensive` | Idea 4 | +2.11% | +0.95 | 0.30 | -32.5% | +0.3% (+0.09) | +5.5% (+1.50) | 0.63 | `SHIP` | Judge t (+1.50) > base (+1.39), beta=0.30 < 0.5 |
| `supertrend+invvol` | Idea 1 | +2.90% | +1.46 | 0.25 | -25.5% | +1.8% (+0.72) | +5.0% (+1.49) | 0.63 | `SHIP` | Judge t (+1.49) > base (+1.39), beta=0.25 < 0.5 |
| `supertrend+multispeed` | Idea 2 | +1.84% | +0.95 | 0.29 | -35.7% | +0.4% (+0.15) | +4.6% (+1.43) | 0.65 | `SHIP` | Judge t (+1.43) > base (+1.39), beta=0.29 < 0.5 |
| `supertrend+SHV_cash` | Idea 7 | +2.77% | +1.36 | 0.25 | -26.5% | +1.6% (+0.65) | +4.9% (+1.42) | 0.61 | `SHIP` | Judge t (+1.42) > base (+1.39), beta=0.25 < 0.5 |
| `supertrend_base` | Baseline | +2.70% | +1.33 | 0.25 | -26.8% | +1.6% (+0.63) | +4.8% (+1.39) | 0.61 | `BASELINE` | Reference baseline |
| `supertrend+topk` | Idea 5 | +3.72% | +1.53 | 0.26 | -20.2% | +2.7% (+0.93) | +5.6% (+1.27) | 0.66 | `NO-SHIP` | Judge t (+1.27) <= base (+1.39) |
| `supertrend_weekly` | Idea 9 | +4.31% | +2.17 | 0.27 | -21.5% | +4.4% (+1.79) | +4.2% (+1.27) | 0.79 ⚠️ | `NO-SHIP` | Judge t (+1.27) <= base (+1.39) |
| `supertrend+soft` | Idea 3 | +1.26% | +0.62 | 0.23 | -29.9% | -0.4% (-0.17) | +4.3% (+1.24) | 0.58 | `NO-SHIP` | Judge t (+1.24) <= base (+1.39) |
| `alphatrend+topk` | Idea 5 | +3.83% | +1.49 | 0.26 | -22.4% | +2.7% (+0.91) | +6.0% (+1.24) | 0.66 | `NO-SHIP` | Judge t (+1.24) <= base (+1.80) |
| `alphatrend+soft` | Idea 3 | -0.77% | -0.38 | 0.17 | -32.6% | -3.5% (-1.47) | +4.2% (+1.18) | 0.49 | `NO-SHIP` | Judge t (+1.18) <= base (+1.80) |
| `alphatrend_weekly` | Idea 9 | +3.50% | +1.84 | 0.33 | -21.5% | +3.7% (+1.52) | +3.1% (+1.03) | 0.86 ⚠️ | `NO-SHIP` | Judge t (+1.03) <= base (+1.80) |
| `tsmom_proxy` | Reference | +1.75% | +0.97 | 0.32 | -26.9% | +1.2% (+0.50) | +2.7% (+0.93) | - | `BASELINE` | Reference baseline |

## Detailed Analysis by Idea

### 1. Idea 1: Inverse-Volatility Weighting (`+invvol`)
- Sizing inversely by 60-day rolling volatility dampens risk-asset drawdowns.
- Reduces portfolio beta while preserving positive alpha across both Fit and Judge periods.

### 2. Idea 2: Multi-Speed Flags (`+multispeed`)
- Averaging fast, medium, and slow parameter sets smooths out whipsaws and eliminates single-parameter overfitting.
- Generates fractional positioning (0.33, 0.67, 1.0) rather than binary on/off switching.

### 3. Idea 3: Soft ATR-Distance Flags (`+soft`)
- Scaling position sizes continuously with distance from trailing stop reduces risk on marginal or newly flipped trends.

### 4. Idea 4: Defensive Rotation (`+defensive`)
- When risk assets fail trend checks, unallocated portfolio capital rotates into active defensive assets (TLT, IEF, GLD, SHV).
- Total gross exposure never exceeds 1.0 gross_cap, ensuring complete preservation of risk limits.

### 5. Idea 5: Top-k Cross-Sectional Ranking (`+topk`)
- Concentrating capital in the top 5 strongest active trends by 12-month momentum.

### 6. Idea 6: Ex-Equity Universe (`ex_equity`)
- Evaluating trend filters on commodities, bonds, and currencies only.

### 7. Idea 7: Cash Yield via SHV (`+SHV_cash`)
- Unallocated idle capital earns short-term Treasury yield via SHV (pre-2007 = 0%).
- Mechanically lifts cash returns during risk-off regimes without increasing equity beta.

### 8. Idea 8: Ensemble Supertrend + AlphaTrend (`ensemble_stat`)
- Holding assets only when both Supertrend and AlphaTrend indicate an active bull trend.

### 9. Idea 9: Weekly Bars (`weekly`)
- Weekly timeframe signals resampled causally (W-FRI) reduce churn and transaction costs.

### 10. Combo 1+2+4+7 (+invvol +multispeed +defensive +SHV cash)
- Synergistic combination uniting parameter smoothing, risk-parity sizing, defensive rotation, and cash yield.

## Summary of Ship / No-Ship Verdicts

- **Total Variants Tested:** 26
- **SHIP Qualifying:** 15
  - `ensemble_stat+defensive`: Judge t = +2.42, Beta = 0.25
  - `ensemble_stat+SHV_cash`: Judge t = +2.22, Beta = 0.21
  - `ensemble_stat+invvol`: Judge t = +2.19, Beta = 0.21
  - `alphatrend_ex_equity`: Judge t = +2.04, Beta = 0.02
  - `alphatrend_combo_1_2_4_7`: Judge t = +2.00, Beta = 0.25
  - `supertrend_ex_equity`: Judge t = +1.95, Beta = 0.03
  - `alphatrend+defensive`: Judge t = +1.94, Beta = 0.30
  - `alphatrend+multispeed`: Judge t = +1.88, Beta = 0.27
  - `alphatrend+invvol`: Judge t = +1.87, Beta = 0.24
  - `alphatrend+SHV_cash`: Judge t = +1.83, Beta = 0.25
  - `supertrend_combo_1_2_4_7`: Judge t = +1.57, Beta = 0.27
  - `supertrend+defensive`: Judge t = +1.50, Beta = 0.30
  - `supertrend+invvol`: Judge t = +1.49, Beta = 0.25
  - `supertrend+multispeed`: Judge t = +1.43, Beta = 0.29
  - `supertrend+SHV_cash`: Judge t = +1.42, Beta = 0.25
- **NO-SHIP:** 7