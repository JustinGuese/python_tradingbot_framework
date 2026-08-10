"""Loader for stock news, earnings, and insider trades from yfinance."""

import contextlib
import logging
import time
from datetime import UTC, datetime

import pandas as pd
import yfinance as yf

from .db import (
    Bot,
    StockEarnings,
    StockInsiderTrade,
    StockNews,
    get_db_session,
)
from .helpers import ensure_utc_timestamp

logger = logging.getLogger(__name__)

# Default limits for yfinance fetches
NEWS_COUNT = 20
EARNINGS_LIMIT = 24
# Small delay between symbols to reduce rate-limit risk (seconds)
SYMBOL_DELAY_SECONDS = 0.5


def get_portfolio_symbols(session) -> set[str]:
    """
    Return the set of all trading symbols from every bot's portfolio, excluding USD.

    Args:
        session: SQLAlchemy session (e.g. from get_db_session).

    Returns:
        Set of symbol strings.
    """
    bots = session.query(Bot).all()
    symbols = set()
    for bot in bots:
        if bot.portfolio:
            for key in bot.portfolio:
                if key and key != "USD":
                    symbols.add(key)
    return symbols


def _published_at_from_unix(ts) -> datetime:
    """Convert yfinance Unix timestamp to UTC datetime."""
    if ts is None:
        return datetime.now(UTC)
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(int(ts), tz=UTC)
    return ensure_utc_timestamp(pd.Timestamp(ts)).to_pydatetime()


def _load_news_for_symbol(symbol: str, existing_links: set[tuple]) -> list:
    """Fetch news for one symbol and return list of StockNews to insert (new only)."""
    try:
        ticker = yf.Ticker(symbol)
        # get_news returns list of dicts: title, link, publisher, date, etc.
        raw = ticker.get_news(count=NEWS_COUNT, tab="news")
    except Exception as e:
        logger.warning("Failed to fetch news for %s: %s", symbol, e)
        return []

    if not raw or not isinstance(raw, list):
        return []

    to_add = []
    for item in raw:
        link = item.get("link") or item.get("url") or ""
        if not link:
            continue
        key = (symbol, link)
        if key in existing_links:
            continue
        title = item.get("title") or ""
        published_at = _published_at_from_unix(item.get("date"))
        related = item.get("related_tickers")
        related_tickers = related if isinstance(related, list) else None

        to_add.append(
            StockNews(
                symbol=symbol,
                title=title,
                link=link,
                publisher=item.get("publisher"),
                publisher_url=item.get("publisher_url"),
                published_at=published_at,
                related_tickers=related_tickers,
            )
        )
        existing_links.add(key)

    return to_add


