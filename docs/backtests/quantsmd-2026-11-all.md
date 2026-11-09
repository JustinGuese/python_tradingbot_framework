# quants.md round 2: the other 35 strategies, backtested (2026-11-03)

The remaining entries of https://quants.md/?sort=sharpe (round 1 covered the top ten, see
quantsmd-2026-11.md), re-implemented as `tradingbot/quantsmd_*.py`. Five walk-forward / optimizer rows
(wf_01 to wf_05) are validation demos of bots we already have and were not built as bots.
Reproduce with `uv run python scripts/onetime_quantsmd_backtest.py <module> ...` (daily bots about
5-50 min each, `monthly_circuit_breaker` is the slowest at about 25 min; offline, yfinance data).

Method: as in round 1. One `backtest_bot` run per bot over its whole common history, default
parameters, framework cost model, `benchmark_close` = QQQ of the same bar size. Alpha / t / beta / corr are
annualised vs QQQ per the CLAUDE.md formula; H1 / H2 are alpha stats on the two halves of that one equity
curve. Start year is the oldest bar every ticker shares (futures 2019, FX and ETF8 2006, ETF3 and sector
bots 2000); for intraday bots the column shows the start month-day of a 7 to 60 day window.

Source Sharpe is in parentheses; it is the quants.md number, not ours. CAGR is in percent per year.

Note: the old column was total return, corrected 2026-11-09, values are approximate (start year, not exact first bar).

