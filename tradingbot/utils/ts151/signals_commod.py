"""Pure mathematical signals for TS151 commodities and futures strategies (§9.1-9.5, 10.1, 10.3, 10.4).

All functions are pure numpy/pandas operations. No database queries and no network calls.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from tradingbot.utils.quantsmd_common import erc_weights
from tradingbot.utils.ts151.common import min_variance_weights, topk_bottomk, trailing_return


def roll_yield(f1: float, f2: float, months_apart: float) -> float:
    """Compute annualized futures roll yield from two contract prices.

    Formula (§9.1):
        Roll yield = log(F1 / F2) * (12.0 / months_apart)

    Returns NaN if either price is non-positive or months_apart <= 0.
    """
    if f1 <= 0.0 or f2 <= 0.0 or months_apart <= 0.0:
        return float("nan")
    if math.isnan(f1) or math.isnan(f2) or math.isnan(months_apart):
        return float("nan")
    return float(math.log(f1 / f2) * (12.0 / months_apart))


def roll_yield_scores(
    contract_quotes: dict[str, tuple[float, float, float]],
) -> dict[str, float]:
    """Compute roll yields for a collection of commodities.

    contract_quotes: mapping from symbol (e.g. ETF name) to (f1, f2, months_apart).
    Returns dict mapping symbol to annualized roll yield.
    """
    out: dict[str, float] = {}
    for sym, (f1, f2, m_apart) in contract_quotes.items():
        ry = roll_yield(f1, f2, m_apart)
        if not math.isnan(ry):
            out[sym] = ry
    return out


def hedging_pressure_signal(
    cot_df: pd.DataFrame,
    etf_to_code: dict[str, str],
    weeks: int = 4,
    k: int = 3,
    gross: float = 1.0,
    long_only: bool = False,
) -> dict[str, float]:
    """Compute hedging pressure weights from CFTC Commitments of Traders data (§9.2).

    Hedging pressure (HP) = comm_long / (comm_long + comm_short),
    averaged over the last `weeks` reports available per commodity.

    Per Kakushadze & Serur §9.2:
    Long the lowest HP (hedgers net short -> producers hedging, earning risk premium),
    short the highest HP.

    Returns empty dict if fewer than 2*k valid commodities exist (or < k in long_only).
    """
    if cot_df.empty or not etf_to_code:
        return {}

    hp_scores: dict[str, float] = {}
    for etf, code in etf_to_code.items():
        sub = cot_df[cot_df["market_code"] == code]
        if sub.empty:
            continue
        sub_sorted = sub.sort_values(by="report_date").tail(weeks)
        if len(sub_sorted) < min(1, weeks):
            continue

        c_long = sub_sorted["comm_long"].astype(float)
        c_short = sub_sorted["comm_short"].astype(float)
        denom = c_long + c_short
        valid = denom > 0.0
        if not valid.any():
            continue

        hp_series = c_long[valid] / denom[valid]
        mean_hp = float(hp_series.mean())
        if not math.isnan(mean_hp):
            # Invert so lowest HP gets highest score -> bought
            hp_scores[etf] = -mean_hp

    return topk_bottomk(hp_scores, k=k, long_only=long_only, gross=gross)


def commod_diversification_weights(
    closes: pd.DataFrame,
    window: int = 252,
    method: str = "minvar",
) -> pd.Series:
    """Compute portfolio weights across multi-asset commodity diversification universe (§9.3).

    Methods:
    - "minvar": Long-only minimum-variance weights summing to 1.0.
    - "erc": Equal-risk-contribution weights summing to 1.0.

    Returns empty Series if closes has insufficient history.
    """
    if len(closes) < window + 1:
        return pd.Series(dtype=float)

    returns = closes.pct_change().dropna()
    if len(returns) < window:
        return pd.Series(dtype=float)

    cov = returns.iloc[-window:].cov()
    valid_cols = cov.columns[cov.isna().sum(axis=0) == 0]
    if len(valid_cols) < 2:
        return pd.Series(dtype=float)

    cov_clean = cov.loc[valid_cols, valid_cols]

    if method == "minvar":
        return min_variance_weights(cov_clean)
    elif method == "erc":
        w_arr = erc_weights(cov_clean.to_numpy())
        return pd.Series(w_arr, index=valid_cols, dtype=float)
    else:
        raise ValueError(f"unsupported method {method!r}; expected 'minvar' or 'erc'")


def commod_value_scores(
    closes: pd.DataFrame,
    lag_start: int = 1386,
    lag_end: int = 1134,
) -> pd.Series:
    """Compute commodity value scores (§9.4).

    Value = log(mean price over [−lag_start, −lag_end]) - log(close).
    Simplification of 4.5-5.5 years ago lookback window.

    Returns empty Series if closes has fewer than `lag_start` bars.
    """
    if len(closes) < lag_start:
        return pd.Series(dtype=float)

    past_window = closes.iloc[-lag_start:-lag_end]
    mean_past = past_window.mean()
    curr_close = closes.iloc[-1]

    valid = (mean_past > 0.0) & (curr_close > 0.0)
    scores = pd.Series(np.nan, index=closes.columns, dtype=float)
    scores[valid] = np.log(mean_past[valid]) - np.log(curr_close[valid])
    return scores.dropna()


def skewness_scores(
    closes: pd.DataFrame,
    window: int = 252,
) -> pd.Series:
    """Compute skewness of daily returns over `window` bars (§9.5).

    Long the most negative skew, short the most positive skew.
    Score is returned as -skew, so top-k corresponds to most negative skew.

    Returns empty Series if insufficient history.
    """
    if len(closes) < window + 1:
        return pd.Series(dtype=float)

    returns = closes.pct_change().iloc[-window:]
    sk = returns.skew()
    valid = sk.dropna()
    if valid.empty:
        return pd.Series(dtype=float)

    # Invert skew so most negative skew has highest score
    return -valid


def hedged_holding_weights(
    closes: pd.DataFrame,
    asset: str = "XLK",
    hedge: str = "SPY",
    window: int = 63,
) -> dict[str, float]:
    """Compute hedged holding weights (§10.1).

    Long `asset` (+1.0), short `hedge` (-beta) where
    beta = cov(asset, hedge) / var(hedge) over `window` bars of daily returns.

    Returns empty dict if insufficient history or zero hedge variance.
    """
    if len(closes) < window + 1:
        return {}
    if asset not in closes.columns or hedge not in closes.columns:
        return {}

    sub = closes[[asset, hedge]].dropna()
    if len(sub) < window + 1:
        return {}

    ret = sub.pct_change().iloc[-window:]
    var_hedge = float(ret[hedge].var())
    if var_hedge <= 0.0 or math.isnan(var_hedge):
        return {}

    cov = float(ret[asset].cov(ret[hedge]))
    if math.isnan(cov):
        return {}

    beta = cov / var_hedge
    return {asset: 1.0, hedge: -float(beta)}


def contrarian_weights(
    closes: pd.DataFrame,
    window: int = 5,
    gross: float = 2.0,
) -> dict[str, float]:
    """Compute short-term contrarian weights across universe (§10.3).

    Weights proportional to -(R_i - mean(R)) over `window` bars, scaled to `gross`.

    Returns empty dict if insufficient history or zero deviation.
    """
    if len(closes) < window + 1:
        return {}

    ret = trailing_return(closes, window=window)
    if isinstance(ret, float) or ret.isna().all():
        return {}

    valid_ret = ret.dropna()
    if len(valid_ret) < 2:
        return {}

    excess = valid_ret - valid_ret.mean()
    signal = -excess

    denom = signal.abs().sum()
    if denom == 0.0 or math.isnan(denom):
        return {}

    scaled = (signal / denom) * gross
    return {str(sym): float(w) for sym, w in scaled.items()}


def trend_following_weights(
    closes: pd.DataFrame,
    window: int = 252,
    vol_window: int = 63,
    target_vol: float = 0.4,
    max_gross: float = 2.0,
) -> dict[str, float]:
    """Compute multi-asset trend-following weights (§10.4).

    Sign(return over `window`) * (target_vol / N) / vol(`vol_window`),
    capped at gross leverage `max_gross`.

    Returns empty dict if insufficient history.
    """
    needed = max(window, vol_window) + 1
    if len(closes) < needed:
        return {}

    ret = trailing_return(closes, window=window)
    if isinstance(ret, float) or ret.isna().all():
        return {}

    daily_ret = closes.pct_change().iloc[-vol_window:]
    ann_vol = daily_ret.std() * np.sqrt(252.0)

    valid_mask = ret.notna() & (ann_vol > 0.0) & ann_vol.notna()
    valid_syms = [s for s in closes.columns if bool(valid_mask.get(s, False))]
    n = len(valid_syms)
    if n == 0:
        return {}

    raw_weights: dict[str, float] = {}
    for sym in valid_syms:
        r_val = float(ret[sym])
        sign_val = 1.0 if r_val > 0.0 else (-1.0 if r_val < 0.0 else 0.0)
        v_val = float(ann_vol[sym])
        w = sign_val * (target_vol / n) / v_val
        raw_weights[sym] = float(w)

    gross_sum = sum(abs(w) for w in raw_weights.values())
    if gross_sum > max_gross and gross_sum > 0.0:
        scale = max_gross / gross_sum
        return {k: v * scale for k, v in raw_weights.items()}

    return raw_weights
