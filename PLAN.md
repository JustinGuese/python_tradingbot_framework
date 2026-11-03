# PLAN: the other 35 quants.md bots (round 2)

Round 1 (top 10 by source Sharpe) is merged on main (`af5f4af`). This round implements the
remaining entries of https://quants.md/?sort=sharpe (50 rows in total). 35 are bots, 5 are
walk-forward / optimizer demos (see "Open decisions"). The first plan lives in git history.

Template to copy for every bot: `tradingbot/quantsmd_sma_daily.py` +
`tests/test_quantsmd_sma_daily.py` (already on main). Shared helpers:
`tradingbot/utils/quantsmd_common.py`.

## 0. Rules every worker must follow

0. **Worktree setup first**: your worktree branches from `main`, which lacks this PLAN.md and any
   earlier batch. Run `git merge haiku/work` before anything else. Read the plan at
   `/home/jguese/code/tradingbot/python_tradingbot_framework/PLAN.md`.
1. Touch only the files listed in your step. Never edit configs, `pyproject.toml`, existing
   tests or other bots. Never weaken a test to make it pass.
2. Framework contract (from AGENTS.md): `targetWeights(rows) -> dict[str, float] | None`.
   Long-only, sum <= 1.0, no `"USD"` key, an omitted ticker means exit, `{}` means all cash,
   `None` means "do not rebalance this bar". `self.datas[ticker]` is the history up to the
   current bar; `self.tradeable_tickers` is the universe.
3. Every bot file has: header docstring, one pure `compute_weights(...)` (no DB, no network),
   a `Bot` subclass, and `if __name__ == "__main__": run_bot(Class)`.
4. Header docstring format (copy from the template):
   ```
   Source: https://quants.md/?sort=sharpe
   Strategy page: https://backtester.quantjourney.cloud/strategies/example-<kind>-<NN>-<slug>
   Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_<kind>_<NN>_<slug_underscored>.py (Apache-2.0, (c) 2026 QuantJourney)
   ```
   then 3-6 lines describing the strategy, then a "Departures from source:" list. The exact
   values for `<kind>-<NN>-<slug>` are in the table in section 1.
5. File is `tradingbot/quantsmd_<name>.py`; class is `Quantsmd<CamelName>Bot`; the DB name passed
   to `super().__init__` equals the class name exactly. Test file is `tests/test_quantsmd_<name>.py`.
6. Daily bots need `BACKTEST_PERIOD: ClassVar[str | None] = "max"`, `interval="1d"` and a live
   `period` longer than every lookback (comment why, as in the template). `param_grid` lists
   the main lookbacks / thresholds (3 values each at most).
7. Tests (mirror the template test file; no DB, no network, build `closes` frames by hand):
   - T1 history shorter than the warmup returns `{}` (or `None` for off-schedule bars, and `{}`
     for a rebalance bar with too little data).
   - T2 a hand-built frame where the signal is clearly on gives the exact expected weights.
   - T3 a frame where the signal is clearly off gives `{}` (or the stated fallback).
   - T4 invariants over a random-walk frame (seeded): sum <= 1 + 1e-9, all >= 0, no `"USD"`,
     keys subset of the universe.
   - T5 the Bot class: `Bot` name equals the class name, `tickers` equals the universe, and (for
     scheduled bots) off-schedule bars give `None`.
   - Plus the extra cases named in the step.
8. Quick check (run exactly this, replacing the two names):
   `uv run ruff format tradingbot/quantsmd_X.py tests/test_quantsmd_X.py && uv run ruff check tradingbot/quantsmd_X.py tests/test_quantsmd_X.py && POSTGRES_URI="postgresql://x:x@localhost:5432/x" PYTHONPATH=. uv run --extra dev pytest tests/test_quantsmd_X.py -q -p no:cacheprovider`
   Max 2 fix rounds, then stop and report what is red. Commit on your worktree branch, report
   the branch name.
9. **Do NOT**: add dependencies, touch `helm/`, `docs/`, `scripts/`, `values.yaml`, or any file
   not listed. Do not rename the upstream strategy; do not "improve" it.

## 1. Bot table (name, URLs, universe)

Page = `https://backtester.quantjourney.cloud/strategies/example-` + the slug column.
Upstream = `https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_`
+ the slug with `-` replaced by `_` + `.py`.

