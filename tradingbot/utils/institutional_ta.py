"""
Institutional technical benchmarks — what large desks screen and execute against.

Three families, all computable from daily OHLCV:

* **Mandate filters.** Funds can only buy what their policy allows: enough
  average daily dollar volume to get in and out without moving the price, and
  — for trend mandates — price above the 200-day moving average.
* **Execution benchmarks.** Desks are graded against VWAP, so the price relative
  to a volume-weighted cost basis says whether recent institutional buyers are
  in profit (and likely to add) or underwater (and likely to supply).
  `ta`'s `volume_vwap` is a 14-bar window, too short for that; this module adds
  a 50-day rolling VWAP and one anchored at the start of each calendar quarter,
  a proxy for funds' quarter-to-date average cost.
* **Accumulation footprints.** Large orders are worked over days, and leave
  volume behind: up-day vs down-day volume, IBD-style accumulation and
  distribution days, and OBV / A-D line slopes. OBV rising while price is flat
  is the classic "quiet accumulation" pattern.

Everything here is pure pandas — no DB, no network — and every column is
trailing-only, so features at bar t are identical whether or not later bars
exist. That is what makes the live path and the backtest agree.

Signals that institutions also watch but that free daily data cannot show —
MOC imbalances, dark-pool prints, ETF creations, 13F changes, options flow —
are deliberately absent rather than approximated.

Warmup is left as NaN, never filled: `DataService.get_yf_data_with_ta` fills
the `ta` columns with 0, and a 0 SMA200 would pass "close > SMA200" for every
stock with too little history. `passes_mandate` rejects any NaN input instead.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# IBD's accumulation/distribution day threshold: a move of at least 0.2% on
# volume higher than the prior session.
ACCUM_MIN_MOVE = 0.002

# Relative-strength horizons and weights, IBD's RS-rating recipe: the most
# recent quarter counts double.
RS_HORIZONS: tuple[tuple[int, float], ...] = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))

# Score components and their default weights. Each is z-scored across the
# eligible universe before weighting, so the weights are directly comparable.
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "inst_rs": 1.0,
    "inst_ud_vol_ratio50": 1.0,
    "volume_cmf": 1.0,
    "inst_obv_slope20": 1.0,
    "inst_accum_net25": 1.0,
    "inst_vwap_qtd_dist": 1.0,
    "divergence": 1.0,
}


def _timestamps(df: pd.DataFrame) -> pd.Series:
    """The bar timestamps, whether they live in a column (live) or the index (backtest)."""
    if "timestamp" in df.columns:
        return pd.to_datetime(df["timestamp"])
    return pd.Series(pd.to_datetime(df.index), index=df.index)


def rolling_vwap(df: pd.DataFrame, window: int) -> pd.Series:
    """Volume-weighted average of the typical price (H+L+C)/3 over `window` bars."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (typical * df["volume"]).rolling(window, min_periods=window).sum()
    vol = df["volume"].rolling(window, min_periods=window).sum()
    return pv / vol.replace(0, np.nan)


def quarter_anchored_vwap(df: pd.DataFrame) -> pd.Series:
    """
    VWAP anchored at the first bar of each calendar quarter, cumulative within it.

    Resets on the quarter boundary, so the first bar of a quarter equals that
    bar's typical price. Only past bars of the same quarter contribute.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    quarter = _timestamps(df).dt.to_period("Q").to_numpy()
    pv = (typical * df["volume"]).groupby(quarter).cumsum()
    vol = df["volume"].groupby(quarter).cumsum()
    return pv / vol.replace(0, np.nan)


def up_down_volume_ratio(df: pd.DataFrame, window: int = 50) -> pd.Series:
    """Volume on up days divided by volume on down days over `window` bars."""
    change = df["close"].diff()
    up = df["volume"].where(change > 0, 0.0).rolling(window, min_periods=window).sum()
    down = df["volume"].where(change < 0, 0.0).rolling(window, min_periods=window).sum()
    return up / down.replace(0, np.nan)


def net_accumulation_days(df: pd.DataFrame, window: int = 25, min_move: float = ACCUM_MIN_MOVE) -> pd.Series:
    """
    IBD-style accumulation days minus distribution days over `window` bars.

    Accumulation: close up at least `min_move` on higher volume than the prior
    bar. Distribution: close down at least `min_move` on higher volume.
    """
    ret = df["close"].pct_change()
    heavier = df["volume"] > df["volume"].shift(1)
    accum = ((ret >= min_move) & heavier).astype(float)
    dist = ((ret <= -min_move) & heavier).astype(float)
    net = (accum - dist).rolling(window, min_periods=window).sum()
    # The first bar has no prior bar to compare against.
    return net.where(ret.notna().rolling(window, min_periods=window).sum() == window)


def volume_line_slope(line: pd.Series, volume: pd.Series, window: int = 20, norm_window: int = 50) -> pd.Series:
    """
    Change of a cumulative volume line (OBV, A-D) over `window` bars, in units
    of average daily volume — comparable across stocks of very different size.

    A cumulative line's level depends on where the fetch window started, but
    its change over a fixed window does not, so live (2y) and backtest (max)
    agree.
    """
    avg_vol = volume.rolling(norm_window, min_periods=norm_window).mean()
    return (line - line.shift(window)) / (window * avg_vol.replace(0, np.nan))


def weighted_relative_strength(close: pd.Series) -> pd.Series:
    """IBD-weighted multi-horizon return: 0.4·r63 + 0.2·(r126 + r189 + r252)."""
    return sum(weight * close.pct_change(horizon) for horizon, weight in RS_HORIZONS)


def add_institutional_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Append the `inst_*` columns to one ticker's OHLCV(+TA) frame.

    Uses `ta`'s `volume_obv` / `volume_adi` when present (they are part of
    add_all_ta_features' output) and computes OBV itself otherwise, so the
    function also works on bare OHLCV in tests and research scripts.
    """
    out = df.copy()
    if out.empty:
        return out
    close, volume = out["close"].astype(float), out["volume"].astype(float)

    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    out["inst_sma50"] = sma50
    out["inst_sma200"] = sma200
    out["inst_sma200_slope20"] = sma200 / sma200.shift(20) - 1.0
    trend = (close > sma50) & (sma50 > sma200) & (out["inst_sma200_slope20"] > 0)
    # NaN, not False, during warmup — "unknown" must not look like "no".
    out["inst_trend_ok"] = trend.astype(float).where(sma200.notna() & out["inst_sma200_slope20"].notna())
    out["inst_adv_usd"] = (close * volume).rolling(20, min_periods=20).mean()

    out["inst_vwap50"] = rolling_vwap(out, 50)
    out["inst_vwap50_dist"] = close / out["inst_vwap50"] - 1.0
    out["inst_vwap_qtd"] = quarter_anchored_vwap(out)
    out["inst_vwap_qtd_dist"] = close / out["inst_vwap_qtd"] - 1.0

    out["inst_ud_vol_ratio50"] = up_down_volume_ratio(out, 50)
    out["inst_accum_net25"] = net_accumulation_days(out, 25)
    out["inst_vol_spike"] = volume / volume.rolling(50, min_periods=50).mean().replace(0, np.nan)

    if "volume_obv" in out.columns:
        obv = out["volume_obv"].astype(float)
    else:
        obv = (np.sign(close.diff()).fillna(0.0) * volume).cumsum()
    out["inst_obv_slope20"] = volume_line_slope(obv, volume)
    if "volume_adi" in out.columns:
        out["inst_ad_slope20"] = volume_line_slope(out["volume_adi"].astype(float), volume)

    out["inst_rs"] = weighted_relative_strength(close)
    out["inst_ret20"] = close.pct_change(20)
    return out


