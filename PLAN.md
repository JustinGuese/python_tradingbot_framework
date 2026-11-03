# PLAN: 10 quants.md bots (QuantJourney strategies) in the tradingbot framework

Goal: implement the top-10-by-Sharpe strategies listed on https://quants.md/?sort=sharpe as
`Bot` subclasses, file `tradingbot/quantsmd_<slug>.py`, each with a header comment linking its
quants.md / QuantJourney page. Then backtest all 10 and report.

Source repo for logic: https://github.com/QuantJourneyOrg/quantjourney-bt/tree/main/strategies
(Apache-2.0, Copyright (c) 2026 QuantJourney). Logic is quoted in each step; do NOT fetch the web.

## Caveats (read before approving)
1. quants.md has **no per-strategy page**. Each strategy's page is on
   `backtester.quantjourney.cloud/strategies/<slug>`. Header comment will carry both
   `https://quants.md/?sort=sharpe` and the strategy URL. The top-10 ranking was extracted by a
   fetch summarizer (Sharpe 0.62 .. 0.34); not independently verified against the live page.
2. Framework is long-only, gross <= 1.0, no leverage. Departures (documented in each file):
   W25 long/short -> long-only (short legs go to cash); W18/W17 vol-target leverage (2.0x/1.5x)
   -> capped at 1.0x (scale down only); W11 tracking-error/turnover gate is a stateful
   approximation; W20 RiskParityModel reimplemented as equal-risk-contribution.
3. Bots are NOT added to helm/values.yaml (no CronJobs, nothing scheduled). Say so if you want them
   scheduled (paper) afterwards.
4. Backtests judged by alpha vs QQQ + t-stat (project rule), not raw return.

## Parallelism
- Batch 0 must run first: every bot imports `quantsmd_common.py` (shared contract).
- Batches 1-3: steps inside a batch touch disjoint files (one bot + one test each).
  Max 4 concurrent, so 10 bots = 4 + 4 + 2.
- Batch 4 (backtest script, run, report) is done by Claude (Opus), NOT Haiku: it needs network,
  yfinance and judgment. Depends on all bots.

## Common brief (applies to EVERY step)
Repo root: `/home/jguese/code/tradingbot/python_tradingbot_framework` (work inside your worktree).
Python via `uv run`. Tests need a stub DB URI:
`POSTGRES_URI="postgresql://x:x@localhost:5432/x" PYTHONPATH=. uv run pytest <file> -q`
Lint/format: `uv run ruff format <files>` then `uv run ruff check <files>`.

Existing bot to copy the shape of: `tradingbot/tsmomtrendbot.py`. Key shape:
```python
import logging
from typing import ClassVar
import pandas as pd
from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot


class TSMOMTrendBot(Bot):
    param_grid: ClassVar[dict] = {"lookback_days": [189, 252, 315]}
    BACKTEST_PERIOD: ClassVar[str | None] = "max"  # needed: default 1y backtest is too short

    def __init__(self, lookback_days: int = 252, **kwargs):
        super().__init__(
            "TSMOMTrendBot",  # DB name == class name
            tickers=list(UNIVERSE),
            interval="1d",
            period="2y",  # live fetch; must exceed longest lookback
            lookback_days=lookback_days,  # every ctor param is also passed to super, as here
            **kwargs,
        )
        self.lookback_days = lookback_days

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None: ...


if __name__ == "__main__":
    run_bot(TSMOMTrendBot)
```
`targetWeights` contract: return long-only `{ticker: weight}` with sum <= 1.0, never a `"USD"` key.
Omitted ticker = exit. `{}` = all cash. `None` = "do not rebalance this bar" (book untouched).
`self.datas[ticker]` is a DataFrame of history up to the current bar. `self.tradeable_tickers` is the
universe. Never call `.lower()` on bot names.

**Every bot file has this structure** (so it is testable without a DB):
1. Module docstring: first lines are exactly
   ```
   Source: https://quants.md/?sort=sharpe
   Strategy page: <STRATEGY URL from the step>
   Upstream code: <GITHUB URL from the step> (Apache-2.0, (c) 2026 QuantJourney)
   ```
   then 2-6 lines describing the rule and the "Departures from source" list from the step.
