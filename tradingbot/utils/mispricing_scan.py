"""
The vol-mispricing scan: every name priced in vol terms against a forward
forecast, and only what survives every filter reported.

Per name, on the expiry nearest the target DTE:

- "vrp": ATM implied vol against a Yang-Zhang HAR forecast of the vol to
  expiry (earnings-reaction days excluded, so it is diffusion vol). The raw
  gap is NOT the signal: implied sits above realized most of the time (the
  variance risk premium), so "IV > forecast" says sell almost every day. The
  signal is the z-score of today's gap against the name's own history of it.
  That history is vol_surface.vrp_30 (30-day ATM IV minus a close-to-close HAR
  forecast), and today's value is measured the same way so the two compare.
  SPY and QQQ have a long proxy history from day one: 0.85 x ^VIX and
  0.90 x ^VXN (the ATM/index ratios calibrated on 2026-09-25) minus the same
  HAR forecast, back to 2001. Single names score only once 60 days of their
  own surface exist.
- "svi": strikes off an arbitrage-free SVI fit by more than half their own
  spread in vol terms (options.svi_surface_fit). Used to pick strikes, never
  traded on their own.
- "event": the implied earnings move (two expiries) against the historical
  RMS move, for names reporting within 10 days. EarningsCrushBot trades it.
- "parity": strike pairs outside the American put-call band at bid/ask. Bad
  data (stale quotes, borrow, dividends), excluded from the rest, never traded.

snapshot_scan writes the whole universe's list once a day after the capture
(from stored quotes, no refetch); the bots rescan their shortlist live.
"""

import logging
import math
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils import vol_estimators as ve
from tradingbot.utils import vol_surface as vs
from tradingbot.utils.db import MispricingScanRow, get_db_session
from tradingbot.utils.option_rules import NameVol, business_days, earnings_clear

logger = logging.getLogger(__name__)

# Index proxies for the z-score history: (vol index, ATM IV / index ratio).
INDEX_PROXIES = {"SPY": ("^VIX", 0.85), "QQQ": ("^VXN", 0.90)}
SURFACE_HORIZON = vs.FAIR_HORIZON_DAYS


# --------------------------------------------------------------------------- #
# Price history                                                               #
# --------------------------------------------------------------------------- #


def load_ohlc(symbols: Iterable[str], period: str = "3y") -> dict[str, pd.DataFrame]:
    """Daily OHLC (lower-case columns, tz-naive dates) per symbol, one batched download."""
    import yfinance as yf

    symbols = sorted(set(symbols))
    data = yf.download(symbols, period=period, auto_adjust=True, progress=False, group_by="ticker")
    out = {}
    for s in symbols:
        try:
            frame = data[s] if isinstance(data.columns, pd.MultiIndex) else data
            frame = frame[["Open", "High", "Low", "Close"]].dropna().rename(columns=str.lower)
            frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
            if len(frame):
                out[s] = frame
        except KeyError:
            logger.warning("no price history for %s", s)
    return out


def yz_fair_vol(ohlc: pd.DataFrame, horizon_days: int, exclude: Iterable | None = None) -> float:
    """Yang-Zhang HAR forecast of annualised vol over the next `horizon_days` sessions."""
    return ve.har_forecast_from_daily_variance(ve.yang_zhang_daily_variance(ohlc), horizon_days, exclude=exclude)


def surface_style_vrp(iv: float | None, close: pd.Series) -> float | None:
    """ATM IV minus the close-to-close 21-day HAR forecast: how vol_surface.vrp_30 is measured."""
    if iv is None or close is None or len(close) < 60:
        return None
    return iv - om.har_rv_forecast(om.log_returns(close), SURFACE_HORIZON)


def index_proxy_history(underlying: str, close: pd.Series, lookback: int = 252) -> pd.Series:
    """For SPY/QQQ: ratio x vol index - HAR forecast, per day of the last `lookback` sessions."""
    import yfinance as yf

    symbol, ratio = INDEX_PROXIES[underlying]
    idx = yf.download(symbol, period="3y", auto_adjust=True, progress=False)["Close"]
    if isinstance(idx, pd.DataFrame):
        idx = idx.iloc[:, 0]
    idx.index = pd.DatetimeIndex(idx.index).tz_localize(None).normalize()
    close = close.copy()
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    returns = om.log_returns(close)
    out = {}
    for ts in close.index[-lookback:]:
        if ts in idx.index:
            out[ts] = ratio * float(idx.loc[ts]) / 100 - om.har_rv_forecast(returns.loc[:ts], SURFACE_HORIZON)
    return pd.Series(out, dtype=float)


