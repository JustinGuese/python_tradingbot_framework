"""
Stock splits: keep share counts and the cached price history in step with them.

A portfolio stores share COUNTS ({"VGT": 0.2}), and nothing used to touch them
when a stock split. On the ex-date the price drops by the ratio while the count
stays, so an 8:1 split books a phantom loss of 7/8 of the position. That
happened: VGT split 8:1 on 2026-04-21 while EarningsInsiderTiltBot and
RegimeAdaptiveBot held it. Both then bought the "dip" back to target weight, and
the lost value stayed lost until it was refunded on 2026-09-30
(scripts/onetime_repair_split.py).

Three pieces:
  * split_events: detected splits. The splitsnapshot CronJob scans every held
    symbol and every symbol with recently cached history, hourly through the US
    session, in one batched yfinance call.
  * historic_data: yfinance serves split-ADJUSTED history, but the cache only
    ever appends bars, so rows written before a split stay at the old scale and
    the series shows a one-day crash (VGT: 809 -> 101). adjust_history divides
    those rows' prices by the ratio (and multiplies volume). It first finds where
    the stored series actually jumps, so it is idempotent and leaves alone rows
    that were fetched after the split.
  * applied_splits: one row per (bot, symbol, ex_date) once a bot's holding was
    adjusted, so no split is applied twice. apply_splits runs at the start of
    Bot.run, before the portfolio-worth calculation, and before the live copier
    reads a bot.

The quantity to scale is what the bot held when the split took effect, not what
it holds now: the current count minus the trades made since, which are already
in post-split units. That makes a late application exact. A bot that traded on
the ex-date before the split was detected (and bought the "dip") gets its
missing shares back and keeps what it bought; its next rebalance trims the
overweight. Trades made on the ex-date itself are classified by price when the
ratio is large enough to tell old prices from new ones, else by the opening bell.

Option contracts on a split underlying are converted the way OCC adjusts them
for a whole-number split: strike / ratio and ratio times the contracts. Any
other ratio leaves a non-standard contract (e.g. 150 shares deliverable) that
the book cannot represent, so apply_splits raises and the bot's run fails loudly.

Splits older than APPLY_WINDOW_DAYS are never applied to a book automatically.
That keeps the reconstruction short and the trade log it relies on recent; an
older miss needs a deliberate repair like the VGT one.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import market_calendar, options
from .bot_repository import BotRepository
from .db import AppliedSplit, HistoricData, SplitEvent, Trade, get_db_session
from .db import Bot as BotModel

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
APPLY_WINDOW_DAYS = 14
# Below this a split moves the price less than a volatile day does, so prices
# cannot tell pre-split trades or bars from post-split ones.
MIN_PRICE_RATIO = 1.5
# How close (as a share of log(ratio)) a jump away from the ex-date must come
# to the split ratio to be taken as the old-scale/new-scale boundary.
BOUNDARY_TOLERANCE = 0.25
HISTORY_LOOKBACK_DAYS = 30  # symbols with cached bars this recent are scanned
FETCH_CHUNK = 100
QTY_EPS = 1e-9


def ratio_label(ratio: float) -> str:
    """8.0 -> "8:1", 0.2 -> "1:5", 1.241 -> "1.241:1"."""
    if 0 < ratio < 1 and abs(1 / ratio - round(1 / ratio)) < 1e-6:
        return f"1:{round(1 / ratio)}"
    return f"{ratio:g}:1"


class SplitError(RuntimeError):
    """A split the book cannot represent: a non-whole-number split of a held option."""


@dataclass(frozen=True)
class Split:
    symbol: str
    ex_date: date
    ratio: float  # new shares per old share: 8.0 for 8:1, 0.2 for 1:5 reverse
    pre_split_close: float | None = None  # last close before the ex-date, old units


@dataclass
class SplitApplication:
    bot_name: str
    symbol: str
    ex_date: date
    ratio: float
    qty_at_ex: float
    qty_added: float
    converted: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.qty_added or self.converted)


# ------------------------------------------------------------------
# Detection (yfinance)
# ------------------------------------------------------------------


def parse_split_frame(df: pd.DataFrame | None) -> list[Split]:
    """yf.download(..., actions=True, group_by="ticker") -> the splits in it."""
    if df is None or df.empty or not isinstance(df.columns, pd.MultiIndex):
        return []
    out = []
    for symbol in df.columns.get_level_values(0).unique():
        sub = df[symbol]
        if "Stock Splits" not in sub.columns:
            continue
        ratios = sub["Stock Splits"].fillna(0.0).to_numpy()
        closes = sub["Close"]
        for i, ratio in enumerate(ratios):
            if ratio <= 0 or ratio == 1:
                continue
            prior = closes.iloc[:i].dropna()
            # yfinance's close is adjusted: times the ratio it is in pre-split units again.
            pre_close = float(prior.iloc[-1]) * float(ratio) if len(prior) else None
            out.append(Split(str(symbol), pd.Timestamp(sub.index[i]).date(), float(ratio), pre_close))
    return out


def fetch_recent_splits(symbols: list[str], period: str = "1mo") -> list[Split] | None:
    """Splits with an ex-date inside `period` for `symbols`; None if every request failed."""
    symbols = sorted(set(symbols))
    chunks = [symbols[i : i + FETCH_CHUNK] for i in range(0, len(symbols), FETCH_CHUNK)]
    out: list[Split] = []
    failed = 0
    for chunk in chunks:
        try:
            df = yf.download(
                chunk,
                period=period,
                interval="1d",
                actions=True,
                auto_adjust=True,
                group_by="ticker",
                progress=False,
                threads=True,
            )
        except Exception as e:
            logger.warning("Split scan of %d symbols failed: %s", len(chunk), e)
            failed += 1
            continue
        if df is None or df.empty:
            failed += 1
            continue
        out.extend(parse_split_frame(df))
    if chunks and failed == len(chunks):
        return None
    return out


def record_splits(session: Session, splits: list[Split]) -> list[Split]:
    """Store splits not seen before; returns the new ones."""
    new = []
    for s in splits:
        row = session.get(SplitEvent, (s.symbol, s.ex_date))
        if row is None:
            session.add(
                SplitEvent(symbol=s.symbol, ex_date=s.ex_date, ratio=s.ratio, pre_split_close=s.pre_split_close)
            )
            new.append(s)
        elif not math.isclose(row.ratio, s.ratio, rel_tol=1e-6):
            logger.error(
                "yfinance now reports %s's %s split as %g but %g is stored; keeping the stored ratio",
                s.symbol,
                s.ex_date,
                s.ratio,
                row.ratio,
            )
    session.flush()
    return new


# ------------------------------------------------------------------
# Cached history
# ------------------------------------------------------------------


def _cutoff(ex_date: date) -> datetime:
    """Midnight UTC of the ex-date, naive like every stored timestamp.

    Every US bar of the day before (daily ones stamped at midnight UTC, midnight
    New York or the open; intraday ones ending by 21:00 UTC) sorts before it, and
    every bar of the ex-date itself after it.
    """
    return datetime.combine(ex_date, time())


def find_unadjusted_boundary(bars: pd.DataFrame, cutoff: datetime, ratio: float) -> datetime | None:
    """
    The timestamp of the last bar still at pre-split scale, or None if nothing is.

    `bars` has timestamp, open, close. The old-scale rows are the ones written
    before the split was known, and the cache only appends, so they are a prefix
    of the series. Usually the prefix ends at the ex-date. It can end earlier: if
    a symbol's cache stopped at D-10 and the next fetch came after the split, that
    fetch appended adjusted bars from D-9 on. Where the prefix ends, the price
    jumps by the ratio.
    """
    bars = bars[bars["close"] > 0].sort_values("timestamp")
    before = bars[bars["timestamp"] < cutoff]
    if before.empty:
        return None
    after = bars[bars["timestamp"] >= cutoff]
    if after.empty:
        # Nothing written since the split, so every stored bar predates it.
        return before["timestamp"].iloc[-1].to_pydatetime()

    target = math.log(ratio)
    closes = before["close"].to_numpy()
    nxt = pd.concat([before.iloc[1:], after.iloc[:1]])
    next_open = nxt["open"].where(nxt["open"] > 0, nxt["close"]).to_numpy()
    jumps = [math.log(c / o) for c, o in zip(closes, next_open, strict=True)]

    straddle = jumps[-1]
    if abs(straddle - target) < abs(straddle):
        return before["timestamp"].iloc[-1].to_pydatetime()
    if abs(target) < math.log(MIN_PRICE_RATIO) or len(jumps) < 2:
        return None
    i = min(range(len(jumps) - 1), key=lambda k: abs(jumps[k] - target))
    if abs(jumps[i] - target) < BOUNDARY_TOLERANCE * abs(target):
        return before["timestamp"].iloc[i].to_pydatetime()
    return None


def adjust_history(session: Session, symbol: str, ex_date: date, ratio: float) -> dict[str, int]:
    """Rescale `symbol`'s old-scale historic_data rows, per interval. Returns rows changed per interval."""
    cutoff = _cutoff(ex_date)
    intervals = [i for (i,) in session.query(HistoricData.interval).filter(HistoricData.symbol == symbol).distinct()]
    changed = {}
    for interval in sorted(intervals):
        rows = (
            session.query(HistoricData.timestamp, HistoricData.open, HistoricData.close)
            .filter(HistoricData.symbol == symbol, HistoricData.interval == interval)
            .order_by(HistoricData.timestamp)
            .all()
        )
        bars = pd.DataFrame(rows, columns=["timestamp", "open", "close"])
        bars["timestamp"] = pd.to_datetime(bars["timestamp"])
        boundary = find_unadjusted_boundary(bars, cutoff, ratio)
        if boundary is None:
            continue
        n = (
            session.query(HistoricData)
            .filter(
                HistoricData.symbol == symbol,
                HistoricData.interval == interval,
                HistoricData.timestamp <= boundary,
            )
            .update(
                {
                    HistoricData.open: HistoricData.open / ratio,
                    HistoricData.high: HistoricData.high / ratio,
                    HistoricData.low: HistoricData.low / ratio,
                    HistoricData.close: HistoricData.close / ratio,
                    HistoricData.volume: HistoricData.volume * ratio,
                },
                synchronize_session=False,
            )
        )
        changed[interval] = n
        logger.info(
            "%s %s split %s: rescaled %d %s bars up to %s", symbol, ex_date, ratio_label(ratio), n, interval, boundary
        )
    return changed