2. Module-level constants (universe, params).
3. ONE pure module-level function `compute_weights(closes: pd.DataFrame, ...) -> dict[str, float] | None`
   holding all logic. `closes` = adjusted close, index = timestamps ascending, columns = tickers, history
   up to and including the current bar. No Bot/DB access inside it.
4. The Bot class: `targetWeights` does only
   ```python
   closes = closes_frame(self.datas, self.tradeable_tickers)
   return compute_weights(closes, <params from self>)
   ```
   (steps with state say otherwise).
5. `if __name__ == "__main__": run_bot(<Class>)`.

Shared helpers (created in Step 0.1, import from `tradingbot.utils.quantsmd_common`):
`ETF8`, `SECTORS9`, `closes_frame(datas, tickers)`, `period_rolled(index, period)`,
`is_weekday_bar(index, weekday)`, `equal_weight(active, cap)`, `annualized_vol(closes, window)`.

Tests: file `tests/test_quantsmd_<slug>.py`, import ONLY `compute_weights` (and constants) from the bot
module, use synthetic data (numpy, fixed seed), never network, never DB. Importing the bot module must
work with only the stub `POSTGRES_URI`.

Global "do NOT" (every step): do not edit any file not listed; do not touch `helm/`, `pyproject.toml`,
`uv.lock`, `ruff` config, other bots, or existing tests; do not add dependencies; do not weaken or
delete tests/lint rules to get green; do not run the full test suite or mypy; no network access.

---

# BATCH 0 (sequential, 1 step)

## Step 0.1 — shared helpers
Files: create `tradingbot/utils/quantsmd_common.py`, create `tests/test_quantsmd_common.py`.

Implement (type-hinted, docstring each, `from __future__` not needed):
```python
ETF8 = ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ"]
SECTORS9 = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]


def closes_frame(datas: dict[str, pd.DataFrame], tickers: list[str]) -> pd.DataFrame:
    """Wide close-price frame. For each ticker take datas[t]; skip None/empty or missing 'close'.
    Time axis: use column 'timestamp' if present, else the frame's index. Build a Series
    close indexed by pd.to_datetime(time axis) (tz-naive: if tz-aware, .tz_convert(None)),
    drop NaN, drop duplicate index (keep last). Combine with pd.concat(axis=1, keys=tickers_found),
    sort index. Return empty DataFrame if nothing found."""


def period_rolled(index: pd.Index, period: str) -> bool:
    """True iff the LAST timestamp in index starts a new period vs the one before it.
    period 'M' -> (year, month) differs; 'Q' -> (year, quarter) differs. len(index) < 2 -> False.
    Raise ValueError for any other period."""


def is_weekday_bar(index: pd.Index, weekday: int) -> bool:
    """True iff last timestamp's .weekday() == weekday (Mon=0 .. Fri=4). Empty index -> False."""


def equal_weight(active: list[str], cap: float) -> dict[str, float]:
    """1/len(active) each, min(w, cap). NO redistribution of the clipped excess (stays cash).
    Empty list -> {}."""


def annualized_vol(closes: pd.DataFrame, window: int) -> pd.Series:
    """Per column: std of pct_change over the LAST `window` returns * sqrt(252).
    Columns with fewer than `window` valid returns -> NaN."""
```
Tests (`tests/test_quantsmd_common.py`), exact cases:
1. `period_rolled`: index of 2024-01-30, 2024-01-31, 2024-02-01 -> 'M' True; index ending 2024-01-31 -> 'M' False;
   2024-03-28, 2024-04-01 -> 'Q' True; 2024-04-01, 2024-04-02 -> 'Q' False; single element -> False; 'W' raises ValueError.
2. `is_weekday_bar`: 2024-01-05 (Friday) with 4 True, with 0 False; empty index False.
3. `equal_weight`: 2 names cap 0.25 -> {a:0.25,b:0.25}; 5 names cap 0.25 -> 0.2 each; [] -> {}.
4. `closes_frame`: two frames, one with 'timestamp' column, one with DatetimeIndex, one empty frame, one None -> result has 2 columns, sorted index.
5. `annualized_vol`: constant-growth series -> ~0.0; series with <window returns -> NaN.
Quick checks: `uv run ruff format tradingbot/utils/quantsmd_common.py tests/test_quantsmd_common.py && uv run ruff check tradingbot/utils/quantsmd_common.py tests/test_quantsmd_common.py && POSTGRES_URI="postgresql://x:x@localhost:5432/x" PYTHONPATH=. uv run pytest tests/test_quantsmd_common.py -q`
Acceptance: [ ] 5 funcs + 2 constants exist [ ] tests pass [ ] ruff clean.
Do NOT: touch any other file.

