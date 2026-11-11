# option_PutWriteBot Walk-Forward Backtest & Tuning (2026-11)

## Executive Summary

- **Strategy**: Cash-secured short put writing on index ETFs (SPY / QQQ) with curve-slope (^VIX/^VIX3M) and trend (SMA200) gating.
- **A-Priori Defaults**: SPY, 0.25Δ, 35 DTE, ungated, 50% take profit / 21 DTE exit.
- **Tuning Objective**: Walk-forward alpha t-statistic vs QQQ (pick on H1, judge on unseen H2).
- **Ship Decision**: **winner**

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

- **Total Combinations Evaluated**: 1728
- **Top-10 Mean H2 t**: 1.09
- **Share of Grid with H2 t > 0**: 46.8%

| Bot                |   Grid |   Defaults, H2 t |   H1 winner, H2 t |   Top-10 mean H2 t | Grid with H2 t > 0   | Shipped   |
|:-------------------|-------:|-----------------:|------------------:|-------------------:|:---------------------|:----------|
| option_PutWriteBot |   1728 |            -2.55 |             -1.58 |               1.09 | 47%                  | Yes       |

## Real Surface Replay Results (2019-05 → 2026-09)

Replay against historical quotes and daily vol surfaces via `SurfaceReplayMarket(live_expiries=True)`.

| Underlying | Days | Trades | CAGR | Alpha/yr | t | Beta | Max DD |
|---|---|---|---|---|---|---|---|
| SPY | 1177 | 173 | 1.39% | -4.00% | -1.59 | 0.18 | -22.00% |


## Detailed Performance Comparison

### Defaults Configuration
- **Rules**: `PutWriteRules(underlying='SPY', put_delta=0.25, target_dte=35, max_term_ratio=None, trend_filter=False, min_gap=None, take_profit=0.5, exit_dte=21)`
- **H1 Alpha**: 0.48% (t: 0.34)
- **H2 Alpha**: -3.08% (t: -2.55, Beta: 0.26, Sharpe: 0.29)
- **Full Window**: CAGR 1.67%, Max DD -28.13%, Trades: 882

### H1 Winner Configuration
- **Rules**: `PutWriteRules(underlying='SPY', put_delta=0.16, target_dte=21, max_term_ratio=None, trend_filter=True, min_gap=None, take_profit=None, exit_dte=7)`
- **H1 Alpha**: 2.47% (t: 3.87)
- **H2 Alpha**: -2.01% (t: -1.58, Beta: 0.15, Sharpe: 0.16)
- **Full Window**: CAGR 1.76%, Max DD -20.47%, Trades: 569

## Protocol Decision & Live Rules

According to the walk-forward protocol:
1. Candidate chosen on H1 by alpha t vs QQQ.
2. Judged on out-of-sample H2.
3. Shipped tuned values only if H1 winner beats defaults on H2.

Result: **winner** (`OptionPutWriteBot.RULES` set accordingly).

## Addendum 2026-11-11: grid size and T-bill yield

- The grid had 1,728 combinations, well above the ~300 cap in PLAN.md, so the H1 winner is
  more exposed to selection noise than the protocol intends.
- The backtests above earned 0% on cash. Crediting ^IRX on the book
  (docs/backtests/putwrite-yield-2026-11.md, pre-declared configs, no new grid) moves the
  shipped SPY rules from -0.9% to +3.2% alpha on the real surface (t 1.56), and the synthetic
  second half from -2.0% (t -1.58) to -0.2% (t -0.13). Still below t >= 2 out of sample.
- The QQQ real-surface rows in that doc have 0 trades and are invalid.
- Verdict unchanged: the bot stays suspended.
