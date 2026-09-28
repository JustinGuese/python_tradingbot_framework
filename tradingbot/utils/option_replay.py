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

`ReplayMarket.view(...)` returns the same options.ChainView the live bots use,
so options.select_* builders and the pure rules in utils/option_rules work
unchanged. Driven by scripts/onetime_option_replay_backtest.py.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.db import OptionQuote, get_db_session

logger = logging.getLogger(__name__)

FALLBACK_SLIPPAGE = 0.02  # fills when a leg has no two-sided quote: mid ± 2%, as live off-hours


class ReplayMarket:
    """The latest snapshot of each contract per underlying per day, loaded once."""

    def __init__(self, start: date, end: date, underlyings: Iterable[str] | None = None):
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


@dataclass
class ReplayBook:
    """Cash and signed share-equivalent positions, filled and marked from a ReplayMarket."""

    market: ReplayMarket
    cash: float = 100_000.0
    positions: dict[str, float] = field(default_factory=dict)
    entry: dict[str, float] = field(default_factory=dict)  # underlying -> signed cash paid to open
    trades: int = 0
    _last_mark: dict[str, float] = field(default_factory=dict)

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

    def underlyings(self) -> set[str]:
        return {options.parse_occ(k).underlying for k in self.positions}

    def legs(self, underlying: str) -> dict[str, float]:
        return {k: q for k, q in self.positions.items() if options.parse_occ(k).underlying == underlying}

    def open(self, pick: options.StructurePick, units: int, day: date) -> bool:
        """Open `units` of a pick at bid/ask. False (nothing traded) if any leg is unpriceable."""
        fills = {}
        for key, lots in pick.legs:
            px = self._fill(key, day, lots > 0)
            if px is None:
                return False
            fills[key] = px
        paid = 0.0
        for key, lots in pick.legs:
            qty = lots * units * om.CONTRACT_MULTIPLIER
            self.positions[key] = self.positions.get(key, 0.0) + qty
            paid += qty * fills[key]
            self._last_mark[key] = fills[key]
        self.cash -= paid
        self.entry[pick.underlying] = self.entry.get(pick.underlying, 0.0) + paid
        self.trades += 1
        return True

    def close(self, underlying: str, day: date) -> None:
        for key, qty in self.legs(underlying).items():
            px = self._fill(key, day, qty < 0) or self.mark(key, day)
            self.cash += qty * px
            del self.positions[key]
        self.entry.pop(underlying, None)

    def settle(self, day: date) -> None:
        """Expired contracts pay intrinsic value off the recorded underlying price at expiry."""
        for key, qty in list(self.positions.items()):
            c = options.parse_occ(key)
            if c.expiry >= day:
                continue
            spot = self.market.spot(c.underlying, c.expiry)
            if spot is None:
                logger.warning("replay: no underlying price for %s at expiry; valued at 0", key)
            self.cash += qty * (options.intrinsic_value(c, spot) if spot else 0.0)
            del self.positions[key]
            if not self.legs(c.underlying):
                self.entry.pop(c.underlying, None)

    def value(self, underlying: str, day: date) -> float:
        return sum(q * self.mark(k, day) for k, q in self.legs(underlying).items())

    def pnl(self, underlying: str, day: date) -> float:
        return self.value(underlying, day) - self.entry.get(underlying, 0.0)

    def dte(self, underlying: str, day: date) -> int | None:
        legs = self.legs(underlying)
        return min((options.parse_occ(k).expiry - day).days for k in legs) if legs else None

    def equity(self, day: date) -> float:
        return self.cash + sum(q * self.mark(k, day) for k, q in self.positions.items())

    def max_loss(self, pick: options.StructurePick, day: date) -> float | None:
        """Worst-case loss of one unit at today's fills, dollars."""
        legs = []
        for key, lots in pick.legs:
            px = self._fill(key, day, lots > 0)
            if px is None:
                return None
            c = options.parse_occ(key)
            legs.append(om.Leg(c.right, c.strike, lots, px))
        return om.max_loss(legs) * om.CONTRACT_MULTIPLIER
