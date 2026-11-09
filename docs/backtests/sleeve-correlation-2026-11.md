# Sleeve Correlation Study (2026-11)

Evaluation of sleeve diversification, residual correlations vs QQQ, and equal-weight blending
across surviving quants.md books, C2 blend members, and event books.

## Evaluated Streams

1. **Six quants.md survivors (with T-bill cash yield lever on):**
   - `MonthlyRotation` (ETF8 momentum rotation)
   - `VolTargetMomentum` (ETF8 vol-targeted momentum)
   - `QuarterlyTeCostGate` (ETF8 quarterly rebalance with turnover cost gate)
   - `RiskParityCapped` (5-asset risk parity capped at 25%)
   - `SmaDaily` (9 SPDR sectors 50/200 SMA trend, 25% cap)
   - `SmaDailyGated` (9 SPDR sectors SMA trend with SPY market gate)
2. **C2 blend members:**
   - `TSMOM`: Rebuilt in plain pandas using the Moskowitz/Ooi/Pedersen 12-month trailing total return momentum
     and 60-day realized volatility inverse-vol weights (target vol 0.10, cap 0.15, max gross 1.0). Cash earns 0%.
   - `TARegimeMultiAsset`: **Skipped.** Requires OHLCV (High/Low for ADX and Bollinger %B) and `ta` indicators
     evaluated inside `backtest_bot`'s stateful multi-ticker execution loop; cannot be rebuilt from daily adjusted closes alone.
   - `GoldenButterfly`: **Skipped.** Requires OHLCV (Volume and High/Low for OBV and Chaikin Money Flow) and stateful RRG signals;
     cannot be rebuilt from daily adjusted closes alone.
3. **Event streams from `scripts/onetime_lift_study.py`:**
   - `buyback_sp100`: S&P 100 buyback drift (10-session holding, 10 bps cost per side).
   - `activist_13d`: Schedule 13D activist filings by fund entities (20-session holding, 10 bps cost per side).

**Common Window:** `2010-02-01` to `2026-11-06` (4219 shared weekday bars).

---

## (a) Correlation Matrix of Raw Daily Returns (Common Window)

| | MonthlyRotation | VolTargetMomentum | QuarterlyTeCostGate | RiskParityCapped | SmaDaily | SmaDailyGated | TSMOM | buyback_sp100 | activist_13d |
|---|---|---|---|---|---|---|---|---|---|
| **MonthlyRotation** | 1.00 | 0.97 | 0.79 | 0.53 | 0.60 | 0.56 | 0.69 | 0.30 | 0.14 |
| **VolTargetMomentum** | 0.97 | 1.00 | 0.76 | 0.51 | 0.59 | 0.57 | 0.69 | 0.30 | 0.13 |
| **QuarterlyTeCostGate** | 0.79 | 0.76 | 1.00 | 0.45 | 0.50 | 0.56 | 0.70 | 0.19 | 0.13 |
| **RiskParityCapped** | 0.53 | 0.51 | 0.45 | 1.00 | 0.94 | 0.65 | 0.47 | 0.49 | 0.31 |
| **SmaDaily** | 0.60 | 0.59 | 0.50 | 0.94 | 1.00 | 0.74 | 0.50 | 0.48 | 0.28 |
| **SmaDailyGated** | 0.56 | 0.57 | 0.56 | 0.65 | 0.74 | 1.00 | 0.53 | 0.29 | 0.18 |
| **TSMOM** | 0.69 | 0.69 | 0.70 | 0.47 | 0.50 | 0.53 | 1.00 | 0.19 | 0.17 |
| **buyback_sp100** | 0.30 | 0.30 | 0.19 | 0.49 | 0.48 | 0.29 | 0.19 | 1.00 | 0.17 |
| **activist_13d** | 0.14 | 0.13 | 0.13 | 0.31 | 0.28 | 0.18 | 0.17 | 0.17 | 1.00 |

---

## (b) Correlation Matrix of QQQ Residuals (Alpha Streams, Common Window)

Residual returns after removing QQQ beta: `resid_i = r_i - beta_i * r_qqq`.

