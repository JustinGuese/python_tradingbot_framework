# PLAN: four TradingView trend bots + reusable TA helpers

Goal: implement Supertrend (MTF), Ultimate MA, AlphaTrend and Slingshot + Chandelier Exit as pure,
reusable TA functions, then one bot per indicator group built on them.

## Decisions already made (say so if you disagree)

- **Helpers are pure pandas.** No DB and no network, same style as `utils/institutional_ta.py` and
  `utils/quantsmd_common.py`. Every output is trailing-only (causal): the value at bar t is identical
  whether or not later bars exist. That is the TradingView `lookahead_off` rule and it keeps live == backtest.
- **HTF = resample, then use only completed HTF bars.** The HTF value is shifted by one HTF bar before it is
  forward-filled onto the base index. This is the one place a repaint/look-ahead bug can hide, so it is tested.
- **Base bar is 1h, not 1m.** yfinance serves ~7 days of 1m bars, so a 1m bot cannot be judged on alpha vs QQQ
  (see memory note on 1m strategies), and the repo's own 5m ORB/VWAP bots lost after costs. The helpers are
  timeframe-agnostic and each bot takes `interval` and `htf` as parameters, so a 1m run is a one-line change.
- **Long-only, weight-based** (`targetWeights`, the `quantsmd_*` pattern). A bearish signal means flat, because the
  framework's `targetWeights` is long-only. Universe `QQQ SPY IWM GLD TLT`; each active ticker gets 1/5, idle
  weight is cash. That caps beta by construction instead of making every bot a QQQ clone.
- **Ships LIVE (paper), not suspended.** Each bot gets an un-suspended helm cron entry. Paper only: no broker
  copier or C2 blend change is part of this plan.
- **Judged by alpha vs QQQ and its t-stat**, per CLAUDE.md. Batch 3 runs `backtest_bot` for each and writes the
  numbers up. Expect "unproven"; the doc will say so if that is what the numbers show.
- Not building a combined "all five components" stack bot. Say so if you want it as an extra step.

## Shared contract (Batch 0, used by everything after)

`tradingbot/utils/tv_core.py` exports:

- `to_ohlcv(frame) -> DataFrame` with tz-naive ascending `DatetimeIndex` and float columns
  `open high low close volume`. Accepts a frame with a `timestamp` column or a datetime index, drops
  NaN-close rows and duplicate timestamps (keep last), fills missing `volume` with 0.
- `rma(s, n)` (Wilder, `ewm(alpha=1/n, adjust=False)`), `sma`, `ema`, `wma`, `hma`, `tema`, `t3(s, n, vfactor=0.7)`.
- `true_range(df)`, `atr(df, n)` (Wilder RMA of true range, matches Pine `ta.atr`).
- `rsi(close, n)` (Wilder) and `mfi(df, n)` (typical price x volume; returns NaN where volume is all 0).
- `crossover(a, b)` / `crossunder(a, b)` -> bool Series, strict, False on the first bar.
- `resample_ohlcv(df, rule)` -> OHLCV on `rule` (`"4h"`, `"1D"`), first/max/min/last/sum, empty bins dropped.
- `align_htf(htf: Series | DataFrame, base_index, rule)` -> HTF values on the base index using only HTF bars
  that were **complete** at each base bar (shift one HTF bar, then `reindex(method="ffill")`).
- `active_weights(flags: dict[str, bool], n_universe) -> dict[str, float]` -> `{t: 1/n_universe}` for True flags.

Indicator modules (Batch 1) all take an OHLCV frame from `to_ohlcv` and return a DataFrame on the same index;
warmup rows are NaN / False, never filled with 0.

---

## Batch 0 (1 step)

### S0 — core primitives and HTF contract
- Files: `tradingbot/utils/tv_core.py`, `tests/test_tv_core.py`
- Acceptance:
  - Everything listed under "Shared contract" exists with the stated semantics.
  - `atr`, `rsi` follow Wilder smoothing; `hma`/`tema`/`t3` defined from `wma`/`ema` exactly as in Pine.
  - No import of `Bot`, DB or network modules.
- Tests (few, important):
  1. `align_htf` has no look-ahead: a 1h series resampled to `"4h"` — the HTF value visible at base bar t
     equals the HTF bar that closed at or before t, and changing a later bar never changes earlier output.
  2. `atr` on a hand-computed 5-bar series equals the Wilder value (rma seed behaviour documented in the test).
  3. `to_ohlcv` handles `timestamp` column and tz-aware input, dedupes, and fills missing `volume` with 0.

