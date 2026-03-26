"""
Stock News Sentiment Bot

Reads stock news articles from the stock_news table (populated daily by
calculate_portfolio_worth via stock_fundamentals_loader), classifies the
aggregate headline sentiment per symbol using AI, and executes trades on
medium/high-confidence BUY or SELL signals.

Crash-safety: all news rows for a symbol are marked acted_on=True BEFORE
the AI call, so articles are never processed twice even on a mid-run crash.

Schedule: runs after the portfolio-worth-calculator (0 22 * * *) has
refreshed the news feed, e.g. 30 22 * * 1-5 (10:30 PM UTC, Mon-Fri).
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from utils.botclass import Bot
from utils.db import StockNews, get_db_session

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 2           # Only consider news published in the last N days
MAX_ARTICLES_PER_SYMBOL = 5 # Max headlines batched per AI call
POSITION_SIZE_PCT = 0.2     # Spend 20% of available cash per BUY signal
MIN_CONFIDENCE = {"medium", "high"}  # Ignore low-confidence signals


def _classify_sentiment(symbol: str, headlines: list[str]) -> dict | None:
    """
    Send a batch of headlines for one symbol to the cheap AI and get back
    an aggregate sentiment verdict.

    Returns:
        {"direction": "BUY"|"SELL"|"HOLD", "confidence": "low"|"medium"|"high"}
        or None on any parse failure.
    """
    from utils.ai import run_ai_simple_with_fallback

    bullets = "\n".join(f"- {h}" for h in headlines)
    system = (
        "You are a trading sentiment classifier. "
        "Given recent news headlines for a single stock or asset, decide the "
        "aggregate market sentiment and whether it suggests opening a position.\n\n"
        "Guidelines:\n"
        "- BUY: predominantly positive news (earnings beat, new products, "
        "partnerships, analyst upgrades, major contracts)\n"
        "- SELL: predominantly negative news (earnings miss, regulatory action, "
        "layoffs, analyst downgrades, fraud, recall)\n"
        "- HOLD: mixed, neutral, routine, or insufficient signal\n"
        "- confidence: how decisive the signal is — "
        "low=mixed/uncertain, medium=leaning one way, high=clearly one-sided\n\n"
        "Respond with valid JSON only, no markdown: "
        '{"direction": "BUY" or "SELL" or "HOLD", "confidence": "low" or "medium" or "high"}'
    )
    user = f"Symbol: {symbol}\n\nRecent headlines:\n{bullets}"

    def _is_valid_json(raw: str) -> bool:
        try:
            d = json.loads(raw.strip())
            return isinstance(d, dict) and "direction" in d and "confidence" in d
        except Exception:
            return False

    try:
        raw = run_ai_simple_with_fallback(
            system_prompt=system,
            user_message=user,
            sanity_check=_is_valid_json,
        )
        return json.loads(raw.strip())
    except Exception as exc:
        logger.warning("AI sentiment classification failed for %s: %s", symbol, exc)
        return None


class StockNewsSentimentBot(Bot):
    """
    Trades on aggregate AI-classified sentiment from recent stock news headlines.

    Data source: stock_news table (populated by stock_fundamentals_loader,
    called from calculate_portfolio_worth.py each evening).

    Uses the complex makeOneIteration() pattern (like TelegramSignalsBankBot)
    because signal data comes from the database rather than live market data.
    """

    def __init__(self):
        super().__init__("StockNewsSentimentBot", symbol=None)

    def makeOneIteration(self) -> int:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)

        with get_db_session() as session:
            rows = (
                session.query(StockNews)
                .filter(
                    StockNews.acted_on == False,  # noqa: E712
                    StockNews.published_at >= cutoff,
                )
                .order_by(StockNews.published_at.asc())
                .all()
            )
            # Snapshot all needed data before the session closes
            by_symbol: dict[str, list[tuple[int, str]]] = defaultdict(list)
            for row in rows:
                by_symbol[row.symbol].append((row.id, row.title))

        logger.info(f">> {sum(len(v) for v in by_symbol.values())} unacted article(s) "
              f"across {len(by_symbol)} symbol(s).")
        if not by_symbol:
            return 0

        for symbol, articles in by_symbol.items():
            db_ids = [a[0] for a in articles]
            headlines = [a[1] for a in articles[:MAX_ARTICLES_PER_SYMBOL]]

            # Mark acted_on=True BEFORE the AI call — crash-safe deduplication
            with get_db_session() as session:
                session.query(StockNews).filter(StockNews.id.in_(db_ids)).update(
                    {StockNews.acted_on: True}, synchronize_session=False
                )

            result = _classify_sentiment(symbol, headlines)
            if not result:
                logger.info(f"  {symbol}: classification failed — skip")
                continue

            direction = result.get("direction", "HOLD")
            confidence = result.get("confidence", "low")
            logger.info(f"  {symbol}: {direction} (confidence={confidence}, {len(headlines)} headline(s))")

            if direction == "HOLD" or confidence not in MIN_CONFIDENCE:
                logger.info(f"    >> Skip: direction={direction}, confidence={confidence}")
                continue

            self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
            cash = self.dbBot.portfolio.get("USD", 0)
            holding = self.dbBot.portfolio.get(symbol, 0)
            position_usd = round(cash * POSITION_SIZE_PCT, 2)

            if direction == "BUY" and position_usd > 10:
                logger.info(f"    >> BUY {symbol} ${position_usd:.2f} "
                      f"({POSITION_SIZE_PCT:.0%} of ${cash:.2f})")
                self.buy(symbol, quantity_usd=position_usd)
            elif direction == "SELL" and holding > 0:
                logger.info(f"    >> SELL {symbol} (holding: {holding:.6f})")
                self.sell(symbol)
            else:
                logger.info(f"    >> {direction} {symbol}: nothing to act on "
                      f"(cash={cash:.2f}, holding={holding})")


        return 0


bot = StockNewsSentimentBot() # backtest not possible, event driven
bot.run()
