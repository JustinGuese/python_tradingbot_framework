"""
Hyperliquid perps broker adapter.

Runs a strategy from the bot fleet on Hyperliquid perpetuals, optionally inside a
Hyperliquid *user vault* so outside depositors can follow it. Every exchange
action is signed by an API/agent wallet; `vault_address` is injected by the SDK
into every signed payload (`Exchange._post_action`), so a single constructor
argument routes all trading to the vault instead of the leader's own account.

Three things make this adapter different from the spot adapters, and all three
are load-bearing:

1. There is no settled cash on perps. `get_cash()` returns `withdrawable`
   (free collateral), because that -- not `accountValue` -- is what actually
   constrains new notional. See `get_cash`.
2. Positions are SIGNED. The copier can only ever request BUY/SELL of a positive
   quantity, so returning abs() would make it double a stray short instead of
   flattening it. See `get_positions`.
3. Leverage is forced to 1x cross before the first opening order, so the
   exchange itself rejects anything that would push notional above equity.
   See `_ensure_leverage` and the module docstring of `livetrade_hyperliquid.py`.

Reads (`user_state`, `open_orders`) must be issued against the VAULT address,
not the signing wallet -- hence `self.query_address`.
"""

import logging
from typing import Literal

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants as hl_constants

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.symbol_map import SymbolMapper
from tradingbot.utils.data_service import DataService

logger = logging.getLogger(__name__)

# Hyperliquid rejects orders below $10 notional, except exact reduce-only closes.
HL_MIN_ORDER_USD = 10.0


