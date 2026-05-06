import json
import logging
import os
from dotenv import load_dotenv
from livetrade.darwinex import DarwinexBroker
from livetrade.copier import LiveTradeCopier

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("livetrade_darwinex")

def main():
    username = os.getenv("DARWINEX_USERNAME")
    password = os.getenv("DARWINEX_PASSWORD")
    if not username or not password:
        logger.error("DARWINEX_USERNAME and DARWINEX_PASSWORD must be set in .env")
        return

    account_id = os.getenv("DARWINEX_ACCOUNT_ID")
    demo = os.getenv("DARWINEX_DEMO", "true").lower() == "true"
    
    bot_weights_str = os.getenv("LIVETRADE_BOT_WEIGHTS", '{"AdaptiveMeanReversionBot": 1.0}')
    try:
        bot_weights = json.loads(bot_weights_str)
    except Exception as e:
        logger.error(f"Failed to parse LIVETRADE_BOT_WEIGHTS: {e}")
        return

    # Validate bot names against the DB before the copier touches them.
    from utils.db import get_db_session, Bot as BotModel
    with get_db_session() as session:
        existing_names = {b.name for b in session.query(BotModel).all()}
    missing = [name for name in bot_weights if name not in existing_names]
    if missing:
        logger.error(
            f"Unknown bot names in LIVETRADE_BOT_WEIGHTS: {missing}. "
            f"Names are case-sensitive. Existing bots: {sorted(existing_names)}"
        )
        return

    min_order = float(os.getenv("LIVETRADE_MIN_ORDER_USD", "50"))
    # Per user decision: Default to false (live from first successful run)
    dry_run = os.getenv("LIVETRADE_DRY_RUN", "false").lower() == "true" 

    try:
        portfolio_fraction = float(os.getenv("LIVETRADE_PORTFOLIO_FRACTION", "1.0"))
    except ValueError:
        logger.error("LIVETRADE_PORTFOLIO_FRACTION must be a float in (0, 1]")
        return
    if not (0 < portfolio_fraction <= 1):
        logger.error(f"LIVETRADE_PORTFOLIO_FRACTION must be in (0, 1], got {portfolio_fraction}")
        return

    logger.info(f"Initializing Darwinex copier (Demo: {demo})")
    logger.info(f"Bot weights: {bot_weights} | Dry Run: {dry_run}")
    
    broker = DarwinexBroker(
        username=username,
        password=password,
        account_id=account_id,
        demo=demo
    )
    
    copier = LiveTradeCopier(
        broker=broker,
        bot_weights=bot_weights,
        min_order_usd=min_order,
        dry_run=dry_run,
        portfolio_fraction=portfolio_fraction
    )
    
    try:
        copier.sync()
    except Exception as e:
        logger.error(f"Error during sync: {e}", exc_info=True)

if __name__ == "__main__":
    main()
