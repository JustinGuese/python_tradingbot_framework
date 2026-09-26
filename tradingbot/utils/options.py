"""
Paper options: contract selection, chain snapshots, pricing and expiry values.

A bot never handles a contract symbol. It says `self.buy("AAPL", option=True)`
and PortfolioManager resolves that here to a concrete contract (first expiry at
least `target_dte` days out, strike nearest spot), which then becomes the
portfolio key — pricing needs the exact contract, so the key has to be it.

Units: a holding's quantity is in UNDERLYING-SHARE equivalents (contracts x 100)
and its price is the per-share premium yfinance quotes. That keeps every
`qty * price` valuation in the framework (PortfolioManager, portfolio_utils, the
Bot weight code, the live copier) correct without threading a contract
multiplier through them. Buys round down to whole contracts, so an option
holding is always a multiple of CONTRACT_MULTIPLIER.

Nothing here is reached unless a bot passes `option=` or sets USE_OPTIONS; the
only always-on part is the `is_option_symbol` guard used to keep contracts away
from the live copier and out of plain `buy()` calls.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

import pandas as pd
import yfinance as yf

from . import option_math as om
from .db import OptionQuote, StockEarnings, StockNews, Trade, get_db_session
from .option_math import CONTRACT_MULTIPLIER

logger = logging.getLogger(__name__)

# OCC symbology as yfinance emits it (no space padding): root, YYMMDD, C/P,
# strike x 1000 zero-padded to 8 digits. e.g. AAPL251017C00150000.
OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")

# Fallback when ^IRX (13-week T-bill) is unavailable; roughly its 2026 level.
DEFAULT_RISK_FREE_RATE = 0.04

# A snapshot older than this is refetched before it is used to price or fill.
QUOTE_MAX_AGE = timedelta(minutes=15)


class MarketClosedError(RuntimeError):
    """A chain was fetched outside regular hours where a live one was required."""


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    expiry: date
    right: str  # "C" or "P"
    strike: float


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    last: float
    snapshot_at: datetime

    @property
    def mid(self) -> float:
        # yfinance reports bid = ask = 0 outside market hours; last trade is the
        # only number left then.
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last


def is_option_symbol(symbol: str) -> bool:
    return bool(symbol) and OCC_RE.match(symbol) is not None


def parse_occ(symbol: str) -> OptionContract:
    m = OCC_RE.match(symbol)
    if not m:
        raise ValueError(f"{symbol!r} is not an OCC option symbol")
    root, ymd, right, strike = m.groups()
    expiry = datetime.strptime(ymd, "%y%m%d").date()
    return OptionContract(underlying=root, expiry=expiry, right=right, strike=int(strike) / 1000)


def normalize_right(option: bool | str | None) -> str | None:
    """Map a bot's `option=` argument to "C" / "P", or None for "not an option"."""
    if option is None or option is False:
        return None
    if option is True:
        return "C"
    key = str(option).strip().lower()
    if key in ("c", "call", "calls"):
        return "C"
    if key in ("p", "put", "puts"):
        return "P"
    raise ValueError(f"option={option!r}: use True / 'call' / 'put'")


def intrinsic_value(contract: OptionContract, underlying_price: float) -> float:
    if contract.right == "C":
        return max(0.0, underlying_price - contract.strike)
    return max(0.0, contract.strike - underlying_price)


def whole_contract_qty(share_equiv_qty: float) -> float:
    """Floor a share-equivalent quantity to whole contracts (a multiple of 100)."""
    # The epsilon absorbs float residue like 299.99999999 that means 3 contracts.
    return math.floor(share_equiv_qty / CONTRACT_MULTIPLIER + 1e-9) * CONTRACT_MULTIPLIER


def held_option_keys(portfolio: dict, underlying: str | None = None, right: str | None = None) -> list[str]:
    """Option holdings, optionally filtered by underlying/right, earliest expiry first."""
    keys = []
    for key, qty in portfolio.items():
        if not is_option_symbol(key) or qty <= 0:
            continue
        c = parse_occ(key)
        if underlying is not None and c.underlying != underlying:
            continue
        if right is not None and c.right != right:
            continue
        keys.append(key)
    return sorted(keys, key=lambda k: (parse_occ(k).expiry, k))


def utc_today() -> date:
    return datetime.now(UTC).date()


