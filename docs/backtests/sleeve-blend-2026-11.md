# Cluster-Representative Sleeve Blend Study (2026-11)

Evaluation of PLAN.md Step B1: constructing a cross-sleeve portfolio allocation across the independent
cluster representatives identified in the sleeve correlation study (`docs/backtests/sleeve-correlation-2026-11.md`).

## 1. Sleeve Selection & Universe Coverage

One representative per redundant cluster was selected based on alpha independence, lowest residual
correlation, and strategy diversification:

1. **`QuarterlyTeCostGate`** (Momentum Cluster Representative): ETF8 quarterly rebalancing with turnover cost gating.
   Residual correlation with other ETF8 momentum variations is 0.72–0.96 (represented here by QuarterlyTeCostGate).
2. **`SmaDailyGated`** (Trend Cluster Representative): 9 SPDR sector 50/200 SMA trend following with SPY market gate.
   Selected as the highest t-stat performer of the sector trend cluster.
3. **`ContinuousFuturesTrend`** (Macro Trend Sleeve): Long-only fast 63d / slow 252d SMA inverse-vol trend on 6 ETF proxies
   (`SPY`, `QQQ`, `IEF`, `USO`, `GLD`, `DBA`) with 5 bp turnover costs (Step F1, `docs/backtests/cft-longhistory-2026-11.md`).
4. **`buyback_sp100`** (Corporate Event Sleeve): S&P 100 share repurchase announcement post-filing drift (10 sessions holding,
   10 bps cost per side, from `scripts/onetime_lift_study.py`).
5. **`activist_13d`** (Activist Event Sleeve): Schedule 13D activist filings by fund entities (20 sessions holding,
   10 bps cost per side, from `scripts/onetime_lift_study.py`).
6. **`C2 Blend Sleeve`** (`TARegimeMultiAsset` / `GoldenButterfly` / `TSMOM`): **Skipped.** Requires OHLCV data
   (High/Low/Volume for ADX, Bollinger %B, Chaikin Money Flow, OBV) and stateful `backtest_bot` multi-ticker execution;
   cannot be rebuilt in plain pandas closes-only environment per PLAN.md global research rules.

**Common Evaluation Window:** `2010-02-01` to `2026-11-06` (4219 weekday bars).
- **H1 (Fitting Half):** `2010-02-01` to `2018-06-15` (2109 bars). Weights are fit strictly on this half.
- **H2 (Out-of-Sample Half):** `2018-06-18` to `2026-11-06` (2110 bars). Completely untouched during weight determination.

---

## 2. Portfolio Weights (Fit on H1 Only)

Following the Collective2 allocation method (`docs/backtests/c2-blend-2026-09.md`), weights are proportional to the
inverse of each stream's realized standard deviation on H1: $w_i \propto 1 / \sigma_{i, H1}$, normalized to sum to 1.0.
No other tuning, fitting, or optimization was performed.

| Sleeve / Stream | Role / Cluster | H1 Ann. Vol | Inverse-Vol Weight ($w_{iv}$) | Equal Weight ($w_{ew}$) |
|---|---|---|---|---|
| **`QuarterlyTeCostGate`** | Momentum Cluster | 11.4% | **0.180 (18.0%)** | 0.200 (20.0%) |
| **`SmaDailyGated`** | Sector Trend Cluster | 11.0% | **0.185 (18.5%)** | 0.200 (20.0%) |
| **`ContinuousFuturesTrend`** | Macro Trend / CFT | 5.2% | **0.394 (39.4%)** | 0.200 (20.0%) |
| **`buyback_sp100`** | Corporate Event (Buyback) | 10.0% | **0.205 (20.5%)** | 0.200 (20.0%) |
| **`activist_13d`** | Activist Event (13D) | 56.4% | **0.036 (3.6%)** | 0.200 (20.0%) |

> [!NOTE]
> `ContinuousFuturesTrend` receives the largest weight (**39.4%**) due to its low daily volatility (5.2% annualized in H1)
> across multi-asset ETF proxies. Conversely, `activist_13d` receives a modest weight (**3.6%**) due to its large
> idiosyncratic return swings during activist campaigns (56.4% annualized volatility in H1).

---

## 3. Out-of-Sample Performance (H2: 2018-06 to 2026-11)

Evaluation over the out-of-sample H2 period (2110 bars) with weights frozen from H1:

| Stream / Portfolio | H2 Alpha/yr | H2 t-stat | H2 Beta | Corr (QQQ) | Max Drawdown |
|---|---|---|---|---|---|
| **Inverse-Vol Blend** | +6.0% | 3.01 | 0.24 | 0.71 | -13.4% |
| **Equal-Weight Blend** | +6.4% | 2.23 | 0.31 | 0.67 | -20.1% |
| QuarterlyTeCostGate | +6.3% | 1.57 | 0.27 | 0.50 | -17.1% |
| SmaDailyGated | +4.5% | 1.43 | 0.23 | 0.51 | -17.1% |
| ContinuousFuturesTrend | +4.5% | 2.56 | 0.18 | 0.64 | -10.8% |
| buyback_sp100 | +9.8% | 1.79 | 0.31 | 0.43 | -24.4% |
| activist_13d | +6.9% | 0.70 | 0.55 | 0.42 | -48.4% |

---

## 4. Full Window Performance (2010-02 to 2026-11)

Full-sample evaluation across the entire 16.8-year dataset, reporting H1, H2, and full-sample metrics:

