# PLAN — execution timing, leg cleanup, tracking, new research (gembuild)

Out of scope (user said NOT): cutting fleet bots, raising the t bar, second benchmark, extending history.
Rules for every step: touch only listed files; max 2 test rounds; never weaken configs/tests; default is LIVE (no new dry-run defaults).

## Timing design (applies to Batches 0-2)
Targets are computed after the US close (21:xx UTC) and are the *previous close's* decision. Copiers then
execute **next day mid-session, never at the open**:
- Single execution slot **15:00-15:10 UTC** for every venue. It is >= 30 min after the US open in winter
  (14:30 UTC) and >= 90 min in summer (13:30 UTC), and still >= 30 min before London UCITS close in summer
  (15:30 UTC). So it works year-round with no DST juggling.
- Orders go out as **marketable limit orders** (last price +/- 0.15%, DAY) instead of raw market orders.
- A guard refuses to submit outside the window [open+30min, close-15min] (US calendar) and logs why.
  IB's "will not be placed until Monday" deferral stops counting as a silent reject.

---
## Batch 0 — shared contract (1 step)
### S0 execution policy
Files: tradingbot/livetrade/execution.py (new), tradingbot/livetrade/copier.py, tradingbot/livetrade/broker.py, tests/test_livetrade_execution.py (new)
- `execution.py`: `ExecutionPolicy` (env: `LIVETRADE_MIN_MIN_AFTER_OPEN=30`, `LIVETRADE_MIN_BEFORE_CLOSE=15`,
  `LIVETRADE_LIMIT_BAND_PCT=0.0015`), `in_window(now)` using `utils/market_calendar.session_open_utc/close_utc`,
  `marketable_limit(side, last_price)`.
- `broker.py`: `LiveBroker.supports_limit_orders: bool = False`; `place_order(..., limit_price: float | None = None)`
  documented as optional. Existing adapters keep working (copier passes `limit_price` only if `supports_limit_orders`).
- `copier.py`: before `_execute_orders`, abort the sync with a clear ERROR line if outside the window
  (crypto brokers, i.e. Hyperliquid, are exempt via `broker.always_open = True`). Pass limit prices to capable brokers.
- Tests: window math (winter/summer/early close/weekend); limit price sides; copier skips outside window and
  does not skip for an always-open broker.

---
## Batch 1 — parallel (4 steps, disjoint files)
### S1a IB limit orders
Files: tradingbot/livetrade/interactive_brokers.py, tests/test_livetrade_ib.py
- Implement `supports_limit_orders = True`; `LimitOrder` with TIF DAY when `limit_price` given. Treat the IB
  "not placed until <next session>" warning (399) as a clear skipped-not-failed status.
- Tests: limit order built with price/TIF; 399 warning classified as deferral.

### S1b MT5 "result expired" fix
Files: tradingbot/livetrade/mt5/broker.py, tradingbot/livetrade/mt5/client.py, tests/test_livetrade_mt5.py, tests/mt5_fake.py
- Diagnose why every BUY on 2026-11-13 ended "result expired" (RPyC result timeout vs a request that was
  filled/rejected: check `order_send` retcode handling, filling mode IOC/FOK/RETURN per symbol, deviation, timeout).
  Fix, add limit/deviation support (`supports_limit_orders = True`), and make the broker confirm via position
  lookup after a timeout instead of reporting failure on an order that may have filled.
- Tests: timeout followed by position present => success, no resend (idempotency); retcode mapping.

### S1c Live-vs-paper tracking
Files: tradingbot/utils/live_tracking.py (new), scripts/live_vs_paper_tracking.py (new), tests/test_live_tracking.py (new)
- From `live_equity` per broker and the paper blend's `portfolio_worth` (weekday + gap rules from AGENTS.md),
  compute daily tracking error, cumulative drift, beta of live to paper, and the share of days the broker was
  flat while the blend was invested. Prints a table. This is the live "does it match the backtest" check.
- Tests: flat-broker detection; weekday/gap filtering; drift on synthetic series.

### S1d Trial registry (bookkeeping only, no gate)
Files: tradingbot/utils/db.py, tradingbot/utils/trial_registry.py (new), tradingbot/utils/backtest.py, tests/test_trial_registry.py (new)
- Table `backtest_trials` (bot, params hash, date, alpha, alpha_t, beta, n). `backtest_bot` and tune helpers
  append a row when `record_trial=True` (default True, failures to write never break a backtest).
  Helper `trial_count(bot|all)`. No threshold changes anywhere.
- Tests: row written once per call; DB failure swallowed; count helper.

---
## Batch 2 — config + docs (1 step; owns values.yaml)
### S2 schedules, defaults, docs
Files: helm/tradingbots/values.yaml, docs/guides/live-trading.md, TODOS.md
- Move `interactiveBrokers`, `etoro`, `mt5_mql5` schedules to 15:00 / 15:05 / 15:10 UTC (keep the 21:xx bot
  runs as the target producers). Document the slot reasoning in comments.
- Set the shared `liveTrade.botWeights` default to the validated blend (remove the paused 3x bot + QQQ clone).
- Leave `collective2XauZerifine` enabled (user decision); only add a comment that it needs a paid Trade Leader plan (error 1020).
- live-trading.md: execution-window section; TODOS.md: replace C2 item, add real-money IBKR master-account item.

---
## Batch 3 — research, parallel (4 steps, disjoint new files)
### S3a Buyback sleeve, broader universe, LONG-ONLY (no shorts, no hedge)
Files: scripts/onetime_buyback_broad_study.py (new), tradingbot/buybackbroadbot.py (new), tests/test_buyback_broad_bot.py (new), docs/backtests/buyback-broad-2026-11.md (new)
- Study: buyback_sp100 vs a broader universe, long-only, idle cash in SHV. Report alpha/t/beta/corr per half.
  Bot (paper) only if alpha > 0 in both halves and beta < 0.5; otherwise doc only.
- Tests: exposure caps; no negative weights.

### S3b Numerai cross-sectional model
Files: tradingbot/utils/numerai_features.py (new), tradingbot/numerai_signals_submit.py, tests/test_numerai_signal.py, tests/test_numerai_submit.py
- Replace pure 12-1 momentum with a rank-blended score: short-term reversal, residual (market/sector-neutral)
  momentum, buyback and insider event flags from existing DB tables, all rank-normalised and neutralised to
  country + market. DB prices only, deterministic, idempotent daily submit stays.
- Tests: neutralisation (zero mean per group), rank bounds [0,1], missing-feature fallback, submit idempotency kept.

### S3c Funding-rate carry study (study only)
Files: scripts/onetime_funding_carry_study.py (new), docs/backtests/funding-carry-2026-11.md (new)
- Pull Hyperliquid public funding history (BTC/ETH/SOL...), model delta-neutral carry net of fees and borrow,
  report yield, max DD, t-stat of the daily PnL, and correlation to QQQ. No bot unless a later decision asks.

### S3d Long-only/flat trend on Darwinex-tradable instruments (study only, no shorts)
Files: scripts/onetime_trend_darwinex_study.py (new), docs/backtests/trend-darwinex-2026-11.md (new)
- Instruments limited to what a Darwinex/DXtrade-style venue offers: FX majors, index and commodity futures proxies
  (yfinance `=F` / `=X`). Long/flat 12-1 trend with vol targeting. Report alpha/t/beta per half and the
  correlation to the C2 blend sleeves.

---
## Final review (me): run new tests once; `git diff pre-agy` vs this plan; confirm no config/test weakened.