def _naive(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def fetch_option_chain(
    underlying: str,
    expiry: date,
    ticker: yf.Ticker | None = None,
    moneyness: float | None = None,
    require_live: bool = False,
) -> pd.DataFrame:
    """
    Fetch one expiry's chain from yfinance, store it as a snapshot, return it.

    Columns: contract_symbol, option_type, strike, bid, ask, last_price, volume,
    open_interest, implied_volatility, underlying_price; `attrs["spot"]` carries
    the underlying's price when yfinance supplied one. With `moneyness`, only
    strikes within spot x (1 +/- moneyness) are kept (and stored): the far
    wings are most of a chain's rows and almost none of its information.
    With `require_live`, an off-hours chain raises MarketClosedError and
    nothing is stored.
    """
    ticker = ticker or yf.Ticker(underlying)
    chain = ticker.option_chain(expiry.isoformat())
    frames = []
    for right, df in (("C", chain.calls), ("P", chain.puts)):
        if df is not None and len(df):
            frames.append(df.assign(option_type=right))
    if not frames:
        raise ValueError(f"Empty option chain for {underlying} {expiry}")
    raw = pd.concat(frames, ignore_index=True)
    spot = _num((chain.underlying or {}).get("regularMarketPrice"))
    if moneyness is not None and spot:
        strikes = raw["strike"].astype(float)
        raw = raw[(strikes >= spot * (1 - moneyness)) & (strikes <= spot * (1 + moneyness))]
        if raw.empty:
            raise ValueError(f"No {underlying} {expiry} strikes within {moneyness:.0%} of {spot}")

    # Outside regular hours yfinance zeroes bid/ask on almost the whole chain but
    # leaves a few stale remnants (seen: SPY pre-market, 7 of 235 calls quoted,
    # one from a trade seven weeks old). Trusting those would select a contract
    # far from the money and fill at a dead price, so bid/ask are only recorded
    # from a live session; otherwise NULL, and pricing falls back to last trade.
    live = (chain.underlying or {}).get("marketState", "REGULAR") == "REGULAR"
    if require_live and not live:
        raise MarketClosedError(f"{underlying} marketState {(chain.underlying or {}).get('marketState')}")
    snapshot_at = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        {
            "underlying": underlying,
            "contract_symbol": str(r["contractSymbol"]),
            "expiration": _naive(expiry),
            "option_type": r["option_type"],
            "strike": float(r["strike"]),
            "bid": _num(r.get("bid")) if live else None,
            "ask": _num(r.get("ask")) if live else None,
            "last_price": _num(r.get("lastPrice")),
            "volume": _num(r.get("volume")),
            "open_interest": _num(r.get("openInterest")),
            "implied_volatility": _num(r.get("impliedVolatility")),
            "underlying_price": spot,
            "snapshot_at": snapshot_at,
        }
        for _, r in raw.iterrows()
    ]
    # snapshot_at is fresh per fetch, so the (contract, snapshot) key cannot
    # collide with an earlier row — no dedup pass needed.
    with get_db_session() as session:
        session.add_all([OptionQuote(**row) for row in rows])

    out = pd.DataFrame(rows).drop(columns=["underlying", "expiration", "snapshot_at"])
    out.attrs["spot"] = spot
    out.attrs["live"] = live
    return out


def latest_quote(contract_symbol: str, max_age: timedelta | None = QUOTE_MAX_AGE) -> Quote | None:
    """Newest stored snapshot of a contract, or None if missing / older than max_age."""
    with get_db_session() as session:
        row = (
            session.query(OptionQuote.bid, OptionQuote.ask, OptionQuote.last_price, OptionQuote.snapshot_at)
            .filter(OptionQuote.contract_symbol == contract_symbol)
            .order_by(OptionQuote.snapshot_at.desc())
            .first()
        )
    if row is None:
        return None
    bid, ask, last, snapshot_at = row
    if max_age is not None and datetime.now(UTC).replace(tzinfo=None) - snapshot_at > max_age:
        return None
    return Quote(bid=bid or 0.0, ask=ask or 0.0, last=last or 0.0, snapshot_at=snapshot_at)


def fresh_quote(contract_symbol: str) -> Quote | None:
    """A quote no older than QUOTE_MAX_AGE, refetching the chain if needed.

    Falls back to the newest stored snapshot of any age when the refetch fails,
    so a yfinance hiccup degrades to a stale price rather than an unpriceable
    holding (which every valuation path silently counts as $0).
    """
    quote = latest_quote(contract_symbol)
    if quote is not None:
        return quote
    c = parse_occ(contract_symbol)
    try:
        fetch_option_chain(c.underlying, c.expiry)
    except Exception as e:
        logger.warning("Option chain refresh for %s failed: %s", contract_symbol, e)
    return latest_quote(contract_symbol, max_age=None)


def underlying_close_on(underlying: str, day: date) -> float:
    """The underlying's last daily close on or before `day` — the expiry settlement price."""
    hist = yf.Ticker(underlying).history(start=day - timedelta(days=7), end=day + timedelta(days=1), interval="1d")
    if hist.empty:
        raise ValueError(f"No daily close for {underlying} on or before {day}")
    return float(hist["Close"].iloc[-1])


