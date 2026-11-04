# quants.md v2 variants, backtested (2026-11-04)

Ten variants of the weak or unproven bots in quantsmd-2026-11.md and quantsmd-2026-11-all.md, each a new file
(`tradingbot/quantsmd_*_v2.py` or a new name) so the originals stay untouched. Same method as round 1 and 2:
one `backtest_bot` run over the whole common history, default parameters, framework cost model,
`benchmark_close` = QQQ, alpha / t / beta vs QQQ, H1 / H2 are the two halves of one equity curve. Reproduce with
`uv run python scripts/onetime_quantsmd_backtest.py <module> ...`.

| Variant | Fixes | Start | Alpha/yr | t | Beta | Corr | Max DD | Trades | H1 t | H2 t | Original t (trades) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SmaDailyGated | SPY < SMA200 -> cash | 2000 | +5.5% | **3.08** | 0.16 | 0.42 | -19.7% | 2479 | 1.98 | 1.62 | 2.27 |
| CrossSectionalMomentumV2 | skip month, vol filter | 2000 | +2.2% | 1.77 | 0.22 | 0.68 | -25.2% | 563 | 1.24 | 0.12 | 2.09 |
| CrossSectionalReversalV2 | rank buffer | 2000 | +2.2% | 1.57 | 0.29 | 0.73 | -33.2% | 1741 | 1.58 | 0.15 | 0.98 (2912) |
| QuarterlyTeCostGateGated | SPY SMA200 gate | 2006 | +3.1% | 1.32 | 0.26 | 0.47 | -21.0% | 630 | 1.19 | 0.64 | 2.02 |
| MacdTrendV2 | hysteresis + min hold | 2000 | +1.3% | 0.62 | 0.30 | 0.60 | -44.0% | 8210 | 0.32 | 0.54 | -0.01 (14625) |
| PairsBasketZscore | 8 pairs, not KO/PEP | 2008 | +0.8% | 0.39 | 0.36 | 0.67 | -30.2% | 1908 | 0.41 | 0.22 | 3.10 |
| DailyPartialDriftV2 | hysteresis + min hold | 2000 | -0.2% | -0.11 | 0.27 | 0.53 | -51.6% | 26318 | -0.33 | -0.35 | -0.11 (28239) |
| OrderEnsemble | 12 order bots, vol target 10%, SHV | 2007 | -0.3% | -0.29 | 0.41 | 0.88 | -20.4% | 8418 | 0.03 | -0.43 | none (each 0.3-1.8) |
| FxEtfTimeSeriesMomentum | currency ETFs for carry | 2007 | -1.8% | -1.10 | 0.03 | 0.08 | -44.8% | 685 | -0.36 | -1.43 | -1.29 |
| IntradayGatedEma (5m, 60d) | cost gates, 1 trade/session | 08-11 | -9.6% (extrapolated) | -1.61 | 0.04 | 0.26 | -2.5% | 122 | n/a | n/a | -4.08 (1m scalp) |

## Reading it by the project bar (t >= 2, positive in both halves)

- **SmaDailyGated is the first quantsmd bot to clear t >= 2 on the full window with both halves positive**
  (H1 t 1.98, H2 t 1.62), beta 0.16 and max drawdown cut from -35% to -20%. The gate does the work: staying
  in cash when SPY is below its 200-day average removes the 2000-02 and 2008 drawdowns. It is a long-only
  sector trend book that is in cash a lot, so treat it as a watch-list candidate, not promoted: neither half
  reaches 2 on its own, and a 25-year window with one gate parameter (200) has little out-of-sample content.
- **The same gate hurt QuarterlyTeCostGate** (t 2.02 -> 1.32). It only re-enters at the next quarterly
  rebalance, so it misses recoveries. Not worth keeping over the original.
- **CrossSectionalReversalV2 helps** (t 0.98 -> 1.57, trades 2912 -> 1741) but H2 is flat (t 0.15).
  **CrossSectionalMomentumV2 is worse** than the original (t 2.09 -> 1.77), so skip-month plus a vol filter is
  not an improvement here.