# ------------------------------------------------------------------
# Books
# ------------------------------------------------------------------


def _ny_date(ts: datetime) -> date:
    return ts.replace(tzinfo=UTC).astimezone(NEW_YORK).date()


def _session_open(day: date) -> datetime:
    """The ex-date's opening bell as naive UTC."""
    opened = market_calendar.session_open_utc(day)
    if opened is None:
        opened = datetime.combine(day, market_calendar.REGULAR_OPEN, tzinfo=NEW_YORK).astimezone(UTC)
    return opened.replace(tzinfo=None)


def traded_after_split(ts: datetime, price: float | None, split: Split) -> bool:
    """Whether a trade at `ts` for `price` was in post-split units."""
    day = _ny_date(ts)
    if day != split.ex_date:
        return day > split.ex_date
    # On the ex-date itself, a bot that ran before the split was detected may
    # have used an adjusted price before the open, or a stale one after it.
    if split.pre_split_close and price and price > 0 and abs(math.log(split.ratio)) >= math.log(MIN_PRICE_RATIO):
        old = math.log(price / split.pre_split_close)
        new = math.log(price * split.ratio / split.pre_split_close)
        return abs(new) < abs(old)
    return ts >= _session_open(split.ex_date)


def shares_at_ex(session: Session, bot_name: str, split: Split, qty_now: float) -> float:
    """What the bot held when the split took effect: now, minus the trades made since."""
    since = datetime.combine(split.ex_date - timedelta(days=1), time())
    trades = (
        session.query(Trade.timestamp, Trade.quantity, Trade.price, Trade.isBuy)
        .filter(Trade.bot_name == bot_name, Trade.symbol == split.symbol, Trade.timestamp >= since)
        .all()
    )
    after = sum((q or 0.0) * (1 if buy else -1) for ts, q, p, buy in trades if traded_after_split(ts, p, split))
    qty = qty_now - after
    return 0.0 if abs(qty) < QTY_EPS else qty