def option_price(contract_symbol: str) -> float:
    """
    Per-share value of a contract: intrinsic once expired, else the quote mid.

    Raises ValueError when nothing can be priced, matching
    DataService.get_latest_price's contract.
    """
    c = parse_occ(contract_symbol)
    if c.expiry < utc_today():
        return intrinsic_value(c, underlying_close_on(c.underlying, c.expiry))
    quote = fresh_quote(contract_symbol)
    if quote is None or quote.mid <= 0:
        raise ValueError(f"No option quote for {contract_symbol}")
    return quote.mid


@lru_cache(maxsize=1)
def risk_free_rate() -> float:
    """13-week T-bill yield (^IRX) as a decimal, once per process; 4% if unavailable."""
    try:
        hist = yf.Ticker("^IRX").history(period="5d", interval="1d")
        rate = float(hist["Close"].dropna().iloc[-1]) / 100.0
    except Exception as e:
        logger.warning("^IRX unavailable (%s); using r=%.2f", e, DEFAULT_RISK_FREE_RATE)
        return DEFAULT_RISK_FREE_RATE
    if not 0.0 <= rate <= 0.2:
        logger.warning("^IRX gave implausible r=%.4f; using %.2f", rate, DEFAULT_RISK_FREE_RATE)
        return DEFAULT_RISK_FREE_RATE
    return rate


def next_earnings_date(underlying: str, today: date | None = None) -> date | None:
    """
    The next scheduled earnings date on or after today, or None if unknown.

    yfinance's calendar first, then any future row in stock_earnings. None is a
    real answer ("unknown"), and callers decide whether unknown blocks a trade.
    """
    today = today or utc_today()
    try:
        cal = yf.Ticker(underlying).calendar or {}
        dates = cal.get("Earnings Date") or []
        upcoming = sorted(d for d in dates if isinstance(d, date) and d >= today)
        if upcoming:
            return upcoming[0]
    except Exception as e:
        logger.warning("Earnings calendar for %s unavailable: %s", underlying, e)
    try:
        with get_db_session() as session:
            row = (
                session.query(StockEarnings.report_date)
                .filter(StockEarnings.symbol == underlying, StockEarnings.report_date >= _naive(today))
                .order_by(StockEarnings.report_date)
                .first()
            )
        return row[0].date() if row else None
    except Exception as e:
        logger.warning("stock_earnings lookup for %s failed: %s", underlying, e)
        return None


@lru_cache(maxsize=32)
def dividend_yield(underlying: str) -> float:
    """Trailing dividend yield as a decimal (AAPL ~0.004), once per process; 0 if unknown."""
    try:
        info = yf.Ticker(underlying).info or {}
        value = _num(info.get("dividendYield"))
    except Exception as e:
        logger.warning("Dividend yield for %s unavailable (%s); using q=0", underlying, e)
        return 0.0
    if value is None or value < 0:
        return 0.0
    # yfinance switched from a fraction (0.0045) to a percent (0.45) in 2025.
    return value / 100.0 if value > 0.2 else value


def earnings_history(underlying: str, limit: int = 40, today: date | None = None) -> list[date]:
    """
    Past earnings report dates, oldest first: yfinance, else the stock_earnings
    table. Used to measure the stock's typical earnings-day move.
    """
    today = today or utc_today()
    dates: set[date] = set()
    try:
        df = yf.Ticker(underlying).get_earnings_dates(limit=limit)
        if df is not None and len(df):
            dates = {ts.date() for ts in pd.to_datetime(df.index)}
    except Exception as e:
        logger.warning("Earnings history for %s from yfinance failed: %s", underlying, e)
    if not dates:
        try:
            with get_db_session() as session:
                rows = session.query(StockEarnings.report_date).filter(StockEarnings.symbol == underlying).all()
            dates = {r[0].date() for r in rows if r[0] is not None}
        except Exception as e:
            logger.warning("stock_earnings history for %s failed: %s", underlying, e)
    return sorted(d for d in dates if d < today)


def recent_news(underlying: str, days: int = 3, refresh: bool = True, limit: int = 20) -> list[str]:
    """
    Headlines on `underlying` from the stock_news table, newest first. With
    refresh, the symbol's news is fetched into the table first: the daily
    loader only covers held symbols, at 22:00 UTC.
    """
    if refresh:
        try:
            from .stock_fundamentals_loader import load_stock_news_earnings_insider

            load_stock_news_earnings_insider({underlying})
        except Exception as e:
            logger.warning("News refresh for %s failed (using what the table has): %s", underlying, e)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    try:
        with get_db_session() as session:
            rows = (
                session.query(StockNews.title)
                .filter(StockNews.symbol == underlying, StockNews.published_at >= since)
                .order_by(StockNews.published_at.desc())
                .limit(limit)
                .all()
            )
    except Exception as e:
        logger.warning("stock_news read for %s failed: %s", underlying, e)
        return []
    return [str(r[0]) for r in rows if r[0]]


# ------------------------------------------------------------------
# Chains and contract selection
# ------------------------------------------------------------------


