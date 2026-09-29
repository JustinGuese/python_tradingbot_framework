"""
Replay backtests over stored option chains: real quotes, real spreads.

Every option backtest so far prices synthetic options (Black-Scholes on a vol
index). This module instead replays what `option_quotes` actually recorded:
one snapshot per underlying per day (the latest of the day), filled at the
recorded bid/ask, marked at mid, settled at intrinsic off the recorded
underlying price. It is what the single-name bots (cross-vol, earnings crush,
the scanner) need before they can be judged, and it is built now so that
imported historical chains only have to land in `option_quotes` with the same
columns:

    underlying, contract_symbol (OCC), expiration, option_type (C/P), strike,
    bid, ask, last_price, volume, open_interest, underlying_price, snapshot_at

`ReplayMarket.view(...)` returns the same options.ChainView the live bots use.
ReplayDayMarket and ReplayHoldings implement utils/option_decide's Market and
Holdings, so the SAME decide function the live bot runs
(utils/option_strategies) decides here too. `execute` sizes and fills its
actions the way the live PortfolioManager does: worst-case loss at the fill
prices, capped by the cash left after margin. Driven by
scripts/onetime_option_replay_backtest.py through run_strategy().

One step per day: the day's last snapshot is both the morning exit run and the
afternoon entry run of the live schedule. Earnings timing, dividends and macro
dates come from the DB-first readers, which hold the past too.
"""

import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from tradingbot.utils import corporate_events, macro_calendar, market_calendar, option_surface, options
from tradingbot.utils import option_math as om
from tradingbot.utils.config import EXECUTION_CONFIG
from tradingbot.utils.db import OptionQuote, get_db_session
from tradingbot.utils.option_decide import Action, Close, Hedge, Holdings, Market, Open
from tradingbot.utils.option_rules import delta_hedge_shares

logger = logging.getLogger(__name__)

FALLBACK_SLIPPAGE = 0.02  # fills when a leg has no two-sided quote: mid ± 2%, as live off-hours
EARNINGS_EVENTS = 40  # options.earnings_events' default window


