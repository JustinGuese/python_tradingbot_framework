# TradingView trend strategies vs QQQ (2026-11)

Four trend-following bots built on pure, reusable TradingView TA indicator helpers (`tradingbot/utils/tv_*.py`),
evaluated on 1h bars across an equal-weight multi-asset ETF universe (`QQQ SPY IWM GLD TLT`).

Reproduce with:
```bash
POSTGRES_URI=postgresql://ci:ci@localhost:5432/ci PYTHONPATH=. python scripts/onetime_tv_trend_backtest.py
```

## Strategy Overview

1. **SupertrendMTFBot** (`tradingbot/supertrendmtfbot.py`):
   - Multi-timeframe Supertrend: 1h base bars filtered by a higher-timeframe (default `1D`) Supertrend.
   - HTF alignment is strictly causal (`align_htf` uses completed HTF bars shifted by 1 bar; no look-ahead / no repainting).
   - Long 1/5 when both base and HTF directions are +1 (bullish); flat/cash otherwise.
   - Defaults: `period=10`, `mult=3.0`, `htf="1D"`.

2. **UltimateMABot** (`tradingbot/ultimatemabot.py`):
   - Multi-timeframe Ultimate MA: 1h base bars filtered by higher-timeframe (default `4h`) slope.
   - Defaults: `kind="sma"`, `length=20`, `slope_bars=1`, `htf="4h"`.
   - Long 1/5 when both base MA direction and HTF MA direction are +1; flat/cash otherwise.

3. **AlphaTrendBot** (`tradingbot/alphatrendbot.py`):
   - AlphaTrend indicator on 1h bars: momentum-biased ATR trailing stops with 2-bar lag crossover.
   - Long on buy signal, held until matching sell signal; replayed from loaded history window (stateless across restarts).
   - Defaults: `period=14`, `mult=1.0`.

4. **SlingshotChandelierBot** (`tradingbot/slingshotchandelierbot.py`):
   - Slingshot EMA Channel (fast=34, slow=50) + Chandelier Exit (period=22, mult=3.0).
   - Enters on pullback into the EMA zone during a bull trend (not overextended).
   - Exits on close below Chandelier `long_stop` or when trend flips bearish.
   - Replayed from loaded history window (stateless across restarts).

## Common Method & Constraints

- **Universe:** `QQQ SPY IWM GLD TLT`. Each active ticker gets 1/5 weight; unallocated weight is held in cash.
- **Timeframe:** 1h base bars over ~730 days (the maximum historical intraday 1h window served by yfinance).
- **Execution Cost:** 5 bps one-way slippage (framework `ExecutionConfig` default).
- **Long-Only:** All strategies operate long-only via `targetWeights` (bearish signals allocate 0.0 weight).
- **Judging Rule:** Judged by alpha vs QQQ and its t-statistic (`t >= 2`, positive in both halves, beta < 0.5), per CLAUDE.md.
- **Untuned:** Code defaults only; no parameter search or curve-fitting.

## Results vs QQQ (Pending Final Review Run)

> [!NOTE]
> The backtest script requires external market data / network access. This table is pre-registered
> and left pending for the final reviewer to populate by running `scripts/onetime_tv_trend_backtest.py`.

| Bot | HTF | Start | End | Alpha/yr | t | Beta | Corr | Max DD | CAGR | Sharpe | Trades | Bar (t>=2) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SupertrendMTFBot | 1D | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* |
| UltimateMABot | 4h | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* |
| AlphaTrendBot | - | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* |
| SlingshotChandelierBot | - | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* | *pending* |

## Reading it by the Project Bar

*(To be written during final review once the script is executed against historical data)*

- **Sample:** ~730 days of hourly bars (~5,000 bars per ETF).
- **Primary metric:** Annualized alpha vs QQQ and its t-statistic.
- **H1 / H2 consistency:** Alpha positive in both halves of the equity curve.
- **Beta constraint:** Low beta (< 0.5) to confirm alpha is not leveraged market beta.

## Deployment Status

All four bots are registered in `helm/tradingbots/values.yaml` as active (un-suspended) paper bots:
- Schedule: `"5 13-20 * * 1-5"` (hourly at :05 past the hour during US trading hours).
- Paper only: paper portfolio tracking via CronJob. None is wired to live broker copiers.