@dataclass(frozen=True)
class ChainView:
    """One expiry's chain as fetched (and stored) at one moment."""

    underlying: str
    expiry: date
    frame: pd.DataFrame
    spot: float
    live: bool
    today: date

    @property
    def T(self) -> float:
        return om.year_fraction(self.expiry, self.today)


def pick_expiry(expiries: Sequence[str | date], target_dte: int, today: date) -> date:
    """The first listed expiry at least `target_dte` days out."""
    parsed = sorted(e if isinstance(e, date) else date.fromisoformat(e) for e in expiries)
    eligible = [e for e in parsed if (e - today).days >= target_dte]
    if not eligible:
        raise ValueError(f"No expiry at least {target_dte} days out (have {parsed[-3:]})")
    return eligible[0]


def load_chain(underlying: str, target_dte: int, spot: float | None = None, today: date | None = None) -> ChainView:
    """Fetch (and store) the chain of the first expiry >= target_dte days out."""
    today = today or utc_today()
    ticker = yf.Ticker(underlying)
    expiry = pick_expiry(ticker.options, target_dte, today)
    frame = fetch_option_chain(underlying, expiry, ticker=ticker)
    spot = spot or frame.attrs.get("spot")
    if not spot:
        raise ValueError(f"No spot price for {underlying}; cannot select strikes")
    return ChainView(underlying, expiry, frame, float(spot), bool(frame.attrs.get("live", False)), today)


def priced_side(view: ChainView, right: str) -> pd.DataFrame:
    """
    One side of the chain with a usable per-share `price` column: the bid/ask
    mid for contracts with a two-sided market, else — only when NOTHING on the
    side is quoted, i.e. off-hours — the last trade.
    """
    side = view.frame[view.frame["option_type"] == right].copy()

    def _pos(col: str) -> pd.Series:
        return pd.to_numeric(side[col], errors="coerce").fillna(0) > 0

    quoted = side[_pos("bid") & _pos("ask")].copy()
    if len(quoted):
        quoted["price"] = (quoted["bid"].astype(float) + quoted["ask"].astype(float)) / 2
        return quoted
    # Outside market hours bid/ask are NULL (see fetch_option_chain); fall back
    # to contracts that at least traded rather than refusing to select.
    traded = side[_pos("last_price")].copy()
    traded["price"] = traded["last_price"].astype(float)
    return traded


def with_greeks(view: ChainView, right: str, r: float | None = None, q: float | None = None) -> pd.DataFrame:
    """
    priced_side plus `iv` and `delta`, solved from each contract's price. We
    solve IV ourselves: off-hours yfinance reports impliedVolatility = 1e-5 for
    the whole chain. Contracts whose price admits no IV get NaN. The dividend
    yield enters as Merton's q.
    """
    r = risk_free_rate() if r is None else r
    q = dividend_yield(view.underlying) if q is None else q
    side = priced_side(view, right)
    T = view.T
    ivs, deltas = [], []
    for strike, price in zip(side["strike"], side["price"], strict=True):
        iv = om.implied_volatility(float(price), view.spot, float(strike), T, r, right, q)
        ivs.append(iv if iv is not None else float("nan"))
        deltas.append(om.delta(view.spot, float(strike), T, r, iv, right, q) if iv is not None else float("nan"))
    side["iv"] = ivs
    side["delta"] = deltas
    return side


def atm_iv(view: ChainView, r: float | None = None, american: bool = False) -> float | None:
    """
    Implied vol at the money: the mean of the nearest-strike call's and put's
    IV. american=True re-solves the put on a binomial tree, which prices the
    early-exercise right Black-Scholes ignores (slower; a handful of contracts).
    """
    r = risk_free_rate() if r is None else r
    ivs = []
    for right in ("C", "P"):
        side = with_greeks(view, right, r).dropna(subset=["iv"])
        if not len(side):
            continue
        row = side.loc[(side["strike"] - view.spot).abs().idxmin()]
        iv = float(row["iv"])
        if american and right == "P":
            q = dividend_yield(view.underlying)
            tree_iv = om.implied_volatility_american(
                float(row["price"]), view.spot, float(row["strike"]), view.T, r, "P", q
            )
            iv = tree_iv if tree_iv is not None else iv
        ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else None


