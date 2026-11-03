# PLAN: quantsmd v2 variants (make the weak quants.md bots better)

Goal: for the weak bots in `docs/backtests/quantsmd-2026-11*.md`, add **new variant bots** (new files, `*_v2`
or new names) that fix the diagnosed problem. Originals are NOT edited, so old and new can be compared.
Judge by alpha vs QQQ and t-stat, never by raw return (CLAUDE.md). Backtests are run by Claude after merge,
not by workers.

Diagnosis (backtest tables): most bots are under-invested (beta 0.03-0.3), the two churners (MacdTrend 14.6k
trades, DailyPartialDrift 28k) lose to costs, FX bots are spot-only with no carry, intraday bots lose to costs.

## 0. Rules every worker must follow

0. **Worktree setup first**: run `git merge haiku/v2` before anything else. Read this plan at
   `/home/jguese/code/tradingbot/python_tradingbot_framework/PLAN.md`.
1. Touch only the files listed in your step. Never edit configs, `pyproject.toml`, existing tests, existing
   bots, `helm/`, `scripts/` or `docs/`. Never weaken a test to make it pass.
2. Framework contract: `targetWeights(rows) -> dict[str, float] | None`. Long-only, sum <= 1.0, no `"USD"`
   key, an omitted ticker means exit, `{}` means all cash, `None` means "do not rebalance this bar".
   `self.datas[ticker]` is history up to the current bar; `self.tradeable_tickers` is the universe.
3. Every bot file has: header docstring, pure `compute_weights(...)` (no DB, no network), a `Bot` subclass
   (DB name == class name), and `if __name__ == "__main__": run_bot(Class)`.
4. Copy the structure of `tradingbot/quantsmd_macd_trend.py` exactly (imports, `param_grid: ClassVar[dict]`,
   `BACKTEST_PERIOD: ClassVar[str | None] = "max"` for daily bots, `super().__init__(name, tickers=list(UNIVERSE),
   interval="1d", period="2y", <params as kwargs>, **kwargs)`, `targetWeights` building `closes_frame(self.datas,
   self.tradeable_tickers)` or `ohlc_frames(...)`). Header docstring: 1 line "Variant of <original module>",
   the strategy text, then a "Departures from <original>:" list.
5. Test file `tests/test_quantsmd_<name>.py`, style of `tests/test_quantsmd_macd_trend.py` (synthetic
   `pd.DataFrame` with `pd.bdate_range("2020-01-01", periods=n)` index, `_up(n)` exp-drift helper). Also one test
   that the Bot class constructs: look at how the original's test does it and copy that pattern.
6. Quick checks (run only these, from the repo root), replacing `<files>` with the step's files:
   ```
   uv run ruff format <files> && uv run ruff check <files>
   POSTGRES_URI="postgresql://x:x@localhost:5432/x" PYTHONPATH=. uv run pytest <step test files> -q
   ```
   Fix failures, max 2 rounds, then stop and report what is red. Commit when done, report the branch name.
7. Do NOT add dependencies, touch `utils/botclass.py`, or add network/DB calls in `compute_weights`.

## Batch 0 (1 step, must finish first: every later step imports it)

### S0 - shared helpers `tradingbot/utils/quantsmd_v2.py`
Files: create `tradingbot/utils/quantsmd_v2.py`, `tests/test_quantsmd_v2.py`.
Context: sibling `tradingbot/utils/quantsmd_common.py` (pure pandas, `from __future__ import annotations`,
`closes_frame`, `annualized_vol(closes, window) -> pd.Series`, `equal_weight`, `scale_to_max_gross`). Same style.
Implement exactly these functions with full docstrings:
1. `schmitt_state(score: pd.Series, enter_above: float, exit_below: float) -> pd.Series`: bool Series same index.
   State starts False. False->True when `score > enter_above`; True->False when `score < exit_below`. NaN score
   resets the state to False. Requires `exit_below <= enter_above` else `ValueError`.
2. `min_hold_state(raw: pd.Series, min_hold: int) -> pd.Series`: `raw` is a bool Series. Output state follows
   `raw` but may only flip if at least `min_hold` bars passed since its last flip (the first bar sets the state
   to `raw.iloc[0]` and counts as a flip). `min_hold <= 1` returns `raw` unchanged. Empty input returns empty.