class ReplayMarket:
    """The latest snapshot of each contract per underlying per day, loaded once."""

    def __init__(self, start: date, end: date, underlyings: Iterable[str] | None = None):
        # Event tables, read once per run: a query per replayed day costs hours over a port-forward.
        self._events: dict[str, corporate_events.StoredEvents | None] = {}
        self._macro: pd.DataFrame | None = None
        with get_db_session() as session:
            query = session.query(OptionQuote).filter(
                OptionQuote.snapshot_at >= datetime.combine(start, datetime.min.time()),
                OptionQuote.snapshot_at < datetime.combine(end + timedelta(days=1), datetime.min.time()),
            )
            if underlyings is not None:
                query = query.filter(OptionQuote.underlying.in_(list(underlyings)))
            cols = [c.name for c in OptionQuote.__table__.columns]
            rows = [{c: getattr(q, c) for c in cols} for q in query.all()]
        frame = pd.DataFrame(rows)
        self._spots: dict[str, pd.Series] = {}
        if frame.empty:
            self.frame = frame
            self._by_day: dict[tuple[str, date], pd.DataFrame] = {}
            return
        frame["day"] = pd.to_datetime(frame["snapshot_at"]).dt.date
        frame["expiry"] = pd.to_datetime(frame["expiration"]).dt.date
        frame = frame.sort_values("snapshot_at").drop_duplicates(["contract_symbol", "day"], keep="last")
        self.frame = frame
        self._by_day = {k: g.reset_index(drop=True) for k, g in frame.groupby(["underlying", "day"])}
        px = frame.assign(px=pd.to_numeric(frame["underlying_price"], errors="coerce")).dropna(subset=["px"])
        for u, g in px.groupby("underlying"):
            self._spots[u] = g.groupby("day")["px"].last().sort_index()

    @property
    def days(self) -> list[date]:
        return sorted({d for _, d in self._by_day})

    def underlyings(self, day: date) -> list[str]:
        return sorted(u for u, d in self._by_day if d == day)

    def chain(self, underlying: str, day: date) -> pd.DataFrame | None:
        return self._by_day.get((underlying, day))

    def spot(self, underlying: str, day: date) -> float | None:
        """The recorded underlying price on `day`, else the last one before it."""
        series = self._spots.get(underlying)
        if series is None:
            return None
        before = series[series.index <= day]
        return float(before.iloc[-1]) if len(before) else None

    def view(self, underlying: str, day: date, target_dte: int) -> options.ChainView | None:
        """The first expiry at least target_dte days out, as the live bots see it."""
        g = self.chain(underlying, day)
        if g is None:
            return None
        expiries = sorted(e for e in g["expiry"].unique() if (e - day).days >= target_dte)
        if not expiries:
            return None
        one = g[g["expiry"] == expiries[0]].reset_index(drop=True)
        spot = self.spot(underlying, day)
        live = bool((pd.to_numeric(one["bid"], errors="coerce").fillna(0) > 0).any())
        return options.ChainView(underlying, expiries[0], one, spot, live, day) if spot else None

    def events(self, underlying: str) -> corporate_events.StoredEvents | None:
        """The stored earnings/dividends of `underlying` (None: not refreshed, use the live readers)."""
        if underlying not in self._events:
            self._events[underlying] = corporate_events.stored_events(underlying)
        return self._events[underlying]

    def macro_events(self) -> pd.DataFrame:
        if self._macro is None:
            try:
                self._macro = macro_calendar.macro_events_frame(start="1990-01-01").sort_values("event_date")
            except Exception as exc:
                logger.warning("Macro calendar unavailable: %s", exc)
                self._macro = pd.DataFrame(columns=["kind", "event_date"])
        return self._macro

    def quote(self, key: str, day: date) -> tuple[float | None, float | None, float | None]:
        """(bid, ask, mid) of a contract on `day`; Nones when it was not recorded."""
        c = options.parse_occ(key)
        g = self.chain(c.underlying, day)
        if g is None:
            return None, None, None
        row = g[g["contract_symbol"] == key]
        if row.empty:
            return None, None, None
        bid, ask = options._num(row["bid"].iloc[0]), options._num(row["ask"].iloc[0])
        last = options._num(row["last_price"].iloc[0])
        mid = (bid + ask) / 2 if bid and ask and bid > 0 and ask > 0 else last
        return bid, ask, mid


class SurfaceReplayMarket(ReplayMarket):
    """
    A ReplayMarket for sampled histories (the DoltHub backfill): a contract the
    day's chain did not record is quoted off that day's interpolated surface
    (utils/option_surface) instead of reading as missing. Recorded quotes are
    used as they are.

    live_expiries=True also replaces the sampled expiries a strategy may enter:
    view() builds the chain of the expiry the live bot would pick (the first
    Friday at least target_dte out) on a listed strike grid, quoted off the
    surface. The sample alone offers ~14/30/45-65 DTE buckets, so a 35-DTE
    strategy replayed on it enters at a median ~53 DTE, which is a different
    trade.
    """

    def __init__(self, start: date, end: date, underlyings: Iterable[str] | None = None, live_expiries: bool = False):
        super().__init__(start, end, underlyings)
        self.live_expiries = live_expiries
        self._surfaces: dict[tuple[str, date], option_surface.DaySurface | None] = {}
        self.recorded = self.modelled = 0

    def view(self, underlying: str, day: date, target_dte: int) -> options.ChainView | None:
        if not self.live_expiries:
            return super().view(underlying, day, target_dte)
        spot = self.spot(underlying, day)
        if not spot or self.surface(underlying, day) is None:
            return None
        expiry = live_expiry(day, target_dte)
        step = strike_step(underlying, spot)
        rows = []
        for i in range(math.ceil(spot * (1 - CHAIN_BAND) / step), math.floor(spot * (1 + CHAIN_BAND) / step) + 1):
            strike = round(i * step, 2)
            for right in ("C", "P"):
                key = f"{underlying}{expiry:%y%m%d}{right}{round(strike * 1000):08d}"
                bid, ask, mid = self._price(key, day)
                if mid is None or mid < MIN_QUOTE:
                    continue
                rows.append(
                    {
                        "contract_symbol": key,
                        "option_type": right,
                        "strike": strike,
                        "bid": bid,
                        "ask": ask,
                        "last_price": None,
                        "expiry": expiry,
                    }
                )
        if not rows:
            return None
        return options.ChainView(underlying, expiry, pd.DataFrame(rows), spot, True, day)

    def surface(self, underlying: str, day: date) -> option_surface.DaySurface | None:
        key = (underlying, day)
        if key not in self._surfaces:
            chain, spot = self.chain(underlying, day), self.spot(underlying, day)
            self._surfaces[key] = (
                option_surface.build_surface(underlying, chain, spot, day) if chain is not None and spot else None
            )
        return self._surfaces[key]

    def _price(self, key: str, day: date) -> tuple[float | None, float | None, float | None]:
        bid, ask, mid = super().quote(key, day)
        if mid is not None:
            return bid, ask, mid
        c = options.parse_occ(key)
        surface = self.surface(c.underlying, day)
        return surface.quote(c.right, c.strike, c.expiry) if surface is not None else (None, None, None)

    def quote(self, key: str, day: date) -> tuple[float | None, float | None, float | None]:
        recorded = super().quote(key, day)
        if recorded[2] is not None:
            self.recorded += 1
            return recorded
        quote = self._price(key, day)
        if quote[2] is not None:
            self.modelled += 1
        return quote


