import json
import logging
import os
from dotenv import load_dotenv
from livetrade.collective2 import Collective2Broker
from livetrade.copier import LiveTradeCopier

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("livetrade_collective2")

def main():
    api_key = os.getenv("COLLECTIVE2_API_KEY")
    system_id = os.getenv("COLLECTIVE2_SYSTEM_ID")
    
    if not api_key or not system_id:
        logger.error("COLLECTIVE2_API_KEY and COLLECTIVE2_SYSTEM_ID must be set in .env")
        return

    bot_weights_str = os.getenv("LIVETRADE_BOT_WEIGHTS", '{"AdaptiveMeanReversionBot": 1.0}')
    try:
        bot_weights = json.loads(bot_weights_str)
    except Exception as e:
        logger.error(f"Failed to parse LIVETRADE_BOT_WEIGHTS: {e}")
        return

    # Validate bot names against the DB *before* the copier touches them.
    # BotRepository.create_or_get_bot silently creates a $10k stub on miss
    # (see AGENTS.md "Common Pitfall 7-8"), so a typo would pollute the DB
    # instead of erroring. Names are case-sensitive and must match the
    # CamelCase the bot registers itself under.
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

    copy_open = os.getenv("LIVETRADE_COPY_OPEN_TRADES", "true").lower() == "true"
    min_order = float(os.getenv("LIVETRADE_MIN_ORDER_USD", "50"))
    dry_run = os.getenv("LIVETRADE_DRY_RUN", "false").lower() == "true"

    logger.info(f"Initializing Collective2 copier for System ID {system_id}")
    logger.info(f"Bot weights: {bot_weights}")
    
    broker = Collective2Broker(api_key=api_key, system_id=system_id)
    copier = LiveTradeCopier(
        broker=broker,
        bot_weights=bot_weights,
        copy_open_trades=copy_open,
        min_order_usd=min_order,
        dry_run=dry_run
    )
    
    try:
        copier.sync()
    except Exception as e:
        logger.error(f"Error during sync: {e}", exc_info=True)

if __name__ == "__main__":
    main()