3. `vol_target_scale(book: dict[str, float], closes: pd.DataFrame, target_vol: float, window: int = 63,
   max_gross: float = 1.0) -> dict[str, float]`: book return series = sum over tickers of `w * pct_change`
   (tickers missing from `closes` ignored); realized vol = std over last `window` returns * sqrt(252). Factor =
   `target_vol / vol`; multiply all weights by it, then if gross > `max_gross` rescale to `max_gross`.
   Returns `dict(book)` unchanged if fewer than `window` returns, vol <= 0 or NaN, or book is empty.
4. `park_cash(book: dict[str, float], symbol: str = "SHV") -> dict[str, float]`: adds `1 - sum(book)` to
   `book[symbol]` when that residual is > 0.005, else returns a copy unchanged. Never adds a negative weight.
5. `regime_scale(closes: pd.DataFrame, ref: str = "SPY", window: int = 200, off_scale: float = 0.0) -> float`:
   1.0 if `closes[ref].iloc[-1] > closes[ref].rolling(window).mean().iloc[-1]`, else `off_scale`. Returns 1.0
   if `ref` is missing or fewer than `window` rows (fail open, documented).
6. `scale_book(book: dict[str, float], factor: float) -> dict[str, float]`: multiplies every weight, drops
   results <= 0.
Tests (>= 14): schmitt: stays on between thresholds; NaN resets; bad thresholds raise. min_hold: flip
suppressed inside hold window, allowed after, `min_hold=1` identity. vol_target_scale: low-vol book scales up
to `max_gross`, high-vol scales down, short history unchanged, empty book `{}`. park_cash: residual goes to SHV,
full book unchanged, residual 0.004 ignored. regime_scale: above, below with `off_scale=0.5`, missing ref,
short history. scale_book: 0.5 halves, 0 returns `{}`.
Acceptance: checks green; no import from any bot module.

## Batch 1 (4 parallel steps, disjoint files; all import only S0 + existing modules)

### A1 - hysteresis on the two churners
Files: create `tradingbot/quantsmd_macd_trend_v2.py`, `tradingbot/quantsmd_daily_partial_drift_v2.py`,
`tests/test_quantsmd_macd_trend_v2.py`, `tests/test_quantsmd_daily_partial_drift_v2.py`.
Context: read `tradingbot/quantsmd_macd_trend.py` (signature `compute_weights(closes, fast, slow, signal)`,
active = MACD above signal on the last bar, `equal_weight(active, CAP)`) and
`tradingbot/quantsmd_daily_partial_drift.py` (`compute_weights(closes, lookback, cap)`, momentum-proportional).
Instructions:
1. MACD v2: per ticker compute the macd-minus-signal histogram series `hist = macd - signal_line` normalised by
   close (`hist / closes`). Per ticker `raw = schmitt_state(hist_norm, enter_above=BAND, exit_below=-BAND)`, then
   `held = min_hold_state(raw, MIN_HOLD)`. Active tickers are those with `held.iloc[-1]` True. `BAND = 0.002`,
   `MIN_HOLD = 10` (module constants and ctor params, `param_grid` `band` [0.001,0.002,0.004], `min_hold`
   [5,10,21]). Then `equal_weight(active, CAP)` as in the original. Fewer than `MIN_ROWS = 35` rows -> `{}`.
   Class `QuantsmdMacdTrendV2Bot`. Replay is over the whole `closes` passed in (live period `"2y"`).
2. Partial drift v2: compute the original momentum-proportional book but a ticker enters the book only when its
   `lookback` momentum has been positive via `schmitt_state(mom_series, enter_above=0.02, exit_below=-0.02)` +
   `min_hold_state(.., 10)`. `mom_series = closes / closes.shift(lookback) - 1` per ticker. Weights are
   `momentum / sum` over held tickers using the latest momentum clipped to a minimum of 0.001, each capped at
   `cap`. Class `QuantsmdDailyPartialDriftV2Bot`, params `lookback` (63), `cap` (0.35), `enter` (0.02),
   `min_hold` (10).
Tests per bot (>= 5): all-up universe holds all; a one-bar dip inside the band does NOT exit (assert the
original would, if easy); sustained crash exits; fewer than min rows -> `{}`; weights sum <= 1 and each <= cap.
Do NOT edit the original bots.