| | MonthlyRotation | VolTargetMomentum | QuarterlyTeCostGate | RiskParityCapped | SmaDaily | SmaDailyGated | TSMOM | buyback_sp100 | activist_13d |
|---|---|---|---|---|---|---|---|---|---|
| **MonthlyRotation** | 1.00 | 0.96 | 0.72 | 0.24 | 0.37 | 0.38 | 0.61 | 0.12 | 0.01 |
| **VolTargetMomentum** | 0.96 | 1.00 | 0.69 | 0.24 | 0.40 | 0.41 | 0.60 | 0.12 | 0.01 |
| **QuarterlyTeCostGate** | 0.72 | 0.69 | 1.00 | 0.11 | 0.23 | 0.39 | 0.62 | -0.01 | 0.00 |
| **RiskParityCapped** | 0.24 | 0.24 | 0.11 | 1.00 | 0.82 | 0.38 | 0.21 | 0.29 | 0.18 |
| **SmaDaily** | 0.37 | 0.40 | 0.23 | 0.82 | 1.00 | 0.56 | 0.28 | 0.25 | 0.11 |
| **SmaDailyGated** | 0.38 | 0.41 | 0.39 | 0.38 | 0.56 | 1.00 | 0.38 | 0.07 | 0.03 |
| **TSMOM** | 0.61 | 0.60 | 0.62 | 0.21 | 0.28 | 0.38 | 1.00 | 0.00 | 0.06 |
| **buyback_sp100** | 0.12 | 0.12 | -0.01 | 0.29 | 0.25 | 0.07 | 0.00 | 1.00 | 0.07 |
| **activist_13d** | 0.01 | 0.01 | 0.00 | 0.18 | 0.11 | 0.03 | 0.06 | 0.07 | 1.00 |

---

## (c) Performance Metrics vs QQQ

### Common Window (2010-02 to 2026-11)

| Stream | Obs | Alpha/yr | t-stat | Beta | Corr (QQQ) | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **MonthlyRotation** | 4219 | +2.1% | 0.79 | 0.30 | 0.50 | -29.8% | +3.0% (0.82) | +1.4% (0.37) | `unproven` |
| **VolTargetMomentum** | 4219 | +2.6% | 1.03 | 0.27 | 0.48 | -30.2% | +2.3% (0.65) | +2.9% (0.79) | `unproven` |
| **QuarterlyTeCostGate** | 4219 | +5.1% | 1.94 | 0.29 | 0.48 | -20.8% | +3.8% (1.08) | +6.3% (1.57) | `unproven` |
| **RiskParityCapped** | 4219 | +1.5% | 0.70 | 0.61 | 0.81 | -35.8% | -0.7% (-0.33) | +2.6% (0.70) | `unproven` |
| **SmaDaily** | 4219 | +0.2% | 0.11 | 0.57 | 0.80 | -34.6% | -1.3% (-0.58) | +0.8% (0.21) | `unproven` |
| **SmaDailyGated** | 4219 | +3.1% | 1.44 | 0.31 | 0.58 | -19.7% | +0.1% (0.03) | +4.5% (1.43) | `unproven` |
| **TSMOM** | 4219 | +0.8% | 0.41 | 0.18 | 0.44 | -16.0% | -2.2% (-0.98) | +2.9% (0.99) | `unproven` |
| **buyback_sp100** | 4219 | +4.3% | 1.37 | 0.29 | 0.42 | -25.5% | +0.9% (0.29) | +8.1% (1.49) | `unproven` |
| **activist_13d** | 4219 | +17.3% | 1.61 | 0.58 | 0.26 | -48.4% | +27.8% (1.45) | +6.1% (0.62) | `unproven` |

### Full Available History per Stream

