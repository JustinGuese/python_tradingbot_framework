"""
Event study utilities: forward returns, abnormal returns vs benchmark,
event-driven strategy backtest series, and alpha significance evaluation.

Pure functions: no DB or network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.market_calendar import session_close_utc

NEW_YORK = ZoneInfo("America/New_York")
FALLBACK_CLOSE_TIME = time(16, 0)


@lru_cache(maxsize=10000)
def _cached_session_close_utc(day: date) -> datetime:
    """Session close as aware UTC datetime, with fallback to 16:00 New York."""
    close_dt = session_close_utc(day)
    if close_dt is not None:
        return close_dt
    return datetime.combine(day, FALLBACK_CLOSE_TIME, tzinfo=NEW_YORK).astimezone(UTC)


def _to_utc_timestamp(ts: pd.Timestamp | datetime | str) -> pd.Timestamp:
    """Ensure timestamp is tz-aware UTC pd.Timestamp."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize(UTC)
    return t.tz_convert(UTC)


def _bar_close_utc(bar_ts: pd.Timestamp | datetime) -> pd.Timestamp:
    """Determine the session close UTC timestamp for a price bar."""
    t = pd.Timestamp(bar_ts)

    # If the bar already has an explicit time of day, respect it.
    if t.time() != time(0, 0):
        if t.tzinfo is None:
            return t.tz_localize(UTC)
        return t.tz_convert(UTC)

    # Daily bar timestamped at midnight: session date is t.date().
    day = t.date()
    return pd.Timestamp(_cached_session_close_utc(day))


def _find_entry_bar_idx(prices: pd.Series, event_ts_utc: pd.Timestamp) -> int | None:
    """
    Find index of the first daily close strictly after the event timestamp.
    Returns None if no such bar exists.
    """
    if prices.empty:
        return None

    for i in range(len(prices)):
        close_utc = _bar_close_utc(prices.index[i])
        if close_utc > event_ts_utc:
            return i
    return None


def forward_returns(
    prices: pd.Series,
    event_ts: pd.Timestamp | datetime | str,
    horizons_days: Sequence[int],
) -> dict[int, float]:
    """
    Calculate forward returns from entry close to `h` sessions later.

    entry = first daily close STRICTLY AFTER the event timestamp's session (no look-ahead).
    return from entry close to close `h` sessions later; NaN if not enough bars.
    """
    out: dict[int, float] = dict.fromkeys(horizons_days, np.nan)
    if prices is None or prices.empty:
        return out

    s = prices.dropna()
    if s.index.has_duplicates:
        s = s[~s.index.duplicated(keep="last")]
    s = s.sort_index()
    if s.empty:
        return out

    event_ts_utc = _to_utc_timestamp(event_ts)
    entry_idx = _find_entry_bar_idx(s, event_ts_utc)
    if entry_idx is None:
        return out

    entry_price = float(s.iloc[entry_idx])
    if not np.isfinite(entry_price) or entry_price <= 0:
        return out

    n_bars = len(s)
    for h in horizons_days:
        target_idx = entry_idx + h
        if 0 <= target_idx < n_bars:
            target_price = float(s.iloc[target_idx])
            if np.isfinite(target_price) and target_price > 0:
                out[h] = (target_price - entry_price) / entry_price
            else:
                out[h] = np.nan
        else:
            out[h] = np.nan

    return out


def _calc_beta(
    stock_series: pd.Series,
    bench_series: pd.Series,
    event_date: date,
    window_bars: int = 250,
    min_obs: int = 20,
) -> float:
    """
    Beta from up to 250 bars pre-event, fallback 1.0.
    """
    stock_s = stock_series.dropna().sort_index()
    bench_s = bench_series.dropna().sort_index()

    s_idx = pd.to_datetime([pd.Timestamp(t).date() for t in stock_s.index])
    b_idx = pd.to_datetime([pd.Timestamp(t).date() for t in bench_s.index])

    stock_s = pd.Series(stock_s.to_numpy(dtype=float), index=s_idx)
    bench_s = pd.Series(bench_s.to_numpy(dtype=float), index=b_idx)

    stock_rets = stock_s.pct_change().dropna()
    bench_rets = bench_s.pct_change().dropna()

    joint = pd.concat({"s": stock_rets, "b": bench_rets}, axis=1, join="inner").sort_index()
    joint = joint.replace([np.inf, -np.inf], np.nan).dropna()

    event_dt = pd.Timestamp(event_date)
    pre = joint[joint.index < event_dt]
    if len(pre) < min_obs:
        return 1.0

    pre_window = pre.tail(window_bars)
    var_b = float(pre_window["b"].var())
    if not np.isfinite(var_b) or var_b <= 0:
        return 1.0

    cov = float(pre_window["s"].cov(pre_window["b"]))
    beta = cov / var_b
    if not np.isfinite(beta):
        return 1.0
    return float(beta)