CHAIN_BAND = 0.35  # listed strikes within +/- 35% of spot, as the synthetic chain
MIN_QUOTE = 0.01  # a contract is listed with a market down to a penny mid
STRIKE_STEPS = {"SPY": 1.0, "QQQ": 1.0}


def strike_step(underlying: str, spot: float) -> float:
    """The listed strike spacing near the money: $1 for the index ETFs, $2.50 / $5 for single names."""
    return STRIKE_STEPS.get(underlying, 2.5 if spot < 250 else 5.0)


def live_expiry(day: date, target_dte: int) -> date:
    """The first Friday at least target_dte days out (the Thursday when that Friday is a holiday)."""
    first = day + timedelta(days=target_dte)
    friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return friday if market_calendar.is_session(friday) else friday - timedelta(days=1)


@dataclass
class ReplayBook:
    """
    Cash, signed share-equivalent option positions and hedge shares, filled
    and marked from a ReplayMarket. It tracks what the live book gets from the
    trades table: each contract's entry value, when each structure opened,
    and its cash flows (for a hedged structure's P&L).
    """

    market: ReplayMarket
    cash: float = 100_000.0
    positions: dict[str, float] = field(default_factory=dict)
    shares: dict[str, float] = field(default_factory=dict)
    entry_by_key: dict[str, float] = field(default_factory=dict)  # contract -> signed cash paid to open
    opened: dict[str, date] = field(default_factory=dict)  # underlying -> first open of the current structure
    flows: dict[str, float] = field(default_factory=dict)  # underlying -> net cash since that open
    trades: int = 0
    _last_mark: dict[str, float] = field(default_factory=dict)

    # -- prices ------------------------------------------------------------

    def _fill(self, key: str, day: date, buy: bool) -> float | None:
        bid, ask, mid = self.market.quote(key, day)
        side = ask if buy else bid
        if side and side > 0:
            return side
        if mid and mid > 0:
            return mid * (1 + FALLBACK_SLIPPAGE) if buy else mid * (1 - FALLBACK_SLIPPAGE)
        return None

    def mark(self, key: str, day: date) -> float:
        """Mid on `day`; the last known mark if the contract was not recorded that day."""
        _, _, mid = self.market.quote(key, day)
        if mid is not None and mid > 0:
            self._last_mark[key] = mid
        return self._last_mark.get(key, 0.0)

    def _stock_fill(self, underlying: str, day: date, buy: bool) -> float | None:
        spot = self.market.spot(underlying, day)
        if spot is None:
            return None
        cfg = EXECUTION_CONFIG
        return cfg.buy_execution_price(spot) if buy else cfg.sell_execution_price(spot)

    # -- state -------------------------------------------------------------

    @property
    def entry(self) -> dict[str, float]:
        """underlying -> signed cash paid to open its option legs."""
        out: dict[str, float] = {}
        for key in self.positions:
            u = options.parse_occ(key).underlying
            out[u] = out.get(u, 0.0) + self.entry_by_key.get(key, 0.0)
        return out

    def underlyings(self) -> set[str]:
        return {options.parse_occ(k).underlying for k in self.positions}

    def legs(self, underlying: str) -> dict[str, float]:
        return {k: q for k, q in self.positions.items() if options.parse_occ(k).underlying == underlying}

    def portfolio(self) -> dict[str, float]:
        """The book in the live bots' portfolio shape, for options.margin_requirement."""
        return {"USD": self.cash, **self.positions, **{u: q for u, q in self.shares.items() if abs(q) > 1e-9}}

    def _flow(self, underlying: str, cash: float, day: date) -> None:
        self.opened.setdefault(underlying, day)
        self.flows[underlying] = self.flows.get(underlying, 0.0) + cash

    def _forget_if_flat(self, underlying: str) -> None:
        if not self.legs(underlying) and abs(self.shares.get(underlying, 0.0)) < 1e-9:
            self.opened.pop(underlying, None)
            self.flows.pop(underlying, None)
            self.shares.pop(underlying, None)

    # -- trading -----------------------------------------------------------

    def open(self, pick: options.StructurePick, units: int, day: date) -> bool:
        """Open `units` of a pick at bid/ask. False (nothing traded) if any leg is unpriceable."""
        fills = {}
        for key, lots in pick.legs:
            px = (
                self._fill(key, day, lots > 0)
                if options.is_option_symbol(key)
                else self._stock_fill(key, day, lots > 0)
            )
            if px is None:
                return False
            fills[key] = px
        paid = 0.0
        for key, lots in pick.legs:
            qty = lots * units * om.CONTRACT_MULTIPLIER
            cost = qty * fills[key]
            if options.is_option_symbol(key):
                self.positions[key] = self.positions.get(key, 0.0) + qty
                self.entry_by_key[key] = self.entry_by_key.get(key, 0.0) + cost
                self._last_mark[key] = fills[key]
            else:
                self.shares[key] = self.shares.get(key, 0.0) + qty
            paid += cost
        self.cash -= paid
        self._flow(pick.underlying, -paid, day)
        self.trades += 1
        return True

    def trade_shares(self, underlying: str, qty: float, day: date) -> bool:
        px = self._stock_fill(underlying, day, qty > 0)
        if px is None or qty == 0:
            return False
        self.cash -= qty * px
        self.shares[underlying] = self.shares.get(underlying, 0.0) + qty
        self._flow(underlying, -qty * px, day)
        return True

    def close(self, underlying: str, day: date, include_stock: bool = False) -> None:
        for key, qty in self.legs(underlying).items():
            px = self._fill(key, day, qty < 0) or self.mark(key, day)
            self.cash += qty * px
            self._flow(underlying, qty * px, day)
            del self.positions[key]
            self.entry_by_key.pop(key, None)
        held = self.shares.get(underlying, 0.0)
        if include_stock and abs(held) > 1e-9:
            self.trade_shares(underlying, -held, day)
        self._forget_if_flat(underlying)

    def settle(self, day: date) -> None:
        """
        Expired contracts pay intrinsic value off the recorded underlying price
        at expiry. Hedge shares left without options are sold at the day's
        spot: no structure is left for them to hedge.
        """
        touched = set()
        for key, qty in list(self.positions.items()):
            c = options.parse_occ(key)
            if c.expiry >= day:
                continue
            spot = self.market.spot(c.underlying, c.expiry)
            if spot is None:
                logger.warning("replay: no underlying price for %s at expiry; valued at 0", key)
            value = qty * (options.intrinsic_value(c, spot) if spot else 0.0)
            self.cash += value
            self._flow(c.underlying, value, day)
            del self.positions[key]
            self.entry_by_key.pop(key, None)
            touched.add(c.underlying)
        for u in touched:
            if not self.legs(u) and abs(self.shares.get(u, 0.0)) > 1e-9:
                self.trade_shares(u, -self.shares[u], day)
            self._forget_if_flat(u)

    # -- valuation ---------------------------------------------------------

    def value(self, underlying: str, day: date) -> float:
        return sum(q * self.mark(k, day) for k, q in self.legs(underlying).items())

    def pnl(self, underlying: str, day: date) -> float:
        return self.value(underlying, day) - self.entry.get(underlying, 0.0)

    def dte(self, underlying: str, day: date) -> int | None:
        legs = self.legs(underlying)
        return min((options.parse_occ(k).expiry - day).days for k in legs) if legs else None

    def equity(self, day: date) -> float:
        stock = sum(q * (self.market.spot(u, day) or 0.0) for u, q in self.shares.items())
        return self.cash + stock + sum(q * self.mark(k, day) for k, q in self.positions.items())

    def max_loss(self, pick: options.StructurePick, day: date) -> float | None:
        """Worst-case loss of one unit at today's fills, dollars."""
        legs = []
        for key, lots in pick.legs:
            if not options.is_option_symbol(key):
                px = self._stock_fill(key, day, lots > 0)
                if px is None:
                    return None
                legs.append(om.stock_leg(lots, px))
                continue
            px = self._fill(key, day, lots > 0)
            if px is None:
                return None
            c = options.parse_occ(key)
            legs.append(om.Leg(c.right, c.strike, lots, px))
        return om.max_loss(legs) * om.CONTRACT_MULTIPLIER


