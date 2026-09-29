"""
Daily features from `prediction_market_snapshots`, point-in-time.

`feature_frame(bar_dates)` returns one row per trading bar. The value on bar D
only uses prices observed at or before D's close. A snapshot covering Eastern day C
is observed at midnight after C (Kalshi) or 20:00 ET on C (Polymarket), which is
after C's 16:00 close in both cases, so bar D sees covered days C <= D-1 and
never D itself. That one-day shift is the whole look-ahead guard; getting it
wrong would hand the backtest the market's reaction to D's own news.

Features (NaN where the series has no data yet):

- recession_prob       Kalshi recession, current-year contract blended into
                       next year's by day of year (the current-year contract
                       decays mechanically toward 0 as the year runs out).
- pm_recession_prob    Polymarket recession contract expiring soonest but > 30d out.
- fed_next_bps         expected change of the upper bound at the next FOMC, bp.
- fed_path_bps         expected change by the meeting nearest 6 months out, bp.
- fed_cut_next_prob    P(cut at next FOMC): KXFEDDECISION, else the KXFED ladder.
- shutdown_prob        max over open Kalshi shutdown contracts expiring within 90d;
                       0 when none is open (no deadline pending), NaN before the
                       series' first day.
- pm_shutdown_prob     same, Polymarket.
- cpi_next_mean/std    m/m CPI distribution for the next release, from its ladder
                       (cpi_next_days: calendar days until that release).
- u3_next_mean/std     same for the unemployment rate (KXU3), u3_next_days.
- cpi_std_z, u3_std_z  log(std) z-scored against the trailing year within the same
                       days-to-release bucket; event_std_z = their max. How unusually
                       uncertain the market is about the next print (option_IndexVolBot).

Ladders are "above X" survival functions; `ladder_moments` makes them monotone
before taking moments (last prices of neighbouring strikes can cross).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .db import PredictionMarketSnapshot, get_db_session

STALE_DAYS = 10  # a feature older than this is NaN, not carried forward
FED_STEP = 0.25


def load_snapshots(series: list[str] | None = None, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Snapshot rows as a DataFrame (plain values, no ORM objects)."""
    cols = [
        "date",
        "venue",
        "series",
        "event_ticker",
        "market_ticker",
        "label",
        "contract_type",
        "strike",
        "strike_cap",
        "expiry",
        "prob",
        "bid",
        "ask",
        "volume",
        "open_interest",
        "result",
    ]
    with get_db_session() as session:
        query = session.query(*[getattr(PredictionMarketSnapshot, c) for c in cols])
        if series:
            query = query.filter(PredictionMarketSnapshot.series.in_(series))
        if start:
            query = query.filter(PredictionMarketSnapshot.date >= start)
        if end:
            query = query.filter(PredictionMarketSnapshot.date <= end)
        frame = pd.DataFrame(query.all(), columns=cols)
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
        frame["expiry"] = pd.to_datetime(frame["expiry"])
    return frame


def ladder_moments(strikes, survival, step: float, discrete: bool = False) -> tuple[float, float]:
    """(mean, std) of a variable from P(X > k) at ascending strikes k.

    Mass below the lowest strike sits at k_min (discrete) or k_min - step/2;
    between strikes at the next level above the lower strike (discrete: k_i + step,
    so a ladder missing a strike -- Jan 2024 listed 5.25 and 5.75 but no 5.50 --
    still puts "above 5.25, not above 5.75" at 5.50) or the midpoint; above the
    top strike at k_max + step (discrete) or + step/2.
    """
    order = np.argsort(strikes)
    k = np.asarray(strikes, dtype=float)[order]
    s = np.clip(np.asarray(survival, dtype=float)[order], 0.0, 1.0)
    s = np.minimum.accumulate(s)  # P(X > k) cannot rise with k
    if len(k) == 0:
        return np.nan, np.nan
    masses = np.concatenate([[1 - s[0]], s[:-1] - s[1:], [s[-1]]])
    if discrete:
        points = np.concatenate([[k[0]], k + step])
    else:
        points = np.concatenate([[k[0] - step / 2], (k[:-1] + k[1:]) / 2, [k[-1] + step / 2]])
    mean = float(np.dot(masses, points))
    std = float(np.sqrt(max(np.dot(masses, (points - mean) ** 2), 0.0)))
    return mean, std