def smile_outliers(view: ChainView, top: int = 2, r: float | None = None) -> list[str]:
    """
    Contracts whose IV sits off a quadratic smile fitted to the out-of-the-money
    side of this expiry by more than half their own bid/ask spread (in vol
    terms), largest first. On a liquid chain there are rarely any: the spread
    is wider than the mispricing. Needs a live chain (bid/ask).
    """
    if not view.live or view.T <= 0:
        return []
    atm = atm_iv(view, r)
    if not atm:
        return []
    r = risk_free_rate() if r is None else r
    q = dividend_yield(view.underlying)
    otm = pd.concat(
        [
            with_greeks(view, "P", r).query("strike < @view.spot"),
            with_greeks(view, "C", r).query("strike >= @view.spot"),
        ]
    ).dropna(subset=["iv"])
    otm = otm[(otm["iv"] > 0) & (otm["delta"].abs() > 0.05)]
    if len(otm) < 6:
        return []
    z = [om.smile_z(view.spot, float(k), view.T, atm) for k in otm["strike"]]
    _, resid = om.fit_smile(z, otm["iv"].astype(float).to_numpy())
    out = []
    for (_, row), res in zip(otm.iterrows(), resid, strict=True):
        vega = om.vega(view.spot, float(row["strike"]), view.T, r, float(row["iv"]), q) * 100
        half_spread = (float(row["ask"]) - float(row["bid"])) / 2 if pd.notna(row.get("bid")) else math.inf
        if vega <= 0 or abs(res) <= half_spread / vega:
            continue
        out.append((abs(res), f"{row['contract_symbol']} {'rich' if res > 0 else 'cheap'} by {res:+.1%} vs smile"))
    return [text for _, text in sorted(out, reverse=True)[:top]]


def listed_expiries(underlying: str) -> list[date]:
    """Every expiry yfinance lists for `underlying`, sorted."""
    return sorted(date.fromisoformat(e) for e in yf.Ticker(underlying).options)


def _nearest_delta(side: pd.DataFrame, target_delta: float) -> pd.Series | None:
    known = side.dropna(subset=["delta"])
    if known.empty:
        return None
    return known.loc[(known["delta"].abs() - abs(target_delta)).abs().idxmin()]


def select_contract(
    underlying: str,
    right: str,
    target_dte: int,
    spot: float | None = None,
    today: date | None = None,
    delta: float | None = None,
    view: ChainView | None = None,
) -> str:
    """
    Pick the contract for "buy <underlying> as an option": the first expiry at
    least `target_dte` days out, preferring contracts with a two-sided market,
    and the strike nearest spot — or, with `delta`, the strike whose delta is
    nearest it (0.70 = a 70-delta call; puts by absolute delta).
    """
    view = view or load_chain(underlying, target_dte, spot=spot, today=today)
    candidates = priced_side(view, right)
    if candidates.empty:
        raise ValueError(f"No quoted {right} contracts for {underlying} {view.expiry}")

    best = None
    if delta is not None:
        best = _nearest_delta(with_greeks(view, right), delta)
        if best is None:
            logger.warning("No %s %s contract with a solvable IV; falling back to ATM", underlying, right)
    if best is None:
        best = candidates.loc[(candidates["strike"] - view.spot).abs().idxmin()]
    logger.info(
        "Selected %s for %s %s (spot %.2f, strike %.2f, expiry %s%s)",
        best["contract_symbol"],
        underlying,
        "call" if right == "C" else "put",
        view.spot,
        best["strike"],
        view.expiry,
        f", delta {best['delta']:.2f}" if delta is not None and "delta" in best else "",
    )
    return str(best["contract_symbol"])


@dataclass(frozen=True)
class StructurePick:
    """
    A multi-leg position. `legs` are (symbol, signed lots per unit): an OCC
    contract counts contracts, the underlying's own symbol counts lots of 100
    shares — so a covered call is ((underlying, +1), (call, -1)) and a bull put
    spread ((short_put, -1), (long_put, +1)). Legs may sit on different
    expiries (calendars, diagonals); `expiry` is then the nearest one.
    """

    underlying: str
    expiry: date
    legs: tuple[tuple[str, int], ...]
    live: bool
    spot: float


def _vertical(view: ChainView, right: str, short_delta: float, width: float) -> list[tuple[str, int]]:
    """Short leg at `short_delta`, long wing the strike nearest `width` further out of the money."""
    side = with_greeks(view, right)
    short = _nearest_delta(side, short_delta)
    if short is None:
        raise ValueError(f"No {view.underlying} {right} contract with a solvable IV on {view.expiry}")
    k = float(short["strike"])
    further = side[side["strike"] < k] if right == "P" else side[side["strike"] > k]
    if further.empty:
        raise ValueError(f"No {view.underlying} {right} wing beyond strike {k} on {view.expiry}")
    target = k - width if right == "P" else k + width
    wing = further.loc[(further["strike"] - target).abs().idxmin()]
    return [(str(short["contract_symbol"]), -1), (str(wing["contract_symbol"]), 1)]


def select_vertical(
    underlying: str,
    right: str,
    short_delta: float,
    width: float,
    target_dte: int,
    spot: float | None = None,
    today: date | None = None,
    view: ChainView | None = None,
) -> StructurePick:
    """
    A credit spread: right="P" is a bull put spread (short put, long lower put),
    right="C" a bear call spread (short call, long higher call).
    """
    view = view or load_chain(underlying, target_dte, spot=spot, today=today)
    legs = _vertical(view, right, short_delta, width)
    return StructurePick(underlying, view.expiry, tuple(legs), view.live, view.spot)


