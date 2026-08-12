"""
The single live-trading entry sequence, shared by every broker.

`run(spec)` is what each `livetrade_<broker>.py` now delegates to. The ordering
below is deliberate and matches what the five scripts did individually:

1. Required credentials are checked first, so a missing secret fails without a
   database round-trip.
2. Config is parsed and bot names validated *before* the adapter is constructed,
   because some adapters open network sessions on construction and there is no
   reason to reach a broker for a run that cannot proceed.
3. Only then is the broker built and the copier run.

Exit codes are meaningful and are the reason this returns an int rather than
calling sys.exit itself (that stays in the shim, so run() is testable):

    0  synced
    1  the sync itself failed
    2  configuration is wrong — nothing was sent to the broker

Every path used to `return` bare, so a config error exited 0 and Kubernetes
reported success on a day the bot never traded.
"""

import logging
from dataclasses import dataclass

from dotenv import load_dotenv

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.livetrade.equity_recorder import record_live_equity
from tradingbot.livetrade.registry import BrokerSpec, ConfigError


@dataclass(frozen=True)
class RunConfig:
    bot_weights: dict[str, float]
    min_order_usd: float
    dry_run: bool
    portfolio_fraction: float


def load_config(spec: BrokerSpec) -> RunConfig:
    """Parse every LIVETRADE_* env var for `spec`. Raises ConfigError."""
    return RunConfig(
        bot_weights=spec.parse_bot_weights(),
        min_order_usd=spec.parse_min_order_usd(),
        dry_run=spec.parse_dry_run(),
        portfolio_fraction=spec.parse_portfolio_fraction(),
    )


def validate_bot_names(bot_weights: dict[str, float]) -> None:
    """Reject bot names that do not already exist in the database.

    BotRepository.create_or_get_bot silently creates a $10k stub on miss (see
    AGENTS.md "Common Pitfall 7-8"), so without this a typo in
    LIVETRADE_BOT_WEIGHTS pollutes the bots table instead of erroring. Names are
    case-sensitive and must match the CamelCase the bot registers itself under.
    """
    from tradingbot.utils.db import Bot as BotModel
    from tradingbot.utils.db import get_db_session

    with get_db_session() as session:
        existing_names = {b.name for b in session.query(BotModel).all()}
    missing = [name for name in bot_weights if name not in existing_names]
    if missing:
        raise ConfigError(
            f"Unknown bot names in LIVETRADE_BOT_WEIGHTS: {missing}. "
            f"Names are case-sensitive. Existing bots: {sorted(existing_names)}"
        )


def run(spec: BrokerSpec) -> int:
    """Run one full copy cycle for `spec`. Returns a process exit code."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger = logging.getLogger(spec.logger_name)

    try:
        spec.check_required_env()
        config = load_config(spec)
        validate_bot_names(config.bot_weights)
        broker: LiveBroker = spec.build()
    except ConfigError as e:
        logger.error(str(e))
        return 2

    logger.info(f"Bot weights: {config.bot_weights} | Dry Run: {config.dry_run}")

    copier = LiveTradeCopier(
        broker=broker,
        bot_weights=config.bot_weights,
        min_order_usd=config.min_order_usd,
        dry_run=config.dry_run,
        portfolio_fraction=config.portfolio_fraction,
    )

    exit_code = 0
    try:
        # No-op for the REST brokers; opens the OAuth session and tickler for IBKR.
        # Driving it unconditionally is why the ABC declares the lifecycle at all.
        broker.connect()
        copier.sync()
    except Exception as e:
        logger.error(f"Error during sync: {e}", exc_info=True)
        exit_code = 1
    finally:
        # Before disconnect, not after: recording equity queries the broker, and
        # for IBKR that needs the session still open.
        #
        # This used to run for Hyperliquid only, so the other four brokers wrote
        # no equity history at all despite equity_recorder being broker-agnostic.
        # It is in `finally` because a failed sync is exactly when the track
        # record most needs a datapoint. record_live_equity never raises.
        record_live_equity(broker, config.bot_weights)
        broker.disconnect()

    return exit_code