def _convert_options(session: Session, bot_name: str, portfolio: dict, split: Split) -> dict[str, str]:
    """Re-key contracts opened before the split to their adjusted terms (in place). Returns old -> new."""
    opened_at = _session_open(split.ex_date)
    old = []
    for key in options.option_legs(portfolio, split.symbol):
        if options.parse_occ(key).expiry < split.ex_date:
            continue
        first = (
            session.query(func.min(Trade.timestamp)).filter(Trade.bot_name == bot_name, Trade.symbol == key).scalar()
        )
        if first is None or first < opened_at:
            old.append(key)
    if not old:
        return {}
    whole = round(split.ratio)
    if whole < 2 or abs(split.ratio - whole) > 1e-9:
        raise SplitError(
            f"{bot_name} holds {old} on {split.symbol}, which split {ratio_label(split.ratio)} on {split.ex_date}. "
            "Only whole-number splits leave standard contracts; close these by hand."
        )
    converted = {}
    for key in old:
        c = options.parse_occ(key)
        new_key = options.occ_symbol(
            options.OptionContract(c.underlying, c.expiry, c.right, round(c.strike / whole, 3))
        )
        qty = portfolio.pop(key)
        portfolio[new_key] = portfolio.get(new_key, 0.0) + qty * whole
        converted[key] = new_key
    return converted