### A2 - cross-sectional bots: rank buffer and skip-month
Files: create `tradingbot/quantsmd_cross_sectional_reversal_v2.py`, `tradingbot/quantsmd_cross_sectional_momentum_v2.py`,
plus their two tests.
Context: read `tradingbot/quantsmd_cross_sectional_reversal.py` (Friday rebalance via `is_weekday_bar`, holds
the `n_long` weakest sectors at `0.5 / n_long` each, returns `None` off-Friday) and
`tradingbot/quantsmd_cross_sectional_momentum.py` (read it fully; keep its structure and universe).
Instructions:
1. Reversal v2: same book and Friday gate, plus a rank buffer. Replay the previous Friday's holdings: for each
   of the last `REPLAY_WEEKS = 26` Fridays in `closes.index` (oldest first) compute the ranking (weakest first)
   and carry a holdings list: a current holding is kept while its rank is within `n_long + BUFFER`
   (`BUFFER = 2`); open slots are filled by the best-ranked non-holdings up to `n_long`. Return the final
   holdings at `0.5 / n_long` each. Params `lookback`, `n_long`, `buffer` (`param_grid` buffer [0,2,3]).
   Not a Friday -> `None`. Class `QuantsmdCrossSectionalReversalV2Bot`.
2. Momentum v2: copy the original but (a) skip the most recent 21 bars: momentum = `close[-1-21] /
   close[-1-21-lookback] - 1` (`SKIP = 21`, ctor param `skip`, `param_grid` skip [0,21]); (b) vol filter: drop
   names whose `annualized_vol(closes, 63)` is above the cross-sectional median * 1.5. Class
   `QuantsmdCrossSectionalMomentumV2Bot`. Keep the original's rebalance gate and sizing.
Tests (>= 5 each): buffer keeps a holding that slipped to rank n_long+1 where `buffer=0` drops it; non-Friday
`None`; skip changes the pick on a crafted series where the last 21 bars reverse; vol filter removes a noisy
name; short history -> `{}`.

### A3 - FX with carry (currency ETFs)
Files: create `tradingbot/quantsmd_fx_etf_time_series_momentum.py`,
`tests/test_quantsmd_fx_etf_time_series_momentum.py`.
Context: read `tradingbot/quantsmd_fx_time_series_momentum.py` (quoted below in structure): monthly (`period_rolled(
closes.index, "M")`, else `None`), `sign(momentum over lookback) * inv_vol(63 bars)`, normalised by sum of
|raw|, long legs only. The reason for v2: spot FX has no carry, so it lost (t -1.3). Currency ETFs hold deposits
and their prices carry the rate differential.
Instructions: same logic and function body shape, universe `FXETF6 = ["FXE", "FXB", "FXA", "FXY", "FXC", "FXF"]`
(define in this file), class `QuantsmdFxEtfTimeSeriesMomentumBot`, `lookback` default 126, `param_grid`
lookback [63,126,252]. Weights are `sign * inv_vol / sum(|raw|)` over positive-sign names, then scaled so gross
is 1.0 when any name is held (`scale_to_max_gross` does not scale UP, so divide by the sum of positive weights
yourself); `{}` when none positive. Docstring: "Departures" = long-only, ETF proxies for carry, 2006+ history.
Tests (>= 5): non-month-start `None`; short history `{}`; two rising + four falling ETFs hold only the rising
two and weights sum to 1.0; inverse-vol ordering (calmer name larger); all falling `{}`.

### A4 - intraday with cost gates
Files: create `tradingbot/quantsmd_intraday_gated_ema.py`, `tests/test_quantsmd_intraday_gated_ema.py`.
Context: read `tradingbot/quantsmd_intraday_1m_ema_scalp.py` (EMA fast/slow on 1m closes, `equal_weight`, cap
0.34, `BACKTEST_PERIOD = "7d"`, live period `"5d"`). It lost (t -4.1) to 350 trades in 9 days.
Instructions: a 5-minute bot on `ETF3`, `interval="5m"`, live `period="5d"`, `BACKTEST_PERIOD = "60d"`.
`compute_weights(closes, fast=9, slow=21, min_edge=0.0015, cap=0.34, skip_open_min=15, skip_close_min=15)`:
1. Take only the bars of the LAST session date in `closes.index` (UTC date). Session window 13:30-20:00 UTC;
   allowed entry bars are `13:30 + skip_open_min` up to `20:00 - skip_close_min` (exclusive). If the last bar is
   at or after `20:00 - skip_close_min` return `{}` (flat into the close).
