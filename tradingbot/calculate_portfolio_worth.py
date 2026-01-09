"""Daily script to calculate and store portfolio worth for all bots."""

import logging
from datetime import datetime, timezone

from utils.data_service import DataService
from utils.db import Bot, PortfolioWorth, get_db_session
from utils.portfolio_worth_calculator import calculate_portfolio_worth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Calculate and store portfolio worth for all bots."""
    data_service = DataService()
    
    # Get today's date at midnight UTC
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    with get_db_session() as session:
        # Get all bots
        bots = session.query(Bot).all()
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
        
        # Commit all changes
        session.commit()
        logger.info("Completed portfolio worth calculation for all bots")


if __name__ == "__main__":
    main()

