# PLAN: make the quantsmd bots profitable (gembuild)

"Profitable" here means the project bar from CLAUDE.md, not raw return: **alpha vs QQQ with t >= 2 in the
full window and positive alpha in both halves, beta < 0.5**, and it must survive a robustness check. State of
play (docs/backtests/quantsmd-v2-2026-11.md): one candidate, `QuantsmdSmaDailyGatedBot` (t 3.08, H1 1.98,
H2 1.62, beta 0.16) whose 2013-2026 half alone is t 1.35. Nothing else clears the bar. No plan can promise a
pass; the deliverable is (a) cheap, mechanical improvements with a real return source, (b) a blend of the
positive-alpha bots, (c) a robustness test that tells us whether the one candidate is real. Backtests and the
verdict are run by Claude after merge, not by workers.

## 0. Rules every worker must follow

1. Touch only the files listed in your step. Never edit configs, `pyproject.toml`, existing tests, existing
   bots, `helm/`, `docs/` or `README.md`. Never weaken a test to make it pass.
2. Framework contract: `targetWeights(rows) -> dict[str, float] | None`, long-only, sum <= 1.0, no `"USD"` key,
   omitted ticker = exit, `{}` = all cash, `None` = no rebalance. `self.datas[ticker]` is history up to the
   current bar. Benchmarks declared with `benchmark_tickers=[...]` are loaded but never traded.
3. Bot files: header docstring, pure `compute_weights(...)`, a `Bot` subclass (DB name == class name),
   `if __name__ == "__main__": run_bot(Class)`. Copy the structure of `tradingbot/quantsmd_sma_daily_gated.py`
   (quoted facts: imports `from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame`,
   `from tradingbot.utils.quantsmd_v2 import regime_scale, scale_book, park_cash, vol_target_scale`,
   `BACKTEST_PERIOD: ClassVar[str | None] = "max"`, `period="2y"`, `param_grid: ClassVar[dict]`).
4. Helpers already in `tradingbot/utils/quantsmd_v2.py`: `regime_scale(closes, ref="SPY", window=200,
   off_scale=0.0) -> float` (fails open to 1.0), `scale_book(book, factor)`, `park_cash(book, symbol="SHV")`
   (puts `1 - sum(book)` into `symbol` when > 0.005), `vol_target_scale(book, closes, target_vol, window, max_gross)`.
5. Test files follow `tests/test_quantsmd_sma_daily_gated.py` (synthetic frames, `pd.bdate_range`, a test that
   the Bot class constructs). Quick checks (repo root, replace `<files>`):
   `uv run ruff format <files> && uv run ruff check <files>` and
   `POSTGRES_URI="postgresql://x:x@localhost:5432/x" PYTHONPATH=. uv run --with pytest pytest <tests> -q`.
   Max 2 fix rounds, then stop and report what is red. Commit when done.
6. Do NOT add dependencies, touch `utils/botclass.py`, or add network / DB calls inside `compute_weights`.

## Batch 0: nothing. The shared helpers exist; steps below are independent.

## Batch 1 (3 parallel steps, disjoint files)

### P1 - SmaDailyGated v3: cash earns yield, gate window is a parameter
Files: create `tradingbot/quantsmd_sma_daily_gated_v3.py`, `tests/test_quantsmd_sma_daily_gated_v3.py`.
Why: the gated book is in cash a lot (0% yield in paper). The live copier already parks idle cash in SHV, so the
paper track should too. A real return source, not a fit. The gate window 200 is a single untested choice.
Instructions:
1. Same book as `quantsmd_sma_daily_gated.py` (import `base = quantsmd_sma_daily` and reuse
   `base.compute_weights`, `base.FAST/SLOW/CAP`), class `QuantsmdSmaDailyGatedV3Bot`, DB name the same.
2. `compute_weights(closes, fast, slow, cap, off_scale=0.0, regime_window=200, cash_symbol="SHV")`: drop SPY and
   SHV columns from the sector frame, build the gated book as in the v2 file but with `window=regime_window`,
   then if `cash_symbol` is truthy return `park_cash(book, cash_symbol)`; falsy returns the book as is.
3. Universe `[*SECTORS9, "SPY", "SHV"]`, `benchmark_tickers=["SPY"]` (SHV is a real tradeable, not a benchmark).
   `param_grid = {"regime_window": [100, 150, 200, 250], "off_scale": [0.0, 0.5]}`.
   Live `period="2y"` (must exceed 250 + slow).
4. Docstring "Departures from quantsmd_sma_daily_gated": SHV cash parking (starts 2007, so backtests begin 2007),
   `regime_window` parameter.
Tests (>= 7): SPY above SMA -> sector book plus SHV for the residual, sums to <= 1.0; SPY below with
`off_scale=0.0` -> `{"SHV": 1.0}`; `cash_symbol=None` -> no SHV key; regime_window changes the gate on a crafted
series (above a 100-bar average but below a 250-bar one); SHV column in `closes` never appears as a sector pick;
short history -> `{}` or fail-open as the v2 file does; Bot constructs.
Acceptance: checks green.

