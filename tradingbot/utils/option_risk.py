"""
Daily risk snapshot of every option bot's book, per underlying, into `option_risk`.

Each bot manages its own structures; nothing else looks at what they add up to.
This does: dollar greeks, P&L under spot and vol shocks, worst case at expiry
and reserved margin, per bot and underlying, plus a logged total across bots.

Marks come from the newest stored quote of each contract (never a refetch):
the job runs after the close, when a refetch would only store last trades.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.db import Bot, OptionRiskSnapshot, get_db_session

logger = logging.getLogger(__name__)

BOT_PREFIX = "option_"


def _mark(key: str) -> float:
    quote = options.latest_quote(key, max_age=None)
    if quote is not None and quote.mid > 0:
        return quote.mid
    return options.option_price(key)


def risk_row(book: options.OptionBook, margin: float, r: float, q: float) -> dict[str, float]:
    """The option_risk fields of one underlying's book."""
    grid = om.stress_pnl(options.stress_legs(book), book.spot, r, q, (-0.20, -0.10, 0.0, 0.10), (0.0, 0.10))
    return {
        "spot": book.spot,
        "value": book.value + book.shares * book.spot,
        "delta_usd": book.net_delta * book.spot,
        "gamma_usd": book.greeks.gamma * book.spot**2 * 0.01,
        "vega": book.greeks.vega,
        "theta": book.greeks.theta,
        "stress_down20": float(grid.loc[-0.20, 0.0]),
        "stress_down10": float(grid.loc[-0.10, 0.0]),
        "stress_up10": float(grid.loc[0.10, 0.0]),
        "stress_vol_up10": float(grid.loc[0.0, 0.10]),
        "stress_crash": om.worst_stress(options.stress_legs(book), book.spot, r, q),
        "max_loss": book.max_loss,
        "margin": margin,
    }


def snapshot_option_risk(today: date | None = None, get_spot=None, r: float | None = None) -> dict[str, list[str]]:
    """
    Write one option_risk row per option bot and underlying held. Idempotent per day.

    `get_spot` (underlying -> price) and `r` are injectable for tests; the
    default spot is the last daily close from yfinance.
    Returns {"written": ["bot/underlying", ...], "failed": [...]}.
    """
    import yfinance as yf

    today = today or datetime.now(UTC).date()
    r = options.risk_free_rate() if r is None else r
    if get_spot is None:

        def get_spot(u: str) -> float:
            hist = yf.Ticker(u).history(period="5d", interval="1d")
            return float(hist["Close"].iloc[-1])

    with get_db_session() as session:
        books = {b.name: dict(b.portfolio or {}) for b in session.query(Bot).filter(Bot.name.like(f"{BOT_PREFIX}%"))}
    result: dict[str, list[str]] = {"written": [], "failed": []}
    totals = dict.fromkeys(("delta_usd", "vega", "theta", "stress_down20", "stress_crash"), 0.0)
    for bot_name, portfolio in sorted(books.items()):
        for underlying in sorted(options.option_underlyings(portfolio)):
            tag = f"{bot_name}/{underlying}"
            try:
                legs = options.option_legs(portfolio, underlying)
                spot = get_spot(underlying)
                prices = {k: _mark(k) for k in legs}
                entries = {k: options.entry_value(bot_name, k) for k in legs}
                shares = float(portfolio.get(underlying, 0.0) or 0.0)
                q = options.dividend_yield(underlying)
                book = options.build_book(underlying, legs, prices, spot, entries, today=today, r=r, shares=shares, q=q)
                margin = options.margin_requirement(
                    {k: v for k, v in portfolio.items() if k in legs or k == underlying}
                )
                row = risk_row(book, margin, r, q)
                with get_db_session() as session:
                    existing = (
                        session.query(OptionRiskSnapshot)
                        .filter_by(bot_name=bot_name, underlying=underlying, snapshot_date=today)
                        .first()
                    )
                    if existing is None:
                        existing = OptionRiskSnapshot(bot_name=bot_name, underlying=underlying, snapshot_date=today)
                        session.add(existing)
                    for k, v in row.items():
                        setattr(existing, k, v)
                for k in totals:
                    totals[k] += row[k]
                result["written"].append(tag)
            except Exception as exc:
                logger.warning("option risk: %s failed: %s", tag, exc)
                result["failed"].append(tag)
    logger.info(
        "option risk %s: %d books, all bots: delta $%.0f, vega $%.0f/pt, theta $%.0f/day, "
        "spot -20%% $%.0f, crash (-20%%, +30 vol) $%.0f; failed %s",
        today,
        len(result["written"]),
        totals["delta_usd"],
        totals["vega"],
        totals["theta"],
        totals["stress_down20"],
        totals["stress_crash"],
        result["failed"],
    )
    return result


def latest_risk(bot_name: str, max_age_days: int = 5) -> list[dict]:
    """The bot's newest option_risk rows (one per underlying) within max_age_days."""
    since = datetime.now(UTC).date() - timedelta(days=max_age_days)
    with get_db_session() as session:
        rows = (
            session.query(OptionRiskSnapshot)
            .filter(OptionRiskSnapshot.bot_name == bot_name, OptionRiskSnapshot.snapshot_date >= since)
            .order_by(OptionRiskSnapshot.snapshot_date)
            .all()
        )
        latest = {
            r.underlying: {c.name: getattr(r, c.name) for c in OptionRiskSnapshot.__table__.columns} for r in rows
        }
    return list(latest.values())
