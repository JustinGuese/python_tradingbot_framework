import json
import logging
import os
from dotenv import load_dotenv
from livetrade.interactive_brokers import InteractiveBrokersBroker
from livetrade.copier import LiveTradeCopier

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("livetrade_ib")

def main():
    host = os.getenv("IB_GATEWAY_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("IB_GATEWAY_PORT", "4004"))
    except ValueError:
        logger.error("IB_GATEWAY_PORT must be an integer")
        return
        
    try:
        client_id = int(os.getenv("IB_CLIENT_ID", "17"))
    except ValueError:
        logger.error("IB_CLIENT_ID must be an integer")
        return
        
    account_id = os.getenv("IB_ACCOUNT_ID")
    if not account_id:
        logger.error("IB_ACCOUNT_ID must be set in .env (e.g., DU1234567 for paper)")
        return

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
    dry_run = os.getenv("LIVETRADE_DRY_RUN", "true").lower() == "true" # Default to true for IB safety

    try:
        portfolio_fraction = float(os.getenv("LIVETRADE_PORTFOLIO_FRACTION", "1.0"))
    except ValueError:
        logger.error("LIVETRADE_PORTFOLIO_FRACTION must be a float in (0, 1]")
        return
    if not (0 < portfolio_fraction <= 1):
        logger.error(f"LIVETRADE_PORTFOLIO_FRACTION must be in (0, 1], got {portfolio_fraction}")
        return

    logger.info(f"Initializing Interactive Brokers copier for Account {account_id} at {host}:{port}")
    logger.info(f"Bot weights: {bot_weights} | Dry Run: {dry_run}")
    
    broker = InteractiveBrokersBroker(
        host=host, 
        port=port, 
        client_id=client_id, 
        account_id=account_id
    )
    
    copier = LiveTradeCopier(
        broker=broker,
        bot_weights=bot_weights,
        min_order_usd=min_order,
        dry_run=dry_run,
        portfolio_fraction=portfolio_fraction
    )
    
    try:
        broker.connect()
        copier.sync()
    except Exception as e:
        logger.error(f"Error during sync: {e}", exc_info=True)
    finally:
        broker.disconnect()

if __name__ == "__main__":
    main()
