"""
Copy paper-bot portfolios onto Hyperliquid perps, optionally into a user vault.

Safety model — three layers, all of which must hold:

1. LEVERAGE 1x CROSS. HyperliquidBroker forces it before the first opening order
   on each coin, so the exchange itself rejects any order that would push total
   notional above account value. This is the layer that holds even if the
   copier's weight maths is wrong.
2. PORTFOLIO_FRACTION 0.95, not 1.0. The copier clamps buys to
   get_cash() * 0.98; at fraction 1.0 the target notional exceeds that budget
   every run, so it scales every order down and sits on the margin boundary.
   At 0.95 the scaling branch never fires.
3. LONG ONLY. The broker refuses SELLs with no open long and clamps oversized
   SELLs to the position, so a short can never be opened by accident.

Reads are scoped to HYPERLIQUID_VAULT_ADDRESS when set. Without it, this trades
the leader's own account — deliberately allowed for the mainnet smoke stage, but
it warns loudly.
"""

import json
import logging
import os

from dotenv import load_dotenv

from livetrade.copier import LiveTradeCopier
from livetrade.equity_recorder import record_live_equity
from livetrade.hyperliquid import HyperliquidBroker

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("livetrade_hyperliquid")


def main():
    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
    if not private_key:
        logger.error("HYPERLIQUID_PRIVATE_KEY must be set")
        return

    account_address = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS") or None
    vault_address = os.getenv("HYPERLIQUID_VAULT_ADDRESS") or None
    testnet = os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"

    if not testnet and not vault_address:
        logger.warning(
            "MAINNET with no HYPERLIQUID_VAULT_ADDRESS: trading the leader's own "
            "account with real funds, not a vault. Expected only during the "
            "mainnet smoke stage."
        )

    bot_weights_str = os.getenv(
        "LIVETRADE_BOT_WEIGHTS", '{"AdaptiveMeanReversionBTCBot": 1.0}'
    )
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
    from utils.db import Bot as BotModel
    from utils.db import get_db_session
    with get_db_session() as session:
        existing_names = {b.name for b in session.query(BotModel).all()}
    missing = [name for name in bot_weights if name not in existing_names]
    if missing:
        logger.error(
            f"Unknown bot names in LIVETRADE_BOT_WEIGHTS: {missing}. "
            f"Names are case-sensitive. Existing bots: {sorted(existing_names)}"
        )
        return

    min_order = float(os.getenv("LIVETRADE_MIN_ORDER_USD", "25"))
    dry_run = os.getenv("LIVETRADE_DRY_RUN", "false").lower() == "true"

    try:
        portfolio_fraction = float(os.getenv("LIVETRADE_PORTFOLIO_FRACTION", "0.95"))
    except ValueError:
        logger.error("LIVETRADE_PORTFOLIO_FRACTION must be a float in (0, 1]")
        return
    if not (0 < portfolio_fraction <= 1):
        logger.error(
            f"LIVETRADE_PORTFOLIO_FRACTION must be in (0, 1], got {portfolio_fraction}"
        )
        return

    target = f"vault {vault_address}" if vault_address else "own account"
    logger.info(
        f"Initializing Hyperliquid copier "
        f"({'TESTNET' if testnet else 'MAINNET'}, {target})"
    )
    logger.info(f"Bot weights: {bot_weights}")

    broker = HyperliquidBroker(
        private_key=private_key,
        account_address=account_address,
        vault_address=vault_address,
        testnet=testnet,
    )
    copier = LiveTradeCopier(
        broker=broker,
        bot_weights=bot_weights,
        min_order_usd=min_order,
        dry_run=dry_run,
        portfolio_fraction=portfolio_fraction,
    )

    try:
        copier.sync()
    except Exception as e:
        logger.error(f"Error during sync: {e}", exc_info=True)
    finally:
        # Free — the sync has already made these API calls. The standalone
        # record_live_equity.py job covers the days this one doesn't run.
        record_live_equity(broker, bot_weights)


if __name__ == "__main__":
    main()