# ------------------------------------------------------------------
# The decide interfaces over a replay
# ------------------------------------------------------------------


class PriceHistory:
    """Daily OHLC per symbol (stocks, ETFs, vol indices), read only up to a given day."""

    def __init__(self, ohlc: dict[str, pd.DataFrame]):
        self._ohlc = {}
        for sym, frame in ohlc.items():
            f = frame.rename(columns=str.lower).copy()
            f.index = pd.DatetimeIndex(f.index).tz_localize(None).normalize()
            self._ohlc[sym] = f.sort_index()

    @classmethod
    def download(cls, symbols: Iterable[str], start: date, years_before: int = 30) -> "PriceHistory":
        import yfinance as yf

        symbols = sorted(set(symbols))
        begin = (pd.Timestamp(start) - pd.DateOffset(years=years_before)).date()
        raw = yf.download(symbols, start=str(begin), auto_adjust=True, progress=False, group_by="ticker")
        out = {}
        for sym in symbols:
            try:
                frame = raw[sym] if len(symbols) > 1 else raw
                if isinstance(frame.columns, pd.MultiIndex):
                    frame.columns = frame.columns.get_level_values(-1)
                out[sym] = frame[["Open", "High", "Low", "Close"]].dropna()
            except KeyError:
                logger.warning("replay: no price history for %s", sym)
        return cls(out)

    def upto(self, symbol: str, day: date, years: float | None = None) -> pd.DataFrame | None:
        frame = self._ohlc.get(symbol)
        if frame is None:
            return None
        end = pd.Timestamp(day)
        part = frame.loc[:end]
        if years is not None:
            part = part.loc[end - pd.DateOffset(days=int(365 * years)) :]
        return part if len(part) else None

    def level(self, symbol: str, day: date) -> float | None:
        part = self.upto(symbol, day)
        return float(part["close"].iloc[-1]) if part is not None else None