def abnormal_returns(
    events: pd.DataFrame,
    prices: dict[str, pd.Series],
    bench: pd.Series,
    horizons: Sequence[int],
) -> pd.DataFrame:
    """
    Compute abnormal returns for events across horizons.

    events cols: ts, symbol, label (optional).
    Output: one row per event/horizon with raw, bench, abnormal (= raw - beta*bench,
    beta from 250d pre-event, fallback 1.0).
    """
    cols = ["ts", "symbol", "horizon", "raw", "bench", "abnormal", "beta"]
    if events is None or events.empty:
        if events is not None and "label" in events.columns:
            cols.append("label")
        return pd.DataFrame(columns=cols)

    ts_col = "ts" if "ts" in events.columns else "timestamp"
    if ts_col not in events.columns or "symbol" not in events.columns:
        raise ValueError(f"events must contain '{ts_col}' and 'symbol' columns")

    rows = []
    has_label = "label" in events.columns

    for _, ev in events.iterrows():
        ev_ts = ev[ts_col]
        symbol = str(ev["symbol"])
        label_val = ev["label"] if has_label else None

        stock_prices = prices.get(symbol)
        stock_fwd = (
            forward_returns(stock_prices, ev_ts, horizons)
            if stock_prices is not None
            else dict.fromkeys(horizons, np.nan)
        )
        bench_fwd = forward_returns(bench, ev_ts, horizons) if bench is not None else dict.fromkeys(horizons, np.nan)

        ev_ts_utc = _to_utc_timestamp(ev_ts)
        ev_date = ev_ts_utc.astimezone(NEW_YORK).date()

        beta = _calc_beta(stock_prices, bench, ev_date) if stock_prices is not None and bench is not None else 1.0

        for h in horizons:
            raw_ret = stock_fwd.get(h, np.nan)
            bench_ret = bench_fwd.get(h, np.nan)
            abnormal_ret = np.nan if np.isnan(raw_ret) or np.isnan(bench_ret) else float(raw_ret - beta * bench_ret)

            row_data = {
                "ts": ev_ts,
                "symbol": symbol,
                "horizon": h,
                "raw": raw_ret,
                "bench": bench_ret,
                "abnormal": abnormal_ret,
                "beta": beta,
            }
            if has_label:
                row_data["label"] = label_val

            for col in events.columns:
                if col not in row_data and col != ts_col:
                    row_data[col] = ev[col]

            rows.append(row_data)

    return pd.DataFrame(rows)


def _normalize_price_series(s: pd.Series) -> pd.Series:
    """Clean and normalize a price series with unique, sorted DatetimeIndex."""
    s = s.dropna()
    if s.index.has_duplicates:
        s = s[~s.index.duplicated(keep="last")]
    idx = pd.to_datetime([pd.Timestamp(t).date() for t in s.index])
    clean = pd.Series(s.to_numpy(dtype=float), index=idx)
    return clean.sort_index()