---

# BATCH 1 (parallel, 4 steps)

Each step: create `tradingbot/<file>` and `tests/test_<file>` ; run the quick check
`uv run ruff format <2 files> && uv run ruff check <2 files> && POSTGRES_URI="postgresql://x:x@localhost:5432/x" PYTHONPATH=. uv run pytest tests/test_<file> -q`
Acceptance for every bot step: [ ] header comment lines exact [ ] one pure `compute_weights` [ ] Bot class + `__main__` [ ] tests pass [ ] ruff clean [ ] weights never negative, sum <= 1.0, no "USD" key.
Every bot test file MUST include these generic cases in addition to its specific ones:
(G1) output weights all >= 0 and sum <= 1.0 + 1e-9 on a random-walk 600-bar frame; (G2) too-short history
(e.g. 10 bars) returns `{}` or `None` (never raises); (G3) importing the Bot class and reading its `param_grid` and
`BACKTEST_PERIOD` works.

## Step 1.1 — Continuous Futures Trend (W25), Sharpe 0.62
Files: `tradingbot/quantsmd_continuous_futures_trend.py`, `tests/test_quantsmd_continuous_futures_trend.py`.
Class `QuantsmdContinuousFuturesTrendBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-25-continuous-futures-trend
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_25_continuous_futures_trend.py
Universe: `["MES=F", "MNQ=F", "ZN=F", "CL=F", "GC=F", "ZC=F"]`. Defaults fast=63, slow=252, vol_window=63. Monthly.
Source logic (quoted):
```python
fast = close.rolling(63, min_periods=63).mean()
slow = close.rolling(252, min_periods=252).mean()
signals = np.sign(fast - slow).where(valid, 0.0)
volatility = close.pct_change().rolling(63, min_periods=63).std() * np.sqrt(252.0)
raw = signals / volatility.replace(0.0, np.nan)
weights = raw.div(raw.abs().sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)  # unit gross, long/short
```
Departure: long-only. For `compute_weights(closes, fast, slow, vol_window)`: `rest = 1/vol` for every ticker with valid
fast, slow, vol>0 (this is `|raw|`); `denominator = sum(rest over ALL valid tickers)` (longs AND would-be shorts);
weight_i = rest_i/denominator only if fast_i > slow_i, else omitted (short leg -> cash). If `period_rolled(closes.index,'M')`
is False return `None`. If < slow+1 rows return `{}`.
`BACKTEST_PERIOD = "max"`, live `period="3y"`. param_grid: fast [42,63,84], slow [189,252], vol_window [42,63].
Specific tests: (a) uptrending series for all 6 tickers on a month-first bar -> weights sum ≈ 1.0 (all long);
(b) all downtrending -> `{}`; (c) mid-month last bar -> `None`; (d) half up/half down -> sum < 1.0 and only uptrending tickers keyed.
Add note in the class docstring: yfinance micro-futures (MES=F, MNQ=F) history starts ~2019 and the framework has no roll/multiplier model: signal proxy only.

## Step 1.2 — Vol Target Momentum (W18), Sharpe 0.57
Files: `tradingbot/quantsmd_vol_target_momentum.py`, `tests/test_quantsmd_vol_target_momentum.py`.
Class `QuantsmdVolTargetMomentumBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-18-vol-target-momentum
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_18_vol_target_momentum.py
Universe `ETF8`. Defaults lookback=126, top_n=3, cap=0.40, target_vol=0.15, vol_lookback=42. Monthly.
Source logic:
```python
momentum = close.iloc[i] / close.iloc[i - 126] - 1.0
picks = momentum[momentum > 0.0].nlargest(3).index  # signal 1.0
weights = active.div(counts).clip(upper=0.40)  # equal weight among picks
# then VolTargetModel(target_vol=0.15, lookback=42, max_leverage=2.0)
```
`compute_weights(closes, lookback, top_n, cap, target_vol, vol_lookback)`: `None` if not `period_rolled(...,'M')`;
`{}` if < lookback+1 rows or no positive-momentum picks. Base = `equal_weight(picks, cap)`. Basket returns =
`(closes.pct_change()[picks].iloc[-vol_lookback:] * base_w).sum(axis=1)` (use base weights, tickers as columns);
`vol = basket_returns.std()*sqrt(252)`; `scale = min(1.0, target_vol/vol)` if vol>0 else 1.0; final = base*scale.
Departure: max_leverage 2.0 -> 1.0 (scale down only).
param_grid: lookback [63,126,189], top_n [2,3,4], target_vol [0.10,0.15].
Specific tests: (a) 8 tickers, 3 rising strongly -> exactly those 3 keyed; (b) all falling -> `{}`; (c) very high-vol picks -> sum < base sum;
(d) not month-first bar -> `None`; (e) each weight <= 0.40.