def _year_of(ticker: str) -> int | None:
    m = re.search(r"-(\d{2})$", ticker) or re.search(r"(20\d{2})", ticker)
    if not m:
        return None
    y = int(m.group(1))
    return y + 2000 if y < 100 else y


def _recession(day_rows: pd.DataFrame, day: pd.Timestamp) -> float:
    by_year = {}
    for row in day_rows.itertuples():
        y = _year_of(row.market_ticker)
        if y is not None:
            by_year[y] = row.prob
    cur, nxt = by_year.get(day.year), by_year.get(day.year + 1)
    if cur is not None and nxt is not None:
        f = day.dayofyear / 366
        return (1 - f) * cur + f * nxt
    return cur if cur is not None else (nxt if nxt is not None else np.nan)


def _nearest_open(day_rows: pd.DataFrame, day: pd.Timestamp, min_days: int = 30) -> float:
    live = day_rows[day_rows["expiry"] > day + timedelta(days=min_days)]
    if live.empty:
        live = day_rows[day_rows["expiry"] > day]
    if live.empty:
        return np.nan
    return float(live.sort_values("expiry").iloc[0]["prob"])


def _max_open(day_rows: pd.DataFrame, day: pd.Timestamp, horizon_days: int = 90) -> float:
    live = day_rows[(day_rows["expiry"] > day) & (day_rows["expiry"] <= day + timedelta(days=horizon_days))]
    return float(live["prob"].max()) if not live.empty else 0.0


def _settled_levels(fed: pd.DataFrame) -> pd.Series:
    """Upper bound set at each settled KXFED meeting: the lowest 'above X' strike that resolved No."""
    settled = fed.dropna(subset=["result"]).drop_duplicates("market_ticker")
    levels = {}
    for _event, group in settled.groupby("event_ticker"):
        no = group.loc[group["result"] == "no", "strike"]
        yes = group.loc[group["result"] == "yes", "strike"]
        if not no.empty:
            levels[group["expiry"].max()] = float(no.min())
        elif not yes.empty:
            levels[group["expiry"].max()] = float(yes.max()) + FED_STEP
    return pd.Series(levels).sort_index()


def _event_mean(group: pd.DataFrame) -> float:
    ladder = group[group["contract_type"] == "close_above"].dropna(subset=["strike"])
    if ladder.empty:
        return np.nan
    return ladder_moments(ladder["strike"], ladder["prob"], FED_STEP, discrete=True)[0]


def _fed(day_rows: pd.DataFrame, day: pd.Timestamp, current: float) -> dict[str, float]:
    out = {"fed_next_bps": np.nan, "fed_path_bps": np.nan, "fed_cut_next_ladder": np.nan}
    if np.isnan(current):
        return out
    events = day_rows[day_rows["expiry"] > day + timedelta(days=1)].groupby("event_ticker")
    meetings = sorted(((g["expiry"].max(), g) for _, g in events), key=lambda x: x[0])
    if not meetings:
        return out
    next_group = meetings[0][1]
    out["fed_next_bps"] = (_event_mean(next_group) - current) * 100
    ladder = next_group[next_group["contract_type"] == "close_above"].set_index("strike")["prob"]
    cut_strike = round(current - FED_STEP, 2)
    if cut_strike in ladder.index:
        out["fed_cut_next_ladder"] = float(1 - ladder.loc[cut_strike])
    target = day + timedelta(days=182)
    far = [(abs((when - target).days), g) for when, g in meetings if 120 <= (when - day).days <= 240]
    if far:
        out["fed_path_bps"] = (_event_mean(min(far, key=lambda x: x[0])[1]) - current) * 100
    return out


def _fed_decision_cut(day_rows: pd.DataFrame, day: pd.Timestamp) -> float:
    live = day_rows[day_rows["expiry"] > day + timedelta(days=1)]
    if live.empty:
        return np.nan
    nxt = live[live["event_ticker"] == live.sort_values("expiry").iloc[0]["event_ticker"]]
    suffix = nxt["market_ticker"].str.rsplit("-", n=1).str[-1]
    total = nxt["prob"].sum()
    if total <= 0:
        return np.nan
    return float(nxt.loc[suffix.str.startswith("C"), "prob"].sum() / total)


