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

## Results vs QQQ

Run 2026-11-17, untuned defaults, 1h bars 2023-12-22 to 2026-11-16, 5 bp cost per trade, QQQ as benchmark.
Each bar recomputes its indicators on the last 500 hourly bars, about what a live run loads (`period="60d"`).

| Bot | HTF | Start | End | Alpha/yr | t | Beta | Corr | Max DD | CAGR | Sharpe | Trades | Bar (t>=2) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SupertrendMTFBot | 1D | 2023-12-22 | 2026-11-16 | +0.4% | 0.14 | 0.10 | 0.39 | -4.8% | +2.1% | 0.42 | 592 | fail |
| UltimateMABot | 4h | 2023-12-22 | 2026-11-16 | -3.3% | -1.21 | 0.10 | 0.40 | -10.6% | -1.7% | -0.28 | 1796 | fail |
| AlphaTrendBot | - | 2023-12-22 | 2026-11-16 | -2.8% | -0.81 | 0.22 | 0.58 | -9.8% | +0.9% | 0.15 | 932 | fail |
| SlingshotChandelierBot | - | 2023-12-22 | 2026-11-16 | -3.1% | -1.45 | 0.09 | 0.42 | -10.0% | -1.7% | -0.38 | 829 | fail |

## Reading it by the Project Bar

- **None has an edge.** No bot has t >= 2; the best (SupertrendMTFBot) is +0.4%/yr at t 0.14, which is zero.
  The other three are negative alpha (t between -0.8 and -1.5), consistent with costs eating a trend signal that
  flips 600-1800 times in under three years. Verdict for all four: **unproven, leaning no edge**.
- **They are not QQQ clones.** Beta is 0.09-0.22 because each ticker is only 1/5 of the book and often flat.
  That is the design working, but low beta with ~0 alpha is just a small, slow cash-like book.
- **UltimateMABot trades most (1796) and loses most to costs.** More trades did not buy more alpha.
- **Caveats.** About 2.9 years of 1h bars, one regime (a QQQ rally). Defaults only, no walk-forward. The study
  stubbed the bot DB rows and read bars straight from yfinance, so it is not the production data path.
- **Next step if pursued:** test the same signals on daily bars (decades of history, far lower turnover) before
  tuning anything on 1h.

## Deployment Status

All four bots are registered in `helm/tradingbots/values.yaml` as active (un-suspended) paper bots:
- Schedule: `"5 13-20 * * 1-5"` (hourly at :05 past the hour during US trading hours).
- Paper only: paper portfolio tracking via CronJob. None is wired to live broker copiers.