## Step 1.3 — Monthly Rotation Orders (O17), Sharpe 0.53
Files: `tradingbot/quantsmd_monthly_rotation.py`, `tests/test_quantsmd_monthly_rotation.py`.
Class `QuantsmdMonthlyRotationBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-orders-17-monthly-rotation-orders
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_17_monthly_rotation_orders.py
Universe `ETF8`. Defaults lookback=126, top_n=3. Monthly (first bar of a new month).
Source logic:
```python
momentum = close.iloc[pos_idx] / close.iloc[pos_idx - 126] - 1.0
target = set(momentum.dropna().nlargest(3).index)  # NO positivity filter: always fully invested
per_name_nav = nav / max(1, len(target))  # equal weight, sells names that left target
```
`compute_weights(closes, lookback, top_n)`: `None` if not month-first; `{}` if < lookback+1 rows; else
`{t: 1/len(target)}` for the top_n by momentum (dropna first). Departure: source executes market orders with 3bp slippage;
here the framework's own cost model applies.
param_grid: lookback [63,126,189], top_n [2,3,4].
Specific tests: (a) weights sum to 1.0 even if every ETF is falling (no positivity filter); (b) exactly top_n keys and they are the highest-momentum
ones; (c) mid-month -> `None`; (d) fewer than top_n valid tickers -> equal split among those available.

## Step 1.4 — Daily SMA Trend (W01), Sharpe 0.45
Files: `tradingbot/quantsmd_sma_daily.py`, `tests/test_quantsmd_sma_daily.py`.
Class `QuantsmdSmaDailyBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-01-sma-daily
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_01_sma_daily.py
Universe `SECTORS9`. Defaults fast=50, slow=200, cap=0.25. Daily (never returns None).
Source logic:
```python
valid = sma_fast.notna() & sma_slow.notna()
signals = (sma_fast > sma_slow).astype(float).where(valid, 0.0)
weights = active.div(counts, axis=0).fillna(0.0).clip(upper=0.25)  # equal weight, cap 0.25, remainder cash
```
`compute_weights(closes, fast, slow, cap)`: `{}` if < slow rows; active = tickers with both SMAs defined and fast > slow;
return `equal_weight(active, cap)`. live `period="2y"`.
param_grid: fast [20,50,100], slow [150,200,250].
Specific tests: (a) all 9 uptrending -> each 0.1111 (1/9, below cap); (b) only 2 active -> each 0.25 (cap binds, sum 0.5);
(c) all downtrending -> `{}`; (d) 199 rows -> `{}`.

---

# BATCH 2 (parallel, 4 steps) — same quick check / acceptance / generic tests G1-G3 as Batch 1

