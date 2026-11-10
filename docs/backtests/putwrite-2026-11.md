# option_PutWriteBot Walk-Forward Backtest & Tuning (2026-11)

> [!NOTE]
> **Smoke Test Run**: Executed on a fast sample to verify end-to-end execution.

## Executive Summary

- **Strategy**: Cash-secured short put writing on index ETFs (SPY / QQQ) with curve-slope (^VIX/^VIX3M) and trend (SMA200) gating.
- **A-Priori Defaults**: SPY, 0.25Δ, 35 DTE, ungated, 50% take profit / 21 DTE exit.
- **Tuning Objective**: Walk-forward alpha t-statistic vs QQQ (pick on H1, judge on unseen H2).
- **Ship Decision**: **defaults**

## Strategy Levers

| Lever | A-Priori Default | Grid Values |
|---|---|---|
| Underlying | SPY | SPY, QQQ |
| Put Delta | 0.25 | 0.10, 0.16, 0.25, 0.30 |
| Target DTE | 35 | 21, 35, 45 |
| Max VIX/VIX3M Gate | None (ungated) | None, 1.0, 0.95 |
| Trend Filter | False (off) | False, True (close > SMA200) |
| Min IV-Fair Gap | None (ungated) | None, 0.0, 3 pts (0.03) |
| Take Profit | 0.50 (50% credit) | None, 0.50 |
| Exit DTE | 21 | 7, 14, 21 |

## Walk-Forward Tuning Summary (Synthetic 2000 → 2026)

- **Total Combinations Evaluated**: 4
- **Top-10 Mean H2 t**: -2.54
- **Share of Grid with H2 t > 0**: 25.0%

| Bot                |   Grid |   Defaults, H2 t |   H1 winner, H2 t |   Top-10 mean H2 t | Grid with H2 t > 0   | Shipped   |
|:-------------------|-------:|-----------------:|------------------:|-------------------:|:---------------------|:----------|
| option_PutWriteBot |      4 |            -0.49 |             -0.49 |              -2.54 | 25%                  | No        |

## Real Surface Replay Results (2019-05 → 2026-09)

Replay against historical quotes and daily vol surfaces via `SurfaceReplayMarket(live_expiries=True)`.

| Underlying | Days | Trades | CAGR | Alpha/yr | t | Beta | Max DD |
|---|---|---|---|---|---|---|---|
| SPY | 10 | 2 | 14.83% | 6.98% | 2.79 | 0.04 | -0.00% |


## Detailed Performance Comparison

### Defaults Configuration
- **Rules**: `PutWriteRules(underlying='SPY', put_delta=0.25, target_dte=35, max_term_ratio=None, trend_filter=False, min_gap=None, take_profit=0.5, exit_dte=21)`
- **H1 Alpha**: 3.93% (t: 1.09)
- **H2 Alpha**: -2.30% (t: -0.49, Beta: 0.11, Sharpe: 1.69)
- **Full Window**: CAGR 4.94%, Max DD -0.35%, Trades: 5

### H1 Winner Configuration
- **Rules**: `PutWriteRules(underlying='SPY', put_delta=0.25, target_dte=35, max_term_ratio=None, trend_filter=False, min_gap=None, take_profit=0.5, exit_dte=21)`
- **H1 Alpha**: 3.93% (t: 1.09)
- **H2 Alpha**: -2.30% (t: -0.49, Beta: 0.11, Sharpe: 1.69)
- **Full Window**: CAGR 4.94%, Max DD -0.35%, Trades: 5

## Protocol Decision & Live Rules

According to the walk-forward protocol:
1. Candidate chosen on H1 by alpha t vs QQQ.
2. Judged on out-of-sample H2.
3. Shipped tuned values only if H1 winner beats defaults on H2.

Result: **defaults** (`OptionPutWriteBot.RULES` set accordingly).
