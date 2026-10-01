"""
The daily vol surface, condensed: one `vol_surface` row per underlying per day,
built from that day's `option_quotes` capture.

Why a summary table: a vol strategy ranks on a handful of numbers (30-day ATM
IV, skew, term slope, IV minus forecast vol) and needs their history to rank
against. Rebuilding those from ~35k raw quote rows for every past day on every
bot run is wasteful; this table is that history, one row per name per day.

What each number is:
- IVs are solved here from bid/ask mids (Black-Scholes-Merton with the
  dividend yield as q), never taken from yfinance.
- ATM IV per expiry: mean IV of the call and put at the strike nearest spot.
- Constant maturity (7/30/60/90/180 days): linear interpolation of TOTAL
  variance σ²T between the bracketing expiries, then back to vol; flat in vol
  outside the listed range. Total variance, not vol, is what is additive in time.
- 25-delta put/call IV at 30 days: from an SVI fit of the expiries around 30
  days when that fit is free of butterfly arbitrage (utils/svi.py), else by
  linear interpolation of IV in delta among out-of-the-money contracts;
  interpolated to 30 days in total variance like ATM.
  rr25 = put − call (positive: puts richer, the usual equity skew);
  fly25 = mean of the wings − ATM (how convex the smile is).
- term_slope = atm_iv_90 / atm_iv_30 (below 1: inverted, near-term fear).
- fair_vol_30: HAR-RV forecast of close-to-close vol over 21 trading days,
  earnings days NOT excluded (a consistent, cheap series for ranking; bots
  that need diffusion vol compute their own). vrp_30 = atm_iv_30 − fair_vol_30.
- Options "TA", all from open interest and volume in the capture:
  pc_volume / pc_oi = put / call; unusual_count = contracts trading more than
  3x their open interest (and at least 500 contracts);
  gex_usd = Σ γ·OI·100·S²·1%, calls positive and puts negative — the textbook
  "dealers are long calls, short puts" sign convention, so positive means
  dealer hedging damps moves. It is a convention, not an observation of who
  holds what; treat it as a regime indicator only.
  max_pain = the strike of the ~30-day expiry at which open option holders
  would lose the most at expiry.
"""

import logging
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from tradingbot.utils import option_math as om
from tradingbot.utils import svi
from tradingbot.utils.db import ImpliedCorrelation, OptionQuote, VolSurfaceSnapshot, get_db_session

logger = logging.getLogger(__name__)

CM_DAYS = (7, 30, 60, 90, 180)
MIN_DTE = 2  # expiries closer than this are all noise (and pin risk)
UNUSUAL_VOL_OI = 3.0
UNUSUAL_MIN_VOLUME = 500
FAIR_HORIZON_DAYS = 21
SURFACE_FIELDS = (
    "spot",
    "atm_iv_7",
    "atm_iv_30",
    "atm_iv_60",
    "atm_iv_90",
    "atm_iv_180",
    "iv_25p_30",
    "iv_25c_30",
    "rr25_30",
    "fly25_30",
    "cp_spread_30",
    "term_slope",
    "fair_vol_30",
    "vrp_30",
    "pc_volume",
    "pc_oi",
    "gex_usd",
    "max_pain",
    "unusual_count",
    "n_contracts",
)


# --------------------------------------------------------------------------- #
# Pure functions on a chain frame                                             #
# --------------------------------------------------------------------------- #