def event_strategy_returns(
    signals: pd.DataFrame,
    prices: dict[str, pd.Series],
    hold_days: int,
) -> pd.Series:
    """
    Daily return series of an equal-weight book that holds each signalled symbol
    for hold_days after entry, cash otherwise.
    """
    if prices is None or not prices:
        return pd.Series(dtype=float)

    clean_prices = {sym: _normalize_price_series(p) for sym, p in prices.items() if p is not None and not p.empty}
    clean_prices = {sym: p for sym, p in clean_prices.items() if len(p) >= 2}
    if not clean_prices:
        return pd.Series(dtype=float)

    # Master timeline of all session dates
    all_dates = sorted(set().union(*(set(p.index) for p in clean_prices.values())))
    if len(all_dates) < 2:
        return pd.Series(dtype=float)

    return_dates = all_dates[1:]

    if hold_days <= 0 or signals is None or signals.empty:
        return pd.Series(0.0, index=pd.DatetimeIndex(return_dates))

    ts_col = "ts" if "ts" in signals.columns else "timestamp"
    if ts_col not in signals.columns or "symbol" not in signals.columns:
        raise ValueError(f"signals must contain '{ts_col}' and 'symbol' columns")

    # Map each return date to a list of returns from held positions
    active_returns_by_date: dict[pd.Timestamp, dict[str, float]] = {d: {} for d in return_dates}

    for _, sig in signals.iterrows():
        symbol = str(sig["symbol"])
        if symbol not in clean_prices:
            continue

        p_series = clean_prices[symbol]
        sig_ts_utc = _to_utc_timestamp(sig[ts_col])
        entry_idx = _find_entry_bar_idx(p_series, sig_ts_utc)
        if entry_idx is None:
            continue

        direction = float(sig["direction"]) if "direction" in sig else 1.0

        # Holding period: subsequent hold_days sessions
        start_hold_idx = entry_idx + 1
        end_hold_idx = min(start_hold_idx + hold_days, len(p_series))

        for j in range(start_hold_idx, end_hold_idx):
            held_date = p_series.index[j]
            prev_price = float(p_series.iloc[j - 1])
            curr_price = float(p_series.iloc[j])
            if prev_price > 0 and np.isfinite(curr_price):
                daily_ret = ((curr_price - prev_price) / prev_price) * direction
                if held_date in active_returns_by_date:
                    active_returns_by_date[held_date][symbol] = daily_ret

    # Compute daily equal-weight book return
    daily_rets = []
    for d in return_dates:
        held_dict = active_returns_by_date[d]
        if not held_dict:
            daily_rets.append(0.0)
        else:
            daily_rets.append(float(np.mean(list(held_dict.values()))))

    return pd.Series(daily_rets, index=pd.DatetimeIndex(return_dates), dtype=float)


def judge(strategy_returns: pd.Series, bench_returns: pd.Series) -> dict:
    """
    Evaluate strategy returns against benchmark.

    Uses tradingbot.utils.alpha_report.alpha_stats; also returns alpha/t for
    first and second half of the sample and a bool `passes` (t>=2, both halves alpha>0, beta<0.5).
    None stats -> passes False.
    """
    empty_result = {
        "passes": False,
        "alpha": None,
        "t": None,
        "beta": None,
        "corr": None,
        "n": 0,
        "alpha_h1": None,
        "t_h1": None,
        "alpha_h2": None,
        "t_h2": None,
        "h1_alpha": None,
        "h1_t": None,
        "h2_alpha": None,
        "h2_t": None,
    }

    if strategy_returns is None or bench_returns is None:
        return empty_result

    # Align on index
    s_clean = strategy_returns.dropna()
    b_clean = bench_returns.dropna()

    s_idx = pd.to_datetime([pd.Timestamp(t).date() for t in s_clean.index])
    b_idx = pd.to_datetime([pd.Timestamp(t).date() for t in b_clean.index])

    s_clean = pd.Series(s_clean.to_numpy(dtype=float), index=s_idx)
    b_clean = pd.Series(b_clean.to_numpy(dtype=float), index=b_idx)

    s_clean = s_clean[~s_clean.index.duplicated(keep="last")]
    b_clean = b_clean[~b_clean.index.duplicated(keep="last")]

    rets = pd.concat({"p": s_clean, "b": b_clean}, axis=1, join="inner").sort_index()
    rets = rets.replace([np.inf, -np.inf], np.nan).dropna()

    if len(rets) < 6:
        res = dict(empty_result)
        res["n"] = len(rets)
        return res

    full_stats = alpha_stats(rets["p"], rets["b"])
    if full_stats is None:
        res = dict(empty_result)
        res["n"] = len(rets)
        return res

    mid = len(rets) // 2
    h1 = rets.iloc[:mid]
    h2 = rets.iloc[mid:]

    stats_h1 = alpha_stats(h1["p"], h1["b"])
    stats_h2 = alpha_stats(h2["p"], h2["b"])

    passes = False
    if stats_h1 is not None and stats_h2 is not None:
        passes = bool(full_stats.t >= 2.0 and stats_h1.alpha > 0.0 and stats_h2.alpha > 0.0 and full_stats.beta < 0.5)

    return {
        "passes": passes,
        "alpha": full_stats.alpha,
        "t": full_stats.t,
        "beta": full_stats.beta,
        "corr": full_stats.corr,
        "n": full_stats.n,
        "alpha_h1": stats_h1.alpha if stats_h1 else None,
        "t_h1": stats_h1.t if stats_h1 else None,
        "alpha_h2": stats_h2.alpha if stats_h2 else None,
        "t_h2": stats_h2.t if stats_h2 else None,
        "h1_alpha": stats_h1.alpha if stats_h1 else None,
        "h1_t": stats_h1.t if stats_h1 else None,
        "h2_alpha": stats_h2.alpha if stats_h2 else None,
        "h2_t": stats_h2.t if stats_h2 else None,
    }