## Step 2.1 — Monthly Inverse Vol (W05), Sharpe 0.45
Files: `tradingbot/quantsmd_monthly_inverse_vol.py`, `tests/test_quantsmd_monthly_inverse_vol.py`.
Class `QuantsmdMonthlyInverseVolBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-05-monthly-inverse-vol
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_05_monthly_inverse_vol.py
Universe `ETF8`. Defaults lookback=63, cap=0.35. Monthly.
Source logic:
```python
realized_vol = close.pct_change().rolling(63).std() * np.sqrt(252)
inverse_vol = 1.0 / realized_vol.replace(0.0, np.nan)
weights = (
    inverse_vol.div(inverse_vol.sum(axis=1), axis=0).fillna(0.0).clip(upper=0.35)
)  # clip AFTER normalising, no redistribution
```
`compute_weights(closes, lookback, cap)`: `None` if not month-first; `{}` if < lookback+1 rows or no valid vols;
use `annualized_vol(closes, lookback)`; drop NaN/0; normalise inverse vols to sum 1; `min(w, cap)` each.
param_grid: lookback [42,63,126], cap [0.30,0.35,0.40].
Tests: (a) one very-low-vol ticker gets clipped to 0.35 and sum < 1.0; (b) equal-vol tickers -> equal weights 0.125; (c) mid-month `None`; (d) constant-price ticker (vol 0) is omitted.

## Step 2.2 — Risk Parity Capped (W20), Sharpe 0.45
Files: `tradingbot/quantsmd_risk_parity_capped.py`, `tests/test_quantsmd_risk_parity_capped.py`.
Class `QuantsmdRiskParityCappedBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-20-risk-parity-capped
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_20_risk_parity_capped.py
Universe `SECTORS9`. Defaults lookback=126 (also warmup), cap=0.25. Monthly.
Source: equal-weight signal after 126-bar warmup, then `RiskParityModel(lookback=126)` chained with `PositionLimitModel(max_weight=0.25)` (capped weights renormalised). The model source is not available; implement equal-risk-contribution:
```python
def _erc(cov: np.ndarray, iters: int = 500) -> np.ndarray:
    n = cov.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(iters):
        rc = w * (cov @ w)  # risk contributions
        total = rc.sum()
        if total <= 0:
            break
        w_new = w * (total / n) / np.where(rc > 0, rc, np.nan)  # move toward equal RC
        w_new = np.nan_to_num(w_new, nan=0.0)
        w_new = w_new / w_new.sum()
        w = 0.5 * w + 0.5 * w_new  # damping
    return w


def _cap(w: np.ndarray, cap: float) -> np.ndarray:  # iterative cap with renormalisation
    for _ in range(20):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w = np.where(over, cap, w)
        free = ~over & (w < cap - 1e-12)
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w
```
`compute_weights(closes, lookback, cap)`: `None` if not month-first; `{}` if < lookback+1 rows; returns = last `lookback` pct changes,
drop columns with any NaN; `cov = returns.cov().values`; `w = _cap(_erc(cov), cap)`; return dict (omit weights < 1e-9). Note: with 9 names and cap 0.25 the cap is satisfiable (9*0.25 > 1).
Departure: RiskParityModel re-implemented as ERC. param_grid: lookback [63,126,252], cap [0.20,0.25,0.30].
Tests: (a) 9 iid same-vol tickers -> each ≈ 1/9 (tol 0.02); (b) a ticker with 4x vol gets a smaller weight than a low-vol one; (c) sum ≈ 1.0 (tol 1e-6) and each <= 0.25+1e-9; (d) mid-month `None`.

## Step 2.3 — Quarterly TE Cost Gate (W11), Sharpe 0.44
Files: `tradingbot/quantsmd_quarterly_te_cost_gate.py`, `tests/test_quantsmd_quarterly_te_cost_gate.py`.
Class `QuantsmdQuarterlyTeCostGateBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-11-quarterly-te-cost-gate
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_11_quarterly_te_cost_gate.py
Universe `ETF8`, benchmark ticker "SPY" (inside the universe). Defaults lookback=126, top_n=3, cap=0.40, te_threshold=0.06, te_window=63, max_annual_turnover=4.0.
Source: signal = top-3 by 126d momentum (NO positivity filter), weights `equal 1/3 clip 0.40`;
`RebalancePolicy(frequency="BQE", tracking_error_threshold=0.06, tracking_error_window=63, max_annual_turnover=4.0)`
(rebalance quarterly, or early if annualised TE vs benchmark > 6%, within a rolling-252d turnover cap of 4x). Gate implementation is not in the source file: this is a stateful approximation.
Pure function:
```python
def compute_weights(closes, lookback, top_n, cap, te_threshold, te_window, max_annual_turnover, state: dict) -> dict | None:
    # state = {"last_target": dict[str,float] | None, "turnover": list[tuple[pd.Timestamp, float]]} ; mutated in place
    if len(closes) < lookback + 1: return {}
    proposed = equal_weight(top_n names by momentum, cap)
    last = state.get("last_target")
    if last is None: due = True                                    # first ever call: build the book
    else:
        due = period_rolled(closes.index, "Q")
        if not due and "SPY" in closes.columns and len(closes) > te_window:
            rets = closes.pct_change().iloc[-te_window:]
            held = sum(rets[t] * w for t, w in last.items() if t in rets.columns)   # last_target held since the last rebalance
            te = (held - rets["SPY"]).std() * sqrt(252)
            due = bool(te > te_threshold)
    if not due: return None
    turnover = 0.5 * sum(abs(proposed.get(t, 0) - (last or {}).get(t, 0)) for t in set(proposed) | set(last or {}))  # one-way
    now = closes.index[-1]
    state["turnover"] = [(d, x) for d, x in state["turnover"] if (now - d).days <= 365]
    if last is not None and sum(x for _, x in state["turnover"]) + turnover > max_annual_turnover: return None
    state["turnover"].append((now, turnover)); state["last_target"] = proposed
    return proposed