## Batch 1 (4 parallel steps, disjoint files; all import only `tv_core`)

### S1a — Supertrend
- Files: `tradingbot/utils/tv_supertrend.py`, `tests/test_tv_supertrend.py`
- `supertrend(df, period=10, mult=3.0) -> DataFrame[supertrend, direction (+1/-1), buy, sell]`, Pine v5
  `ta.supertrend` semantics: hl2 +/- mult*ATR, bands only ratchet toward price unless the previous close broke
  them, direction flips on close vs the previous bar's band.
- `htf_supertrend_direction(df, rule, period=10, mult=3.0) -> Series[+1/-1/NaN]` on `df.index`:
  resample, compute, `align_htf`. This is the "HTF bias" helper.
- Tests: (1) direction is +1 in a monotonic uptrend and -1 in a monotonic downtrend; flips exactly one bar after a
  close through the band; (2) `htf_supertrend_direction` is unchanged at earlier bars when later bars are
  altered (no repaint).

### S1b — Ultimate MA
- Files: `tradingbot/utils/tv_ultimate_ma.py`, `tests/test_tv_ultimate_ma.py`
- `ultimate_ma(close, kind="hma", length=20, slope_bars=1, htf_rule=None) -> DataFrame[ma, direction, turn_up, turn_down]`.
  `kind` in `sma ema wma hma tema t3`; unknown kind raises `ValueError`. Direction = sign of
  `ma - ma.shift(slope_bars)` (CM_Ultimate_MA_MTF); `turn_up/turn_down` fire on the bar the sign reverses.
  With `htf_rule`, the MA is built on resampled closes and aligned via `align_htf`.
- Tests: (1) each `kind` returns a finite last value on a ramp and `direction == +1`; bad `kind` raises;
  (2) turn flags fire exactly once at a V-shaped reversal; (3) with `htf_rule` earlier output is unchanged
  by later bars.

### S1c — AlphaTrend
- Files: `tradingbot/utils/tv_alphatrend.py`, `tests/test_tv_alphatrend.py`
- `alphatrend(df, period=14, mult=1.0, use_mfi=None) -> DataFrame[alphatrend, lagged, buy, sell, momentum_used]`
  per Kivanc's Pine: `upT = low - mult*ATR`, `downT = high + mult*ATR`; momentum >= 50 may only raise the line
  (`max(upT, prev)`), else only lower it (`min(downT, prev)`); `lagged = alphatrend.shift(2)`;
  buy = crossover(alphatrend, lagged), sell = crossunder. `use_mfi=None` auto-picks MFI when volume has any
  non-zero value, else RSI; `momentum_used` records which ("mfi"/"rsi").
- Tests: (1) with all-zero volume the function falls back to RSI and does not return NaN-only; (2) the line
  never moves down while momentum >= 50 and never up while < 50 (property check on synthetic noisy data);
  (3) buy fires on the first bar the line crosses above its 2-bar lag and not on consecutive bars.

### S1d — Slingshot and Chandelier Exit
- Files: `tradingbot/utils/tv_slingshot_chandelier.py`, `tests/test_tv_slingshot_chandelier.py`
- `slingshot(df, fast=34, slow=50, max_extension_atr=2.0, atr_period=14) -> DataFrame[ema_fast, ema_slow,
  bull_trend, bear_trend, in_zone, pullback_long, pullback_short, overextended]`:
  `bull_trend = fast > slow`; `in_zone` = low/high touches the fast/slow channel; `pullback_long` = bull trend,
  `in_zone`, and close back at or above the slow EMA; `overextended` = close more than `max_extension_atr` ATR
  above the channel top (long) or below the bottom (short).
- `chandelier_exit(df, period=22, mult=3.0) -> DataFrame[long_stop, short_stop, direction, buy, sell]`
  (Everget): `long_stop = highest(high, n) - mult*ATR(n)`, ratcheted up (never lowered while the previous close
  stayed above it); `short_stop` mirrored; direction flips when close crosses the opposite stop.
- Tests: (1) `long_stop` is non-decreasing across a rising run and holds through a consolidation; (2) a close
  below `long_stop` flips direction to -1 on that bar; (3) a pullback into the channel in an uptrend sets
  `pullback_long`, a spike far above it sets `overextended` and clears `pullback_long`.