def select_iron_condor(
    underlying: str,
    short_delta: float,
    width: float,
    target_dte: int,
    spot: float | None = None,
    today: date | None = None,
    view: ChainView | None = None,
) -> StructurePick:
    """A bull put spread and a bear call spread on the same expiry."""
    view = view or load_chain(underlying, target_dte, spot=spot, today=today)
    legs = _vertical(view, "P", short_delta, width) + _vertical(view, "C", short_delta, width)
    return StructurePick(underlying, view.expiry, tuple(legs), view.live, view.spot)


def _atm_strike(view: ChainView) -> float:
    """The strike nearest spot that has a price on BOTH sides of the chain."""
    calls, puts = priced_side(view, "C"), priced_side(view, "P")
    common = sorted(set(calls["strike"].astype(float)) & set(puts["strike"].astype(float)))
    if not common:
        raise ValueError(f"No {view.underlying} strike priced on both sides for {view.expiry}")
    return min(common, key=lambda k: abs(k - view.spot))


def _contract_at(view: ChainView, right: str, strike: float) -> str:
    side = priced_side(view, right)
    if side.empty:
        raise ValueError(f"No priced {view.underlying} {right} contracts on {view.expiry}")
    row = side.loc[(side["strike"].astype(float) - strike).abs().idxmin()]
    return str(row["contract_symbol"])


def _pick(view: ChainView, legs: list[tuple[str, int]], *, live: bool | None = None) -> StructurePick:
    return StructurePick(view.underlying, view.expiry, tuple(legs), view.live if live is None else live, view.spot)


def select_short_leg(
    view: ChainView,
    right: str,
    delta: float,
    *,
    min_strike: float | None = None,
    max_strike: float | None = None,
    with_stock: bool = False,
) -> StructurePick:
    """
    One short option at `delta`: a cash-secured put (right="P"), or a covered
    call (right="C", with_stock=True buys the 100 shares in the same trade;
    without, the shares must already be held or the margin check refuses it).
    min/max_strike bound the choice, e.g. a covered call never below cost basis.
    """
    side = with_greeks(view, right)
    if min_strike is not None:
        side = side[side["strike"].astype(float) >= min_strike]
    if max_strike is not None:
        side = side[side["strike"].astype(float) <= max_strike]
    row = _nearest_delta(side, delta)
    if row is None:
        raise ValueError(f"No {view.underlying} {right} contract near delta {delta} within the strike bounds")
    legs = [(str(row["contract_symbol"]), -1)]
    return _pick(view, [(view.underlying, 1), *legs] if with_stock else legs)


def select_iron_butterfly(view: ChainView, width: float) -> StructurePick:
    """Short ATM straddle with long wings `width` away on each side: sells the at-the-money vol."""
    k = _atm_strike(view)
    return _pick(
        view,
        [
            (_contract_at(view, "P", k - width), 1),
            (_contract_at(view, "P", k), -1),
            (_contract_at(view, "C", k), -1),
            (_contract_at(view, "C", k + width), 1),
        ],
    )


def select_straddle(view: ChainView) -> StructurePick:
    """Long ATM call and put at the same strike: buys the at-the-money vol."""
    k = _atm_strike(view)
    return _pick(view, [(_contract_at(view, "C", k), 1), (_contract_at(view, "P", k), 1)])


def select_collar(view: ChainView, put_delta: float, call_delta: float, *, with_stock: bool = True) -> StructurePick:
    """100 shares, a long put at `put_delta` and a short call at `call_delta`, per unit."""
    put = _nearest_delta(with_greeks(view, "P"), put_delta)
    call = _nearest_delta(with_greeks(view, "C"), call_delta)
    if put is None or call is None:
        raise ValueError(f"No {view.underlying} collar strikes with solvable IV on {view.expiry}")
    legs = [(str(put["contract_symbol"]), 1), (str(call["contract_symbol"]), -1)]
    return _pick(view, [(view.underlying, 1), *legs] if with_stock else legs)


def select_calendar(front: ChainView, back: ChainView, right: str = "C") -> StructurePick:
    """
    Short the front expiry, long the back one, same (at-the-money) strike. The
    long leg outlives the short one, which is the shape margin_requirement
    treats conservatively.
    """
    if back.expiry <= front.expiry:
        raise ValueError(f"Calendar back expiry {back.expiry} must be after front {front.expiry}")
    k = _atm_strike(front)
    short = _contract_at(front, right, k)
    long = _contract_at(back, right, parse_occ(short).strike)
    if parse_occ(long).strike != parse_occ(short).strike:
        raise ValueError(f"No {right} strike {k} on both {front.expiry} and {back.expiry}")
    return _pick(front, [(short, -1), (long, 1)], live=front.live and back.live)