| Series | Bars | Alpha/yr | t-stat | Beta | Corr | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **Inverse-Vol Blend** | 4219 | +4.2% | 3.19 | 0.26 | 0.71 | -13.4% | +2.0% (1.18) | +6.0% (3.01) | `unproven` |
| **Equal-Weight Blend** | 4219 | +6.7% | 2.70 | 0.33 | 0.56 | -20.1% | +6.6% (1.62) | +6.4% (2.23) | `unproven` |
| QuarterlyTeCostGate | 4219 | +5.1% | 1.94 | 0.29 | 0.48 | -20.8% | +3.8% (1.08) | +6.3% (1.57) | `unproven` |
| SmaDailyGated | 4219 | +3.1% | 1.44 | 0.31 | 0.58 | -19.7% | +0.1% (0.03) | +4.5% (1.43) | `unproven` |
| ContinuousFuturesTrend | 4219 | +2.4% | 2.23 | 0.19 | 0.66 | -10.8% | +0.2% (0.13) | +4.5% (2.56) | `unproven` |
| buyback_sp100 | 4219 | +5.2% | 1.65 | 0.29 | 0.42 | -24.4% | +1.1% (0.34) | +9.8% (1.79) | `unproven` |
| activist_13d | 4219 | +17.8% | 1.65 | 0.58 | 0.26 | -48.4% | +27.9% (1.46) | +6.9% (0.70) | `unproven` |

---

## 5. Leave-One-Out Sensitivity Analysis (H2 Out-of-Sample)

To measure each sleeve's marginal contribution, the blend was re-evaluated on H2 with each sleeve removed in turn
(renormalizing the remaining H1 inverse-volatility weights to sum to 1.0):

| Excluded Sleeve | Remaining Sleeves | H2 Alpha/yr | H2 t-stat | H2 Beta | Corr (QQQ) | H2 Max DD | Marginal Impact |
|---|---|---|---|---|---|---|---|
| **w/o `QuarterlyTeCostGate`** | 4 remaining | +5.9% | 2.93 | 0.24 | 0.70 | -15.9% | Minor drop in t (3.01 → 2.93); drawdown deepens (-13.4% → -15.9%). |
| **w/o `SmaDailyGated`** | 4 remaining | +6.3% | 2.95 | 0.25 | 0.69 | -12.9% | Slight rise in alpha (+0.3%), but t softens (3.01 → 2.95). |
| **w/o `ContinuousFuturesTrend`** | 4 remaining | +6.9% | 2.58 | 0.29 | 0.66 | -15.1% | Sharp drop in t (3.01 → 2.58); beta rises (+0.05). Major risk damper. |
| **w/o `buyback_sp100`** | 4 remaining | +5.0% | 2.50 | 0.23 | 0.69 | -12.0% | Drop in alpha (-1.0%) and t (3.01 → 2.50). Strong alpha engine. |
| **w/o `activist_13d`** | 4 remaining | +5.9% | 3.03 | 0.23 | 0.70 | -11.9% | Negligible t change (3.01 → 3.03); drawdown improves (-13.4% → -11.9%). |

---

## 6. Acceptance Criteria & Direct Verdict

1. **Does the blend beat its best single stream on H2 t?**
   - **YES.** The **Inverse-Vol Blend achieves H2 t = 3.01** (Alpha +6.0%/yr, Max DD -13.4%),
     surpassing the best standalone sleeve on H2 t (**`ContinuousFuturesTrend`: t = 2.56**, Alpha +4.5%/yr, Max DD -10.8%).
   - The equal-weight blend also delivers positive alpha (+6.4%/yr), but achieves a lower t-stat (t = 2.23) and larger drawdown (-20.1%).

2. **Verdict per the project bar:**
   - **`unproven`.**
   - **Reasoning:** The project rule strictly defines edge as: *`unproven` unless t >= 2 and alpha > 0 in both halves. Never round up.*
   - While the Inverse-Vol Blend attains robust out-of-sample edge in **H2 (Alpha +6.0%, t = 3.01)**,
     its in-sample **H1 performance achieved Alpha +2.0%, t = 1.18** (< 2.0).
     Because t < 2.0 in H1, the formal designation remains strictly **`unproven`**.

---

## 7. Realism Notes & Copier Implementation Guidance

1. **Activist 13D Trade Count & Fragility:**
   - Schedule 13D filings by qualified hedge funds are relatively infrequent (averaging fewer than 20 positions/year across the S&P 1500).
   - In H1 (2010–2018), 13D delivered spectacular alpha (+27.9%/yr, t=1.46) on high idiosyncratic volatility (56.4%).
   - In H2 (2018–2026), 13D alpha normalized substantially to +6.9%/yr (t=0.70).
   - Inverse-volatility correctly dampens this fragility by assigning 13D a modest 3.6% weight, preventing event noise from destabilizing the book.

2. **Event Sleeve Beta Characteristics:**
   - Both `buyback_sp100` (beta 0.29–0.31) and `activist_13d` (beta 0.55–0.58) hold unhedged single-stock equities during their active windows.
   - However, because positions are held for short windows (10 and 20 sessions respectively) and idle capital rests in cash,
     their effective beta to QQQ across the blended book remains well controlled (overall blend beta = 0.24–0.26).

3. **Starting Copier Allocation:**
   - Because the H1-fit inverse-volatility weights were calibrated without any lookahead into H2, they provide an empirically grounded,
     un-overfit baseline allocation for a live multi-sleeve copier or C2 strategy:
     - **39.4% ContinuousFuturesTrend** (`SPY, QQQ, IEF, USO, GLD, DBA` 6-proxy trend)
     - **20.5% Buyback Drift** (`buyback_sp100`, 10-day post-announcement window)
     - **18.5% SmaDailyGated** (9 SPDR sector 50/200 SMA trend with SPY market gate)
     - **18.0% QuarterlyTeCostGate** (ETF8 quarterly rebalanced momentum)
     - **3.6% Activist 13D** (`activist_13d`, 20-day fund filer window)