def _load_earnings_for_symbol(symbol: str, existing_dates: set[tuple]) -> list:
    """Fetch earnings for one symbol and return list of StockEarnings to insert (new only)."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.get_earnings_dates(limit=EARNINGS_LIMIT)
    except Exception as e:
        logger.warning("Failed to fetch earnings for %s: %s", symbol, e)
        return []

    if df is None or df.empty:
        return []

    to_add = []
    # earnings_dates: index is report date (timezone-aware), columns vary
    for report_date, row in df.iterrows():
        if pd.isna(report_date):
            continue
        report_dt = ensure_utc_timestamp(pd.Timestamp(report_date)).to_pydatetime()
        key = (symbol, report_dt)
        if key in existing_dates:
            continue

        # Column names can be 'EPS Estimate', 'Reported EPS', 'Surprise(%)' etc.
        row_dict = row.to_dict() if hasattr(row, "to_dict") else {}
        eps_estimate = None
        reported_eps = None
        surprise_pct = None
        for k, v in row_dict.items():
            k_lower = (k or "").lower()
            if "estimate" in k_lower and "eps" in k_lower:
                with contextlib.suppress(TypeError, ValueError):
                    eps_estimate = float(v) if v is not None and not pd.isna(v) else None
            elif "reported" in k_lower and "eps" in k_lower:
                with contextlib.suppress(TypeError, ValueError):
                    reported_eps = float(v) if v is not None and not pd.isna(v) else None
            elif "surprise" in k_lower:
                with contextlib.suppress(TypeError, ValueError):
                    surprise_pct = float(v) if v is not None and not pd.isna(v) else None

        to_add.append(
            StockEarnings(
                symbol=symbol,
                report_date=report_dt,
                eps_estimate=eps_estimate,
                reported_eps=reported_eps,
                surprise_pct=surprise_pct,
                fiscal_period=None,
            )
        )
        existing_dates.add(key)

    return to_add


def _insider_key(symbol: str, transaction_date: datetime, insider_name, transaction_type, shares) -> tuple:
    """Normalize key for deduplication (handle None)."""
    return (
        symbol,
        transaction_date,
        insider_name if insider_name is not None else "",
        transaction_type if transaction_type is not None else "",
        float(shares) if shares is not None and not (isinstance(shares, float) and pd.isna(shares)) else 0.0,
    )


def _load_insider_for_symbol(symbol: str, existing_insider_keys: set[tuple]) -> list:
    """Fetch insider transactions for one symbol and return list of StockInsiderTrade to insert (new only)."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.insider_transactions
    except Exception as e:
        logger.warning("Failed to fetch insider transactions for %s: %s", symbol, e)
        return []

    if df is None or df.empty:
        return []

    # yfinance columns often: Start Date, Insider, Transaction, Shares, Value
    def col_lower_match(cols, *names):
        for c in df.columns:
            cstr = (c or "").lower()
            for n in names:
                if n in cstr:
                    return c
        return None

    date_col = col_lower_match(df.columns, "start date", "date", "transaction date") or df.columns[0]
    insider_col = col_lower_match(df.columns, "insider", "name")
    type_col = col_lower_match(df.columns, "transaction", "type")
    shares_col = col_lower_match(df.columns, "shares")
    value_col = col_lower_match(df.columns, "value")

    to_add = []
    for _, row in df.iterrows():
        try:
            raw_date = row.get(date_col) if hasattr(row, "get") else row[date_col]
            if pd.isna(raw_date):
                continue
            transaction_date = ensure_utc_timestamp(pd.Timestamp(raw_date)).to_pydatetime()
        except (TypeError, KeyError, ValueError):
            continue

        insider_name = None
        if insider_col and insider_col in row.index:
            v = row[insider_col]
            insider_name = str(v).strip() if v is not None and not pd.isna(v) else None

        transaction_type = None
        if type_col and type_col in row.index:
            v = row[type_col]
            transaction_type = str(v).strip() if v is not None and not pd.isna(v) else None

        shares = None
        if shares_col and shares_col in row.index:
            v = row[shares_col]
            with contextlib.suppress(TypeError, ValueError):
                shares = float(v) if v is not None and not pd.isna(v) else None

        value = None
        if value_col and value_col in row.index:
            v = row[value_col]
            with contextlib.suppress(TypeError, ValueError):
                value = float(v) if v is not None and not pd.isna(v) else None

        key = _insider_key(symbol, transaction_date, insider_name, transaction_type, shares)
        if key in existing_insider_keys:
            continue

        to_add.append(
            StockInsiderTrade(
                symbol=symbol,
                transaction_date=transaction_date,
                insider_name=insider_name,
                transaction_type=transaction_type,
                shares=shares,
                value=value,
            )
        )
        existing_insider_keys.add(key)

    return to_add


def load_stock_news_earnings_insider(symbols: set[str]) -> None:
    """
    Fetch news, earnings, and insider trades from yfinance for the given symbols
    and persist only new rows (deduplicated) to the database.

    Uses its own DB session(s). Skips symbols that are not equity tickers or
    when yfinance returns empty/errors; logs warnings and continues.

    Args:
        symbols: Set of ticker symbols (e.g. from get_portfolio_symbols).
    """
    if not symbols:
        logger.info("No symbols to load for news/earnings/insider")
        return

    with get_db_session() as session:
        # Bulk load existing keys for deduplication
        existing_news = {
            (r.symbol, r.link)
            for r in session.query(StockNews.symbol, StockNews.link).filter(StockNews.symbol.in_(symbols)).all()
        }
        existing_earnings = {
            (r.symbol, r.report_date)
            for r in session.query(StockEarnings.symbol, StockEarnings.report_date)
            .filter(StockEarnings.symbol.in_(symbols))
            .all()
        }
        existing_insider = set()
        for r in (
            session.query(
                StockInsiderTrade.symbol,
                StockInsiderTrade.transaction_date,
                StockInsiderTrade.insider_name,
                StockInsiderTrade.transaction_type,
                StockInsiderTrade.shares,
            )
            .filter(StockInsiderTrade.symbol.in_(symbols))
            .all()
        ):
            existing_insider.add(
                _insider_key(r.symbol, r.transaction_date, r.insider_name, r.transaction_type, r.shares)
            )

        news_added = 0
        earnings_added = 0
        insider_added = 0

        for i, symbol in enumerate(sorted(symbols)):
            try:
                new_news = _load_news_for_symbol(symbol, existing_news)
                if new_news:
                    session.add_all(new_news)
                    news_added += len(new_news)

                new_earnings = _load_earnings_for_symbol(symbol, existing_earnings)
                if new_earnings:
                    session.add_all(new_earnings)
                    earnings_added += len(new_earnings)

                new_insider = _load_insider_for_symbol(symbol, existing_insider)
                if new_insider:
                    session.add_all(new_insider)
                    insider_added += len(new_insider)
            except Exception as e:
                logger.warning("Error loading fundamentals for %s: %s", symbol, e, exc_info=True)

            if SYMBOL_DELAY_SECONDS and i < len(symbols) - 1:
                time.sleep(SYMBOL_DELAY_SECONDS)

        logger.info(
            "Stock fundamentals load: %d news, %d earnings, %d insider trades added for %d symbols",
            news_added,
            earnings_added,
            insider_added,
            len(symbols),
        )