```
Bot: `self._state = {"last_target": None, "turnover": []}` in `__init__`; `targetWeights` passes `self._state`.
Docstring must state: state lives on the instance, so each live CronJob run starts fresh (first call always builds the book, then quarterly/TE logic only applies within a process, i.e. in backtests); this is a known limitation.
param_grid: lookback [63,126,189], te_threshold [0.04,0.06,0.08].
Tests: (a) first call returns 3 names weights 1/3; (b) second call mid-quarter with benchmark-tracking history -> `None`; (c) second call on a quarter-first bar -> a dict;
(d) turnover budget: set `state["turnover"]=[(index[-5], 4.0)]` and `last_target` to a different book on a quarter-first bar -> `None`; (e) fresh state per test.

## Step 2.4 — Bollinger Reversion (W21), Sharpe 0.42
Files: `tradingbot/quantsmd_bollinger_reversion.py`, `tests/test_quantsmd_bollinger_reversion.py`.
Class `QuantsmdBollingerReversionBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-21-bollinger-reversion
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_21_bollinger_reversion.py
Universe `SECTORS9`. Defaults window=20, n_std=2.0, cap=0.25. Daily (never None).
Source logic (per instrument, stateful): `mid = close.rolling(20).mean(); lower = mid - 2.0*close.rolling(20).std()`.
Not holding and `close < lower` -> enter. Holding and `close > mid` -> exit. If `lower` or `mid` is NaN -> flag False.
Weights: active names equal weight, `clip(upper=0.25)`, remainder cash.
Stateless replay: `compute_weights(closes, window, n_std, cap, replay=250)`: take `closes.tail(replay + window)`; compute mid/lower on the tail
with pandas rolling; walk the last `replay` rows forward per ticker with the flag rules above (numpy arrays, plain Python loop is fine); the flags
at the final row define the active set; return `equal_weight(active, cap)`; `{}` if < window rows. Document: state is replayed over the last 250 bars because the bot holds no state between CronJob runs.
param_grid: window [10,20,30], n_std [1.5,2.0,2.5].
Tests: (a) a ticker that dips below the lower band on the last bar (flat then sharp drop) -> keyed with 0.25 cap-limited weight;
(b) a ticker that dipped 3 bars ago and has not yet recovered above mid -> still held (hysteresis);
(c) after recovering above mid -> not held; (d) flat prices -> `{}` ; (e) 10 rows -> `{}`.

---

# BATCH 3 (parallel, 2 steps) — same quick check / acceptance / generic tests G1-G3