def _apply_one(session: Session, bot_name: str, portfolio: dict, split: Split) -> SplitApplication:
    qty_now = float(portfolio.get(split.symbol, 0.0))
    qty_at_ex = shares_at_ex(session, bot_name, split, qty_now)
    added = qty_at_ex * (split.ratio - 1.0)
    if added:
        new_qty = qty_now + added
        if abs(new_qty) < 1e-6:
            portfolio.pop(split.symbol, None)
        else:
            portfolio[split.symbol] = new_qty
    converted = _convert_options(session, bot_name, portfolio, split)
    session.add(
        AppliedSplit(
            bot_name=bot_name,
            symbol=split.symbol,
            ex_date=split.ex_date,
            ratio=split.ratio,
            qty_at_ex=qty_at_ex,
            qty_added=added,
            note=", ".join(f"{a} -> {b}" for a, b in converted.items()) or None,
        )
    )
    return SplitApplication(bot_name, split.symbol, split.ex_date, split.ratio, qty_at_ex, added, converted)


def recent_splits(session: Session, today: date) -> list[Split]:
    """Splits whose ex-date falls inside the apply window."""
    rows = (
        session.query(SplitEvent)
        .filter(SplitEvent.ex_date <= today, SplitEvent.ex_date >= today - timedelta(days=APPLY_WINDOW_DAYS))
        .order_by(SplitEvent.ex_date, SplitEvent.symbol)
        .all()
    )
    return [Split(r.symbol, r.ex_date, r.ratio, r.pre_split_close) for r in rows]


def _applied(session: Session, bot_name: str) -> set[tuple[str, date]]:
    rows = session.query(AppliedSplit.symbol, AppliedSplit.ex_date).filter(AppliedSplit.bot_name == bot_name)
    return {(s, d) for s, d in rows}


