"""Daily script to calculate and store portfolio worth for all bots."""

import logging
from datetime import datetime, timezone

from utils.core import BotModel, PortfolioWorth, get_db_session
from utils.data import (
    DataService,
    get_portfolio_symbols,
    load_stock_news_earnings_insider,
)
from utils.portfolio import calculate_portfolio_worth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Calculate and store portfolio worth for all bots."""
    data_service = DataService()
    
    # Get today's date at midnight UTC
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    with get_db_session() as session:
        # Get all bots
        bots = session.query(BotModel).all()
        logger.info(f"Found {len(bots)} bots to process")
        
        for bot in bots:
            try:
                logger.info(f"Processing bot: {bot.name}")
                
                # Check if we already have an entry for today
                existing = (
                    session.query(PortfolioWorth)
                    .filter_by(bot_name=bot.name, date=today)
                    .first()
                )
                
                if existing:
                    logger.info(f"  Portfolio worth for {bot.name} already calculated for {today.date()}, skipping")
                    continue
                
                # Calculate current portfolio worth
                worth = calculate_portfolio_worth(bot, data_service)
                
                # Store in database
                portfolio_worth = PortfolioWorth(
                    bot_name=bot.name,
                    date=today,
                    portfolio_worth=worth,
                    holdings=bot.portfolio.copy(),
                )
                session.add(portfolio_worth)
                session.flush()
                
                logger.info(f"  Stored portfolio worth for {bot.name}: ${worth:,.2f}")
                
            except Exception as e:
                logger.error(f"  Error processing bot {bot.name}: {e}", exc_info=True)
                # Continue with next bot
                continue

        # Commit portfolio worth first so it is persisted even if fundamentals load times out
        session.commit()
        logger.info("Stored portfolio worth for all bots")

    # Load news, earnings, and insider trades (best-effort; can be slow and is after persist)
    with get_db_session() as session:
        symbols = get_portfolio_symbols(session)
        if symbols:
            logger.info(f"Loading stock fundamentals for {len(symbols)} symbols")
            try:
                load_stock_news_earnings_insider(symbols)
            except Exception as e:
                logger.warning(f"Stock fundamentals load failed (portfolio worth already saved): {e}")
        logger.info("Completed portfolio worth calculation for all bots")


if __name__ == "__main__":
    main()