def vrp_history(
    underlying: str, close: pd.Series | None = None, as_of: date | None = None, own: pd.Series | None = None
) -> pd.Series:
    """
    The name's own vrp_30 history, or for SPY/QQQ the longer of it and the index
    proxy. `own` skips the vol_surface read (a replay passes its cached copy).
    """
    if own is None:
        own = vs.surface_history(underlying, "vrp_30", 400, as_of)
    if underlying in INDEX_PROXIES and close is not None and len(own) < 252:
        try:
            proxy = index_proxy_history(underlying, close)
            if len(proxy) > len(own):
                return proxy
        except Exception as exc:
            logger.warning("%s proxy vrp history unavailable: %s", underlying, exc)
    return own


# --------------------------------------------------------------------------- #
# One name, live                                                              #
# --------------------------------------------------------------------------- #


def live_name_vol(
    underlying: str, target_dte: int, ohlc: pd.DataFrame | None, today: date | None = None, min_obs: int = 60
) -> tuple[NameVol, options.ChainView | None]:
    """
    A name's IV, forecast and z-score on the expiry a bot would trade, from its live chain.

    fair: Yang-Zhang HAR over the sessions to expiry, earnings-reaction days
    excluded. z: surface-style vrp against the name's history (None below
    min_obs). earnings_clear: no report between today and expiry.
    """
    today = today or options.utc_today()
    try:
        view = options.load_chain(underlying, target_dte, today=today)
    except Exception as exc:
        logger.warning("%s: no chain (%s)", underlying, exc)
        return NameVol(underlying, None, None, False), None
    return name_vol_from_view(view, ohlc, today, min_obs), view


def name_vol_from_view(
    view: options.ChainView,
    ohlc: pd.DataFrame | None,
    today: date,
    min_obs: int = 60,
    events: list | None = None,
    next_earnings: date | bool | None = False,
    history: pd.Series | None = None,
) -> NameVol:
    """
    live_name_vol on a chain already in hand (live, replayed or synthetic).
    `events` / `next_earnings` / `history` (the vrp_30 history) default to the
    DB readers; pass them to read from elsewhere (False means "look it up").
    """
    underlying = view.underlying
    iv = options.atm_iv(view)
    fair, z = None, None
    if ohlc is not None and len(ohlc) >= 60:
        events = options.earnings_events(underlying) if events is None else events
        reactions = om.earnings_reaction_returns(ohlc["close"], [d for d, _ in events if d < today], dict(events))
        horizon = max(business_days(today, view.expiry), 1)
        fair = yz_fair_vol(ohlc, horizon, exclude=reactions.index)
        current = surface_style_vrp(iv, ohlc["close"])
        history = vrp_history(underlying, ohlc["close"], today) if history is None else history
        if current is not None:
            z = ve.vrp_zscore(history.to_numpy(), current, min_obs=min_obs)
    if next_earnings is False:
        next_earnings = options.next_earnings_date(underlying, today)
    clear = earnings_clear(next_earnings, view.expiry, today)
    return NameVol(underlying, iv, fair, clear, z)


# --------------------------------------------------------------------------- #
# The daily scan over stored quotes                                           #
# --------------------------------------------------------------------------- #


def _view_from_quotes(underlying: str, frame: pd.DataFrame, day: date, target_dte: int) -> options.ChainView | None:
    frame = frame.copy()
    frame["expiry"] = pd.to_datetime(frame["expiration"]).dt.date
    expiries = sorted(e for e in frame["expiry"].unique() if (e - day).days >= 7)
    if not expiries:
        return None
    expiry = min(expiries, key=lambda e: abs((e - day).days - target_dte))
    one = frame[frame["expiry"] == expiry].reset_index(drop=True)
    spot = float(pd.to_numeric(one["underlying_price"], errors="coerce").dropna().iloc[-1])
    live = bool((pd.to_numeric(one["bid"], errors="coerce").fillna(0) > 0).any())
    return options.ChainView(underlying, expiry, one, spot, live, day)


