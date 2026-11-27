"""Pure mathematical and signal helpers for TS151 strategy bots (PLAN-ts151.md S0).

No database and no network access. Everything here operates on pandas/numpy data structures.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from tradingbot.utils.quantsmd_common import period_rolled


def is_live_as_of(as_of: Any, max_days: int = 5) -> bool:
    """Return True if as_of is within max_days calendar days of now (UTC)."""
    if as_of is None:
        return False
    try:
        as_of_ts = pd.to_datetime(as_of)
        if hasattr(as_of_ts, "tzinfo") and as_of_ts.tzinfo is not None:
            as_of_ts = as_of_ts.tz_convert("UTC").tz_localize(None)
        as_of_ts = as_of_ts.normalize()
    except Exception:
        return False

    now_ts = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    return abs((now_ts - as_of_ts).days) <= max_days


def rebalance_due(index: pd.Index, freq: str) -> bool:
    """Return True if a portfolio rebalance is due at the current bar.

    freq in {"D", "W", "M", "Q"}:
    - "D": Every bar (returns True if index has at least 1 bar).
    - "W": ISO calendar week rolled (at least 2 bars required).
    - "M": Calendar month rolled (defers to quantsmd_common.period_rolled).
    - "Q": Calendar quarter rolled (defers to quantsmd_common.period_rolled).
    """
    if freq not in ("D", "W", "M", "Q"):
        raise ValueError(f"unsupported freq {freq!r}; expected 'D', 'W', 'M' or 'Q'")

    if len(index) == 0:
        return False

    if freq == "D":
        return True

    if len(index) < 2:
        return False

    if freq == "W":
        last = pd.Timestamp(index[-1])
        prev = pd.Timestamp(index[-2])
        return last.isocalendar()[:2] != prev.isocalendar()[:2]

    return period_rolled(index, freq)


def topk_bottomk(
    scores: pd.Series | dict[str, float],
    k: int | float,
    long_only: bool = False,
    gross: float = 1.0,
) -> dict[str, float]:
    """Compute equal-weighted portfolio weights from cross-sectional scores.

    - Long-only gives longs summing to `gross`.
    - Signed gives each side `gross / 2` (net zero, dollar-neutral).
    - `k` may be an integer count or a float fraction of assets.

    Returns {} if scores has insufficient valid elements.
    """
    s = pd.Series(scores, dtype=float) if isinstance(scores, dict) else scores.astype(float)

    valid = s.dropna()
    valid = valid[np.isfinite(valid)]
    num_assets = len(valid)

    if num_assets == 0:
        return {}

    n = max(1, round(num_assets * k)) if isinstance(k, float) else int(k)

    if n <= 0:
        return {}

    if long_only:
        if num_assets < n:
            return {}
        sorted_names = valid.sort_values(ascending=False).index[:n]
        w_each = gross / n
        return {str(sym): float(w_each) for sym in sorted_names}

    # Signed (long/short)
    if num_assets < 2 * n:
        return {}

    sorted_scores = valid.sort_values(ascending=False)
    long_symbols = sorted_scores.index[:n]
    short_symbols = sorted_scores.index[-n:]

    w_long = (gross / 2.0) / n
    w_short = -(gross / 2.0) / n

    out: dict[str, float] = {}
    for sym in long_symbols:
        out[str(sym)] = float(w_long)
    for sym in short_symbols:
        out[str(sym)] = float(w_short)
    return out


def rank_weights(
    scores: pd.Series | dict[str, float],
    gross: float = 1.0,
) -> dict[str, float]:
    """Compute dollar-neutral weights proportional to (rank - mean rank).

    Total gross leverage equals `gross`, and net weight equals 0.0.
    Returns {} if fewer than 2 valid scores exist or all ranks are identical.
    """
    s = pd.Series(scores, dtype=float) if isinstance(scores, dict) else scores.astype(float)

    valid = s.dropna()
    valid = valid[np.isfinite(valid)]
    if len(valid) < 2:
        return {}

    ranks = valid.rank(ascending=True)
    centered = ranks - ranks.mean()
    abs_sum = centered.abs().sum()
    if abs_sum == 0.0 or math.isnan(abs_sum):
        return {}

    scaled = (centered / abs_sum) * gross
    return {str(sym): float(w) for sym, w in scaled.items()}


def inverse_vol(closes: pd.DataFrame, window: int) -> pd.Series:
    """Compute inverse-volatility weights from daily close prices.

    Weights are non-negative, sum to 1.0, and proportional to 1 / std(returns).
    Returns an empty Series if insufficient data or no valid volatility.
    """
    if len(closes) < window + 1:
        return pd.Series(dtype=float)

    returns = closes.pct_change()
    vol = returns.iloc[-window:].std()
    valid_vol = vol.dropna()
    valid_vol = valid_vol[valid_vol > 0.0]

    if valid_vol.empty:
        return pd.Series(dtype=float)

    inv = 1.0 / valid_vol
    inv_sum = inv.sum()
    if inv_sum == 0.0 or math.isnan(inv_sum):
        return pd.Series(dtype=float)

    return inv / inv_sum


def trailing_return(
    closes: pd.DataFrame | pd.Series,
    window: int,
    skip: int = 0,
) -> pd.Series | float:
    """Compute trailing return over `window` bars ending `skip` bars ago.

    Return = (P[-(1 + skip)] / P[-(1 + skip + window)]) - 1.0.
    """
    needed = window + skip + 1
    if len(closes) < needed:
        if isinstance(closes, pd.DataFrame):
            return pd.Series(np.nan, index=closes.columns, dtype=float)
        return float("nan")

    past = closes.iloc[-1 - skip - window]
    now = closes.iloc[-1 - skip]
    ret = (now / past) - 1.0
    if isinstance(ret, pd.Series):
        return ret.astype(float)
    return float(ret)


def zscore(s: pd.Series) -> pd.Series:
    """Compute z-score of a Series: (s - mean) / std."""
    valid = s.dropna()
    if len(valid) < 2:
        return pd.Series(np.nan, index=s.index, dtype=float)
    std = valid.std()
    if std == 0.0 or math.isnan(std):
        return pd.Series(0.0, index=s.index, dtype=float)
    return (s - valid.mean()) / std


def ols_beta(y: pd.Series, x: pd.Series) -> float:
    """Compute OLS slope beta of y on x."""
    aligned = pd.concat([y, x], axis=1).dropna()
    if len(aligned) < 2:
        return float("nan")
    y_clean = aligned.iloc[:, 0]
    x_clean = aligned.iloc[:, 1]
    var_x = x_clean.var()
    if var_x == 0.0 or math.isnan(var_x):
        return float("nan")
    return float(y_clean.cov(x_clean) / var_x)


def ibs(row: pd.Series | dict[str, Any]) -> float:
    """Internal Bar Strength: (close - low) / (high - low).

    Returns 0.5 if high == low.
    """
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    if high == low:
        return 0.5
    return (close - low) / (high - low)


def pivot_levels(h: float, lo: float, c: float) -> tuple[float, float, float]:
    """Compute classic floor-trader pivot levels: Center (C), Resistance (R), Support (S).

    C = (h + lo + c) / 3
    R = 2 * C - lo
    S = 2 * C - h
    """
    center = (float(h) + float(lo) + float(c)) / 3.0
    resistance = 2.0 * center - float(lo)
    support = 2.0 * center - float(h)
    return center, resistance, support


def dv01_neutral(
    body: str,
    wings: tuple[str, str],
    durations: dict[str, float],
) -> dict[str, float]:
    """Compute DV01-neutral butterfly weights with wings split 50/50 by DV01.

    Given body weight = 1.0:
    wing0_weight = -0.5 * duration_body / duration_wing0
    wing1_weight = -0.5 * duration_body / duration_wing1
    """
    d_body = float(durations[body])
    d_w0 = float(durations[wings[0]])
    d_w1 = float(durations[wings[1]])

    w_w0 = -0.5 * d_body / d_w0
    w_w1 = -0.5 * d_body / d_w1

    return {
        body: 1.0,
        wings[0]: float(w_w0),
        wings[1]: float(w_w1),
    }


def replay_state(
    long_enter: Sequence[bool] | pd.Series,
    long_exit: Sequence[bool] | pd.Series,
    short_enter: Sequence[bool] | pd.Series | None = None,
    short_exit: Sequence[bool] | pd.Series | None = None,
) -> int:
    """Replay state transitions chronologically over boolean condition histories.

    Returns the final position state in {-1, 0, 1} (flat=0, long=1, short=-1).
    A NaN at any bar resets state to 0 (flat).
    """
    n = len(long_enter)
    state = 0

    def _val(seq: Any, i: int) -> Any:
        if seq is None:
            return None
        if hasattr(seq, "iloc"):
            return seq.iloc[i]
        return seq[i]

    for i in range(n):
        le = _val(long_enter, i)
        lx = _val(long_exit, i)
        se = _val(short_enter, i)
        sx = _val(short_exit, i)

        # Check for NaN / None in active inputs
        if pd.isna(le) or pd.isna(lx):
            state = 0
            continue
        if short_enter is not None and pd.isna(se):
            state = 0
            continue
        if short_exit is not None and pd.isna(sx):
            state = 0
            continue

        b_le = bool(le)
        b_lx = bool(lx)
        b_se = bool(se) if se is not None else False
        b_sx = bool(sx) if sx is not None else False

        if state == 1:
            if b_lx:
                state = -1 if b_se else 0
        elif state == -1:
            if b_sx:
                state = 1 if b_le else 0
        else:  # state == 0
            if b_le and not b_se:
                state = 1
            elif b_se and not b_le:
                state = -1

    return state


def min_variance_weights(
    cov: pd.DataFrame | np.ndarray,
    max_weight: float = 1.0,
    min_weight: float = 0.0,
) -> pd.Series:
    """Compute long-only minimum-variance portfolio weights summing to 1.0.

    Uses SLSQP optimization bounded by [min_weight, max_weight].
    """
    if isinstance(cov, pd.DataFrame):
        names = list(cov.columns)
        cov_matrix = cov.to_numpy()
    else:
        cov_matrix = np.asarray(cov)
        names = [str(i) for i in range(cov_matrix.shape[0])]

    n = cov_matrix.shape[0]
    if n == 0:
        return pd.Series(dtype=float)
    if n == 1:
        return pd.Series([1.0], index=names, dtype=float)

    def obj(w: np.ndarray) -> float:
        return float(w @ cov_matrix @ w)

    w0 = np.full(n, 1.0 / n)
    bounds = [(min_weight, max_weight) for _ in range(n)]
    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}

    try:
        res = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=constraints)
        if res.success:
            weights = res.x
        else:
            weights = np.clip(w0, min_weight, max_weight)
            weights /= weights.sum()
    except Exception:
        weights = np.clip(w0, min_weight, max_weight)
        weights /= weights.sum()

    return pd.Series(weights, index=names, dtype=float)