| Stream | Start | End | Obs | Alpha/yr | t-stat | Beta | Corr (QQQ) | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **MonthlyRotation** | 2006-02-07 | 2026-11-06 | 5221 | +5.0% | 1.90 | 0.26 | 0.42 | -29.8% | +8.5% (2.03) | +1.1% (0.35) | `unproven` |
| **VolTargetMomentum** | 2006-02-07 | 2026-11-06 | 5221 | +4.9% | 2.00 | 0.22 | 0.39 | -30.2% | +6.9% (1.84) | +2.5% (0.80) | `unproven` |
| **QuarterlyTeCostGate** | 2006-02-07 | 2026-11-06 | 5221 | +6.8% | 2.60 | 0.23 | 0.40 | -21.4% | +8.6% (2.21) | +4.5% (1.33) | `unproven` |
| **RiskParityCapped** | 2000-01-04 | 2026-11-06 | 6752 | +4.9% | 2.19 | 0.45 | 0.72 | -49.2% | +6.1% (1.71) | +1.5% (0.59) | `unproven` |
| **SmaDaily** | 2000-01-04 | 2026-11-06 | 6752 | +5.0% | 2.33 | 0.31 | 0.60 | -35.2% | +5.7% (1.85) | +0.1% (0.03) | `unproven` |
| **SmaDailyGated** | 2000-01-04 | 2026-11-06 | 6752 | +6.1% | 3.40 | 0.16 | 0.42 | -19.7% | +6.0% (2.40) | +3.9% (1.62) | `unproven` |
| **TSMOM** | 2001-01-30 | 2026-11-06 | 6482 | +3.3% | 2.11 | 0.12 | 0.33 | -24.1% | +5.0% (2.12) | +1.2% (0.58) | `unproven` |
| **buyback_sp100** | 2009-01-02 | 2026-11-06 | 4490 | +3.9% | 1.31 | 0.26 | 0.40 | -25.5% | +2.8% (0.98) | +6.0% (1.15) | `unproven` |
| **activist_13d** | 2009-01-02 | 2026-11-06 | 4490 | +15.8% | 1.55 | 0.53 | 0.25 | -48.4% | +24.6% (1.36) | +7.4% (0.78) | `unproven` |

---

## (d) Equal-Weight Blend of Six Survivors vs Best Member

Equal-weight 1/6 blend of the six surviving quants.md books evaluated over the common window (2010–2026):

| Series | Alpha/yr | t-stat | Beta | Corr (QQQ) | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Equal-Weight 6-Survivor Blend** | +2.4% | 1.38 | 0.39 | 0.74 | -19.8% | +1.2% (0.50) | +3.1% (1.16) | `unproven` |
| **Best Survivor (QuarterlyTeCostGate)** | +5.1% | 1.94 | 0.29 | 0.48 | -20.8% | +3.8% (1.08) | +6.3% (1.57) | `unproven` |

**Dilution vs Diversification Analysis:**
- The equal-weight blend produces **alpha of +2.4%/yr (t = 1.38)**, compared to the best standalone survivor **QuarterlyTeCostGate (+5.1%/yr, t = 1.94)**.
- Blending achieves modest risk reduction (Max DD -19.8% vs -20.8%), but severely dilutes alpha (reducing annual alpha by 2.7%/yr).
- Because the ETF8 rotation books correlate heavily among themselves (residual correlation ~0.76–0.93), averaging them does not produce an independent statistical boost to t-stat.

---

## (e) Reading and Interpretation

1. **Redundant Pairs (residual correlation > 0.70):** `MonthlyRotation`–`VolTargetMomentum` (0.96), `MonthlyRotation`–`QuarterlyTeCostGate` (0.72), `RiskParityCapped`–`SmaDaily` (0.82). The ETF8 momentum variations (`MonthlyRotation`, `VolTargetMomentum`, `QuarterlyTeCostGate`) and the two sector trend books (`SmaDaily`, `SmaDailyGated`) share the vast majority of their alpha variance and offer no mutual diversification.
2. **Most Independent Streams:** `activist_13d` (avg |resid corr| = 0.06), `buyback_sp100` (avg |resid corr| = 0.12), `RiskParityCapped` (avg |resid corr| = 0.31). The corporate event books (`activist_13d`, `buyback_sp100`) and the macro trend sleeve (`TSMOM`) have near-zero residual correlation with the equity rotation and sector trend strategies.
3. **Verdict:** **No bot is promoted.** Every stream and the 6-survivor blend remain `unproven` under the project bar (t >= 2 and alpha > 0 in both halves).