| Bot (source Sharpe) | Start | Alpha/yr | t | Beta | Corr | Sharpe | CAGR (true) | Max DD | QQQ CAGR | H1 alpha (t) | H2 alpha (t) | Trades | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PairsRatioZscore (-0.05) | 2000 | +4.2% | 3.10 | 0.05 | 0.19 | 0.67 | 4.5% | -17.4% | 8.0% | +6.5% (3.13) | +1.5% (0.89) | 252 | watch (t>=2, both halves +) |
| CrossSectionalMomentum (-0.24) | 2000 | +2.7% | 2.09 | 0.22 | 0.67 | 0.59 | 4.9% | -25.8% | 8.0% | +2.7% (1.32) | +0.9% (0.66) | 587 | watch (t>=2, both halves +) |
| RiskParityMultiasset (0.16) | 2006 | +2.7% | 1.84 | 0.20 | 0.55 | 0.76 | 5.7% | -19.8% | 15.4% | +4.6% (2.21) | +0.6% (0.30) | 1464 | unproven |
| BracketTrend (-0.38) | 2000 | +0.8% | 1.77 | 0.03 | 0.36 | 0.48 | 1.3% | -7.8% | 8.5% | +0.9% (1.46) | +0.4% (0.70) | 412 | unproven |
| BracketRsiReversion (-0.43) | 2000 | +1.3% | 1.75 | 0.09 | 0.54 | 0.53 | 2.2% | -9.3% | 8.5% | +1.8% (1.56) | +0.6% (0.65) | 643 | unproven |
| StopBreakoutEntry (-0.19) | 2000 | +0.7% | 1.55 | 0.05 | 0.51 | 0.49 | 1.3% | -9.0% | 8.5% | +0.5% (0.66) | +0.9% (1.51) | 1866 | unproven |
| StopLimitBreakout (-0.05) | 2000 | +0.7% | 1.51 | 0.06 | 0.55 | 0.49 | 1.3% | -6.4% | 8.5% | +0.9% (1.20) | +0.4% (0.69) | 1658 | unproven |
| OcoDipOrBreakout (-0.08) | 2000 | +0.8% | 1.39 | 0.14 | 0.78 | 0.52 | 2.2% | -10.5% | 8.5% | +1.0% (1.14) | +0.5% (0.69) | 2336 | unproven |
| TrailingStopLimit (-0.38) | 2000 | +0.6% | 1.26 | 0.04 | 0.36 | 0.39 | 1.0% | -6.7% | 8.5% | +0.7% (0.94) | +0.3% (0.48) | 467 | unproven |
| TrailingStopTrend (-0.33) | 2000 | +0.6% | 1.18 | 0.04 | 0.37 | 0.38 | 1.0% | -7.6% | 8.5% | +0.7% (0.95) | +0.2% (0.32) | 471 | unproven |
| MarketSmaCross (0.21) | 2000 | +1.3% | 1.18 | 0.16 | 0.60 | 0.45 | 3.0% | -15.4% | 8.5% | +1.8% (1.06) | -0.1% (-0.09) | 563 | unproven |
| SignalChangeRotationOrders (0.22) | 2000 | +1.3% | 1.15 | 0.16 | 0.60 | 0.45 | 2.8% | -15.5% | 8.5% | +1.7% (1.03) | -0.2% (-0.12) | 452 | unproven |
| FuturesDonchianContracts (0.30) | 2019 | +3.1% | 1.11 | 0.18 | 0.51 | 0.80 | 6.2% | -12.4% | 20.2% | +2.3% (0.56) | +2.7% (0.74) | 511 | unproven |
| SignalChangeDefensive (0.26) | 2006 | +2.9% | 1.07 | 0.22 | 0.37 | 0.50 | 5.7% | -24.8% | 15.4% | +1.8% (0.42) | +3.9% (1.23) | 1255 | unproven |
| StopLimitProtection (0.02) | 2000 | +0.8% | 1.01 | 0.10 | 0.53 | 0.40 | 1.8% | -14.0% | 8.5% | +1.8% (1.50) | -0.8% (-0.81) | 542 | unproven |
| CrossSectionalReversal (-0.24) | 2000 | +1.4% | 0.98 | 0.30 | 0.73 | 0.44 | 4.2% | -32.1% | 8.0% | +2.5% (1.09) | -0.3% (-0.19) | 2912 | unproven |
| MonthlyDriftEtf (0.31) | 2006 | +2.0% | 0.96 | 0.26 | 0.52 | 0.58 | 5.7% | -25.1% | 15.4% | +2.8% (0.86) | +1.1% (0.44) | 865 | unproven |
| LimitTrendPullback (-0.04) | 2000 | +0.5% | 0.88 | 0.07 | 0.50 | 0.37 | 1.3% | -6.7% | 8.5% | +1.0% (1.07) | -0.2% (-0.27) | 1491 | unproven |
| StopLossProtection (0.00) | 2000 | +0.6% | 0.73 | 0.11 | 0.55 | 0.36 | 1.8% | -14.0% | 8.5% | +1.5% (1.19) | -0.9% (-0.94) | 543 | unproven |
| MonthlyCircuitBreaker (—) | 2006 | +1.3% | 0.66 | 0.24 | 0.49 | 0.50 | 4.9% | -22.4% | 15.4% | +2.3% (0.72) | +0.3% (0.12) | 1169 | unproven |
| PairsHedgeRatio (-0.30) | 2000 | +0.9% | 0.64 | 0.09 | 0.31 | 0.25 | 1.5% | -28.5% | 8.0% | +3.5% (1.73) | -2.9% (-1.76) | 189 | unproven |
| TrailingStopRsi (-0.41) | 2000 | +0.3% | 0.32 | 0.19 | 0.74 | 0.37 | 2.2% | -16.4% | 8.5% | +1.6% (1.10) | -1.3% (-1.23) | 716 | unproven |
| MacdTrend (0.22) | 2000 | -0.0% | -0.01 | 0.29 | 0.57 | 0.24 | 2.4% | -56.0% | 8.0% | -1.9% (-0.57) | +1.8% (0.70) | 14625 | unproven |
| QuarterlyDualMomentum (0.12) | 2006 | -0.1% | -0.02 | 0.38 | 0.47 | 0.36 | 4.9% | -44.0% | 15.4% | +0.8% (0.15) | -1.3% (-0.31) | 164 | unproven |
| DailyPartialDrift (0.30) | 2000 | -0.2% | -0.11 | 0.26 | 0.52 | 0.20 | 1.8% | -51.2% | 8.0% | -1.1% (-0.33) | -1.0% (-0.38) | 28239 | unproven |
| IntradayRsi15m (—) | 08-11 | -2.9% | -0.35 | 0.22 | 0.69 | -1.44 | n/a | -3.1% | -22.0% | -1.8% (-0.15) | -4.1% (-0.34) | 112 | too short |
| MarketRsiReversion (-0.17) | 2000 | -0.4% | -0.52 | 0.17 | 0.75 | 0.27 | 1.3% | -17.4% | 8.5% | +0.3% (0.30) | -1.2% (-1.27) | 550 | unproven |
| LimitRsiDip (0.14) | 2000 | -0.5% | -0.55 | 0.23 | 0.80 | 0.29 | 2.0% | -22.3% | 8.5% | +1.0% (0.76) | -1.5% (-1.30) | 596 | unproven |
| FxMomentumLots (-0.71) | 2006 | -0.6% | -0.60 | 0.01 | 0.06 | -0.08 | -0.5% | -27.7% | 16.1% | +0.4% (0.27) | -1.5% (-1.33) | 890 | unproven |
| Intraday30mStopBreakout (—) | 08-12 | -3.9% | -0.93 | 0.05 | 0.42 | -2.41 | n/a | -1.5% | -24.4% | -0.8% (-0.14) | -6.2% (-1.03) | 152 | too short |
| FxCrossSectionalMomentum (-1.00) | 2006 | -1.6% | -1.19 | 0.03 | 0.12 | -0.17 | -1.1% | -36.1% | 16.1% | -2.3% (-1.02) | -0.7% (-0.47) | 250 | unproven |
| FxTimeSeriesMomentum (-0.59) | 2006 | -1.6% | -1.29 | 0.02 | 0.07 | -0.23 | -1.7% | -38.4% | 16.1% | -0.7% (-0.32) | -2.4% (-1.69) | 392 | unproven |
| Intraday1hSmaTrend (—) | 12-08 | -4.8% | -1.39 | 0.26 | 0.63 | 0.03 | n/a | -12.6% | 21.0% | -4.3% (-0.78) | -5.3% (-1.27) | 622 | too short |
| Intraday5mBracketReversion (—) | 08-10 | -23.2% | -3.66 | 0.17 | 0.70 | -6.28 | n/a | -6.5% | -22.4% | -19.6% (-2.51) | -30.2% (-3.10) | 319 | too short |
| Intraday1mEmaScalp (—) | 10-23 | -178.3% | -4.08 | 0.47 | 0.84 | -17.68 | n/a | -7.3% | -71.0% | -264.4% (-5.30) | -56.4% (-0.81) | 351 | too short |