def select_diagonal(
    long_view: ChainView,
    short_view: ChainView,
    long_delta: float,
    short_delta: float,
    right: str = "C",
    *,
    min_short_strike: float | None = None,
) -> StructurePick:
    """
    A poor man's covered call (right="C"): a deep ITM long-dated call standing
    in for the shares, plus a short near-dated call further out of the money.
    """
    if long_view.expiry <= short_view.expiry:
        raise ValueError(f"Diagonal long expiry {long_view.expiry} must be after short {short_view.expiry}")
    long = _nearest_delta(with_greeks(long_view, right), long_delta)
    if long is None:
        raise ValueError(f"No {long_view.underlying} long {right} near delta {long_delta}")
    floor = max(float(long["strike"]), min_short_strike or 0.0)
    short = select_short_leg(short_view, right, short_delta, min_strike=floor)
    legs = [(str(long["contract_symbol"]), 1), *short.legs]
    return _pick(short_view, legs, live=long_view.live and short_view.live)


# ------------------------------------------------------------------
# Book-level views: legs, margin, entry value, greeks
# ------------------------------------------------------------------


def option_legs(portfolio: dict, underlying: str | None = None) -> dict[str, float]:
    """Every non-zero option holding, SHORT ones included, earliest expiry first."""
    keys = [
        k
        for k, q in portfolio.items()
        if is_option_symbol(k) and abs(q) > 1e-6 and (underlying is None or parse_occ(k).underlying == underlying)
    ]
    return {k: portfolio[k] for k in sorted(keys, key=lambda k: (parse_occ(k).expiry, k))}


def payoff_legs(positions: dict[str, float]) -> list[om.Leg]:
    """
    Holdings as option_math legs (share-equivalent qty, no premium). A key that
    is not an OCC symbol is the underlying's stock: a zero-strike call.
    """
    legs = []
    for key, qty in positions.items():
        if is_option_symbol(key):
            c = parse_occ(key)
            legs.append(om.Leg(c.right, c.strike, qty))
        else:
            legs.append(om.stock_leg(qty))
    return legs


def option_underlyings(portfolio: dict) -> set[str]:
    """Underlyings on which the book holds any option leg."""
    return {parse_occ(k).underlying for k in option_legs(portfolio)}


def structure_positions(portfolio: dict, underlying: str) -> dict[str, float]:
    """The option legs on `underlying` plus its shares: one structure, one payoff."""
    out = dict(option_legs(portfolio, underlying))
    shares = float(portfolio.get(underlying, 0.0) or 0.0)
    if abs(shares) > 1e-9:
        out[underlying] = shares
    return out


def margin_requirement(portfolio: dict) -> float:
    """
    Cash that must stay in the book to cover the structures' worst case.

    Per underlying: the largest amount its option legs AND its shares could
    cost at expiry (option_math.max_loss with zero premium — premiums and share
    costs are already out of cash). Shares count as zero-strike calls, so:
    - a bull put spread reserves its width, an iron condor its wider side;
    - a cash-secured put reserves its strike;
    - a covered call, a collar and a long-only book reserve nothing;
    - a naked short call or naked short stock is infinite, which is how
      trade_option_legs refuses them;
    - short stock beside long calls (a delta-hedged straddle) is bounded.

    Expiries are ignored: every leg is taken at its own expiry payoff as if all
    expired together. That is conservative for long-far / short-near structures
    (calendars, diagonals), where the long leg is worth at least its intrinsic
    value when the short one expires; the builders only create that shape.

    An underlying with no option legs and no short shares adds nothing, so
    ordinary stock bots are unaffected.
    """
    unders = option_underlyings(portfolio) | {
        k for k, q in portfolio.items() if k != "USD" and not is_option_symbol(k) and q < -1e-9
    }
    return sum(om.max_loss(payoff_legs(structure_positions(portfolio, u))) for u in unders)


def entry_value(bot_name: str, key: str) -> float:
    """
    Signed cash paid to open the CURRENT position in `key`: positive for a long
    (premium paid), negative for a short (credit received). Rebuilt from the
    trades table since the position was last flat; partial closes release cost
    proportionally (average cost).
    """
    with get_db_session() as session:
        rows = (
            session.query(Trade.isBuy, Trade.quantity, Trade.price)
            .filter(Trade.bot_name == bot_name, Trade.symbol == key)
            .order_by(Trade.timestamp, Trade.id)
            .all()
        )
    position = cost = 0.0
    for is_buy, qty, price in rows:
        signed = float(qty) if is_buy else -float(qty)
        new_position = position + signed
        if abs(new_position) < 1e-6:
            cost = 0.0
        elif position == 0 or (position > 0) == (signed > 0):
            cost += signed * float(price)  # opening or adding
        elif (position > 0) != (new_position > 0):
            cost = new_position * float(price)  # flipped through zero
        else:
            cost *= new_position / position  # reducing
        position = 0.0 if abs(new_position) < 1e-6 else new_position
    return cost