def _num(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


def mid_prices(frame: pd.DataFrame) -> pd.Series:
    """Bid/ask mid where both sides are quoted, else the last trade, else NaN."""
    bid, ask, last = _num(frame, "bid"), _num(frame, "ask"), _num(frame, "last_price")
    two_sided = (bid > 0) & (ask > 0) & (ask >= bid)
    mid = ((bid + ask) / 2).where(two_sided)
    return mid.fillna(last.where(last > 0))


def solve_ivs(frame: pd.DataFrame, spot: float, today: date, r: float, q: float = 0.0) -> pd.DataFrame:
    """
    Every contract with a price, with expiry, T, IV, delta and gamma solved at `spot`.

    `frame` has the fetch_option_chain / option_quotes columns and an
    `expiration` column (datetime or date). Rows whose IV does not solve (price
    below intrinsic, zero bid) are dropped. Expiries under MIN_DTE are dropped.
    """
    out = frame.copy()
    out["expiry"] = pd.to_datetime(out["expiration"]).dt.date
    out["dte"] = [(e - today).days for e in out["expiry"]]
    out = out[out["dte"] >= MIN_DTE].copy()
    out["price"] = mid_prices(out)
    out["strike"] = out["strike"].astype(float)
    out = out[out["price"] > 0]
    ivs, deltas, gammas, Ts = [], [], [], []
    for row in out.itertuples():
        T = om.year_fraction(row.expiry, today)
        iv = om.implied_volatility(float(row.price), spot, row.strike, T, r, row.option_type, q)
        Ts.append(T)
        ivs.append(iv if iv is not None else np.nan)
        if iv is None:
            deltas.append(np.nan)
            gammas.append(np.nan)
        else:
            deltas.append(om.delta(spot, row.strike, T, r, iv, row.option_type, q))
            gammas.append(om.gamma(spot, row.strike, T, r, iv, q))
    out["T"], out["iv"], out["delta"], out["gamma"] = Ts, ivs, deltas, gammas
    return out.dropna(subset=["iv"])


def expiry_atm_iv(solved: pd.DataFrame, spot: float) -> float | None:
    """Mean IV of the call and put at the strike nearest spot, for one expiry."""
    ivs = []
    for right in ("C", "P"):
        side = solved[solved["option_type"] == right]
        if len(side):
            ivs.append(float(side.loc[(side["strike"] - spot).abs().idxmin(), "iv"]))
    return sum(ivs) / len(ivs) if ivs else None


def _otm(solved: pd.DataFrame, spot: float) -> pd.DataFrame:
    return solved[
        ((solved["option_type"] == "P") & (solved["strike"] < spot))
        | ((solved["option_type"] == "C") & (solved["strike"] >= spot))
    ]


def _svi_fit(solved: pd.DataFrame, spot: float, r: float, q: float) -> svi.SVIParams | None:
    """SVI fit of one expiry's out-of-the-money IVs, or None if it fails or has butterfly arbitrage."""
    otm = _otm(solved, spot)
    if len(otm) < 6:
        return None
    T = float(otm["T"].iloc[0])
    forward = spot * math.exp((r - q) * T)
    k = np.log(otm["strike"].to_numpy() / forward)
    w = otm["iv"].to_numpy() ** 2 * T
    params = svi.fit_svi(k, w)
    if params is None or not svi.butterfly_arbitrage_free(params):
        return None
    return params


def smile_iv_at_delta(
    solved: pd.DataFrame, spot: float, right: str, target: float, r: float = 0.0, q: float = 0.0
) -> float | None:
    """
    IV at a given |delta| (e.g. 0.25) on one side of one expiry's smile.

    Uses the SVI fit when it passes the butterfly check: solve the strike whose
    BSM delta at the fitted IV equals the target. Otherwise interpolates IV
    linearly in delta among out-of-the-money contracts of that side.
    """
    T = float(solved["T"].iloc[0]) if len(solved) else 0.0
    if T <= 0:
        return None
    params = _svi_fit(solved, spot, r, q)
    if params is not None:
        forward = spot * math.exp((r - q) * T)
        grid = np.linspace(-1.5, 1.5, 601) if right == "P" else np.linspace(-1.0, 2.0, 601)
        ks = grid[grid < 0] if right == "P" else grid[grid >= 0]
        best, best_err = None, float("inf")
        for k in ks:
            iv = float(params.iv(k, T))
            if not iv > 0:
                continue
            d = abs(om.delta(spot, forward * math.exp(k), T, r, iv, right, q))
            if abs(d - target) < best_err:
                best, best_err = iv, abs(d - target)
        if best is not None and best_err < 0.02:
            return best
    side = _otm(solved, spot)
    side = side[side["option_type"] == right].dropna(subset=["delta"])
    if len(side) < 2:
        return None
    d = side["delta"].abs().to_numpy()
    order = np.argsort(d)
    d, iv = d[order], side["iv"].to_numpy()[order]
    if not d[0] <= target <= d[-1]:
        return None
    return float(np.interp(target, d, iv))


CP_SPREAD_BAND = 0.10  # strikes within +/-10% of spot pair a call with a put


def call_put_spread(solved: pd.DataFrame, spot: float, band: float = CP_SPREAD_BAND) -> float | None:
    """
    Mean call IV minus put IV over the strikes of one expiry where both are
    quoted, within `band` of spot (Cremers & Weinbaum 2010). Parity makes it
    zero up to American early exercise and frictions; calls priced rich
    against puts have predicted the stock's next weeks. Equal weights: the
    imported history has no open interest to weight by.
    """
    near = solved[(solved["strike"] / spot - 1.0).abs() <= band]
    calls = near[near["option_type"] == "C"].groupby("strike")["iv"].last()
    puts = near[near["option_type"] == "P"].groupby("strike")["iv"].last()
    both = calls.index.intersection(puts.index)
    if not len(both):
        return None
    return float((calls[both] - puts[both]).mean())


def interpolate_in_time(points: Sequence[tuple[float, float]], days: float) -> float | None:
    """A quantity at `days` from (T years, value) points: linear in T, flat outside."""
    pts = sorted((T, v) for T, v in points if T > 0 and v is not None and math.isfinite(v))
    if not pts:
        return None
    return float(np.interp(days / 365.0, [p[0] for p in pts], [p[1] for p in pts]))


def constant_maturity_iv(points: Sequence[tuple[float, float]], days: float) -> float | None:
    """IV at `days` from (T years, iv) points: linear in total variance, flat in vol outside."""
    pts = sorted((T, iv) for T, iv in points if T > 0 and iv is not None and iv > 0)
    if not pts:
        return None
    target = days / 365.0
    if target <= pts[0][0]:
        return pts[0][1]
    if target >= pts[-1][0]:
        return pts[-1][1]
    Ts = np.array([p[0] for p in pts])
    w = np.array([p[1] ** 2 * p[0] for p in pts])
    w_t = float(np.interp(target, Ts, w))
    return math.sqrt(max(w_t, 0.0) / target)


def gamma_exposure(solved: pd.DataFrame, spot: float) -> float:
    """Σ γ·OI·100·S²·1%: dollars of delta dealers would trade per 1% move (calls +, puts −)."""
    oi = _num(solved, "open_interest").fillna(0.0)
    sign = np.where(solved["option_type"] == "C", 1.0, -1.0)
    return float((solved["gamma"].fillna(0.0) * oi * 100 * spot**2 * 0.01 * sign).sum())


def max_pain(expiry_frame: pd.DataFrame) -> float | None:
    """The strike at which all open calls and puts of one expiry would pay out least."""
    oi = _num(expiry_frame, "open_interest").fillna(0.0)
    if oi.sum() <= 0:
        return None
    strikes = np.sort(expiry_frame["strike"].astype(float).unique())
    k = expiry_frame["strike"].astype(float).to_numpy()
    is_call = (expiry_frame["option_type"] == "C").to_numpy()
    o = oi.to_numpy()
    payout = [float((o * np.where(is_call, np.maximum(s - k, 0), np.maximum(k - s, 0))).sum()) for s in strikes]
    return float(strikes[int(np.argmin(payout))])


def put_call_ratios(frame: pd.DataFrame) -> tuple[float | None, float | None]:
    """(put/call volume, put/call open interest); None when the call side is zero."""
    out = []
    for col in ("volume", "open_interest"):
        v = _num(frame, col).fillna(0.0)
        calls = float(v[frame["option_type"] == "C"].sum())
        puts = float(v[frame["option_type"] == "P"].sum())
        out.append(puts / calls if calls > 0 else None)
    return out[0], out[1]


def unusual_activity(frame: pd.DataFrame) -> pd.DataFrame:
    """Contracts trading more than UNUSUAL_VOL_OI x open interest, at least UNUSUAL_MIN_VOLUME."""
    vol, oi = _num(frame, "volume").fillna(0.0), _num(frame, "open_interest").fillna(0.0)
    return frame[(vol >= UNUSUAL_MIN_VOLUME) & (vol > UNUSUAL_VOL_OI * oi)]


def summarize(
    frame: pd.DataFrame, spot: float, today: date, r: float, q: float = 0.0, fair_vol: float | None = None
) -> dict[str, float | int | None]:
    """Every vol_surface field for one underlying's chain snapshot (all expiries)."""
    solved = solve_ivs(frame, spot, today, r, q)
    row: dict[str, float | int | None] = dict.fromkeys(SURFACE_FIELDS)
    row["spot"] = spot
    row["n_contracts"] = len(frame)
    pc_vol, pc_oi = put_call_ratios(frame)
    row["pc_volume"], row["pc_oi"] = pc_vol, pc_oi
    row["unusual_count"] = len(unusual_activity(frame))
    if solved.empty:
        return row

    atm_pts, p25_pts, c25_pts, cp_pts = [], [], [], []
    for _, grp in solved.groupby("expiry"):
        T = float(grp["T"].iloc[0])
        atm = expiry_atm_iv(grp, spot)
        if atm is not None:
            atm_pts.append((T, atm))
        if 10 <= grp["dte"].iloc[0] <= 75:  # the expiries that bracket 30 days
            p25 = smile_iv_at_delta(grp, spot, "P", 0.25, r, q)
            c25 = smile_iv_at_delta(grp, spot, "C", 0.25, r, q)
            if p25 is not None:
                p25_pts.append((T, p25))
            if c25 is not None:
                c25_pts.append((T, c25))
            cp = call_put_spread(grp, spot)
            if cp is not None:
                cp_pts.append((T, cp))
    for d in CM_DAYS:
        row[f"atm_iv_{d}"] = constant_maturity_iv(atm_pts, d)
    row["iv_25p_30"] = constant_maturity_iv(p25_pts, 30)
    row["iv_25c_30"] = constant_maturity_iv(c25_pts, 30)
    row["cp_spread_30"] = interpolate_in_time(cp_pts, 30)
    atm30 = row["atm_iv_30"]
    if row["iv_25p_30"] is not None and row["iv_25c_30"] is not None:
        row["rr25_30"] = row["iv_25p_30"] - row["iv_25c_30"]
        if atm30 is not None:
            row["fly25_30"] = (row["iv_25p_30"] + row["iv_25c_30"]) / 2 - atm30
    if atm30 and row["atm_iv_90"]:
        row["term_slope"] = row["atm_iv_90"] / atm30
    if fair_vol is not None and math.isfinite(fair_vol):
        row["fair_vol_30"] = fair_vol
        if atm30 is not None:
            row["vrp_30"] = atm30 - fair_vol
    row["gex_usd"] = gamma_exposure(solved, spot)
    near30 = solved.loc[(solved["dte"] - 30).abs() == (solved["dte"] - 30).abs().min(), "expiry"].iloc[0]
    row["max_pain"] = max_pain(frame[pd.to_datetime(frame["expiration"]).dt.date == near30])
    return row


def implied_correlation(index_iv: float, ivs: Sequence[float], weights: Sequence[float]) -> float | None:
    """(σ_I² − Σwᵢ²σᵢ²) / Σ_{i≠j} wᵢwⱼσᵢσⱼ, weights normalised to 1. None if undefined."""
    w = np.asarray(weights, dtype=float)
    s = np.asarray(ivs, dtype=float)
    ok = np.isfinite(w) & np.isfinite(s) & (w > 0) & (s > 0)
    w, s = w[ok], s[ok]
    if len(w) < 2 or not index_iv > 0:
        return None
    w = w / w.sum()
    ws = w * s
    cross = ws.sum() ** 2 - (ws**2).sum()
    if cross <= 0:
        return None
    return float((index_iv**2 - (ws**2).sum()) / cross)


# --------------------------------------------------------------------------- #
# Database                                                                    #
# --------------------------------------------------------------------------- #


def load_day_quotes(day: date, underlyings: Iterable[str] | None = None) -> dict[str, pd.DataFrame]:
    """That day's option_quotes per underlying, the latest snapshot of each contract."""
    start = datetime.combine(day, datetime.min.time())
    with get_db_session() as session:
        query = session.query(OptionQuote).filter(
            OptionQuote.snapshot_at >= start, OptionQuote.snapshot_at < start + timedelta(days=1)
        )
        if underlyings is not None:
            query = query.filter(OptionQuote.underlying.in_(list(underlyings)))
        cols = [c.name for c in OptionQuote.__table__.columns]
        rows = [{c: getattr(q, c) for c in cols} for q in query.all()]
    if not rows:
        return {}
    df = pd.DataFrame(rows).sort_values("snapshot_at")
    df = df.drop_duplicates("contract_symbol", keep="last")
    return {u: g.reset_index(drop=True) for u, g in df.groupby("underlying")}


CLOSE_HISTORY_YEARS = 3  # what the live capture downloads for the fair-vol fit


def download_closes(symbols: list[str], start: date | None = None) -> dict[str, pd.Series]:
    """Daily closes per symbol, one batched yfinance download: the last 3 years, or from `start`."""
    import yfinance as yf

    when = {"start": str(start)} if start else {"period": f"{CLOSE_HISTORY_YEARS}y"}
    data = yf.download(symbols, **when, auto_adjust=True, progress=False, group_by="column")
    close = data.get("Close", data)
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    return {s: close[s].dropna() for s in symbols if s in close}


def close_window(close: pd.Series | None, day: date) -> pd.Series | None:
    """
    The closes a capture on `day` would have seen: the CLOSE_HISTORY_YEARS up to
    and including `day`. A backfilled day must not fit its fair vol on later
    prices (until 2026-10-01 the backfill fitted every past day on the 3 years
    up to the run date). Undated series (tests) pass through unchanged.
    """
    if close is None or not isinstance(close.index, pd.DatetimeIndex):
        return close
    idx = close.index.tz_localize(None) if close.index.tz is not None else close.index
    end = pd.Timestamp(day)
    begin = end - pd.DateOffset(years=CLOSE_HISTORY_YEARS)
    return close[(idx.normalize() <= end) & (idx > begin)]


def fair_vol_from_close(close: pd.Series | None) -> float | None:
    if close is None or len(close) < 60:
        return None
    return om.har_rv_forecast(om.log_returns(close), FAIR_HORIZON_DAYS)


def _upsert(model, keys: dict, values: dict) -> None:
    with get_db_session() as session:
        row = session.query(model).filter_by(**keys).first()
        if row is None:
            row = model(**keys)
            session.add(row)
        for k, v in values.items():
            setattr(row, k, v)


def snapshot_vol_surface(
    snapshot_date: date | None = None,
    underlyings: Iterable[str] | None = None,
    closes: dict[str, pd.Series] | None = None,
    r: float | None = None,
    dividend_yield=None,
) -> dict[str, list[str]]:
    """
    Write one vol_surface row per underlying captured on `snapshot_date` (default today, UTC).

    Idempotent (select-then-write, like fundamentals.snapshot_fundamentals).
    `closes` / `r` / `dividend_yield` are injectable for tests and backfills;
    by default closes come from one batched yfinance download, r from ^IRX and
    q from options.dividend_yield. Closes are cut at `snapshot_date` either way
    (close_window), so injecting the whole history is safe.

    Returns {"written": [...], "failed": [...]}.
    """
    from tradingbot.utils import options

    day = snapshot_date or datetime.now(UTC).date()
    quotes = load_day_quotes(day, underlyings)
    result: dict[str, list[str]] = {"written": [], "failed": []}
    if not quotes:
        logger.warning("vol surface: no option_quotes on %s", day)
        return result
    r = options.risk_free_rate() if r is None else r
    q_of = dividend_yield or options.dividend_yield
    if closes is None:
        past = day < datetime.now(UTC).date() - timedelta(days=7)
        start = (pd.Timestamp(day) - pd.DateOffset(years=CLOSE_HISTORY_YEARS)).date() if past else None
        try:
            closes = download_closes(sorted(quotes), start)
        except Exception as exc:
            logger.warning("vol surface: close history unavailable (%s); fair vol left empty", exc)
            closes = {}
    for underlying, frame in quotes.items():
        try:
            spot = float(pd.to_numeric(frame["underlying_price"], errors="coerce").dropna().iloc[-1])
            fair = fair_vol_from_close(close_window(closes.get(underlying), day))
            row = summarize(frame, spot, day, r, q_of(underlying), fair)
            _upsert(VolSurfaceSnapshot, {"underlying": underlying, "snapshot_date": day}, row)
            result["written"].append(underlying)
        except Exception as exc:
            logger.warning("vol surface: %s failed: %s", underlying, exc)
            result["failed"].append(underlying)
    logger.info(
        "vol surface %s: %d written, %d failed %s", day, len(result["written"]), len(result["failed"]), result["failed"]
    )
    return result


def snapshot_implied_correlation(
    snapshot_date: date | None = None,
    index_symbol: str = "SPY",
    weights: dict[str, float] | None = None,
    fallback_weights: dict[str, float] | None = None,
) -> float | None:
    """
    Implied correlation of `index_symbol` against the captured single names on a day.

    Reads that day's vol_surface rows (so run it after snapshot_vol_surface);
    weights default to the latest stock_fundamentals market caps, and names
    without one take `fallback_weights` (a backfill's historical caps: the
    capture began 2026-09-25). Returns the value written, or None when there
    was not enough to compute it.
    """
    from tradingbot.utils.fundamentals import get_fundamentals_batch

    day = snapshot_date or datetime.now(UTC).date()
    with get_db_session() as session:
        rows = {
            r.underlying: r.atm_iv_30
            for r in session.query(VolSurfaceSnapshot).filter(VolSurfaceSnapshot.snapshot_date == day)
        }
    index_iv = rows.pop(index_symbol, None)
    rows.pop("QQQ", None)
    names = [u for u, iv in rows.items() if iv]
    if weights is None:
        caps = get_fundamentals_batch(names, day, max_age_days=10)
        weights = {u: (c or {}).get("market_cap") for u, c in caps.items()}
    if fallback_weights:
        weights = {u: weights.get(u) or fallback_weights.get(u) for u in names}
    names = [u for u in names if weights.get(u)]
    value = None
    if index_iv and len(names) >= 10:
        value = implied_correlation(index_iv, [rows[u] for u in names], [weights[u] for u in names])
    coverage = len(names) / max(len(rows), 1)
    _upsert(
        ImpliedCorrelation,
        {"index_symbol": index_symbol, "snapshot_date": day},
        {"value": value, "index_iv": index_iv, "n_names": len(names), "weight_coverage": coverage},
    )
    logger.info("implied correlation %s %s: %s over %d names", index_symbol, day, value, len(names))
    return value


def captured_dates() -> list[date]:
    """Every UTC date with at least one option_quotes row, oldest first (for backfills)."""
    from sqlalchemy import func

    with get_db_session() as session:
        rows = session.query(func.date(OptionQuote.snapshot_at)).distinct().all()
    return sorted(pd.Timestamp(r[0]).date() for r in rows if r[0] is not None)


# --------------------------------------------------------------------------- #
# Read side                                                                   #
# --------------------------------------------------------------------------- #


def surface_history(underlying: str, field: str, lookback_days: int = 400, as_of: date | None = None) -> pd.Series:
    """`field` of `underlying` by snapshot date over the lookback, oldest first (NaNs dropped)."""
    as_of = as_of or datetime.now(UTC).date()
    column = getattr(VolSurfaceSnapshot, field)
    with get_db_session() as session:
        rows = (
            session.query(VolSurfaceSnapshot.snapshot_date, column)
            .filter(
                VolSurfaceSnapshot.underlying == underlying,
                VolSurfaceSnapshot.snapshot_date <= as_of,
                VolSurfaceSnapshot.snapshot_date >= as_of - timedelta(days=lookback_days),
            )
            .order_by(VolSurfaceSnapshot.snapshot_date)
            .all()
        )
    s = pd.Series(dict(rows), dtype=float)
    return s.dropna()


def surface_rank(
    underlying: str, field: str, value: float, min_obs: int = 60, as_of: date | None = None
) -> tuple[float, float] | None:
    """(rank 0-100, percentile 0-100) of `value` in the field's past-year history; None below min_obs."""
    history = surface_history(underlying, field, 365, as_of)
    if len(history) < min_obs:
        return None
    return om.iv_rank(history, value), om.iv_percentile(history, value)


def latest_surface(underlying: str, max_age_days: int = 3, as_of: date | None = None) -> dict | None:
    """The newest vol_surface row within max_age_days, as a plain dict; None if none."""
    as_of = as_of or datetime.now(UTC).date()
    with get_db_session() as session:
        row = (
            session.query(VolSurfaceSnapshot)
            .filter(
                VolSurfaceSnapshot.underlying == underlying,
                VolSurfaceSnapshot.snapshot_date <= as_of,
                VolSurfaceSnapshot.snapshot_date >= as_of - timedelta(days=max_age_days),
            )
            .order_by(VolSurfaceSnapshot.snapshot_date.desc())
            .first()
        )
        return (
            None
            if row is None
            else {"snapshot_date": row.snapshot_date, **{f: getattr(row, f) for f in SURFACE_FIELDS}}
        )


def latest_surfaces(max_age_days: int = 5, as_of: date | None = None) -> pd.DataFrame:
    """The newest row per underlying within max_age_days, one row each, indexed by underlying."""
    as_of = as_of or datetime.now(UTC).date()
    with get_db_session() as session:
        rows = (
            session.query(VolSurfaceSnapshot)
            .filter(
                VolSurfaceSnapshot.snapshot_date <= as_of,
                VolSurfaceSnapshot.snapshot_date >= as_of - timedelta(days=max_age_days),
            )
            .order_by(VolSurfaceSnapshot.snapshot_date)
            .all()
        )
        data = [
            {"underlying": r.underlying, "snapshot_date": r.snapshot_date, **{f: getattr(r, f) for f in SURFACE_FIELDS}}
            for r in rows
        ]
    if not data:
        return pd.DataFrame(columns=["snapshot_date", *SURFACE_FIELDS])
    return pd.DataFrame(data).drop_duplicates("underlying", keep="last").set_index("underlying")


def implied_correlation_history(
    index_symbol: str = "SPY", lookback_days: int = 400, as_of: date | None = None
) -> pd.Series:
    """Stored implied-correlation values by date, oldest first (NaNs dropped)."""
    as_of = as_of or datetime.now(UTC).date()
    with get_db_session() as session:
        rows = (
            session.query(ImpliedCorrelation.snapshot_date, ImpliedCorrelation.value)
            .filter(
                ImpliedCorrelation.index_symbol == index_symbol,
                ImpliedCorrelation.snapshot_date <= as_of,
                ImpliedCorrelation.snapshot_date >= as_of - timedelta(days=lookback_days),
            )
            .order_by(ImpliedCorrelation.snapshot_date)
            .all()
        )
    return pd.Series(dict(rows), dtype=float).dropna()