| Step | name (`quantsmd_<name>`) | slug | universe |
|---|---|---|---|
| W02 | monthly_drift_etf | weights-02-monthly-drift-etf | ETF8 |
| W04 | quarterly_dual_momentum | weights-04-quarterly-dual-momentum | ETF8 |
| W06 | signal_change_defensive | weights-06-signal-change-defensive | ETF8 |
| W07 | intraday_rsi_15m | weights-07-intraday-rsi-15m | ETF3 |
| W08 | intraday_1m_ema_scalp | weights-08-intraday-1m-ema-scalp | ETF3 |
| W09 | intraday_1h_sma_trend | weights-09-intraday-1h-sma-trend | ETF3 |
| W10 | monthly_circuit_breaker | weights-10-monthly-circuit-breaker | ETF8 |
| W12 | daily_partial_drift | weights-12-daily-partial-drift | SECTORS9 |
| W13 | pairs_ratio_zscore | weights-13-pairs-ratio-zscore | KO, PEP |
| W14 | pairs_hedge_ratio | weights-14-pairs-hedge-ratio | EWA, EWC |
| W15 | cross_sectional_momentum | weights-15-cross-sectional-momentum | SECTORS9 |
| W16 | cross_sectional_reversal | weights-16-cross-sectional-reversal | SECTORS9 |
| W19 | risk_parity_multiasset | weights-19-risk-parity-multiasset | ETF8 |
| W22 | macd_trend | weights-22-macd-trend | SECTORS9 |
| W23 | fx_time_series_momentum | weights-23-fx-time-series-momentum | FX4 |
| W24 | fx_cross_sectional_momentum | weights-24-fx-cross-sectional-momentum | FX4 |
| O01 | market_sma_cross | orders-01-market-sma-cross | ETF3 |
| O02 | market_rsi_reversion | orders-02-market-rsi-reversion | ETF3 |
| O03 | limit_rsi_dip | orders-03-limit-rsi-dip | ETF3 |
| O04 | limit_trend_pullback | orders-04-limit-trend-pullback | ETF3 |
| O05 | stop_breakout_entry | orders-05-stop-breakout-entry | ETF3 |
| O06 | stop_loss_protection | orders-06-stop-loss-protection | ETF3 |
| O07 | stop_limit_breakout | orders-07-stop-limit-breakout | ETF3 |
| O08 | stop_limit_protection | orders-08-stop-limit-protection | ETF3 |
| O09 | trailing_stop_trend | orders-09-trailing-stop-trend | ETF3 |
| O10 | trailing_stop_rsi | orders-10-trailing-stop-rsi | ETF3 |
| O11 | trailing_stop_limit | orders-11-trailing-stop-limit | ETF3 |
| O12 | bracket_trend | orders-12-bracket-trend | ETF3 |
| O13 | bracket_rsi_reversion | orders-13-bracket-rsi-reversion | ETF3 |
| O14 | oco_dip_or_breakout | orders-14-oco-dip-or-breakout | ETF3 |
| O15 | intraday_5m_bracket_reversion | orders-15-intraday-5m-bracket-reversion | ETF3 |
| O16 | intraday_30m_stop_breakout | orders-16-intraday-30m-stop-breakout | ETF3 |
| O18 | signal_change_rotation_orders | orders-18-signal-change-rotation-orders | ETF3 |
| O19 | fx_momentum_lots | orders-19-fx-momentum-lots | FX4 |
| O20 | futures_donchian_contracts | orders-20-futures-donchian-contracts | FUTS4 |

Class names: CamelCase of the name, e.g. `QuantsmdMonthlyDriftEtfBot`,
`QuantsmdIntraday1mEmaScalpBot`, `QuantsmdPairsRatioZscoreBot`, `QuantsmdMacdTrendBot`.
The DB name equals the class name.

## 2. Departures that apply to many bots (state them in each docstring)

- **Long-only, gross <= 1.0.** The framework cannot short. Where the source is long/short, only
  the long leg is kept and the short leg's weight stays in cash. Such a bot is no longer
  market-neutral and says so in its docstring.
- **No resting orders.** CronJobs keep no state and cannot leave limit/stop orders at a broker.
  Order-style strategies are replayed statelessly over the last N bars on every run (N = 250
  daily bars, all rows for intraday), with a mini fill model on OHLC bars
  (`tradingbot/utils/quantsmd_orders.py`). Market orders act at the decision bar's close. A
  stop or limit that fills on bar j changes the position on bar j. Execution price in the
  framework is that bar's close, not the intrabar fill price.
- **Position size** in order strategies is a fixed fraction of NAV while the position is open
  (the source holds a fixed share count and does not rebalance). The framework's no-trade
  band avoids churn.
- **Drift bands / partial rebalance / signal-change triggers** of the source's rebalance engine
  cannot be reproduced statelessly. They are replaced by the framework's own no-trade band, and
  the docstring says so.

## 3. Fill-model contract for order strategies (Batch 0, step 0B)

All fills use the bar's `o`, `h`, `l` and a trigger price. All return the fill price or `None`.

- `fill_limit_buy(o, l, limit)`: fills if `l <= limit`, at `min(o, limit)`.
- `fill_limit_sell(o, h, limit)`: fills if `h >= limit`, at `max(o, limit)`.
- `fill_stop_buy(o, h, stop)`: fills if `h >= stop`, at `max(o, stop)`.
- `fill_stop_sell(o, l, stop)`: fills if `l <= stop`, at `min(o, stop)`.
- `fill_stop_limit_buy(o, h, l, stop, limit) -> tuple[float | None, bool]`: `(None, False)` if
  `h < stop`. Otherwise triggered: `p = max(o, stop)`; if `p <= limit` return `(p, True)`;
  elif `l <= limit` return `(limit, True)`; else `(None, True)` (triggered, still a live limit).
- `fill_stop_limit_sell(o, h, l, stop, limit) -> tuple[float | None, bool]`: `(None, False)` if
  `l > stop`. Otherwise `p = min(o, stop)`; if `p >= limit` return `(p, True)`; elif
  `h >= limit` return `(limit, True)`; else `(None, True)`.
- `bracket_exit(o, h, l, take_profit, stop_loss) -> tuple[float, str] | None`: stop wins
  ties. If `l <= stop_loss` return `(min(o, stop_loss), "stop")`; elif `h >= take_profit`
  return `(max(o, take_profit), "target")`; else `None`.
- `ohlc_frames(datas, tickers) -> dict[str, pd.DataFrame]`: per ticker a frame with columns
  `open, high, low, close`, tz-naive ascending `DatetimeIndex`, NaN rows dropped, duplicate
  timestamps keep the last. Reads the `timestamp` column when present, else the index (same
  rule as `closes_frame` in `quantsmd_common.py`). Tickers with no usable data are omitted.

### Replay conventions for every order bot (O01-O20)

For one ticker, with `n` bars, loop `i` over the last `REPLAY_BARS` bars (250 for daily; all
rows for intraday). State: `pos` (0.0 flat, or the fixed fraction `f` while held), `entry`
(price at entry), the pending order list. Each bar `i`:

- (a) **Fills**: for each pending order submitted on an earlier bar, in submit order, test it
  against bar `i`'s open/high/low with the `fill_*` functions. A filled buy sets
  `pos = f`, `entry = fill price`; a filled sell sets `pos = 0.0`, clears every other pending
  order of that ticker. A pending order with `expires_after_bars = N` is tested on bars
  `i_submit + 1 .. i_submit + N` and removed after bar `i_submit + N` unfilled, **before** step
  (b) of that same bar (so step (b) may submit a new order on that bar).