- **MacdTrendV2 halves the trades and lifts t from 0 to 0.62**, still no edge, and a -44% drawdown. It does
  enter positions on real data (the band is not too wide). **DailyPartialDriftV2 is unchanged** (26k trades, t
  -0.11): its momentum-proportional weights drift every bar, so a state gate on entry does not stop the churn.
  The fix would be on the weights, not the entry.
- **PairsBasketZscore confirms the KO/PEP hindsight worry.** Eight pairs give t 0.39 against 3.10 for the single
  pair.
- **OrderEnsemble is a QQQ-correlated book** (corr 0.88, beta 0.41, alpha about 0, t -0.29). Averaging twelve
  copies of one SMA signal and levering to 10% vol rebuilds QQQ exposure, not alpha. The project rule
  says: replace with QQQ, do not blend it.
- **FxEtfTimeSeriesMomentum is still negative** (t -1.10), so currency-ETF carry did not rescue FX
  momentum. The H2 half is the loser (t -1.43). Drop the idea, or test short-rate carry directly.
- **IntradayGatedEma** is better than the 1m and 5m originals (t -1.61 vs -4.08 and -3.66; 122 trades vs 350 and
  319) but still negative, and the 60-day window is "too short" for a verdict.

## Caveats

- Same as the earlier rounds: long-only, today's ETF survivors, FX / futures proxies, framework cost model
  with no 1m spread, default parameters, no retune. None of these ten was walk-forward tuned.
- The A1 hysteresis bots replay only the last 500 bars of state per call (to keep backtests linear).
- All ten are registered as paper CronJobs in `helm/tradingbots/values.yaml` and none feeds a copier.

## Walk-forward re-tune (2026-11-04)

Same rule as `retune-2026-09.md`: grid-search on H1 by `alpha_t`, then judge the H1 winner against the
defaults on the unseen H2. New parameters ship only if they beat the defaults on H2 with positive H2 alpha.
Differences: H1 = 2000-2012 and H2 = 2013-2026 (these bots have 25 years of daily history), via
`RETUNE_START` / `RETUNE_SPLIT` on `scripts/onetime_retune_walkforward.py`. The grids are the bots' own
`param_grid` (2-9 combinations), so this is a narrow search. IntradayGatedEma is not tuned: yfinance serves 60
days of 5m bars, so no H1/H2 split exists.

H2 alpha (t) of the defaults vs the H1 winner:

| Bot | H1 winner | Defaults H2 | Winner H2 | Verdict |
|---|---|---|---|---|
| DailyPartialDriftV2 | `lookback` 126 | -1.1% (-0.43) | +0.6% (0.25) | **shipped** (better in both halves; still no edge) |
| OrderEnsemble | `target_vol` 0.06, `sleeve_boost` 2.0 | -0.9% (-0.68) | +0.2% (0.23) | **shipped** (lower beta; still a QQQ clone) |
| QuarterlyTeCostGateGated | `off_scale` 0.5 | +1.3% (0.51) | +2.4% (0.93) | **shipped** (better in both halves) |
| SmaDailyGated | `off_scale` 0.5 | +3.1% (1.35) | +1.2% (0.60) | keep 0.0 |
| MacdTrendV2 | `min_hold` 21 | +1.7% (0.68) | +1.0% (0.38) | keep |
| FxEtfTimeSeriesMomentum | `lookback` 63 | -2.3% (-1.44) | -3.5% (-2.10) | keep |
| CrossSectionalReversalV2 | `buffer` 3 | +0.1% (0.09) | +0.1% (0.05) | keep |
| CrossSectionalMomentumV2 | `skip` 21 | -0.2% (-0.15) | same as defaults | keep |
| PairsBasketZscore | `window` 60 | +0.2% (0.08) | same as defaults | keep |

None of the shipped changes gives a bot real edge: the largest H2 t is 0.93. They are small improvements
that survived the out-of-sample check, and the other six defaults held up. SmaDailyGated's default (`off_scale`
0.0) beat the grid winner out of sample, which supports the gate as designed. Note that on the 2013-2026 half
alone its default t is 1.35 (H1 1.68), lower than the full-window 3.08, so the headline number leans on the
2000-2012 drawdowns the gate avoided.