class HyperliquidBroker(LiveBroker):
    def __init__(
        self,
        private_key: str,
        account_address: str | None = None,
        vault_address: str | None = None,
        testnet: bool = True,
        symbol_mapper: SymbolMapper | None = None,
        data_service: DataService | None = None,
        cash_mode: str = "withdrawable",
        long_only: bool = True,
        leverage: int = 1,
        slippage: float = 0.02,
        info: Info | None = None,
        exchange: Exchange | None = None,
    ):
        self.name = "hyperliquid"
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()

        self.testnet = testnet
        self.vault_address = vault_address or None
        self.cash_mode = cash_mode
        self.long_only = long_only
        self.leverage = leverage
        self.slippage = slippage

        wallet = eth_account.Account.from_key(private_key)
        self.wallet_address = wallet.address
        self.account_address = account_address or wallet.address

        # Every read is scoped to the vault when one is configured. Getting this
        # wrong means sizing against the leader's personal balance.
        self.query_address = self.vault_address or self.account_address

        base_url = hl_constants.TESTNET_API_URL if testnet else hl_constants.MAINNET_API_URL

        # skip_ws=True is mandatory: Info() opens a websocket by default and the
        # CronJob would hang until activeDeadlineSeconds. (Exchange builds its
        # own Info with skip_ws already set.)
        self.info = info or Info(base_url, skip_ws=True)
        self.exchange = exchange or Exchange(
            wallet,
            base_url,
            vault_address=self.vault_address,
            account_address=self.account_address,
        )

        self._sz_decimals: dict[str, int] | None = None
        self._leverage_set: set[str] = set()

    @property
    def account_ref(self) -> str:
        # The vault when trading one, else the leader's own wallet — i.e. the
        # address whose equity get_total_equity() actually reports.
        return str(self.query_address or "")

    @property
    def is_sandbox(self) -> bool:
        return self.testnet

    # ---------------------------------------------------------------- helpers

    def _user_state(self) -> dict:
        return self.info.user_state(self.query_address)

    def _universe(self) -> dict[str, int]:
        """coin -> szDecimals, cached for the life of the process."""
        if self._sz_decimals is None:
            meta = self.info.meta() or {}
            self._sz_decimals = {
                a["name"]: int(a.get("szDecimals", 0)) for a in meta.get("universe", []) if a.get("name")
            }
        return self._sz_decimals

    @staticmethod
    def _check_result(result, context: str) -> None:
        """Hyperliquid returns rejections inside a 200 -- surface them as errors."""
        if result is None:
            logger.error(f"Hyperliquid {context}: no response (no matching position?)")
            return
        if result.get("status") != "ok":
            logger.error(f"Hyperliquid {context} failed: {result}")
            return
        statuses = (result.get("response") or {}).get("data", {}).get("statuses", [])
        for status in statuses:
            if isinstance(status, dict) and "error" in status:
                logger.error(f"Hyperliquid {context} rejected: {status['error']}")
                return
        logger.info(f"Hyperliquid {context} ok: {statuses}")

    def _ensure_leverage(self, coin: str) -> None:
        """Force 1x cross before the first opening order on a coin.

        This is what makes 'notional <= equity' true even if the copier's weight
        maths is wrong: at 1x the exchange requires initial margin >= notional and
        rejects anything larger, rather than silently levering up.
        """
        if coin in self._leverage_set:
            return
        try:
            self.exchange.update_leverage(self.leverage, coin, is_cross=True)
            logger.info(f"Set {coin} leverage to {self.leverage}x cross")
        except Exception as e:
            # Non-fatal: HL keeps the previous setting. Loud, because it weakens
            # the exchange-side half of the notional invariant.
            logger.error(f"Failed to set {coin} leverage to {self.leverage}x: {e}")
        self._leverage_set.add(coin)

    # ------------------------------------------------------------ account state

    def get_total_equity(self) -> float:
        try:
            return float(self._user_state()["marginSummary"]["accountValue"])
        except Exception as e:
            logger.error(f"Failed to get Hyperliquid equity: {e}")
            return 0.0

    def get_cash(self) -> float:
        """Available buying power -- NOT settled cash, because perps use margin.

        `withdrawable` is free collateral (accountValue - margin used), which is
        the true constraint on opening new notional. `accountValue` would
        over-report buying power at 1x and produce orders the exchange rejects,
        which `place_order` can only log -- a silent no-op that is much harder to
        diagnose than the copier's cash clamp.
        """
        try:
            state = self._user_state()
            if self.cash_mode == "account_value":
                return float(state["marginSummary"]["accountValue"])
            return float(state.get("withdrawable", 0.0))
        except Exception as e:
            logger.error(f"Failed to get Hyperliquid cash: {e}")
            return 0.0

    def get_positions(self) -> dict[str, float]:
        """coin -> SIGNED size. Signed deliberately, Darwinex-style.

        With a stray short (szi=-0.01) and a target weight of 0, the copier's
        full-liquidation branch tests `current_qty > 0`, which is False for a
        signed value, so it falls through to the general diff and emits a BUY --
        flattening the short. Returning abs() would take the liquidation branch
        and emit a SELL, doubling the short, every single run.
        """
        try:
            positions: dict[str, float] = {}
            for entry in self._user_state().get("assetPositions", []):
                position = entry.get("position", {})
                coin = position.get("coin")
                szi = float(position.get("szi", 0.0) or 0.0)
                if coin and szi != 0.0:
                    positions[coin] = szi
            return positions
        except Exception as e:
            logger.error(f"Failed to get Hyperliquid positions: {e}")
            return {}

    def _get_native_price(self, broker_symbol: str) -> float:
        try:
            mid = (self.info.all_mids() or {}).get(broker_symbol)
            return float(mid) if mid is not None else 0.0
        except Exception as e:
            logger.debug(f"Hyperliquid mid fetch failed for {broker_symbol}: {e}")
            return 0.0

    # ------------------------------------------------------------------ orders

    def place_order(
        self,
        broker_symbol: str,
        quantity: float,
        side: Literal["BUY", "SELL"],
        symbol_type: str | None = None,
    ) -> None:
        coin = broker_symbol
        universe = self._universe()
        if coin not in universe:
            logger.error(f"{coin} is not a Hyperliquid perp. Skipping order.")
            return

        sz_decimals = universe[coin]
        size = round(abs(float(quantity)), sz_decimals)
        if size <= 0:
            logger.warning(f"Quantity {quantity} for {coin} rounded to 0 at {sz_decimals} decimals. Skipping.")
            return

        price = self.get_latest_price(coin)
        notional = size * price if price else 0.0

        if side == "SELL":
            current = self.get_positions().get(coin, 0.0)
            if current <= 0:
                logger.error(
                    f"Refusing SELL {size} {coin}: no long position (current={current}). long_only={self.long_only}."
                )
                return
            max_close = round(current, sz_decimals)
            if size > max_close:
                logger.warning(f"Clamping SELL {size} {coin} to the open long {max_close} -- never opening a short.")
                size = max_close
            if size <= 0:
                return

            # An exact close is exempt from the $10 floor; a partial reduce is not.
            is_exact_close = abs(size - max_close) < 10 ** -(sz_decimals + 2)
            if not is_exact_close and notional and notional < HL_MIN_ORDER_USD:
                logger.warning(
                    f"Skipping partial SELL {size} {coin} (${notional:.2f} < ${HL_MIN_ORDER_USD} Hyperliquid minimum)."
                )
                return

            logger.info(f"Hyperliquid market_close {size} {coin} (~${size * price:.2f})")
            result = self.exchange.market_close(coin, sz=size, slippage=self.slippage)
            self._check_result(result, f"SELL {size} {coin}")
            return

        if notional and notional < HL_MIN_ORDER_USD:
            logger.warning(f"Skipping BUY {size} {coin} (${notional:.2f} < ${HL_MIN_ORDER_USD} Hyperliquid minimum).")
            return

        self._ensure_leverage(coin)
        logger.info(f"Hyperliquid market_open {size} {coin} (~${notional:.2f})")
        result = self.exchange.market_open(coin, is_buy=True, sz=size, slippage=self.slippage)
        self._check_result(result, f"BUY {size} {coin}")

    def cancel_open_orders(self) -> int:
        cancelled = 0
        try:
            for order in self.info.open_orders(self.query_address) or []:
                coin, oid = order.get("coin"), order.get("oid")
                if not coin or oid is None:
                    continue
                try:
                    self.exchange.cancel(coin, int(oid))
                    cancelled += 1
                except Exception as e:
                    logger.error(f"Failed to cancel {coin} order {oid}: {e}")
        except Exception as e:
            logger.error(f"Failed to list Hyperliquid open orders: {e}")
        if cancelled:
            logger.info(f"Cancelled {cancelled} open Hyperliquid order(s)")
        return cancelled

    # ------------------------------------------------------------------ symbols

    def map_symbol(self, yf_symbol: str) -> dict | None:
        """yfinance symbol -> Hyperliquid coin, or None if it isn't listed.

        SymbolMapper never returns None (its default rule passes anything
        through as a stock), so the universe check is what actually rejects
        QQQ/GLD/EURUSD=X here. With LIVETRADE_STRICT_MAPPING=true that aborts
        the sync rather than silently trading a subset of the book.
        """
        entry = self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)
        if not entry:
            return None
        coin = entry.get("symbol")
        if coin not in self._universe():
            logger.debug(f"{yf_symbol} -> {coin} is not a Hyperliquid perp")
            return None
        return entry

    def search_symbol(self, query: str) -> list[dict]:
        needle = (query or "").upper()
        return [
            {
                "symbol": coin,
                "description": f"{coin} perpetual",
                "type": "crypto",
                "exchange": "hyperliquid",
                "score": 100 if coin == needle else 50,
            }
            for coin in self._universe()
            if needle in coin
        ]

    # ------------------------------------------------------------------- pretty

    # print_account_summary() itself lives on LiveBroker. Hyperliquid is the
    # one adapter with no settled cash to show (perps use margin, not a cash
    # balance) — it shows signer/vault identity plus withdrawable collateral,
    # total notional and effective leverage instead. Positions carry entry
    # price and unrealized P&L that get_positions()'s signed qty discards.
    def _summary_header(self) -> str:
        target = "vault" if self.vault_address else "account"
        return f"Hyperliquid {'TESTNET' if self.testnet else 'MAINNET'} ({target})"

    def _account_lines(self) -> list[str]:
        state = self._user_state()
        margin = state.get("marginSummary", {})
        equity = float(margin.get("accountValue", 0) or 0)
        notional = float(margin.get("totalNtlPos", 0) or 0)
        return [
            self._summary_line("Signer:", self.wallet_address, fmt=""),
            self._summary_line("Queried address:", self.query_address, fmt=""),
            self._summary_line("Equity:", equity),
            self._summary_line("Withdrawable:", float(state.get("withdrawable", 0) or 0)),
            self._summary_line("Total notional:", notional),
            self._summary_line("Effective lev:", notional / equity if equity else 0) + "x",
        ]

    def _position_rows(self) -> tuple[str, list[str]]:
        positions = self._user_state().get("assetPositions", [])
        header = f"  {'Coin':<8} {'Size':>14} {'Entry':>12} {'Notional':>14} {'uPnL':>12}"
        rows = []
        for entry in positions:
            p = entry.get("position", {})
            rows.append(
                f"  {p.get('coin', '')!s:<8} {float(p.get('szi', 0) or 0):>14.6f} "
                f"{float(p.get('entryPx', 0) or 0):>12.2f} "
                f"{float(p.get('positionValue', 0) or 0):>14.2f} "
                f"{float(p.get('unrealizedPnl', 0) or 0):>12.2f}"
            )
        return header, rows


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
    if not private_key:
        raise SystemExit("HYPERLIQUID_PRIVATE_KEY must be set in .env")

    broker = HyperliquidBroker(
        private_key=private_key,
        account_address=os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS"),
        vault_address=os.getenv("HYPERLIQUID_VAULT_ADDRESS") or None,
        testnet=os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true",
    )
    broker.print_account_summary()