2. Replay the session bars per ticker (EMAs computed on the whole `closes` history with `ewm(span, adjust=False)`):
   enter when inside the allowed window and `(ema_fast - ema_slow) / close >= min_edge` and the ticker has not
   traded yet today; exit when `ema_fast < ema_slow`; after an exit it never re-enters that session (one trade
   per ticker per session).
3. Held tickers get `equal_weight(held, cap)`.
Class `QuantsmdIntradayGatedEmaBot`, `param_grid` min_edge [0.001,0.0015,0.003], fast [9,12], slow [21,34].
Tests (>= 6, use `pd.date_range("2026-10-26 13:30", periods=78, freq="5min")`): entry blocked inside the first
15 min; entry blocked when edge below `min_edge`; entry allowed mid-session with a strong rise; exit then no
re-entry; last bar after 19:45 -> `{}`; only the last session is considered (earlier day's position ignored).

## Batch 2 (3 parallel steps; start after Batch 1 is merged and green)

### E1 - vol-targeted order ensemble with SHV idle cash
Files: create `tradingbot/quantsmd_order_ensemble.py`, `tests/test_quantsmd_order_ensemble.py`.
Context: twelve order-style ETF3 bots expose `compute_weights(ohlc)` returning a book of ~15-18% sleeves,
callable with defaults: modules `quantsmd_bracket_trend`, `quantsmd_bracket_rsi_reversion`,
`quantsmd_stop_breakout_entry`, `quantsmd_stop_limit_breakout`, `quantsmd_trailing_stop_limit`,
`quantsmd_trailing_stop_rsi`, `quantsmd_trailing_stop_trend`, `quantsmd_oco_dip_or_breakout`,
`quantsmd_limit_trend_pullback`, `quantsmd_stop_loss_protection`, `quantsmd_stop_limit_protection`,
`quantsmd_limit_rsi_dip`. Each takes `ohlc: dict[str, DataFrame]` from `ohlc_frames(...)`. Problem: each is
10-20% invested at beta ~0.1, so alone each is a cash book.
Instructions: `compute_weights(ohlc, target_vol=0.10, vol_window=63, max_gross=1.0)`:
1. Call each component's `compute_weights(ohlc)` (wrap each in try/except returning `{}` so one bad component
   cannot kill the book; import the modules at top of file).
2. Average the books per ticker: `avg[t] = sum(book.get(t, 0)) / n_components`, then multiply by
   `SLEEVE_BOOST = 4.0` and clip total with `scale_to_max_gross` (a ticker held by every component reaches
   ~0.6-0.7 weight).
3. `closes = DataFrame of ohlc[t]["close"]`; `book = vol_target_scale(book, closes, target_vol, vol_window,
   max_gross)`.
4. Add SHV: `park_cash(book, "SHV")`. Universe is `ETF3 + ["SHV"]`, `benchmark_tickers=[]`; SHV is a real
   tradeable. Pass `ohlc` for SHV along with the others, but components only look at `ETF3` names.
Class `QuantsmdOrderEnsembleBot`, `interval="1d"`, `period="2y"`, `BACKTEST_PERIOD = "max"`, param_grid
target_vol [0.06,0.10,0.14], `sleeve_boost` [2.0,4.0]. Docstring departure: equal weights across components
(a first-half inverse-vol fit is a later step).
Tests (>= 5, monkeypatch each component's `compute_weights` via `monkeypatch.setattr` on the imported module
references in the ensemble module): a failing component is skipped; averaging maths with two fake components;
result has an SHV key when invested < 1; gross <= 1.0; empty books return `{"SHV": 1.0}`.

### E2 - pairs basket
Files: create `tradingbot/quantsmd_pairs_basket_zscore.py`, `tests/test_quantsmd_pairs_basket_zscore.py`.
Context: read `tradingbot/quantsmd_pairs_ratio_zscore.py` (KO/PEP ratio z-score, long the cheap leg only,
t 3.10 but single pair = hindsight). Instructions: generalise to `PAIRS` = fixed list of 8 classic pairs:
KO/PEP, XOM/CVX, JPM/BAC, HD/LOW, MA/V, UPS/FDX (use "UPS","FDX"), MCD/YUM, WMT/COST. For each pair apply the
original's z-score rule and entry/exit thresholds (copy its constants), long the cheap leg only, each pair gets
`1/len(PAIRS)` of NAV (so up to 1.0 total). Reuse the original's `compute_weights` helper for a single pair if it
is importable with a clean signature; otherwise copy its logic into a local `_pair_weights(closes_pair, ...)`.
Class `QuantsmdPairsBasketZscoreBot`, universe = all 16 tickers.
Tests (>= 5): one pair triggered -> only its cheap leg weighted at 1/8; no pair triggered `{}`; two pairs
triggered; short history `{}`; weights sum <= 1.