def apply_splits(bot_name: str, today: date | None = None) -> list[SplitApplication]:
    """
    Apply every recent split not yet applied to `bot_name`'s book, in one locked
    transaction. A no-op costing one query when no split is recent.
    """
    today = today or datetime.now(UTC).date()
    with get_db_session() as session:
        splits = recent_splits(session, today)
        if not splits:
            return []
        seen = _applied(session, bot_name)
        if all((s.symbol, s.ex_date) in seen for s in splits):
            return []
        row = session.query(BotModel).filter_by(name=bot_name).with_for_update().one_or_none()
        if row is None:
            return []  # never create a row here; callers that need one fail on their own
        done = _applied(session, bot_name)  # again, under the lock
        portfolio = dict(row.portfolio or {})
        results = [_apply_one(session, bot_name, portfolio, s) for s in splits if (s.symbol, s.ex_date) not in done]
        if any(r.changed for r in results):
            row.portfolio = portfolio
            BotRepository.update_bot(row, session=session)
    for r in results:
        if r.changed:
            logger.warning(
                "SPLIT %s %s on %s applied to %s: held %.6f at the ex-date, %+.6f shares%s",
                r.symbol,
                ratio_label(r.ratio),
                r.ex_date,
                r.bot_name,
                r.qty_at_ex,
                r.qty_added,
                f", options {r.converted}" if r.converted else "",
            )
    return results


def apply_splits_all_bots(today: date | None = None) -> tuple[list[SplitApplication], dict[str, str]]:
    """apply_splits for every bot. Returns (applications that changed a book, {bot: error})."""
    today = today or datetime.now(UTC).date()
    with get_db_session() as session:
        if not recent_splits(session, today):
            return [], {}
        names = [n for (n,) in session.query(BotModel.name).order_by(BotModel.name)]
    changed: list[SplitApplication] = []
    failed: dict[str, str] = {}
    for name in names:
        try:
            changed += [r for r in apply_splits(name, today) if r.changed]
        except Exception as e:
            logger.error("Applying splits to %s failed: %s", name, e)
            failed[name] = str(e)
    return changed, failed


# ------------------------------------------------------------------
# The sweep (splitsnapshot CronJob)
# ------------------------------------------------------------------


@dataclass
class SweepResult:
    symbols: int
    new: list[Split]
    history: dict[tuple[str, date], dict[str, int]]
    applied: list[SplitApplication]
    failed: dict[str, str]


def sweep_universe() -> list[str]:
    """Every symbol a bot holds (option underlyings included) or has recently cached bars of."""
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=HISTORY_LOOKBACK_DAYS)
    with get_db_session() as session:
        portfolios = [dict(p or {}) for (p,) in session.query(BotModel.portfolio)]
        cached = {s for (s,) in session.query(HistoricData.symbol).filter(HistoricData.timestamp >= since).distinct()}
    held = set()
    for p in portfolios:
        held |= {k for k in p if k != "USD" and not options.is_option_symbol(k)}
        held |= options.option_underlyings(p)
    return sorted(held | cached)


def adjust_pending_history() -> dict[tuple[str, date], dict[str, int]]:
    """adjust_history for every split whose history has not been handled yet."""
    with get_db_session() as session:
        pending = [
            (e.symbol, e.ex_date, e.ratio)
            for e in session.query(SplitEvent)
            .filter(SplitEvent.history_adjusted_at.is_(None))
            .order_by(SplitEvent.ex_date)
        ]
    out = {}
    for symbol, ex_date, ratio in pending:
        with get_db_session() as session:
            event = session.get(SplitEvent, (symbol, ex_date), with_for_update=True)
            if event is None or event.history_adjusted_at is not None:
                continue
            out[(symbol, ex_date)] = adjust_history(session, symbol, ex_date, ratio)
            event.history_adjusted_at = datetime.now(UTC).replace(tzinfo=None)
    return out


def run_sweep(symbols: list[str], period: str = "1mo", today: date | None = None) -> SweepResult | None:
    """Detect, store, fix history, apply to books. None if yfinance returned nothing at all."""
    found = fetch_recent_splits(symbols, period)
    if found is None:
        return None
    with get_db_session() as session:
        new = record_splits(session, found)
    for s in new:
        logger.warning("New split: %s %s ex %s", s.symbol, ratio_label(s.ratio), s.ex_date)
    history = adjust_pending_history()
    applied, failed = apply_splits_all_bots(today)
    return SweepResult(len(set(symbols)), new, history, applied, failed)
