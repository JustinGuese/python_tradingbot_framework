"""TradingView trend signals shared contract for daily-bar cross-asset strategies.

Shared contract (Step S0 of PLAN-tvdaily.md):
- UNIVERSE_WIDE: cross-asset ETF universe with daily history to ~2007.
- signal_flags: causal long-flag per bar for the four TradingView trend signals:
  'supertrend', 'ultimate_ma', 'alphatrend', 'slingshot_chandelier'.
- vote_weights: ensemble voting weight allocation with per-ticker cap (0.2), gross_cap,
  never includes 'USD'.
- apply_min_hold: hysteresis filter ensuring minimum holding periods between state flips.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tradingbot.utils.tv_alphatrend import alphatrend
from tradingbot.utils.tv_core import to_ohlcv
from tradingbot.utils.tv_slingshot_chandelier import chandelier_exit, slingshot
from tradingbot.utils.tv_supertrend import htf_supertrend_direction, supertrend
from tradingbot.utils.tv_ultimate_ma import SUPPORTED_KINDS as UMA_SUPPORTED_KINDS, ultimate_ma

# Cross-asset ETF universe with daily history back to ~2007 (no leveraged/inverse).
UNIVERSE_WIDE: list[str] = [
    "SPY",
    "QQQ",
    "IWM",
    "EFA",
    "EEM",
    "XLE",
    "XLF",
    "XLK",
    "XLV",
    "XLP",
    "XLU",
    "GLD",
    "SLV",
    "DBC",
    "USO",
    "TLT",
    "IEF",
    "HYG",
    "UUP",
]

SUPPORTED_KINDS: tuple[str, ...] = (
    "supertrend",
    "ultimate_ma",
    "alphatrend",
    "slingshot_chandelier",
)

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "supertrend": {"period": 10, "mult": 3.0},
    "ultimate_ma": {"kind": "hma", "length": 20, "slope_bars": 1},
    "alphatrend": {"period": 14, "mult": 1.0},
    "slingshot_chandelier": {
        "fast": 34,
        "slow": 50,
        "ch_period": 22,
        "ch_mult": 3.0,
        "max_extension_atr": 2.0,
        "atr_period": 14,
    },
}


def signal_flags(
    ohlcv: pd.DataFrame,
    kind: str,
    params: dict[str, Any] | None = None,
) -> pd.Series:
    """Calculate causal boolean long-flags per bar for a TradingView trend signal.

    Parameters
    ----------
    ohlcv : pd.DataFrame
        OHLCV price frame (open, high, low, close, volume).
    kind : str
        One of {'supertrend', 'ultimate_ma', 'alphatrend', 'slingshot_chandelier'}.
    params : dict, optional
        Indicator-specific hyperparameters. If None or omitted, default parameters
        from DEFAULT_PARAMS are used.

    Returns
    -------
    pd.Series
        Boolean Series on the index of ``ohlcv`` where True indicates a long position.
    """
    kind_clean = str(kind).lower().strip().replace("-", "_")
    if kind_clean not in SUPPORTED_KINDS:
        raise ValueError(
            f"Unknown signal kind '{kind}'. Expected one of: {', '.join(SUPPORTED_KINDS)}"
        )

    if ohlcv is None or ohlcv.empty:
        return pd.Series(dtype=bool, index=ohlcv.index if ohlcv is not None else None, name=kind_clean)

    p = dict(DEFAULT_PARAMS.get(kind_clean, {}))
    if params:
        p.update(params)

    clean_ohlcv = to_ohlcv(ohlcv)
    if clean_ohlcv.empty:
        return pd.Series(False, index=ohlcv.index, dtype=bool, name=kind_clean)

    if kind_clean == "supertrend":
        period = int(p.get("period", 10))
        mult = float(p.get("mult", 3.0))
        st = supertrend(clean_ohlcv, period=period, mult=mult)
        flag = (st["direction"] == 1.0)
        htf = p.get("htf")
        if htf:
            htf_dir = htf_supertrend_direction(clean_ohlcv, rule=htf, period=period, mult=mult)
            flag = flag & (htf_dir == 1.0)
        flag = flag.fillna(False).astype(bool)

    elif kind_clean == "ultimate_ma":
        ma_kind = str(p.get("ma_type", p.get("ma_kind", p.get("kind", "hma")))).lower().strip()
        if ma_kind not in UMA_SUPPORTED_KINDS:
            ma_kind = "hma"
        length = int(p.get("length", 20))
        slope_bars = int(p.get("slope_bars", 1))
        uma = ultimate_ma(clean_ohlcv, kind=ma_kind, length=length, slope_bars=slope_bars)
        flag = (uma["direction"] == 1.0)
        htf = p.get("htf") or p.get("htf_rule")
        if htf:
            uma_htf = ultimate_ma(clean_ohlcv, kind=ma_kind, length=length, slope_bars=slope_bars, htf_rule=htf)
            flag = flag & (uma_htf["direction"] == 1.0)
        flag = flag.fillna(False).astype(bool)

    elif kind_clean == "alphatrend":
        period = int(p.get("period", 14))
        mult = float(p.get("mult", 1.0))
        use_mfi = p.get("use_mfi", None)
        at = alphatrend(clean_ohlcv, period=period, mult=mult, use_mfi=use_mfi)
        buys = at["buy"].to_numpy(dtype=bool)
        sells = at["sell"].to_numpy(dtype=bool)
        n = len(clean_ohlcv)
        held = np.zeros(n, dtype=bool)
        cur = False
        for i in range(n):
            if buys[i]:
                cur = True
            elif sells[i]:
                cur = False
            held[i] = cur
        flag = pd.Series(held, index=clean_ohlcv.index, dtype=bool)

    elif kind_clean == "slingshot_chandelier":
        fast = int(p.get("fast", 34))
        slow = int(p.get("slow", 50))
        ch_period = int(p.get("ch_period", p.get("period", 22)))
        ch_mult = float(p.get("ch_mult", p.get("mult", 3.0)))
        max_extension_atr = float(p.get("max_extension_atr", 2.0))
        atr_period = int(p.get("atr_period", 14))

        ss = slingshot(
            clean_ohlcv,
            fast=fast,
            slow=slow,
            max_extension_atr=max_extension_atr,
            atr_period=atr_period,
        )
        ce = chandelier_exit(
            clean_ohlcv,
            period=ch_period,
            mult=ch_mult,
        )

        closes = clean_ohlcv["close"].to_numpy(dtype=float)
        long_stops = ce["long_stop"].to_numpy(dtype=float)
        bull = ss["bull_trend"].to_numpy(dtype=bool)
        bear = ss["bear_trend"].to_numpy(dtype=bool)
        pb = ss["pullback_long"].to_numpy(dtype=bool)
        oe = ss["overextended"].to_numpy(dtype=bool)

        n = len(clean_ohlcv)
        in_pos = np.zeros(n, dtype=bool)
        cur = False
        for i in range(n):
            close_i = closes[i]
            stop_i = long_stops[i]
            bear_i = bear[i]
            bull_i = bull[i]
            pb_i = pb[i]
            oe_i = oe[i]

            exit_cond = bear_i or (not np.isnan(stop_i) and close_i < stop_i)
            entry_cond = bull_i and pb_i and (not oe_i)

            if cur:
                if exit_cond:
                    cur = False
            else:
                if entry_cond and not exit_cond:
                    cur = True
            in_pos[i] = cur
        flag = pd.Series(in_pos, index=clean_ohlcv.index, dtype=bool)

    # Reindex onto original ohlcv index
    if len(flag) == len(ohlcv) and (flag.index.equals(ohlcv.index) or not isinstance(ohlcv.index, pd.DatetimeIndex)):
        return pd.Series(flag.to_numpy(dtype=bool), index=ohlcv.index, dtype=bool, name=kind_clean)

    out = pd.Series(False, index=ohlcv.index, dtype=bool, name=kind_clean)
    common_idx = flag.index.intersection(ohlcv.index)
    if len(common_idx) > 0:
        out.loc[common_idx] = flag.loc[common_idx].to_numpy(dtype=bool)
    return out


def vote_weights(
    flags: dict[str, dict[str, bool]],
    min_votes: int,
    gross_cap: float = 1.0,
) -> dict[str, float]:
    """Calculate portfolio weights via ensemble voting across trend signals.

    Allocates equal weight among tickers with at least ``min_votes`` long signals.
    Enforces a per-ticker cap of 0.20 and ensures the sum of weights does not exceed ``gross_cap``.
    Returns an empty dict ``{}`` if no tickers qualify or if ``gross_cap <= 0``.
    Never includes a "USD" key.

    Parameters
    ----------
    flags : dict[str, dict[str, bool]]
        Mapping of ticker -> {kind: is_long_flag}.
    min_votes : int
        Minimum number of long signal votes required for a ticker to qualify.
    gross_cap : float, default 1.0
        Maximum total portfolio gross weight.

    Returns
    -------
    dict[str, float]
        Target weights per qualified ticker (excluding "USD").
    """
    if not flags or gross_cap <= 0.0 or min_votes < 1:
        return {}

    qualifying: list[str] = []
    for ticker, votes_dict in flags.items():
        if ticker == "USD":
            continue
        if not votes_dict:
            continue
        n_votes = sum(1 for v in votes_dict.values() if bool(v))
        if n_votes >= min_votes:
            qualifying.append(ticker)

    if not qualifying:
        return {}

    k = len(qualifying)
    # Equal weight among qualifying tickers, per-ticker cap 0.2, sum <= gross_cap
    w = min(0.2, gross_cap / k)
    if w <= 0.0:
        return {}

    return {ticker: float(w) for ticker in sorted(qualifying)}


def apply_min_hold(flag: pd.Series, bars: int) -> pd.Series:
    """Apply a minimum holding period to a boolean flag series (hysteresis).

    The output follows ``flag`` but may only flip state if at least ``bars``
    bars have passed since its last flip. The first bar sets the state to
    ``flag.iloc[0]`` and counts as a flip. ``bars <= 1`` returns ``flag``
    unchanged (as bool series).

    Parameters
    ----------
    flag : pd.Series
        Boolean state series over time.
    bars : int
        Minimum bars between state flips.

    Returns
    -------
    pd.Series
        Hysteresis-filtered boolean Series on the same index.
    """
    if flag is None or len(flag) == 0:
        return pd.Series(dtype=bool, index=flag.index if flag is not None else None)
    if bars <= 1:
        return flag.fillna(False).astype(bool)

    values = [bool(v) if pd.notna(v) else False for v in flag]
    state = values[0]
    last_flip = 0
    out = [state]
    for i in range(1, len(values)):
        if values[i] != state and i - last_flip >= bars:
            state = values[i]
            last_flip = i
        out.append(state)
    return pd.Series(out, index=flag.index, dtype=bool)