def scan_rows(
    underlying: str,
    day: date,
    frame: pd.DataFrame,
    surface: dict | None,
    history: pd.Series,
    r: float,
    q: float,
    target_dte: int = 35,
    min_obs: int = 60,
) -> list[dict]:
    """The scan rows of one name for one day, from its stored chain and surface row."""
    rows: list[dict] = []
    if surface and surface.get("vrp_30") is not None:
        z = ve.vrp_zscore(history.to_numpy(), surface["vrp_30"], min_obs=min_obs)
        rows.append(
            {
                "kind": "vrp",
                "iv": surface.get("atm_iv_30"),
                "fair": surface.get("fair_vol_30"),
                "z": z,
                "score": z,
                "note": f"vrp {surface['vrp_30']:+.3f}" + ("" if z is not None else f", {len(history)}/{min_obs} obs"),
            }
        )
    view = _view_from_quotes(underlying, frame, day, target_dte)
    if view is not None and view.live:
        fit = options.svi_surface_fit(view, r, q)
        for k in sorted(fit.excluded):
            rows.append(
                {"kind": "parity", "expiry": view.expiry, "strike": k, "note": "outside the American parity band"}
            )
        for o in fit.outliers:
            rows.append(
                {
                    "kind": "svi",
                    "expiry": view.expiry,
                    "strike": o.strike,
                    "right": o.right,
                    "iv": o.iv,
                    "fair": o.fit_iv,
                    "half_spread_vol": o.half_spread_vol,
                    "score": o.resid / o.half_spread_vol if o.half_spread_vol else None,
                    "note": f"{'rich' if o.resid > 0 else 'cheap'} by {o.resid:+.1%} vs SVI",
                }
            )
    return rows


def snapshot_scan(snapshot_date: date | None = None, r: float | None = None, dividend_yield=None) -> int:
    """
    Write the day's option_mispricing_scan rows for every name in vol_surface
    that day (idempotent: the day's rows are replaced). Returns rows written.
    Event rows are left to the bots, which need live earnings timing.
    """
    day = snapshot_date or datetime.now(UTC).date()
    r = options.risk_free_rate() if r is None else r
    q_of = dividend_yield or options.dividend_yield
    quotes = vs.load_day_quotes(day)
    # Plain dicts, not ORM rows: the rows detach once the session commits, and
    # the summary below reads them afterwards.
    rows: list[dict] = []
    for underlying, frame in quotes.items():
        try:
            surface = vs.latest_surface(underlying, max_age_days=0, as_of=day)
            history = vs.surface_history(underlying, "vrp_30", 400, day)
            history = history[history.index < day]
            for row in scan_rows(underlying, day, frame, surface, history, r, q_of(underlying)):
                rows.append({"scan_date": day, "underlying": underlying, **row})
        except Exception as exc:
            logger.warning("scan: %s failed: %s", underlying, exc)
    with get_db_session() as session:
        session.query(MispricingScanRow).filter(MispricingScanRow.scan_date == day).delete()
        session.add_all([MispricingScanRow(**row) for row in rows])
    ranked = sorted((r for r in rows if r["kind"] == "vrp" and r.get("z") is not None), key=lambda r: -abs(r["z"]))
    logger.info(
        "scan %s: %d rows; top |z|: %s",
        day,
        len(rows),
        ", ".join(f"{r['underlying']} {r['z']:+.1f}" for r in ranked[:8]) or "none yet (history too short)",
    )
    return len(rows)


def latest_scan_scores(max_age_days: int = 5, as_of: date | None = None) -> dict[str, float]:
    """underlying -> |z| of its newest vrp scan row within max_age_days (for shortlisting)."""
    as_of = as_of or datetime.now(UTC).date()
    with get_db_session() as session:
        rows = (
            session.query(MispricingScanRow.underlying, MispricingScanRow.z, MispricingScanRow.scan_date)
            .filter(
                MispricingScanRow.kind == "vrp",
                MispricingScanRow.scan_date <= as_of,
                MispricingScanRow.scan_date >= as_of - timedelta(days=max_age_days),
            )
            .order_by(MispricingScanRow.scan_date)
            .all()
        )
    return {u: abs(z) for u, z, _ in rows if z is not None and not math.isnan(z)}


def shortlist(universe: Iterable[str], scores: dict[str, float], n: int, always: Iterable[str] = ()) -> list[str]:
    """`always` first, then the highest-scoring names, then (no scores yet) the universe order."""
    universe = list(universe)
    out = [u for u in always if u in universe]
    ranked = sorted((u for u in universe if u in scores and u not in out), key=lambda u: -scores[u])
    for u in ranked + [u for u in universe if u not in scores]:
        if len(out) >= n:
            break
        if u not in out:
            out.append(u)
    return out
