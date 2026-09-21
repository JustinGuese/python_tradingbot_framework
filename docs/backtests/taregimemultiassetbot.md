# TARegimeMultiAssetBot — backtest

TARegimeAdaptiveBot's Hurst-regime TA signal (`utils.ta_regime`), with its live
parameters unchanged, run on 8 US ETFs: SPY, EFA, EEM, TLT, IEF, GLD, DBC, UUP.
Each asset gets an equal 1/8 sleeve. A 1 buys the sleeve, a -1 exits it, and a 0
holds.

**Status: paper only** (`10 21 * * 1-5`). It is not in any copier. See the verdict.

## Method

- `backtest_bot` on yfinance adjusted daily closes, with TA from the same
  `add_all_ta_features` + `ffill().fillna(0)` pipeline as `DataService`.
- Default cost model: 5 bps slippage and the `EXECUTION_CONFIG` no-trade band.
- Scored on alpha vs QQQ (see CLAUDE.md), using the `alpha` / `alpha_t` / `beta`
  that `backtest_bot` reports.
- Idle cash earns 0% in the backtest. Live, the copier parks it in SHV.
- Script: an offline harness that fetches from yfinance and never touches the
  DB. It was not committed; the whole setup is described above.

## Result

Same parameters in every row. SPY-only is what TARegimeAdaptiveBot trades.

| Window | Universe | Return | Sharpe | Max DD | Beta | Alpha/yr | t |
|---|---|---|---|---|---|---|---|
| 2020-09 → 2026-09 | SPY only | 49.3% | 0.62 | 16.5% | 0.38 | +0.4% | 0.10 |
| | **macro8** | 33.4% | 0.96 | **9.2%** | **0.11** | **+3.0%** | **1.56** |
| | macro12 (+QQQ, IWM, SLV, XLU) | 32.5% | 0.77 | 10.6% | 0.17 | +1.8% | 0.82 |
| | no US equity (10) | 28.1% | 0.74 | 11.6% | 0.09 | +2.6% | 1.14 |
| | all 15 | 36.5% | 0.83 | 11.8% | 0.15 | +2.6% | 1.13 |
| 2020-09 → 2023-08 | SPY only | 27.4% | 0.70 | 15.5% | 0.38 | +4.3% | 0.77 |
| | macro8 | 5.1% | 0.35 | 9.2% | 0.10 | +0.5% | 0.19 |
| 2023-03 → 2026-09 | SPY only | 22.9% | 0.62 | 16.5% | 0.38 | −3.5% | −0.85 |
| | macro8 | 26.7% | 1.40 | 4.7% | 0.11 | +4.0% | 1.65 |

## Verdict

- **What holds up in both halves is lower risk, not alpha.** Beta is 0.10–0.11
  and max drawdown about half of SPY-only's in each half. Alpha does not hold
  up: macro8 made +0.5% then +4.0%. SPY-only swung the other way, from +4.3% to
  −3.5%, which is why TARegimeAdaptiveBot's +14% live alpha should be read as luck.
- **macro8 was the best of 4 universes on the full sample.** That is mild
  selection bias. No row clears t ≥ 2.
- It is still a better-shaped sleeve than SPY-only: never negative alpha in
  either half, with a third of the beta. That justifies running it as a paper
  bot to build a live record. It does not justify live money. Re-score it on
  `portfolio_worth` after ~6 months, and only add it to a copier if live alpha
  holds up.