## Reading it by the project bar (t >= 2 and positive in both halves)

- **No bot has real edge.** Two clear t >= 2 on the full window and are positive in both halves, but
  neither is convincing: **PairsRatioZscore** (t 3.10, beta 0.05, KO/PEP long leg only; H2 t is 0.89) and
  **CrossSectionalMomentum** (t 2.09, H2 t 0.66, corr 0.67). KO/PEP is the textbook pair, so a good
  backtest on it carries hindsight selection. Treat both as watch-list, not promoted.
- **Order-style bots are tiny, low-beta books.** Sizing is 10-20% of NAV per name, so betas are 0.03-0.2 and
  CAGR is 0.3-1%. Their small positive alphas (t 1.2-1.8) are the low-beta artefact in a rising tape, not skill.
  The stop / bracket / trailing variants of one SMA signal give nearly identical results because the entry
  signal dominates and the exit order only changes the tail.
- **MarketSmaCross and SignalChangeRotationOrders are the same signal** (t 1.18 and 1.15) and differ only in
  the rebalance gate. Keep one.
- **Churn:** MacdTrend (14,625 trades) and DailyPartialDrift (28,239) rebalance every bar; both have t about 0
  and max drawdown above 50%. The live no-trade band cuts trades, the backtest does not fully show that.
- **FX bots lose** (t -1.2 to -1.6 with betas near 0): spot price returns without carry, long-only. Futures
  Donchian (t 1.11, 2019 start) is not proven.
- **Intraday bots: windows are 7-60 days, so no verdict is possible** ("too short"). The two with the
  worst numbers, Intraday1mEmaScalp (t -4.08, Sharpe -17.7) and Intraday5mBracketReversion (t -3.66, Sharpe -6.3),
  trade 350 and 320 times in under two weeks and lose to costs; they are **suspended** in helm. The
  alpha %/yr column of intraday rows is an extrapolation of a few days and meaningless.
- **Source Sharpe does not carry over.** Ranking by it is not the ranking here, as in round 1.

## Caveats

- Departures from the QuantJourney code are listed in each bot's docstring. The main ones: long-only
  (pairs, cross-sectional, FX and futures bots hold only the long leg, so they are no longer
  market-neutral); no resting orders (order strategies are replayed statelessly on OHLC bars with the fill model
  in `utils/quantsmd_orders.py`, executed at bar close); drift bands, partial rebalances and
  signal-change triggers replaced by the framework's no-trade band; whole-lot / contract sizing replaced by the
  equivalent weight, unrounded.
- A bearish flip on the bar right after an entry submits the protective stop instead of selling (StopLoss,
  StopLimit and TrailingStop variants). This is what the upstream `if/elif` order does.
- FX and futures bots use yfinance spot / continuous series and are paper only; no roll, multiplier or
  swap accounting.
- ETF universes are today's survivors of a fixed list; sector bots start in 2000, ETF8 bots in 2006.
- All 35 are scheduled as paper CronJobs (two suspended). None is wired into any live copier.