def _next_release(day_rows: pd.DataFrame, day: pd.Timestamp, step: float) -> tuple[float, float, float]:
    """(mean, std, calendar days to expiry) of the next release's "above X" ladder."""
    ladder = day_rows[(day_rows["contract_type"] == "close_above") & (day_rows["expiry"] > day + timedelta(days=1))]
    if ladder.empty:
        return np.nan, np.nan, np.nan
    first = ladder.sort_values("expiry").iloc[0]
    nxt = ladder[ladder["event_ticker"] == first["event_ticker"]].dropna(subset=["strike"])
    if len(nxt) < 2:
        return np.nan, np.nan, np.nan
    mean, std = ladder_moments(nxt["strike"], nxt["prob"], step=step)
    return mean, std, float((first["expiry"] - day).days)


# Release uncertainty: the ladder's std narrows ~30% over the month before a
# release and moves with the regime (CPI median 0.20pp in 2022, 0.12pp in 2025),
# so it is z-scored as log(std) against the trailing year, within
# days-to-release buckets. Payrolls is left out: open interest ~10 contracts.
EVENT_SERIES = ("cpi_mom", "unemployment")
EVENT_Z_WINDOW = "365D"
EVENT_Z_MIN_OBS = 20
EVENT_DAY_BUCKETS = (7, 21)  # <=7, 8..21, >21 calendar days to the release


def _std_z(std: pd.Series, days: pd.Series) -> pd.Series:
    """Point-in-time z of log(std) vs the trailing window of the same days-to-release bucket."""
    log_std = np.log(std.where(std > 0))
    bucket = np.digitize(days.fillna(-1), EVENT_DAY_BUCKETS, right=True)
    out = pd.Series(np.nan, index=std.index)
    for b in np.unique(bucket):
        s = log_std[(bucket == b) & days.notna()].dropna()
        if s.empty:
            continue
        past = s.rolling(EVENT_Z_WINDOW, min_periods=EVENT_Z_MIN_OBS)
        mean, sd = past.mean().shift(1), past.std().shift(1)  # strictly earlier days
        out.loc[s.index] = (s - mean) / sd
    return out


def add_event_uncertainty(daily: pd.DataFrame) -> pd.DataFrame:
    """Add cpi_std_z / u3_std_z and their max, event_std_z, to a covered-day feature frame."""
    daily = daily.copy()
    zs = []
    for prefix, name in (("cpi_next", "cpi_std_z"), ("u3_next", "u3_std_z")):
        if f"{prefix}_std" in daily and f"{prefix}_days" in daily:
            daily[name] = _std_z(daily[f"{prefix}_std"], daily[f"{prefix}_days"])
            zs.append(name)
    if zs:
        daily["event_std_z"] = daily[zs].max(axis=1, skipna=True)
    return daily


def fill_gaps(snapshots: pd.DataFrame, limit: int = STALE_DAYS) -> pd.DataFrame:
    """Carry each market's last price over days it has no row, for at most `limit` days.

    Kalshi returns a daily candle only when a market traded or was quoted, so on
    a quiet day a ladder can be missing its lower strikes. The moments of what is
    left are then biased toward the strikes that did trade: 2023-12-31 read +24bp
    for a January meeting that was a certain hold. A stale last price is the
    better estimate of a strike's odds than no strike at all.
    """
    if snapshots.empty:
        return snapshots
    newest = snapshots["date"].max()
    frames = []
    for _, group in snapshots.groupby(["venue", "market_ticker"], sort=False):
        group = group.set_index("date").sort_index()
        # through gaps and past the last row, never beyond the market's close or the newest day stored
        end = min(group.index.max() + timedelta(days=limit), newest)
        expiry = group["expiry"].iloc[-1]
        if pd.notna(expiry):
            end = min(end, pd.Timestamp(expiry).normalize())
        full = pd.date_range(group.index.min(), max(end, group.index.max()), freq="D")
        if len(full) == len(group):
            frames.append(group.reset_index())
            continue
        filled = group.reindex(full).ffill(limit=limit).dropna(subset=["prob"])
        frames.append(filled.rename_axis("date").reset_index())
    return pd.concat(frames, ignore_index=True)