- (b) **Decisions** from bar `i`'s close / indicators, in the exact `if / elif` order given in
  the step. Market buy: `pos = f`, `entry = close`. Market sell: `pos = 0.0` and cancel pending.
  A new limit/stop order is active from bar `i + 1`.
- Return `pos` after the last bar's step (b). `compute_weights` returns `{ticker: pos}` for
  tickers with `pos > 0`, scaled down proportionally if the sum exceeds 1.0, and `{}` if
  nothing is held. Indicators that are NaN skip the bar (nothing happens).
- Prices passed to orders are rounded with `round(x, 2)` exactly as in the step text.

## 4. Batches

Within a batch the steps are independent (disjoint files). Batches are sequential because later
batches import Batch 0, and because full checks gate each batch.

### Batch 0 (shared contracts, 2 parallel steps)

#### Step 0A - common helpers
Files: modify `tradingbot/utils/quantsmd_common.py` (append only, keep existing code and
its docstring style), modify `tests/test_quantsmd_common.py` (append tests only).

Append (pure pandas / numpy, `from __future__ import annotations` already present):
1. Constants: `ETF3 = ["SPY", "QQQ", "IWM"]`, `FX4 = ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"]`,
   `FUTS4 = ["MES=F", "MNQ=F", "CL=F", "GC=F"]`.
2. `rsi(close: pd.Series, window: int = 14) -> pd.Series`: use `ta.momentum.RSIIndicator(close, window=window).rsi()`.
   Open `tradingbot/quantsmd_weekly_rsi_reversion.py` and call RSI the same way.
3. `rsi_hysteresis(rsi_values: pd.Series, enter_below: float, exit_above: float) -> bool`:
   state starts False; for each value: NaN sets state False; if not in position and
   `value < enter_below` set True; elif in position and `value > exit_above` set False.
   Return the final state.
4. `atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series`:
   true range = max(high-low, |high-prev_close|, |low-prev_close|), then
   `.rolling(window, min_periods=window).mean()`.
5. `top_n(scores: pd.Series, n: int, positive_only: bool = False) -> list[str]`: drop NaN, keep
   `> 0` when `positive_only`, return the `n` largest index labels as `str`, largest first.
6. `scale_to_max_gross(weights: dict[str, float], max_gross: float = 1.0) -> dict[str, float]`:
   drop entries `<= 0`; if the sum exceeds `max_gross` scale all by `max_gross / sum`.
7. `erc_weights(cov: np.ndarray, iters: int = 500) -> np.ndarray`: copy `_erc` from
   `tradingbot/quantsmd_risk_parity_capped.py` verbatim (including its comment).
   Add `import numpy as np` at the top.
8. `last_rows(df: pd.DataFrame, n: int) -> pd.DataFrame`: returns `df.iloc[-n:]`.

Tests (append): `rsi_hysteresis` enter/exit/NaN, `top_n` positive filter and order,
`scale_to_max_gross` scaling and dropping non-positive, `erc_weights` equal-vol uncorrelated
matrix returns equal weights and sums to 1, `atr` on a constant-range frame equals that range,
`rsi` of a strictly rising series is > 90 at the end, `last_rows` length.
Quick check: same as rule 8 with `tradingbot/utils/quantsmd_common.py tests/test_quantsmd_common.py`.

