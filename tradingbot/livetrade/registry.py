"""
One `BrokerSpec` per live-trading venue: everything that actually differs
between the `livetrade_<broker>.py` entry points.

Those five scripts were ~90 lines each and 42-49 of those lines were byte-identical
to a sibling — the same weights parse, the same DB name validation, the same
min-order/dry-run/fraction parsing, the same copier construction and try/sync/except.
Everything genuinely broker-specific is data, and it is that data that lives here:
which env vars are required, how to construct the adapter, and the four defaults
that varied only as string literals.

The defaults below are load-bearing. They are the values the production CronJobs
run with whenever the corresponding env var is unset, so they are reproduced here
verbatim from the scripts they replace — note that dry-run defaults differ per
broker (true for eToro/IBKR, false for Collective2/Darwinex/Hyperliquid) and that
Hyperliquid deliberately runs at fraction 0.95, not 1.0.

Adapter imports are deferred into each `build` function on purpose: a Hyperliquid
CronJob should not pay for importing `ibind`, and an IBKR job should not import the
Hyperliquid SDK. Importing this module imports no broker SDK at all.
"""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from tradingbot.livetrade.broker import LiveBroker

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when broker configuration is missing or malformed.

    The runner turns this into exit code 2 (bad config) as distinct from
    exit code 1 (the sync itself failed) so Kubernetes job failures can be
    triaged without reading logs.
    """


def _require(*names: str) -> list[str]:
    """Return the values of `names`, or raise ConfigError naming all missing ones."""
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise ConfigError(f"{' and '.join(missing)} must be set in .env")
    return [os.environ[n] for n in names]


def _is_true(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() == "true"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _build_collective2() -> LiveBroker:
    from tradingbot.livetrade.collective2 import Collective2Broker

    api_key, system_id = _require("COLLECTIVE2_API_KEY", "COLLECTIVE2_SYSTEM_ID")
    logger.info(f"Initializing Collective2 copier for System ID {system_id}")
    return Collective2Broker(api_key=api_key, system_id=system_id)


def _build_darwinex() -> LiveBroker:
    from tradingbot.livetrade.darwinex import DarwinexBroker

    username, password = _require("DARWINEX_USERNAME", "DARWINEX_PASSWORD")
    demo = _is_true("DARWINEX_DEMO", "true")
    logger.info(f"Initializing Darwinex copier (Demo: {demo})")
    return DarwinexBroker(
        username=username,
        password=password,
        account_id=os.getenv("DARWINEX_ACCOUNT_ID"),
        demo=demo,
    )


def _build_etoro() -> LiveBroker:
    from tradingbot.livetrade.etoro import EtoroBroker

    api_key, user_key = _require("ETORO_API_KEY", "ETORO_USER_KEY")
    demo = _is_true("ETORO_DEMO", "true")
    logger.info(f"Initializing eToro copier (Demo: {demo})")
    return EtoroBroker(api_key=api_key, user_key=user_key, demo=demo)


def _build_interactive_brokers() -> LiveBroker:
    from tradingbot.livetrade.interactive_brokers import InteractiveBrokersBroker

    (account_id,) = _require("IB_ACCOUNT_ID")
    logger.info(f"Initializing Interactive Brokers (Web API) copier for Account {account_id}")
    return InteractiveBrokersBroker(account_id=account_id)


def _build_hyperliquid() -> LiveBroker:
    from tradingbot.livetrade.hyperliquid import HyperliquidBroker

    (private_key,) = _require("HYPERLIQUID_PRIVATE_KEY")
    vault_address = os.getenv("HYPERLIQUID_VAULT_ADDRESS") or None
    testnet = _is_true("HYPERLIQUID_TESTNET", "true")

    if not testnet and not vault_address:
        # Deliberately a warning, not an abort: this is the expected shape of the
        # mainnet smoke stage. It must stay loud because it means real personal
        # funds, not vault funds, are being traded.
        logger.warning(
            "MAINNET with no HYPERLIQUID_VAULT_ADDRESS: trading the leader's own "
            "account with real funds, not a vault. Expected only during the "
            "mainnet smoke stage."
        )

    target = f"vault {vault_address}" if vault_address else "own account"
    logger.info(f"Initializing Hyperliquid copier ({'TESTNET' if testnet else 'MAINNET'}, {target})")
    return HyperliquidBroker(
        private_key=private_key,
        account_address=os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS") or None,
        vault_address=vault_address,
        testnet=testnet,
    )


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BrokerSpec:
    """Everything the runner needs to drive one venue."""

    key: str
    #: Logger name, preserved from the script this spec replaces so existing log
    #: greps and any alert rules keyed on it keep matching.
    logger_name: str
    #: Constructs the adapter. Raises ConfigError when credentials are missing.
    build: Callable[[], LiveBroker]
    #: Checked before anything touches the database, so a missing credential
    #: fails fast rather than after a DB round-trip.
    required_env: tuple[str, ...]
    default_bot_weights: str = '{"AdaptiveMeanReversionBot": 1.0}'
    default_min_order_usd: str = "50"
    default_dry_run: str = "true"
    default_portfolio_fraction: str = "1.0"

    def check_required_env(self) -> None:
        """Raise ConfigError if any required credential is unset."""
        _require(*self.required_env)

    def parse_bot_weights(self) -> dict[str, float]:
        raw = os.getenv("LIVETRADE_BOT_WEIGHTS", self.default_bot_weights)
        try:
            weights = json.loads(raw)
        except Exception as e:
            raise ConfigError(f"Failed to parse LIVETRADE_BOT_WEIGHTS: {e}") from e
        if not isinstance(weights, dict):
            raise ConfigError(f"LIVETRADE_BOT_WEIGHTS must be a JSON object, got {type(weights).__name__}")
        return weights

    def parse_min_order_usd(self) -> float:
        raw = os.getenv("LIVETRADE_MIN_ORDER_USD", self.default_min_order_usd)
        try:
            return float(raw)
        except ValueError as e:
            raise ConfigError("LIVETRADE_MIN_ORDER_USD must be a float") from e

    def parse_dry_run(self) -> bool:
        return _is_true("LIVETRADE_DRY_RUN", self.default_dry_run)

    def parse_portfolio_fraction(self) -> float:
        raw = os.getenv("LIVETRADE_PORTFOLIO_FRACTION", self.default_portfolio_fraction)
        try:
            fraction = float(raw)
        except ValueError as e:
            raise ConfigError("LIVETRADE_PORTFOLIO_FRACTION must be a float in (0, 1]") from e
        if not (0 < fraction <= 1):
            raise ConfigError(f"LIVETRADE_PORTFOLIO_FRACTION must be in (0, 1], got {fraction}")
        return fraction


REGISTRY: dict[str, BrokerSpec] = {
    "collective2": BrokerSpec(
        key="collective2",
        logger_name="livetrade_collective2",
        build=_build_collective2,
        required_env=("COLLECTIVE2_API_KEY", "COLLECTIVE2_SYSTEM_ID"),
        default_dry_run="false",
    ),
    "darwinex": BrokerSpec(
        key="darwinex",
        logger_name="livetrade_darwinex",
        build=_build_darwinex,
        required_env=("DARWINEX_USERNAME", "DARWINEX_PASSWORD"),
        # Live from the first successful run, per an explicit product decision.
        default_dry_run="false",
    ),
    "etoro": BrokerSpec(
        key="etoro",
        logger_name="livetrade_etoro",
        build=_build_etoro,
        required_env=("ETORO_API_KEY", "ETORO_USER_KEY"),
    ),
    "interactive_brokers": BrokerSpec(
        key="interactive_brokers",
        # Kept as "livetrade_ib" — this is what the deployed job logs under.
        logger_name="livetrade_ib",
        build=_build_interactive_brokers,
        required_env=("IB_ACCOUNT_ID",),
    ),
    "hyperliquid": BrokerSpec(
        key="hyperliquid",
        logger_name="livetrade_hyperliquid",
        build=_build_hyperliquid,
        required_env=("HYPERLIQUID_PRIVATE_KEY",),
        default_bot_weights='{"AdaptiveMeanReversionBTCBot": 1.0}',
        default_min_order_usd="25",
        default_dry_run="false",
        # Not 1.0. The copier clamps buys to get_cash() * 0.98; at fraction 1.0 the
        # target notional exceeds that budget every run, so it scales every order
        # down and sits on the margin boundary. At 0.95 that branch never fires.
        default_portfolio_fraction="0.95",
    ),
}
