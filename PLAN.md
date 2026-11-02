# PLAN: two intraday QQQ strategies from the reels

Written for: you, to approve before any worktree or agy run starts.

## What is being built

| | Bot | Rule (from the transcript) |
|---|---|---|
| 1 | `VWAPPullbackBot` | Wait for the opening range to break up, then buy the **first pullback into VWAP**. Stop below the recent range, target **2R**. Plain VWAP crosses are *not* traded (the reel's point: −89R after costs). |
| 2 | `OpenRangeBreakoutBot` | Mark the opening range, buy the first break above it inside the first **90 min**, take a **small target** (~0.35R) with a **wide stop** (~1R). Reel claims 148/162 wins, avg win ≈ $37 vs avg loss ≈ $105. |

## Decisions I made (shout if wrong)

- **Instrument: QQQ**, long-only, flat every night. The reels trade NQ futures. The framework has no futures or shorts, so QQQ is the proxy.
- **5-minute bars, cron every 5 min** during the NY session. The ORB reel's "half of trades last < 90 s" needs 1m bars and a per-minute pod, so that part is **not replicated**. If the 0.35R target is too tight at 5m, the backtest will show it.
- **Pattern B (`makeOneIteration`)**, because these are path-dependent intraday state machines. The built-in backtester can't run Pattern B (AGENTS.md hard limit 1), so there is a custom backtest script (step B2-3).
- **One pure function shared by live and backtest:** `simulate_session(bars, rules) -> SessionResult` (entry, stop, target, exit). Live **replays today's bars through it** and trades the difference between the book and what the replay says it should hold. This is stateless, crash-safe and idempotent: no stop/target needs persisting, and live and backtest cannot drift (same lesson as `option_decide`).
- **DST-safe:** the session is derived with `zoneinfo("America/New_York")`. Cron is a wide UTC window (13:30–20:59), and the bot no-ops outside the session. Early closes come from `utils/market_calendar.py`.
- **Costs:** the framework's 0.05%/side slippage is applied in the backtest. At a 0.35R target this may dominate, so the backtest reports with and without it.
- **Live, not dry-run** (your standing rule): both bots ship **enabled as paper bots** in `values.yaml`. They are **not** added to the C2 blend or copier weights. Their guards are one trade per day, a hard flat before the close, and the stale-bar check.
- **Judged by alpha vs QQQ / t-stat**, per CLAUDE.md. Honest expectation: yfinance gives only ~60 days of 5m bars, so ~40 sessions. That can never reach t ≥ 2, and the verdict will be "unproven". The bots run with `saveToDB=True`, so `historic_data` (keyed by interval) accumulates real 5m history for a proper test later.

## Batch 0: shared contract (sequential, 1 step)

Can't be parallelized: everything else imports it.

### B0-1 `intraday_session` module
- Files: `tradingbot/utils/intraday_session.py`, `tests/test_intraday_session.py`
- Contents:
  - `SessionBars`: filters a bar frame to one NY date and the RTH window, tz-aware.
  - `session_vwap(bars)`: cumulative typical-price × volume / volume. It falls back to typical price when volume is 0.
  - `opening_range(bars, minutes)`: returns `(high, low, end_ts)`.
  - Frozen dataclasses `Trade(entry_ts, entry, stop, target, exit_ts, exit, exit_reason, r_multiple)` and `SessionResult(date, trade | None)`.
  - `resolve_exit(bar, stop, target)`: **stop wins if one bar touches both**.
  - `Protocol IntradayRules` with `simulate_session`.
- Acceptance:
  - Correct on a hand-built 5m frame across a DST boundary (March and November).
  - VWAP matches a manual calc.
  - Partial days and early-close days don't raise.
  - No look-ahead: nothing uses a bar's data before its close.

## Batch 1: the two rule sets (parallel, 2 steps, disjoint files)

### B1-1 VWAP pullback rules
- Files: `tradingbot/utils/vwap_pullback_rules.py`, `tests/test_vwap_pullback_rules.py`
- `VWAPPullbackRules(or_minutes=15, swing_bars=6, target_r=2.0, min_risk_pct=0.0005, pullback_tol=0.0003, max_entry_minutes=240, flat_before_close_minutes=10)`.
- Logic:
  - Arm after a bar **closes above the OR high**.
  - Enter at the close of the first later bar where `low <= vwap*(1+tol)` and `close > vwap`.
  - Stop = min low of the last `swing_bars` bars, and it must sit below VWAP.
  - Target = entry + 2×risk.
  - Skip if risk < `min_risk_pct`.
  - One trade per day. Forced exit before the close.
- Acceptance tests: plain VWAP cross with no OR break → no trade; break then pullback → entry, stop and target exact; stop-and-target same bar → stop; timed exit; tiny-risk skip.

### B1-2 ORB rules
- Files: `tradingbot/utils/orb_rules.py`, `tests/test_orb_rules.py`
- `ORBRules(or_minutes=15, window_minutes=90, target_r=0.35, stop="or_low" | fixed pct, max_stop_pct=0.006)`.
- Logic:
  - Enter at the close of the first bar after the OR whose close is above the OR high, inside the window.
  - Stop = OR low, capped at `max_stop_pct`.
  - Target = entry + `target_r`×risk.
  - Exit at the window end if neither is hit.
  - One trade per day.
- Acceptance tests: break inside the window → entry; break after 90 min → none; target, stop and window-end exits; stop-first tie-break; cap applied.

## Batch 2: bots and backtest (parallel, 3 steps, disjoint files)

### B2-1 `VWAPPullbackBot`
- Files: `tradingbot/vwappullbackbot.py`, `tests/test_vwappullbackbot.py`
- QQQ, `interval="5m"`, `period="5d"`, `saveToDB=True`, `param_grid` over `or_minutes`, `swing_bars`, `target_r`.
- Replays today through B1-1, then buys or sells to match.
- Skips if the last bar is stale (> 2 bars old) or the session is closed.
- Entry points: `if __name__ == "__main__": run_bot(...)`, like TSMOMTrendBot.
- Acceptance tests (with the DB stubbed):
  - Entered state → buys once, and a second run is a no-op.
  - Exit state → sells.
  - Closed or stale session → no trade.
  - `POSTGRES_URI` stub per AGENTS.md pitfall 12.

### B2-2 `OpenRangeBreakoutBot`
- Files: `tradingbot/openrangebreakoutbot.py`, `tests/test_openrangebreakoutbot.py`
- Same shape as B2-1, on B1-2.

### B2-3 Backtest and alpha script
- Files: `scripts/onetime_intraday_orb_vwap_backtest.py`, `tests/test_intraday_backtest_script.py`
- Fetches QQQ 5m (yfinance 60d plus any `historic_data` rows) and runs both rule sets through `simulate_session`.
- Applies slippage per side. Reports, with and without costs:
  - trades, win rate, avg win/loss in R, expectancy;
  - daily P&L → `alpha_stats` vs QQQ close-to-close: alpha, t, beta, corr, max DD;
  - first-half / second-half split, and a **random-entry control** at matched risk, as in the reel (100 draws).
- Acceptance tests: runs on a synthetic frame; cost application correct.

## Batch 3: wire-up and verdict (sequential, 1 step)

Can't be parallelized: it touches shared files (`values.yaml`, `AGENTS.md`) and needs the finished backtest.

### B3-1 Deploy config, docs, results
- Files: `helm/tradingbots/values.yaml`, `AGENTS.md` (Existing Strategies table), `docs/backtests/intraday-orb-vwap-2026-11.md`
- Two CronJob entries, `*/5 13-20 * * 1-5` UTC, not suspended.
- Docs hold the backtest numbers and the honest verdict.
- Acceptance: `helm template` renders both; the docs table matches the script output.

## Execution notes

- **Checkpoint caveat:** `git add -A` will sweep your uncommitted `README.md` edit and the untracked `TODOS.md` into `pre-agy`. Say if you'd rather I stash or exclude them.
- Per step: ruff format and check plus that step's own tests only. The full ruff, mypy and pytest run after each batch merge, as you specified.
- Max 4 concurrent agy workers (Batches 1 and 2 use 2 and 3).
- Final review diffs `pre-agy` against this plan. No test or config gets weakened.
- Nothing is merged to `main` until you approve at the end.

**Waiting for your approval** (and any answers on the decisions above) before I run step 2 (the checkpoint commit).