### E3 - regime-gated candidates
Files: create `tradingbot/quantsmd_sma_daily_gated.py`, `tradingbot/quantsmd_quarterly_te_cost_gate_gated.py`,
plus their two tests.
Context: read `tradingbot/quantsmd_sma_daily.py` (sector SMA book) and
`tradingbot/quantsmd_quarterly_te_cost_gate.py` (ETF8 incl. SPY). Both showed t >= 2 but H2 weak and beta
0.3. Instructions: each v2 calls the original module's `compute_weights` with the original's arguments (read how
the original's `targetWeights` calls it, mirror that), then multiplies the book by
`regime_scale(closes, ref="SPY", window=200, off_scale=0.0)` via `scale_book`. SmaDaily's universe is sectors, so
the v2 declares `tickers=UNIVERSE + ["SPY"]`, `benchmark_tickers=["SPY"]` (see `quantsmd_common` docstring in
AGENTS.md: benchmarks are loaded but never traded). The ETF8 bot already contains SPY, so no benchmark needed.
Both return `None` when the original returns `None` (do not gate a no-rebalance bar). Classes
`QuantsmdSmaDailyGatedBot`, `QuantsmdQuarterlyTeCostGateGatedBot`; ctor param `off_scale` (0.0), `param_grid`
off_scale [0.0, 0.5].
Tests (>= 4 each): SPY above SMA200 -> book equals original (monkeypatch the original compute to a fixed book);
below -> `{}` for `off_scale=0.0`, halved for 0.5; `None` passthrough; fewer than 200 rows fails open.

## Batch 3 (1 step, not parallelisable: edits two shared files)

### I1 - register the new bots
Files: modify `helm/tradingbots/values.yaml` (append only), `scripts/onetime_quantsmd_backtest.py` (extend `BOTS`).
Instructions:
1. Append at the end of the quantsmd block in `values.yaml` (find the last `- name: quantsmd_...` entry, add
   after it, before any unrelated entry) a comment `# quants.md v2 variants (2026-11), paper. See
   docs/backtests/quantsmd-v2-2026-11.md.` then one entry per new bot, form
   `- name: quantsmd_<module>` + `schedule: "7 21 * * 1-5"`, for these modules: macd_trend_v2,
   daily_partial_drift_v2, cross_sectional_reversal_v2, cross_sectional_momentum_v2, fx_etf_time_series_momentum,
   order_ensemble, pairs_basket_zscore, sma_daily_gated, quarterly_te_cost_gate_gated. For
   `quantsmd_intraday_gated_ema` use `schedule: "*/5 13-20 * * 1-5"`. NOT suspended (CLAUDE.md: ship live; these
   are paper bots).
2. Append to `BOTS` in the script, after the `futures_donchian_contracts` row, one tuple per new bot
   `("quantsmd_<module>", "<ClassName>", None)` with the class names stated above.
3. Run `uv run ruff format scripts/onetime_quantsmd_backtest.py && uv run ruff check scripts/...` and
   `helm lint helm/tradingbots` (skip helm if not installed).
Do NOT edit any other entry or file.

## After each batch (Claude, not workers)
Merge branches into `haiku/v2`, run full `uv run ruff check . && uv run ruff format --check . && uv run mypy
tradingbot && pytest tests -q` (with `POSTGRES_URI` stub). After Batch 3: Claude runs the backtest script for
the new bots, writes `docs/backtests/quantsmd-v2-2026-11.md` with alpha / t / beta / H1 / H2 versus the
originals, and reports.

## Open decisions for the owner
- **Pruning duplicates is NOT in this plan.** You said earlier that no quantsmd bot should be disabled; the
  ensemble supersedes the 12 order-style bots in effect, but all keep running.
- Ensemble uses equal component weights, not first-half inverse-vol (needs per-component return series; a
  follow-up if the ensemble shows promise).
- The walk-forward retune of the t >= 2 candidates is done by the existing `local_optimize` after backtests, not
  by a worker.
- Intraday: only the one gated 5m bot is added; the 1m/5m originals keep running as you instructed.