def daily_features(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Features indexed by the Eastern day the prices cover (NOT yet shifted)."""
    if snapshots.empty:
        return pd.DataFrame()
    snapshots = fill_gaps(snapshots)
    by_series = {key: g for key, g in snapshots.groupby("series")}  # noqa: C416
    fed_levels = _settled_levels(by_series["fed_rate"]) if "fed_rate" in by_series else pd.Series(dtype=float)
    days = pd.DatetimeIndex(sorted(snapshots["date"].unique()))
    grouped = {key: dict(tuple(g.groupby("date"))) for key, g in by_series.items()}
    first = {key: g["date"].min() for key, g in by_series.items()}
    records = []
    for day in days:
        rec: dict[str, float] = {}
        rows = {key: grouped[key].get(day) for key in grouped}
        if rows.get("recession") is not None:
            rec["recession_prob"] = _recession(rows["recession"], day)
        if rows.get("pm_recession") is not None:
            rec["pm_recession_prob"] = _nearest_open(rows["pm_recession"], day)
        if rows.get("fed_rate") is not None:
            # a meeting settled strictly before `day` is public by `day`
            known = fed_levels[fed_levels.index < day]
            current = float(known.iloc[-1]) if not known.empty else np.nan
            rec.update(_fed(rows["fed_rate"], day, current))
        if rows.get("fed_decision") is not None:
            rec["fed_cut_next_prob"] = _fed_decision_cut(rows["fed_decision"], day)
        for key, name in (("shutdown", "shutdown_prob"), ("pm_shutdown", "pm_shutdown_prob")):
            if key in first and day >= first[key]:
                r = rows.get(key)
                rec[name] = _max_open(r, day) if r is not None else 0.0
        for key, prefix in (("cpi_mom", "cpi_next"), ("unemployment", "u3_next")):
            if rows.get(key) is not None:
                rec[f"{prefix}_mean"], rec[f"{prefix}_std"], rec[f"{prefix}_days"] = _next_release(rows[key], day, 0.1)
        records.append(rec)
    frame = pd.DataFrame(records, index=days)
    if "fed_cut_next_ladder" in frame:
        cut = frame["fed_cut_next_prob"] if "fed_cut_next_prob" in frame else pd.Series(np.nan, index=frame.index)
        frame["fed_cut_next_prob"] = cut.fillna(frame.pop("fed_cut_next_ladder"))
    return add_event_uncertainty(frame)


def as_of_bars(daily: pd.DataFrame, bar_dates) -> pd.DataFrame:
    """Align covered-day features to bars: bar D sees covered days <= D-1, carried at most STALE_DAYS."""
    bars = pd.DatetimeIndex(pd.to_datetime(bar_dates)).normalize()
    if bars.tz is not None:
        bars = bars.tz_localize(None)
    if daily.empty:
        return pd.DataFrame(index=bars)
    available = daily.copy()
    available.index = available.index + timedelta(days=1)  # known from the start of the next day
    calendar = pd.date_range(available.index.min(), max(bars.max(), available.index.max()), freq="D")
    carried = available.reindex(calendar).ffill(limit=STALE_DAYS)
    return carried.reindex(bars)


def feature_frame(bar_dates, series: list[str] | None = None) -> pd.DataFrame:
    """Point-in-time features for each bar date (see module docstring)."""
    bars = pd.DatetimeIndex(pd.to_datetime(bar_dates))
    start = (bars.min() - timedelta(days=400)).date() if len(bars) else None
    return as_of_bars(daily_features(load_snapshots(series, start=start)), bars)


def event_uncertainty(day: date) -> float | None:
    """event_std_z as bar `day` sees it (prices through day-1), None when unknown."""
    frame = feature_frame([day], series=list(EVENT_SERIES))
    if "event_std_z" not in frame or frame.empty:
        return None
    value = frame["event_std_z"].iloc[-1]
    return None if pd.isna(value) else float(value)