def _finite(row: pd.Series, key: str) -> float | None:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def passes_mandate(row: pd.Series, min_adv_usd: float = 50e6, min_price: float = 10.0) -> bool:
    """
    The institutional buy filter: enough history, liquid, not a penny stock,
    and in a confirmed uptrend (close > SMA50 > SMA200, SMA200 rising).

    Any missing or warmup (NaN) input fails — never a silent pass.
    """
    price = _finite(row, "close")
    adv = _finite(row, "inst_adv_usd")
    trend = _finite(row, "inst_trend_ok")
    rs = _finite(row, "inst_rs")
    if price is None or adv is None or trend is None or rs is None:
        return False
    return price >= min_price and adv >= min_adv_usd and trend >= 1.0


def cross_sectional_z(values: dict[str, float | None], clip: float = 3.0) -> dict[str, float]:
    """
    z-score each value against the others, winsorised at ±clip.

    Missing or non-finite values score 0 (neutral) rather than being dropped,
    so one absent component cannot knock a stock out of the ranking. A
    cross-section with no dispersion scores everyone 0.
    """
    finite = {k: float(v) for k, v in values.items() if v is not None and math.isfinite(float(v))}
    if len(finite) < 2:
        return dict.fromkeys(values, 0.0)
    arr = np.array(list(finite.values()))
    mean, std = float(arr.mean()), float(arr.std())
    if std <= 0 or not math.isfinite(std):
        return dict.fromkeys(values, 0.0)
    return {k: float(np.clip((finite[k] - mean) / std, -clip, clip)) if k in finite else 0.0 for k in values}


def accumulation_score(
    rows: dict[str, pd.Series],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Composite institutional-accumulation score for each ticker in `rows`.

    Weighted mean of cross-sectional z-scores of: relative strength, up/down
    volume ratio (log), Chaikin Money Flow, OBV slope, net accumulation days,
    distance above the quarter-anchored VWAP, and a divergence term
    z(OBV slope) − z(20-day return) that rewards OBV rising while price lags.

    Rank only the names that already passed `passes_mandate`: z-scores are
    relative to whoever is in `rows`.
    """
    weights = weights or DEFAULT_SCORE_WEIGHTS
    if not rows:
        return {}

    def column(key: str) -> dict[str, float | None]:
        return {t: _finite(r, key) for t, r in rows.items()}

    ud = {t: (math.log(v) if v is not None and v > 0 else None) for t, v in column("inst_ud_vol_ratio50").items()}
    z: dict[str, dict[str, float]] = {
        "inst_rs": cross_sectional_z(column("inst_rs")),
        "inst_ud_vol_ratio50": cross_sectional_z(ud),
        "volume_cmf": cross_sectional_z(column("volume_cmf")),
        "inst_obv_slope20": cross_sectional_z(column("inst_obv_slope20")),
        "inst_accum_net25": cross_sectional_z(column("inst_accum_net25")),
        "inst_vwap_qtd_dist": cross_sectional_z(column("inst_vwap_qtd_dist")),
    }
    ret20 = cross_sectional_z(column("inst_ret20"))
    z["divergence"] = {t: z["inst_obv_slope20"][t] - ret20[t] for t in rows}

    total_weight = sum(w for k, w in weights.items() if k in z) or 1.0
    return {t: sum(w * z[k][t] for k, w in weights.items() if k in z) / total_weight for t in rows}