### P2 - blend of the positive-alpha bots
Files: create `tradingbot/quantsmd_gated_blend.py`, `tests/test_quantsmd_gated_blend.py`.
Why: CLAUDE.md precedent: the C2 blend of three low-beta bots reached t 2.02 where none did alone. Components
(all `compute_weights` already importable): `quantsmd_sma_daily_gated` (sectors + SPY),
`quantsmd_cross_sectional_reversal_v2` (sectors, Friday gate, returns `None` off-Friday),
`quantsmd_cross_sectional_momentum_v2` (sectors), `quantsmd_quarterly_te_cost_gate_gated` (ETF8 incl. SPY).
Read each file first for its exact `compute_weights` signature and defaults and mirror how its own
`targetWeights` calls it.
Instructions:
1. `compute_weights(closes, weights=None)` with `COMPONENT_WEIGHTS = {"sma_gated": 0.4, "reversal": 0.2,
   "momentum": 0.2, "te_gated": 0.2}` default. Build each component's book from the relevant columns of
   `closes` (one wide frame of sectors + ETF8 + SPY); a component returning `None` (non-rebalance bar) is
   replaced by its last book, which is NOT knowable statelessly, so treat `None` as `{}` for that component
   and document it. Wrap each call in try/except -> `{}`.
2. Blend = weighted sum of books per ticker, then `scale_to_max_gross(blend, 1.0)`. No SHV parking here.
3. Universe = union of sectors, ETF8, SPY. `benchmark_tickers` empty. Class `QuantsmdGatedBlendBot`,
   `BACKTEST_PERIOD = "max"`, `period="2y"`, `param_grid = {}` (weights are fixed, deliberately not tuned).
4. Docstring: equal-ish static weights, no fit, so there is nothing to overfit; the weights are the owner's
   priors (largest on the one candidate that cleared t >= 2).
Tests (>= 6, monkeypatch the component `compute_weights` references in the module): weights are applied;
a `None` component contributes nothing; a raising component is skipped; gross <= 1.0; same ticker in two
components adds; Bot constructs.
Acceptance: checks green.

### P3 - robustness script for the one candidate
Files: create `scripts/onetime_quantsmd_robustness.py` only (a one-off script: no unit tests; acceptance is that it
imports, passes ruff and `--help` exits 0).
Why: decide whether SmaDailyGated's t 3.08 is real or an artefact of one gate parameter and one 25-year path.
Copy the offline setup (scratch SQLite, yfinance in-memory patch of `DataService.get_yf_data`, `_cache`,
`backtest_bot` with `benchmark_close`) from `scripts/onetime_quantsmd_backtest.py` (read lines 1-75 and
`run_one`) and from `scripts/onetime_retune_walkforward.py` (`load`, `cut`, `score`). Reuse, do not import
them (both execute work at import time).
Instructions: for `QuantsmdSmaDailyGatedBot` run and print as JSON, each with alpha / alpha_t / beta / maxdd /
trades:
1. Gate window sweep via a subclass or `regime_window` if the class has it; the existing v2 class does NOT, so
   monkeypatch `tradingbot.quantsmd_sma_daily_gated.REGIME_WINDOW` to each of 100, 150, 200, 250, 300 before
   constructing.
2. Costs: slippage 5 bps (default) vs 10 bps vs 20 bps (pass via the same mechanism the backtest script's
   `backtest_bot` accepts, `slippage_pct=`; check the signature in `tradingbot/utils/backtest.py`).
3. Leave-one-sector-out: for each of the 9 sectors, drop it from the universe (monkeypatch `UNIVERSE`).
4. Sub-periods: 2000-2006, 2007-2012, 2013-2019, 2020-2026, each cut from the same frames as in `cut`.
Output one JSON object with keys `gate_window`, `costs`, `leave_one_out`, `subperiods`. CLI: `module` is fixed;
accept `--section <name>` to run one section so the four can run in parallel; `--help` must work.
Acceptance: ruff clean; `uv run python scripts/onetime_quantsmd_robustness.py --help` exits 0.

## Batch 2 (1 step, not parallelisable: edits two shared files)

### I1 - register P1 and P2
Files: modify `helm/tradingbots/values.yaml` (append) and `scripts/onetime_quantsmd_backtest.py` (extend `BOTS`).
1. Append after the last `quantsmd_` entry: `- name: quantsmd_sma_daily_gated_v3` and
   `- name: quantsmd_gated_blend`, each `schedule: "7 21 * * 1-5"`, with a one-line comment. Not suspended
   (CLAUDE.md: ship live; these are paper bots).
2. Append to `BOTS`: `("quantsmd_sma_daily_gated_v3", "QuantsmdSmaDailyGatedV3Bot", None)` and
   `("quantsmd_gated_blend", "QuantsmdGatedBlendBot", None)`.
3. `uv run ruff format` + `ruff check` on the script; `helm lint helm/tradingbots` if installed.

## After each batch (Claude, not workers)
Merge branches into `agy/v3`; run full `uv run ruff check . && uv run ruff format --check . && uv run mypy
tradingbot && pytest tests -q` (POSTGRES_URI stub). After Batch 2 Claude runs the backtests for the two new bots
(`scripts/onetime_quantsmd_backtest.py`) and the four robustness sections, writes
`docs/backtests/quantsmd-v3-2026-11.md`, and reports against the bar above.

## Open decisions for the owner
- **What counts as done.** The pass condition is the CLAUDE.md bar (t >= 2, positive in both halves, beta < 0.5)
  plus the robustness check. If SmaDailyGated fails robustness, the honest answer is "no profitable quantsmd bot
  yet" and the next step is more live days, not more tuning.
- **Not in this plan:** the 40+ other quantsmd bots, tuning more parameters (the grids already ran
  walk-forward), shorting / leverage (the framework has neither), and pruning duplicates (you asked that no bot
  be disabled).
- The SHV bot starts in 2007, so its backtest window differs from the v2 gated bot (2000). Compare them on the
  same 2007+ window.
- Pushing `main` is still pending; it was denied by the permission system earlier.