## Batch 2 (4 parallel steps, disjoint files)

Common to S2a–S2d: a pure `compute_weights(frames: dict[str, DataFrame], ...) -> dict[str, float]` holding all
logic (testable without a `Bot`); a `Bot` subclass built like `quantsmd_intraday_1h_sma_trend.py`
(`tickers=UNIVERSE`, `interval="1h"`, `period="60d"`, `BACKTEST_PERIOD` set to the longest 1h history the data
layer accepts, `param_grid`), `targetWeights` calling `compute_weights` with `to_ohlcv(self.datas[t])`;
fewer rows than the warmup returns `{}`; `if __name__ == "__main__": run_bot(...)`. Module docstring states the
indicator source, the long-only departure, and "alpha vs QQQ, not return" as the judging rule.

### S2a — SupertrendMTFBot
- Files: `tradingbot/supertrendmtfbot.py`, `tests/test_supertrendmtfbot.py`
- Long when the HTF Supertrend (default `htf="1D"`) is +1 **and** the base-bar Supertrend is +1.
  Params: `period`, `mult`, `htf`.
- Tests: (1) HTF -1 forces flat even when base Supertrend is +1; (2) both +1 -> `{ticker: 1/5}`; (3) too little
  history returns `{}`.

### S2b — UltimateMABot
- Files: `tradingbot/ultimatemabot.py`, `tests/test_ultimatemabot.py`
- Long while `ultimate_ma.direction == +1` on the base bar and on the HTF (default `"4h"`) MA. Params: `kind`,
  `length`, `slope_bars`, `htf`.
- Tests: (1) falling HTF MA forces flat; (2) a bad `kind` fails at construction time, not at the first trade;
  (3) rising on both -> long.

### S2c — AlphaTrendBot
- Files: `tradingbot/alphatrendbot.py`, `tests/test_alphatrendbot.py`
- Enter on an AlphaTrend buy (line crosses above its 2-bar lag) and stay long until the matching sell. State is
  rebuilt every run by replaying the signal series over the loaded window (no persisted position), so a crashed
  or repeated run converges to the same book. Params: `period`, `mult`.
- Tests: (1) replay: buy -> held for later bars -> sell -> flat; (2) two runs on identical data give identical
  weights; (3) a stale entry older than the loaded window is not invented (flat when no buy in the window).

### S2d — SlingshotChandelierBot
- Files: `tradingbot/slingshotchandelierbot.py`, `tests/test_slingshotchandelierbot.py`
- Entry: `bull_trend` and `pullback_long` and not `overextended`. Exit: close below the Chandelier `long_stop`
  (22, 3.0) or `bear_trend`. Same replay-from-window state rule as S2c. Params: `fast`, `slow`, `ch_period`,
  `ch_mult`.
- Tests: (1) entry only on a pullback in a bull trend; (2) exit on a close below the Chandelier stop, not before;
  (3) overextended spike does not enter.

## Batch 3 (1 step)

### S3 — registration, study script, docs
- Files: `helm/tradingbots/values.yaml`, `scripts/onetime_tv_trend_backtest.py`,
  `docs/backtests/tv-trend-2026-11.md`, `tests/test_tv_trend_registration.py`
- helm: four un-suspended entries (`supertrendmtfbot`, `ultimatemabot`, `alphatrendbot`,
  `slingshotchandelierbot`), schedule `"5 13-20 * * 1-5"` (same window as `quantsmd_intraday_1h_sma_trend`),
  each with a comment: paper only, 1h base bars, judged on alpha vs QQQ.
- script: runs `backtest_bot` per bot with `benchmark_close` for QQQ and prints `alpha`, `alpha_t`, `beta`,
  `benchmark_corr`, max drawdown. No tuning. Write-up in the doc is filled from that output by the reviewer
  (needs DB/network, which the agy sandbox may not have): the step writes the doc skeleton with a results-pending
  table and the script; the final review runs it and fills the table.
- Tests: (1) every bot module imports and its class name equals its helm entry's name lower-cased;
  (2) none of the four helm entries has `suspend: true`.

## Final review (me, not agy)
Run the new tests once; run the study script and fill the doc; `git diff pre-agy` against this plan; check no
config or test was weakened; then merge to main.