#### Step 0B - order fill model
Files: create `tradingbot/utils/quantsmd_orders.py`, create `tests/test_quantsmd_orders.py`.
Implement exactly the functions of section 3 (module docstring: "Daily/intraday bar fill model
used by the quants.md order-style bots. Pure functions, no DB"). Tests: for every `fill_*`
function one fill case, one no-fill case and one gap case (open beyond the trigger); stop-limit
buy: triggered and filled, triggered and not filled, not triggered; stop-limit sell likewise;
`bracket_exit` stop-first tie, target only, neither; `ohlc_frames` with a `timestamp` column,
with a tz-aware index (becomes tz-naive), with a missing ticker (omitted), duplicates keep last.
Quick check: rule 8 with `tradingbot/utils/quantsmd_orders.py tests/test_quantsmd_orders.py`.

### Batch 1 (daily weights bots, 4 parallel steps, 2 bots each)

Common pattern: `closes = closes_frame(self.datas, self.tradeable_tickers)`, then
`compute_weights(closes, ...)`. Use `equal_weight(active, cap)` and `period_rolled` /
`is_weekday_bar` from `quantsmd_common.py`. Monthly bots return `None` when
`not period_rolled(closes.index, "M")`.

**Step 1A - W02 + W04.** Files: `tradingbot/quantsmd_monthly_drift_etf.py`, `tests/test_quantsmd_monthly_drift_etf.py`, `tradingbot/quantsmd_quarterly_dual_momentum.py`, `tests/test_quantsmd_quarterly_dual_momentum.py`.
- W02: universe ETF8. Active = `SMA50 > SMA200` (rolling means, `min_periods` equal to window,
  last bar, both defined). Weights `equal_weight(active, 0.30)`. Monthly: `None` unless
  `period_rolled(index, "M")`. Fewer than 200 rows returns `{}` on a rebalance bar. Live
  `period="2y"`. Departure: the source's 5% drift band is replaced by the framework no-trade band.
  `param_grid`: fast [20, 50, 100], slow [150, 200, 250].
- W04: universe ETF8. `momentum = close / close.shift(252) - 1` on the last bar; picks =
  `top_n(momentum, 2, positive_only=True)`; weights `equal_weight(picks, 0.50)`. Quarterly:
  `period_rolled(index, "Q")` else `None`. Fewer than 253 rows returns `{}` on a rebalance bar.
  Live `period="3y"`. `param_grid`: lookback [126, 252], top_n [2, 3]. Extra test: only one
  positive asset gives that asset at 0.50.

**Step 1B - W06 + W22.** Files: `tradingbot/quantsmd_signal_change_defensive.py`, `tests/test_quantsmd_signal_change_defensive.py`, `tradingbot/quantsmd_macd_trend.py`, `tests/test_quantsmd_macd_trend.py`.
- W06: universe ETF8. `RISK_ON = ["SPY","EFA","EEM","DBC","VNQ"]`, `DEFENSIVE = ["TLT","IEF","GLD"]`.
  `sma = SPY.rolling(200).mean()`; if fewer than 200 rows or `sma` is NaN return `{}`. If last
  `SPY > sma` active = `RISK_ON` else `DEFENSIVE` (only tickers present in `closes`). Weights
  `equal_weight(active, 0.50)`, returned on every bar (never `None`). Departure: the source
  trades only on a regime change; here the book is returned every bar and the no-trade band
  absorbs drift. Live `period="2y"`. Extra tests: risk-on gives 0.2 each for five names;
  risk-off gives 1/3 each for three.
- W22: universe SECTORS9. For each column: `ema12 = close.ewm(span=12, adjust=False).mean()`,
  `ema26` likewise, `macd = ema12 - ema26`, `signal = macd.ewm(span=9, adjust=False).mean()`.
  Active = last-bar `macd > signal`. Need at least 35 rows else `{}` (departure: warmup guard,
  the source has none). Weights `equal_weight(active, 0.25)`. Daily, never `None`. Live
  `period="1y"`. `param_grid`: fast [8, 12], slow [21, 26], signal [7, 9].

**Step 1C - W12 + W19.** Files: `tradingbot/quantsmd_daily_partial_drift.py`, `tests/test_quantsmd_daily_partial_drift.py`, `tradingbot/quantsmd_risk_parity_multiasset.py`, `tests/test_quantsmd_risk_parity_multiasset.py`.
- W12: universe SECTORS9. `momentum = close.iloc[-1] / close.iloc[-1-63] - 1` per ticker; keep
  `momentum > 0` only; weight_i = momentum_i / sum(momentum), then `min(weight_i, 0.35)` (clipped
  excess stays in cash, NOT redistributed). Fewer than 64 rows returns `{}`. Daily, never
  `None`. Departure: source's 10% partial drift rebalance replaced by the no-trade band.
  Live `period="1y"`. `param_grid`: lookback [21, 63, 126]. Extra test: three positive names
  with momentum 0.1, 0.1, 0.8 gives weights 0.1, 0.1, 0.35 (the large one clipped, cash left).
- W19: universe ETF8. Monthly (`period_rolled(index, "M")` else `None`). Need at least 64 rows
  else `{}`. `returns = closes.pct_change().iloc[-63:].dropna(axis=1, how="any")`; if no columns
  or covariance not finite or `trace <= 0` return `{}`. `w = erc_weights(returns.cov().to_numpy())`.
  Output weights below 1e-9 omitted. No cap. Live `period="2y"`. `param_grid`: lookback [63, 126].
  Extra tests: two assets with 3x volatility ratio give weights near 0.75 / 0.25 (tolerance
  0.05), sum is 1.0 (tolerance 1e-6).

**Step 1D - W15 + W16.** Files: `tradingbot/quantsmd_cross_sectional_momentum.py`, `tests/test_quantsmd_cross_sectional_momentum.py`, `tradingbot/quantsmd_cross_sectional_reversal.py`, `tests/test_quantsmd_cross_sectional_reversal.py`.
- W15: universe SECTORS9. Monthly gate (`period_rolled "M"` else `None`). Need >= 253 rows
  else `{}`. `momentum = close.iloc[-1] / close.iloc[-253] - 1`; drop NaN; need at least 6
  names else `{}`. Long the 3 largest (`top_n`), each weight `0.5 / 3`. Departure: the
  source is dollar-neutral (also shorts the 3 weakest); here only the long leg is held, so the
  bot is net 50% long and not market-neutral. Live `period="3y"`.
- W16: universe SECTORS9. Friday gate (`is_weekday_bar(index, 4)` else `None`). Need >= 22 rows
  else `{}`. `ret = close.iloc[-1] / close.iloc[-22] - 1`; need at least 6 names. Long the 3 with
  the **smallest** return (losers), each weight `0.5 / 3`. Same long-only departure. Live
  `period="1y"`. Extra test for both: the chosen names and weights on a hand-built frame, and
  fewer than 6 valid names gives `{}`.

### Batch 2 (pairs, FX, circuit breaker, intraday, 4 parallel steps)

**Step 2A - W13 + W14 (pairs).** Files: `tradingbot/quantsmd_pairs_ratio_zscore.py`, `tests/test_quantsmd_pairs_ratio_zscore.py`, `tradingbot/quantsmd_pairs_hedge_ratio.py`, `tests/test_quantsmd_pairs_hedge_ratio.py`.
Both: two tickers `A` (first) and `B` (second). Compute `z` over the whole frame, then replay a
state machine over the last 250 rows: `state = 0`; for each `z_i`: if NaN then `state = 0`;
elif `state == 0`: `z_i > 2.0` sets `state = -1`, `z_i < -2.0` sets `state = 1`; elif
`abs(z_i) < 0.5` sets `state = 0`. Final `state`: `+1` means long A / short B, so hold `{A: 0.5}`;
`-1` means short A / long B, so hold `{B: 0.5}`; `0` returns `{}`. Departure: the short leg is
dropped, so each position is a plain 50% long in the cheap name and not market-neutral. Daily,
never `None`. Live `period="3y"`. Need at least 61 rows else `{}`.
- W13: universe `["KO", "PEP"]`. `spread = log(A) - log(B)`;
  `z = (spread - spread.rolling(60).mean()) / spread.rolling(60).std()`.
- W14: universe `["EWA", "EWC"]`. `la, lb = log(A), log(B)`;
  `beta = la.rolling(60).cov(lb) / lb.rolling(60).var()`; `spread = la - beta * lb`; `z` as above.
Extra tests: a series built so the final `z` is below -2 gives `{A: 0.5}`; above +2 gives
`{B: 0.5}`; a spread that crossed -2 then returned to -1 stays long A (hysteresis), and after
|z| < 0.5 goes flat.

**Step 2B - W23 + W24 (FX).** Files: `tradingbot/quantsmd_fx_time_series_momentum.py`, `tests/test_quantsmd_fx_time_series_momentum.py`, `tradingbot/quantsmd_fx_cross_sectional_momentum.py`, `tests/test_quantsmd_fx_cross_sectional_momentum.py`.
Universe `FX4` (yfinance spot pairs, paper only). Monthly gate (`None` unless `period_rolled "M"`).
- W23: need >= 127 rows else `{}`. `momentum = close.pct_change(126)` (last row), `sign = +1 if
  momentum > 0 else -1 if < 0 else 0`, `vol = close.pct_change().rolling(63).std().iloc[-1] * sqrt(252)`,
  `inv = 1/vol` (NaN or 0 vol gives 0). Source weights: `raw_i = sign_i * inv_i`,
  `w_i = raw_i / sum_j |raw_j|`. **Long-only keeps only the `w_i > 0` entries**, with the
  denominator still summing over all four names (so shorted names' share stays in cash).
  Live `period="2y"`. `param_grid`: momentum lookback [63, 126, 252]. Extra test: with three
  rising pairs and one falling pair the falling pair is omitted and the sum is below 1.
- W24: need >= 64 rows else `{}`. `score = close.pct_change(63)` last row; drop NaN; need 4
  names; long the single best (`top_n(score, 1)`) at weight 0.5. Source also shorts the
  worst; dropped. Live `period="1y"`.
Docstring for both says: paper only, spot FX price returns, no carry or swap.

**Step 2C - W10 + W07.** Files: `tradingbot/quantsmd_monthly_circuit_breaker.py`, `tests/test_quantsmd_monthly_circuit_breaker.py`, `tradingbot/quantsmd_intraday_rsi_15m.py`, `tests/test_quantsmd_intraday_rsi_15m.py`.
- W10: universe ETF8. Base book = the W02 book (SMA50 > SMA200, `equal_weight(active, 0.30)`)
  recomputed at each month-first bar. Circuit breaker via replay over the **last 504 rows**:
  `nav = 1.0`, `peak = 1.0`, `book = {}`, `cooldown = 0`, `tripped = False`. For each row `i`
  from the second: (1) `nav *= 1 + sum(w_j * ret_ij)` using the book held **before** this row;
  (2) `peak = max(peak, nav)`; (3) if `cooldown > 0`: `cooldown -= 1`; (4) if
  `nav / peak - 1 < -0.15` and `book` non-empty: `book = {}`, `cooldown = 10`, `tripped = True`;
  (5) elif the row is a month-first bar (`period_rolled(index[:i+1], "M")`) and `cooldown == 0`:
  `book = base book at row i`; if `tripped`, set `peak = nav` and `tripped = False`.
  Return the final `book` on **every** bar (never `None`). Need >= 200 rows else `{}`.
  Departure: nav is a model of the bot's own sleeve (fixed weights between rebalances), not
  the account value. Live `period="3y"`. Extra tests: a frame with a 20% crash after a month
  start gives `{}` for the next 10 rows; a frame with no crash equals the plain monthly book.
- W07: universe ETF3, `interval="15m"`, live `period="5d"`,
  `BACKTEST_PERIOD = "60d"`. For each ticker `rsi(close, 14)` and
  `rsi_hysteresis(rsi, 35, 55)`; active tickers get `equal_weight(active, 0.25)`. Every bar,
  never `None`. Fewer than 20 rows returns `{}`. Departure: the source's signal-change trigger is
  replaced by the framework no-trade band.

**Step 2D - W08 + W09.** Files: `tradingbot/quantsmd_intraday_1m_ema_scalp.py`, `tests/test_quantsmd_intraday_1m_ema_scalp.py`, `tradingbot/quantsmd_intraday_1h_sma_trend.py`, `tests/test_quantsmd_intraday_1h_sma_trend.py`.
- W08: universe ETF3, `interval="1m"`, live `period="5d"`, `BACKTEST_PERIOD = "7d"`.
  Active = `EMA9 > EMA21` (`ewm(span=n, adjust=False)`, last bar); needs >= 30 rows else `{}`.
  `equal_weight(active, 0.34)`. Docstring warns: yfinance serves ~7 days of 1m bars, so a
  backtest says almost nothing; 1m trading costs are ignored by a daily-cost model.
- W09: universe ETF3, `interval="1h"`, live `period="3mo"`, `BACKTEST_PERIOD = "1y"`. Active =
  `SMA10 > SMA30` (rolling means, last bar); needs >= 30 rows else `{}`. `equal_weight(active, 0.25)`.
Tests as the generic list; intraday frames built with `pd.date_range(freq="1min"/"1h")`.

### Batch 3 (order bots, group 1; needs Batch 0B)

All order bots use `ohlc_frames(self.datas, self.tradeable_tickers)` and a pure
`compute_weights(ohlc: dict[str, pd.DataFrame], ...) -> dict[str, float]` that runs a per-ticker
`_replay(df, ...) -> float` following section 3's replay conventions. Daily bots: `interval="1d"`,
`BACKTEST_PERIOD = "max"`, live `period="2y"`, `REPLAY_BARS = 250`. Indicators (SMA, RSI via
`rsi()`) are computed over the **whole** frame before replaying its tail. Never return `None`
(except O18). In every docstring list the two order-style departures from section 2.
Extra tests for every order bot: (E1) a hand-built OHLC series where the order fills and the
bot is held at the last bar; (E2) one where the order never fills and the result is `{}`;
(E3) for bots with an exit rule, one where the exit fires and the result is `{}`.

**Step 3A - O01 + O18.** Files: `tradingbot/quantsmd_market_sma_cross.py`, `tests/test_quantsmd_market_sma_cross.py`, `tradingbot/quantsmd_signal_change_rotation_orders.py`, `tests/test_quantsmd_signal_change_rotation_orders.py`.
Both: ETF3, SMA20 and SMA50, `signal = 1 if fast > slow else 0`, size `f = 0.18`, no OHLC needed
(use `closes_frame`). Both are the same signal in the source (market orders on flips); document
that, and that they differ here only in the `None` gate.
- O01: every bar returns `{t: 0.18}` for each ticker with `fast > slow` (both defined), else
  omitted. Needs >= 50 rows else `{}`.
- O18: same book, but returns `None` unless a signal **flip** happened on the last bar for at least
  one ticker (`signal[-1] != signal[-2]`); on a flip bar it returns the full book. Needs >= 51
  rows (else `{}`). Docstring flags the limit: a missed CronJob run on a flip bar leaves the book
  stale until the next flip. Extra test: no flip gives `None`; flip gives the book.

**Step 3B - O02 + O19.** Files: `tradingbot/quantsmd_market_rsi_reversion.py`, `tests/test_quantsmd_market_rsi_reversion.py`, `tradingbot/quantsmd_fx_momentum_lots.py`, `tests/test_quantsmd_fx_momentum_lots.py`.
- O02: ETF3. `f = 0.12`; `rsi_hysteresis(rsi14, 35, 60)` per ticker over the last 250 bars; held
  tickers get 0.12 each. `closes_frame` is enough.
- O19: FX4, paper only. `LOOKBACK = 126`, `ATR_WINDOW = 20`. Per ticker on the last bar:
  `direction = +1 if close > close[-1-126] else -1`; `atr20 = atr(high, low, close, 20)`.
  Long-only weight = `min(0.005 * close / atr20, 0.20)` when `direction == +1`, else omitted
  (the source sizes whole lots by risk 0.5% of NAV and a 20% notional cap; this is that sizing
  in weight terms, without lot rounding, and the short side is dropped). Needs >= 127 rows,
  NaN ATR skips the ticker, `scale_to_max_gross` at the end. Extra test: tiny ATR hits the 0.20
  cap; large ATR gives `0.005 * close / atr20`.

**Step 3C - O20 + O15.** Files: `tradingbot/quantsmd_futures_donchian_contracts.py`, `tests/test_quantsmd_futures_donchian_contracts.py`, `tradingbot/quantsmd_intraday_5m_bracket_reversion.py`, `tests/test_quantsmd_intraday_5m_bracket_reversion.py`.
- O20: FUTS4 = MES=F, MNQ=F, CL=F, GC=F (yfinance continuous futures, paper only, no roll or
  multiplier accounting). Constants `SPECS = {"MES=F": (5.0, 1500.0), "MNQ=F": (2.0, 2000.0),
  "CL=F": (1000.0, 6000.0), "GC=F": (100.0, 9000.0)}` as `(multiplier, margin)`. State replay
  over the last 250 bars with `window = 55`: `direction = 0` initially; each bar `i >= 55`:
  `ph = max(high[i-55:i])`, `pl = min(low[i-55:i])`; `close > ph` sets `direction = +1`;
  `close < pl` sets `direction = -1`; otherwise unchanged. Long-only: hold only when the final
  `direction == +1`, weight `min(0.005 * close / atr20, 0.25, 0.15 * close * mult / margin)`;
  `scale_to_max_gross` at the end. Departure: no whole-contract rounding, shorts dropped.
  Live `period="2y"`. Needs >= 56 rows. Extra tests: a breakout up then a drift inside the band
  stays long; a later break below the 55-bar low goes flat.
- O15: ETF3, `interval="5m"`, live `period="5d"`, `BACKTEST_PERIOD = "60d"`, `f = 0.20`,
  replay over all rows. Rule: flat and `rsi14 < 30` and no bracket live -> market buy at close
  with `take_profit = round(close * 1.006, 2)` and `stop_loss = round(close * 0.996, 2)`; from
  the next bar `bracket_exit(o, h, l, tp, sl)` each bar closes the position. Departure: no
  end-of-day flatten (neither has the source).

**Step 3D - O12 + O13.** Files: `tradingbot/quantsmd_bracket_trend.py`, `tests/test_quantsmd_bracket_trend.py`, `tradingbot/quantsmd_bracket_rsi_reversion.py`, `tests/test_quantsmd_bracket_rsi_reversion.py`.
- O12: ETF3, SMA20/50 flips, `f = 0.15`. Bar `i` decisions: flip `0 -> 1` and flat -> market buy
  at close with `tp = round(close * 1.06, 2)`, `sl = round(close * 0.97, 2)`; elif `1 -> 0`
  flip and `pos > 0` -> market sell and drop the bracket. While held, from the bar after entry,
  each bar `bracket_exit(...)`; a hit sets `pos = 0`. `prev_signal` starts at 0 and is
  updated every bar once SMA50 is defined.
- O13: ETF3, RSI14, `f = 0.15`. Flat and `rsi < 35` -> market buy with `tp = round(close * 1.04, 2)`,
  `sl = round(close * 0.98, 2)`; elif `pos > 0` and `rsi > 70` -> market sell. Bracket exit
  as above from the bar after entry.

### Batch 4 (order bots, group 2)

**Step 4A - O03 + O04 (limit orders).** Files: `tradingbot/quantsmd_limit_rsi_dip.py`, `tests/test_quantsmd_limit_rsi_dip.py`, `tradingbot/quantsmd_limit_trend_pullback.py`, `tests/test_quantsmd_limit_trend_pullback.py`.
- O03: ETF3, RSI14. `f = 0.15`. Decisions at bar `i` (`pending` = any live order of this ticker):
  (1) `pos == 0` and not pending and `rsi < 40` -> submit BUY LIMIT at `round(close * 0.985, 2)`,
  expires 3 bars; (2) elif `pos > 0` and not pending -> submit SELL LIMIT at
  `round(entry * 1.03, 2)`, expires 3 bars (`entry` = the buy fill price). Fills use
  `fill_limit_buy(o, l, limit)` / `fill_limit_sell(o, h, limit)`.
- O04: ETF3, SMA50, `f = 0.12`. Decisions: (1) `pos == 0` and not pending and `close > sma` ->
  BUY LIMIT at `round(close * 0.99, 2)`, expires 2 bars; (2) elif `pos > 0` and `close < sma`
  -> cancel pending, market sell; (3) elif `pos > 0` and not pending -> SELL LIMIT at
  `round(entry * 1.04, 2)`, expires 5 bars.

**Step 4B - O05 + O07 (stop-breakout entries).** Files: `tradingbot/quantsmd_stop_breakout_entry.py`, `tests/test_quantsmd_stop_breakout_entry.py`, `tradingbot/quantsmd_stop_limit_breakout.py`, `tests/test_quantsmd_stop_limit_breakout.py`.
Both: ETF3, no indicators. Skip bars with fewer than 21 prior rows. Per ticker keep `entry_bar`
(index of the first bar on which `pos > 0` is seen in step (b)). Decisions at bar `i`:
(1) if `pos > 0` and `i - entry_bar >= HOLD` -> cancel pending, market sell, clear
`entry_bar`, `continue`; (2) elif `pos == 0` and no pending: `prior_high = max(close[i-20:i])`
(20 closes before bar `i`, excluding `i`), submit the entry order with `f = 0.10`.
- O05: `HOLD = 10`. BUY STOP at `round(prior_high * 1.002, 2)`, expires 5 bars,
  filled with `fill_stop_buy`.
- O07: `HOLD = 12`. `stop = round(prior_high * 1.002, 2)`, `limit = round(stop * 1.004, 2)`,
  BUY STOP_LIMIT expires 5 bars, filled with `fill_stop_limit_buy`; a triggered but unfilled
  order continues as `fill_limit_buy(o, l, limit)` on later bars until it expires.

**Step 4C - O06 + O08 (protective stops).** Files: `tradingbot/quantsmd_stop_loss_protection.py`, `tests/test_quantsmd_stop_loss_protection.py`, `tradingbot/quantsmd_stop_limit_protection.py`, `tests/test_quantsmd_stop_limit_protection.py`.
Both: ETF3, SMA20/50 flips, `f = 0.15`, `has_stop` flag reset to False whenever `pos == 0`.
Decisions at bar `i`, `if/elif` order: (1) flip `0 -> 1` and `pos == 0` -> market buy at close
(entry = close); (2) elif `pos > 0` and not `has_stop` -> submit the protective SELL order from
`entry`, set `has_stop`; (3) elif flip `1 -> 0` and `pos > 0` -> cancel pending, market sell, clear
`has_stop`. `prev_signal` updated every bar once SMA50 is defined (starts 0). The protective
order has no expiry.
- O06: SELL STOP at `round(entry * 0.95, 2)` via `fill_stop_sell(o, l, stop)`.
- O08: SELL STOP_LIMIT `stop = round(entry * 0.96, 2)`, `limit = round(entry * 0.955, 2)` via
  `fill_stop_limit_sell`; a triggered but unfilled order continues as `fill_limit_sell(o, h, limit)`.

**Step 4D - O09 + O10 (trailing stops).** Files: `tradingbot/quantsmd_trailing_stop_trend.py`, `tests/test_quantsmd_trailing_stop_trend.py`, `tradingbot/quantsmd_trailing_stop_rsi.py`, `tests/test_quantsmd_trailing_stop_rsi.py`.
Trailing stop model: on submission at bar `i`, `high_water = close_i`. On each later bar `j`:
first test `fill_stop_sell(o, l, high_water * (1 - pct))` using the high water **from before**
bar `j`; if unfilled set `high_water = max(high_water, h)`.
- O09: ETF3, SMA20/50 flips, `f = 0.15`. Same `if/elif` structure as step 4C (buy on flip,
  then submit the protective order if none, then exit on the opposite flip) but the protective
  order is a trailing stop with `pct = 0.04`.
- O10: ETF3, RSI14, `f = 0.15`. (1) `pos == 0` and `rsi < 38` -> market buy; (2) elif `pos > 0`
  and no trailing stop -> submit trailing stop `pct = 0.05`; (3) elif `pos > 0` and `rsi > 65`
  -> cancel stop, market sell.

### Batch 5 (last order bots, backtest script)

**Step 5A - O11 + O14.** Files: `tradingbot/quantsmd_trailing_stop_limit.py`, `tests/test_quantsmd_trailing_stop_limit.py`, `tradingbot/quantsmd_oco_dip_or_breakout.py`, `tests/test_quantsmd_oco_dip_or_breakout.py`.
- O11: as O09 (SMA20/50, `f = 0.15`) but the protective order is a trailing stop-limit: at
  submission `trail_amount = round(close * 0.04, 2)` and `limit_offset = round(close * 0.005, 2)`
  (fixed dollar amounts). Each later bar: `level = high_water - trail_amount` (high water from
  before the bar), `fill_stop_limit_sell(o, h, l, level, level - limit_offset)`; once triggered the
  order continues as `fill_limit_sell` at that limit; if not triggered `high_water = max(high_water, h)`.
- O14: ETF3, `f = 0.10`. State per ticker: `oco_live`, `entry_bar`. Decisions at bar `i`:
  (1) `pos > 0`: if `entry_bar` unset set `entry_bar = i`; if `i - entry_bar >= 10` market sell
  and `continue`. (2) `pos == 0`: clear `entry_bar`; if no OCO is live and the bar's date is a
  **Monday** (`index[i].weekday() == 0`; departure: the source uses every 5th bar, which depends on
  the window start), submit an OCO pair: BUY LIMIT at `round(close * 0.98, 2)` and BUY STOP at
  `round(close * 1.02, 2)`, both expire 5 bars; `oco_live = True`. Fills each later bar: test the
  limit first (`fill_limit_buy`), then the stop (`fill_stop_buy`); the first fill cancels the
  other, sets `pos`, clears `oco_live`. An unfilled OCO clears `oco_live` when it expires.

**Step 5B - O16.** Files: `tradingbot/quantsmd_intraday_30m_stop_breakout.py`, `tests/test_quantsmd_intraday_30m_stop_breakout.py`.
ETF3, `interval="30m"`, live `period="5d"`, `BACKTEST_PERIOD = "60d"`, replay over all rows,
`f = 0.20`, `LOOKBACK = 12`, `HOLD_BARS = 8`. Decisions at bar `i >= 12`: (1) `pos > 0`: if
`i - entry_bar >= 8` market sell, clear; `continue`; (2) `pos == 0` and no pending:
`prior_high = max(high[i-12:i])`, BUY STOP at `round(prior_high * 1.001, 2)` expires 3 bars,
`entry_bar = i` (set at **submission**, as in the source). Departure: one pending order at a time
(the source stacks a new stop every flat bar).

**Step 5C - backtest script.** Files: modify `scripts/onetime_quantsmd_backtest.py`.
1. Fix the module docstring so it no longer says "ten"; keep the rest.
2. Extend `BOTS` with all 35 new `(module, class, source_sharpe)` rows after the existing 10, using
   the module names `quantsmd_<name>` and class names from section 1, and the source Sharpe from
   this list: W02 0.31, W04 0.12, W06 0.26, W07 None, W08 None, W09 None, W10 -0.45, W12 0.30,
   W13 -0.05, W14 -0.30, W15 -0.24, W16 -0.24, W19 0.16, W22 0.22, W23 -0.59, W24 -1.00,
   O01 0.21, O02 -0.17, O03 0.14, O04 -0.04, O05 -0.19, O06 0.00, O07 -0.05, O08 0.02,
   O09 -0.33, O10 -0.41, O11 -0.38, O12 -0.38, O13 -0.43, O14 -0.08, O15 None, O16 None,
   O18 0.22, O19 -0.71, O20 0.30.
3. `FETCH_START = "2000-01-01"` must not be used for intraday intervals. In `_yf`, if
   `interval != "1d"` fetch with `yf.download(symbol, period=period_for(interval), interval=interval, auto_adjust=True, progress=False)`
   where `period_for = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d"}`; the
   `timestamp` column keeps the time (still tz-naive). The cache key already includes the interval.
4. In `run_one`, use `bot.interval` for `getYFDataWithTA(symbol=t, interval=bot.interval, period=...)`
   (period `"max"` for daily, the value of `period_for` for intraday) and `backtest_bot(..., )`
   unchanged. The QQQ benchmark frame for an intraday bot is the same interval: fetch it with
   `_yf(None, "QQQ", interval=bot.interval)`; keep the daily one for daily bots. The annualisation
   argument `"1d"` in `_stats` / `_compute_alpha_metrics` must become the bot's interval.
5. Keep the CLI (module names as args select a subset). Add `"intraday": bool` to the output rows.
6. Quick check: `uv run ruff format scripts/onetime_quantsmd_backtest.py && uv run ruff check scripts/onetime_quantsmd_backtest.py && uv run python -c "import ast; ast.parse(open('scripts/onetime_quantsmd_backtest.py').read())"`.
Do NOT run the backtest (the lead does).

## 5. After the batches (done by the lead, not Haiku)

1. Full checks on `haiku/work` after every batch: `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy tradingbot` (42 pre-existing errors, none in quantsmd files), full pytest.
2. Run the backtest in the background for the 35 new bots (about 2-3 h, in two processes by
   subset), write `docs/backtests/quantsmd-2026-11-all.md` (alpha vs QQQ, t, beta, corr, max DD,
   H1/H2) and a ranked per-bot verdict by the CLAUDE.md bar (t >= 2 in both halves = edge,
   otherwise "unproven"; t <= -2 = pause).
3. Helm: add all 35 as paper CronJobs in `helm/tradingbots/values.yaml` in the same style as the
   round-1 block: daily bots `* * 1-5` at 21:xx UTC (stagger minutes 20-59), intraday bots
   `*/5 13-20 * * 1-5` (15m / 30m bots `*/15` and `*/30`; 1h bot `5 13-20`). Bots with
   backtest alpha t <= -2 get `suspend: true` with the numbers in a comment (project rule).
   `helm template` must render the new CronJobs.
4. Final review `git diff af5f4af --stat` against this plan, then report and ask before merging.

## 6. Open decisions (defaults used if you do not object)

- **D1 - the 5 walk-forward / optimizer rows** (wf_01 rolling, wf_02 expanding, wf_03 anchored
  with purge, wf_04 grid search, wf_05 Optuna TPE). They are validation demos of the SMA(50/200)
  sector trend and the weekly RSI reversion, which are already bots here (`quantsmd_sma_daily`,
  `quantsmd_weekly_rsi_reversion`). Default: **not built as bots**; instead one script
  `scripts/onetime_quantsmd_walkforward.py` that runs rolling / expanding / anchored
  walk-forward and a small grid search on those two existing bots with our own backtester, and a
  section in the backtest doc. Say if you want five more (duplicate) bots instead.
- **D2 - 1-minute EMA scalp live cadence.** A CronJob every minute is heavy; default is
  `*/5` (the bot evaluates the last 1m bar at each run). The 15m / 30m / 1h bots run on their
  bar size.
- **D3 - long-only versions of market-neutral rows** (W13-W16, W23, W24, O19, O20): kept as
  long-leg-only bots with the docstring stating they are no longer market-neutral.
- **D4 - O01 and O18 are the same signal in the source**; both are built (different gate).