## Step 3.1 — Vol Target Trend (W17), Sharpe 0.36
Files: `tradingbot/quantsmd_vol_target_trend.py`, `tests/test_quantsmd_vol_target_trend.py`.
Class `QuantsmdVolTargetTrendBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-17-vol-target-trend
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_17_vol_target_trend.py
Universe `ETF8`. Defaults fast=50, slow=200, cap=0.34, target_vol=0.10, vol_lookback=63. Monthly.
Source: `signals = (sma50 > sma200)` (both defined); `weights = active.div(counts).clip(upper=0.34)`; then
`VolTargetModel(target_vol=0.10, lookback=63, max_leverage=1.5)`.
`compute_weights(closes, fast, slow, cap, target_vol, vol_lookback)`: `None` if not month-first; `{}` if < slow rows or no active; base =
`equal_weight(active, cap)`; basket returns = `(closes.pct_change()[active].iloc[-vol_lookback:] * base_w).sum(axis=1)`; `vol = std*sqrt(252)`;
`scale = min(1.0, target_vol/vol)` if vol>0 else 1.0; final = base*scale. Departure: max_leverage 1.5 -> 1.0.
param_grid: fast [20,50,100], slow [150,200,250], target_vol [0.08,0.10,0.12].
Tests: (a) calm uptrends -> weights equal base (scale 1.0) and each <= 0.34; (b) very noisy uptrending tickers -> sum below base sum;
(c) all downtrending -> `{}`; (d) mid-month `None`.

## Step 3.2 — Weekly RSI Reversion (W03), Sharpe 0.34
Files: `tradingbot/quantsmd_weekly_rsi_reversion.py`, `tests/test_quantsmd_weekly_rsi_reversion.py`.
Class `QuantsmdWeeklyRsiReversionBot`. Strategy URL: https://backtester.quantjourney.cloud/strategies/example-weights-03-weekly-rsi-reversion
Upstream: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_03_weekly_rsi_reversion.py
Universe `SECTORS9`. Defaults rsi_period=14, entry=35, exit=60, cap=0.25. Rebalance Fridays only.
Source logic:
```python
if pd.isna(value):
    in_position[inst] = False
elif not in_position[inst] and value < 35:
    in_position[inst] = True
elif in_position[inst] and value > 60:
    in_position[inst] = False  # 35..60: state unchanged
weights = active.div(counts).fillna(0).clip(upper=0.25)  # applied on Fridays (RebalancePolicy W, weekday=4)
```
RSI(14) per ticker: use `ta.momentum.RSIIndicator(close=series, window=rsi_period).rsi()` (the `ta` library is already a dependency).
`compute_weights(closes, rsi_period, entry, exit_, cap, replay=250)`: `None` if not `is_weekday_bar(closes.index, 4)`; `{}` if < rsi_period+2 rows;
compute RSI on the whole `closes.tail(replay + rsi_period + 5)` frame, replay the state machine daily over the last `replay` rows (flags carry
across days, NaN resets), final flags -> `equal_weight(active, cap)`. Document the stateless replay (as in Bollinger step).
param_grid: entry [30,35,40], exit [55,60,65].
Tests: (a) non-Friday last bar -> `None`; (b) Friday, a ticker with a sharp sell-off (RSI < 35) -> keyed at <= 0.25; (c) Friday, ticker whose RSI fell below 35
and is now ~45 (hysteresis) -> still held; (d) rising prices (RSI > 60 never < 35) -> `{}`; (e) < 16 rows -> `{}`.
(Use 2024-01-05 as a known Friday when building a business-day index ending on a Friday.)

---

# BATCH 4 — backtest + report (Claude/Opus, not Haiku)
Files: create `scripts/onetime_quantsmd_backtest.py`, create `docs/backtests/quantsmd-2026-11.md`.
1. Script follows `scripts/onetime_institutional_flow_backtest.py` (offline sqlite + yfinance caching, `backtest_bot(..., data=..., benchmark_close=QQQ)`).
2. Run all 10 with defaults over each bot's own `BACKTEST_PERIOD`, plus a first-half/second-half split.
3. Report per bot: alpha vs QQQ, t-stat, beta, correlation, max DD, Sharpe, yearly return, QQQ over the same window; verdict by the project bar (t >= 2 real edge, else "unproven"; beta ~1 & alpha ~0 -> QQQ clone).
4. Final answer: ranked table + honest note on departures from source (long-only, no leverage) and that source Sharpes (0.34-0.62) were measured on 2000-2026 long/short-free-of-framework-costs backtests.

# Final review (Claude)
Full `uv run ruff check`, `ruff format --check`, `mypy`, `pytest` on `haiku/work`; `git diff pre-haiku` vs this plan; confirm no config/test weakened, no helm change.