class ReplayDayMarket(Market):
    """utils/option_decide.Market as of one replayed day."""

    def __init__(self, market: ReplayMarket, day: date, prices: PriceHistory):
        close = market_calendar.session_close_utc(day)
        # The live entry runs sit just before the close; the snapshot is the day's last.
        now = (close - timedelta(minutes=15)) if close else datetime.combine(day, datetime.min.time(), UTC)
        super().__init__(day, now)
        self._market = market
        self._prices = prices

    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        return self._market.view(underlying, self.today, target_dte)

    def vol_index(self, symbol: str) -> float | None:
        return self._prices.level(symbol, self.today)

    def closes(self, underlying: str) -> pd.Series:
        part = self._prices.upto(underlying, self.today)  # every close so far: see Market.closes
        return part["close"] if part is not None else pd.Series(dtype=float)

    def ohlc(self, symbols) -> dict[str, pd.DataFrame]:
        out = {}
        for u in symbols:
            part = self._prices.upto(u, self.today, years=3)
            if part is not None:
                out[u] = part
        return out

    # The event readers answer from the run's cached tables, point in time.

    def earnings_events(self, underlying: str) -> list[tuple[date, bool | None]]:
        stored = self._market.events(underlying)
        return stored.known_by(self.today, EARNINGS_EVENTS) if stored else super().earnings_events(underlying)

    def next_earnings_event(self, underlying: str) -> tuple[date, bool | None] | None:
        stored = self._market.events(underlying)
        return stored.next_earnings(self.today) if stored else super().next_earnings_event(underlying)

    def next_earnings_date(self, underlying: str) -> date | None:
        stored = self._market.events(underlying)
        if stored is None:
            return super().next_earnings_date(underlying)
        event = stored.next_earnings(self.today)
        return event[0] if event else None

    def next_dividend(self, underlying: str) -> tuple[date, float] | None:
        stored = self._market.events(underlying)
        return stored.next_dividend(self.today) if stored else super().next_dividend(underlying)

    def next_macro_event(self) -> tuple[tuple[str, date] | None, int | None]:
        return macro_calendar.next_event_in(self._market.macro_events(), self.today)