def opened_on(bot_name: str, key: str) -> date | None:
    """Date the CURRENT position in `key` was opened (last time it left zero), from trades."""
    with get_db_session() as session:
        rows = (
            session.query(Trade.isBuy, Trade.quantity, Trade.timestamp)
            .filter(Trade.bot_name == bot_name, Trade.symbol == key)
            .order_by(Trade.timestamp, Trade.id)
            .all()
        )
    position, opened = 0.0, None
    for is_buy, qty, ts in rows:
        if abs(position) < 1e-6:
            opened = ts.date() if ts is not None else None
        position += float(qty) if is_buy else -float(qty)
    return opened if abs(position) > 1e-6 else None


def structure_flows(bot_name: str, underlying: str, since: date) -> float:
    """
    Net cash from every trade on `underlying` — its options and its shares —
    since `since` (inclusive): + for sales, - for purchases. Add the current
    mark of what is still held and you have the structure's P&L, including
    whatever its share hedge has already realized.
    """
    with get_db_session() as session:
        rows = (
            session.query(Trade.symbol, Trade.isBuy, Trade.quantity, Trade.price)
            .filter(Trade.bot_name == bot_name, Trade.timestamp >= _naive(since))
            .all()
        )
    total = 0.0
    for symbol, is_buy, qty, price in rows:
        u = parse_occ(symbol).underlying if is_option_symbol(symbol) else symbol
        if u == underlying:
            total += (-1.0 if is_buy else 1.0) * float(qty) * float(price)
    return total


@dataclass(frozen=True)
class OptionPosition:
    key: str
    contract: OptionContract
    qty: float  # signed share-equivalents
    price: float  # per-share mark
    iv: float | None
    greeks: om.Greeks  # position-level (per-share greeks x qty)

    @property
    def contracts(self) -> float:
        return self.qty / CONTRACT_MULTIPLIER


@dataclass(frozen=True)
class OptionBook:
    """Everything a strategy needs to manage its options on one underlying."""

    underlying: str
    spot: float
    today: date
    positions: list[OptionPosition] = field(default_factory=list)
    entry_value: float = 0.0  # + premium paid, - credit received
    greeks: om.Greeks = om.ZERO_GREEKS
    shares: float = 0.0  # the underlying's stock held beside the options (signed)

    @property
    def empty(self) -> bool:
        return not self.positions

    @property
    def net_delta(self) -> float:
        """Share-equivalent delta of options plus shares: what a delta hedge flattens."""
        return self.greeks.delta + self.shares

    @property
    def value(self) -> float:
        """Signed mark-to-market: what closing at mid would pay (+) or cost (-)."""
        return sum(p.qty * p.price for p in self.positions)

    @property
    def pnl(self) -> float:
        return self.value - self.entry_value

    @property
    def pnl_pct(self) -> float:
        """P&L over the premium at stake (debit paid, or credit received)."""
        return self.pnl / abs(self.entry_value) if self.entry_value else 0.0

    @property
    def credit(self) -> float:
        return max(0.0, -self.entry_value)

    @property
    def dte(self) -> int | None:
        return min((p.contract.expiry - self.today).days for p in self.positions) if self.positions else None

    @property
    def max_loss(self) -> float:
        """Worst case at expiry from the ENTRY prices."""
        legs = [om.Leg(p.contract.right, p.contract.strike, p.qty, 0.0) for p in self.positions]
        return om.max_loss(legs) + self.entry_value if legs else 0.0


def build_book(
    underlying: str,
    positions: dict[str, float],
    prices: dict[str, float],
    spot: float,
    entry_values: dict[str, float],
    today: date | None = None,
    r: float | None = None,
    shares: float = 0.0,
    q: float | None = None,
) -> OptionBook:
    """Assemble an OptionBook from holdings, marks and entry values (no I/O besides r and q)."""
    today = today or utc_today()
    r = risk_free_rate() if r is None else r
    q = dividend_yield(underlying) if q is None else q
    out: list[OptionPosition] = []
    total = om.ZERO_GREEKS
    for key, qty in positions.items():
        c = parse_occ(key)
        price = float(prices.get(key, 0.0))
        T = om.year_fraction(c.expiry, today)
        iv = om.implied_volatility(price, spot, c.strike, T, r, c.right, q) if price > 0 else None
        g = om.greeks(spot, c.strike, T, r, iv, c.right, q) if iv is not None else om.ZERO_GREEKS
        pos_greeks = g.scaled(qty)
        out.append(OptionPosition(key, c, qty, price, iv, pos_greeks))
        total = total + pos_greeks
    return OptionBook(
        underlying=underlying,
        spot=spot,
        today=today,
        positions=out,
        entry_value=sum(entry_values.get(k, 0.0) for k in positions),
        greeks=total,
        shares=shares,
    )