class ReplayHoldings(Holdings):
    """utils/option_decide.Holdings over a ReplayBook on one day."""

    def __init__(self, book: ReplayBook, day: date):
        self._book = book
        self._day = day

    def underlyings(self) -> set[str]:
        return self._book.underlyings()

    def book(self, underlying: str) -> options.OptionBook:
        legs = self._book.legs(underlying)
        spot = self._book.market.spot(underlying, self._day) or 0.0
        marks = {k: self._book.mark(k, self._day) for k in legs}
        return options.build_book(
            underlying,
            legs,
            marks,
            spot,
            self._book.entry_by_key,
            today=self._day,
            shares=self._book.shares.get(underlying, 0.0),
        )

    def opened_on(self, underlying: str) -> date | None:
        return self._book.opened.get(underlying)

    def flows_since(self, underlying: str, since: date) -> float:
        return self._book.flows.get(underlying, 0.0)

    def equity(self) -> float:
        return self._book.equity(self._day)


def execute(
    actions: list[Action], book: ReplayBook, day: date, holdings: ReplayHoldings | None = None
) -> tuple[int, int]:
    """
    Carry out a decide function's actions on the replay book, as the live
    OptionStrategyBot does. Returns (structures opened, underlyings closed).
    """
    holdings = holdings or ReplayHoldings(book, day)
    opened = closed = 0
    for action in actions:
        if isinstance(action, Close):
            book.close(action.underlying, day, include_stock=action.include_stock)
            closed += 1
        elif isinstance(action, Open):
            if not action.pick.live:
                continue
            per_unit = book.max_loss(action.pick, day)
            free = book.cash - options.margin_requirement(book.portfolio())
            units = options.units_for_risk(per_unit or 0.0, action.max_risk_usd, free)
            if units and book.open(action.pick, units, day):
                opened += 1
        elif isinstance(action, Hedge):
            if action.underlying not in book.underlyings():
                continue
            b = holdings.book(action.underlying)
            qty = delta_hedge_shares(b.net_delta, b.spot, b.greeks.gamma, action.band_usd, action.ww)
            if qty:
                book.trade_shares(action.underlying, qty, day)
        else:
            raise TypeError(f"Unknown action {action!r}")
    return opened, closed


def run_strategy(
    market: ReplayMarket,
    decide: Callable[[Market, Holdings], list[Action]],
    prices: PriceHistory,
    cash: float = 100_000.0,
) -> tuple[pd.Series, ReplayBook]:
    """Replay every stored day through `decide` (a live bot's strategy function). Returns (equity curve, book)."""
    book, curve = ReplayBook(market, cash=cash), {}
    for day in market.days:
        book.settle(day)
        holdings = ReplayHoldings(book, day)
        actions = decide(ReplayDayMarket(market, day, prices), holdings)
        execute(actions, book, day, holdings)
        curve[pd.Timestamp(day)] = book.equity(day)
    return pd.Series(curve, dtype=float), book
