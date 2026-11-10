import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.mt5.client import (
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_REMOVE,
    TRADE_RETCODE_DONE,
    AccountInfo,
    MT5Client,
    SymbolInfo,
)
from tradingbot.livetrade.symbol_map import SymbolMapper
from tradingbot.utils.data_service import DataService

logger = logging.getLogger(__name__)

_UNSET = object()


class MT5Broker(LiveBroker):
    """
    LiveBroker implementation for MetaTrader 5 via MT5Client protocol.
    Works with local MetaTrader5 or remote mt5linux bridge over RPyC.
    """

    def __init__(
        self,
        client: MT5Client,
        name: str = "mt5_mql5",
        login: int | str = 0,
        server: str = "",
        is_sandbox: bool = False,
        symbol_mapper: SymbolMapper | None = None,
        data_service: DataService | None = None,
        rate_provider: Callable[[], float] | None = None,
        cash_proxy: str | None = None,
    ):
        self.client = client
        self.name = name
        self.login = login
        self.server = server
        self._is_sandbox = is_sandbox
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        self.rate_provider = rate_provider
        self._cash_proxy_override: Any = cash_proxy if cash_proxy is not None else _UNSET

    @property
    def account_ref(self) -> str:
        """Stable account identifier (MT5 login)."""
        return str(self.login)

    @property
    def is_sandbox(self) -> bool:
        """Whether this session trades demo/paper funds."""
        return self._is_sandbox

    @property
    def cash_proxy(self) -> str | None:
        """Cash proxy symbol (None unless SHV is listed on the MT5 server)."""
        if self._cash_proxy_override is not _UNSET:
            return self._cash_proxy_override
        try:
            if self._resolve_symbol("SHV") is not None:
                return "SHV"
        except Exception as exc:
            logger.debug(f"Error checking SHV availability: {exc}")
        return None

    @cash_proxy.setter
    def cash_proxy(self, value: str | None) -> None:
        self._cash_proxy_override = value

    def connect(self, readonly: bool = False) -> None:
        """Open or verify session with MT5 terminal."""
        pass

    def disconnect(self) -> None:
        """Close connection to MT5 terminal."""
        try:
            self.client.shutdown()
        except Exception as exc:
            logger.debug(f"MT5 shutdown error: {exc}")

    def _get_usd_multiplier(self, info: AccountInfo) -> float:
        """
        Account-currency rule:
        - USD passes through (1.0).
        - EUR is converted with injectable rate provider defaulting to DataService
          latest price of EURUSD=X (missing or zero rate must raise, never default to 1.0).
        - Any other currency raises ConfigError.
        """
        currency = (getattr(info, "currency", "") or "").strip().upper()
        if currency == "USD":
            return 1.0
        if currency == "EUR":
            rate: float | None = None
            if self.rate_provider is not None:
                try:
                    rate = self.rate_provider()
                except Exception as exc:
                    raise RuntimeError(f"Rate provider failed for EURUSD: {exc}") from exc
            else:
                try:
                    rate = self.data_service.get_latest_price("EURUSD=X")
                except Exception as exc:
                    raise RuntimeError(f"DataService failed to fetch EURUSD=X price: {exc}") from exc

            if rate is None or rate <= 0:
                raise ValueError(f"EURUSD rate must be positive and non-zero, got: {rate}")
            return float(rate)

        from tradingbot.livetrade.registry import ConfigError

        raise ConfigError(f"Unsupported MT5 account currency: '{currency}'. Only USD and EUR are supported.")

    def get_cash(self) -> float:
        """Return the current cash balance in USD."""
        info = self.client.account_info()
        if info is None:
            raise RuntimeError("MT5 client account_info returned None")
        multiplier = self._get_usd_multiplier(info)
        return float(info.balance) * multiplier

    def get_total_equity(self) -> float:
        """Return total equity in USD."""
        info = self.client.account_info()
        if info is None:
            raise RuntimeError("MT5 client account_info returned None")
        multiplier = self._get_usd_multiplier(info)
        return float(info.equity) * multiplier

    def get_positions(self) -> dict[str, float]:
        """
        Return current open positions as a dict: broker_symbol -> net quantity.
        Nets volume per symbol across hedged/netted tickets.
        """
        raw_positions = self.client.positions_get()
        if raw_positions is None:
            # MT5 returns an empty tuple for "no positions"; None means the call failed.
            # Reading that as "flat" would make the copier buy the whole target again.
            err_code, err_msg = self.client.last_error()
            raise RuntimeError(f"MT5 positions_get failed: ({err_code}) {err_msg}")

        net_positions: dict[str, float] = {}
        for pos in raw_positions:
            symbol = getattr(pos, "symbol", "")
            if not symbol:
                continue
            volume = float(getattr(pos, "volume", 0.0))
            pos_type = getattr(pos, "type", ORDER_TYPE_BUY)
            signed_volume = volume if pos_type == ORDER_TYPE_BUY else -volume
            net_positions[symbol] = net_positions.get(symbol, 0.0) + signed_volume

        result: dict[str, float] = {}
        for sym, vol in net_positions.items():
            rounded_vol = round(vol, 8)
            if abs(rounded_vol) > 1e-8:
                result[sym] = rounded_vol
        return result

    def _determine_filling_mode(self, symbol_info: SymbolInfo) -> int:
        """
        Select supported order filling mode:
        SYMBOL_FILLING_FOK = 1 -> ORDER_FILLING_FOK (0)
        SYMBOL_FILLING_IOC = 2 -> ORDER_FILLING_IOC (1)
        Default -> ORDER_FILLING_RETURN (2)
        """
        filling_mode = getattr(symbol_info, "filling_mode", 0)
        if filling_mode & 1:
            return 0
        if filling_mode & 2:
            return 1
        return 2

    def place_order(
        self,
        broker_symbol: str,
        quantity: float,
        side: Literal["BUY", "SELL"],
        symbol_type: str | None = None,
    ) -> None:
        """
        Place a market order.
        - Rounds volume to volume_step and clamps to volume_min/max.
        - Skips with a warning if below volume_min.
        - Sets deviation and supported filling mode.
        - SELL closes tickets by position id and never oversells.
        - Rejections raise RuntimeError.
        """
        if quantity <= 0:
            logger.warning(f"Order quantity {quantity} for {broker_symbol} must be positive; skipping.")
            return

        sym_info = self.client.symbol_info(broker_symbol)
        if sym_info is None:
            raise RuntimeError(f"MT5 symbol_info unavailable for {broker_symbol}")

        # The copier sizes in shares (notional / price) and MT5 volume is in lots, which are
        # the same thing only when one lot is one share. Refuse anything else rather than
        # send an order 100x or 0.01x the intended size.
        contract_size = float(getattr(sym_info, "trade_contract_size", 1.0) or 1.0)
        if contract_size != 1.0:
            raise RuntimeError(
                f"MT5 symbol {broker_symbol} has contract size {contract_size}, not 1: "
                "volume would not equal shares. Refusing to size this order."
            )

        vol_step = getattr(sym_info, "volume_step", 0.01) or 0.01
        vol_min = getattr(sym_info, "volume_min", 0.01) or 0.01
        vol_max = getattr(sym_info, "volume_max", 1000.0) or 1000.0
        filling_mode = self._determine_filling_mode(sym_info)

        if side == "BUY":
            steps = round(quantity / vol_step)
            vol = round(steps * vol_step, 8)

            if vol < vol_min:
                logger.warning(f"Order volume {vol} below volume_min {vol_min} for {broker_symbol}; skipping.")
                return

            if vol > vol_max:
                logger.warning(f"Order volume {vol} exceeds volume_max {vol_max} for {broker_symbol}; clamping to max.")
                vol = vol_max

            tick = self.client.symbol_info_tick(broker_symbol)
            price = tick.ask if tick and tick.ask else getattr(sym_info, "ask", 0.0)

            req = {
                "action": TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": vol,
                "type": ORDER_TYPE_BUY,
                "price": price,
                "deviation": 20,
                "type_filling": filling_mode,
            }
            res = self.client.order_send(req)
            if res is None or getattr(res, "retcode", None) != TRADE_RETCODE_DONE:
                retcode = getattr(res, "retcode", "None")
                comment = getattr(res, "comment", "Unknown")
                logger.error(f"MT5 BUY order rejected: retcode={retcode}, comment='{comment}'")
                raise RuntimeError(f"MT5 BUY order rejected with retcode {retcode}: {comment}")
            return

        if side == "SELL":
            raw_positions = self.client.positions_get(symbol=broker_symbol)
            long_positions = [p for p in (raw_positions or ()) if getattr(p, "type", ORDER_TYPE_BUY) == ORDER_TYPE_BUY]
            total_open_vol = sum(float(getattr(p, "volume", 0.0)) for p in long_positions)

            if total_open_vol <= 1e-8:
                logger.warning(f"No open long positions to sell for {broker_symbol}; skipping.")
                return

            steps = round(quantity / vol_step)
            requested_vol = round(steps * vol_step, 8)

            if requested_vol < vol_min:
                logger.warning(f"SELL volume {requested_vol} below volume_min {vol_min} for {broker_symbol}; skipping.")
                return

            if requested_vol > total_open_vol:
                logger.warning(
                    f"SELL volume {requested_vol} exceeds open position volume {total_open_vol} "
                    f"for {broker_symbol}; clamping to open volume."
                )
                steps = round(total_open_vol / vol_step)
                vol_to_sell = round(steps * vol_step, 8)
                if vol_to_sell > total_open_vol:
                    vol_to_sell = round((steps - 1) * vol_step, 8)
            else:
                vol_to_sell = requested_vol

            if vol_to_sell < vol_min:
                logger.warning(
                    f"Clamped SELL volume {vol_to_sell} below volume_min {vol_min} for {broker_symbol}; skipping."
                )
                return

            # Close tickets by position id
            tick = self.client.symbol_info_tick(broker_symbol)
            price = tick.bid if tick and tick.bid else getattr(sym_info, "bid", 0.0)

            for pos in sorted(long_positions, key=lambda p: getattr(p, "ticket", 0)):
                if vol_to_sell <= 1e-8:
                    break
                pos_id = getattr(pos, "ticket", None)
                pos_vol = float(getattr(pos, "volume", 0.0))
                lot_vol = min(vol_to_sell, pos_vol)
                lot_steps = round(lot_vol / vol_step)
                lot_vol = round(lot_steps * vol_step, 8)
                if lot_vol > pos_vol:
                    lot_vol = round(pos_vol, 8)
                if lot_vol < vol_min:
                    continue

                req = {
                    "action": TRADE_ACTION_DEAL,
                    "symbol": broker_symbol,
                    "volume": lot_vol,
                    "type": ORDER_TYPE_SELL,
                    "position": pos_id,
                    "price": price,
                    "deviation": 20,
                    "type_filling": filling_mode,
                }
                res = self.client.order_send(req)
                if res is None or getattr(res, "retcode", None) != TRADE_RETCODE_DONE:
                    retcode = getattr(res, "retcode", "None")
                    comment = getattr(res, "comment", "Unknown")
                    logger.error(f"MT5 SELL order rejected: retcode={retcode}, comment='{comment}'")
                    raise RuntimeError(f"MT5 SELL order rejected with retcode {retcode}: {comment}")

                vol_to_sell = round(vol_to_sell - lot_vol, 8)

    def cancel_open_orders(self) -> int:
        """Remove all pending working orders via TRADE_ACTION_REMOVE."""
        orders = self.client.orders_get()
        if orders is None:
            # None is a failed call (an empty tuple means "no orders"). Abort the sync
            # rather than size new orders against a book we could not read.
            err_code, err_msg = self.client.last_error()
            raise RuntimeError(f"MT5 orders_get failed: ({err_code}) {err_msg}")

        cancelled_count = 0
        for order in orders:
            ticket = getattr(order, "ticket", None)
            if ticket is None:
                continue
            req = {
                "action": TRADE_ACTION_REMOVE,
                "order": ticket,
            }
            res = self.client.order_send(req)
            if res and getattr(res, "retcode", None) == TRADE_RETCODE_DONE:
                cancelled_count += 1
            else:
                retcode = getattr(res, "retcode", "None")
                comment = getattr(res, "comment", "")
                # A working order we could not remove may still fill: abort, don't continue.
                raise RuntimeError(f"MT5 failed to cancel order {ticket}: retcode={retcode}, comment='{comment}'")
        return cancelled_count

    def _resolve_symbol(self, yf_symbol: str) -> str | None:
        """
        Resolve symbol on MT5:
        1. Check symbol_mapper override.
        2. Exact bare ticker match via symbols_get.
        3. Suffixed match (.US, .P, .O, .N); refuses ambiguous matches if multiple exist.
        """
        if self.symbol_mapper is not None:
            override = self.symbol_mapper.overrides.get(yf_symbol, {}).get(self.name)
            if override and isinstance(override, dict) and override.get("symbol"):
                return str(override["symbol"])

        if yf_symbol.endswith("=X"):
            ticker = yf_symbol[:-2]
        elif "-USD" in yf_symbol:
            ticker = yf_symbol.replace("-USD", "USD")
        else:
            ticker = yf_symbol

        symbols = self.client.symbols_get()
        if symbols is None:
            return None

        available_upper = {
            getattr(s, "name", "").upper(): getattr(s, "name", "") for s in symbols if getattr(s, "name", "")
        }

        # 1. Exact bare ticker match
        if ticker.upper() in available_upper:
            return available_upper[ticker.upper()]

        # 2. Suffixed match (.US, .P, .O, .N)
        suffixes = [".US", ".P", ".O", ".N"]
        matches = [
            available_upper[f"{ticker}{sfx}".upper()] for sfx in suffixes if f"{ticker}{sfx}".upper() in available_upper
        ]

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning(f"Ambiguous symbol match for {yf_symbol}: {matches}; refusing to guess.")
            return None

        return None

    def is_tradeable(self, yf_symbol: str) -> bool:
        """Whether MT5 lists this symbol (exact or suffixed)."""
        return self._resolve_symbol(yf_symbol) is not None

    def map_symbol(self, yf_symbol: str) -> dict | None:
        """Translate yfinance symbol to MT5 metadata; selects symbol before use."""
        resolved = self._resolve_symbol(yf_symbol)
        if resolved is None:
            return None

        try:
            self.client.symbol_select(resolved, True)
        except Exception as exc:
            logger.warning(f"Failed to symbol_select {resolved}: {exc}")

        symbol_type = "forex" if yf_symbol.endswith("=X") else "stock"
        return {
            "symbol": resolved,
            "type": symbol_type,
            "verified": datetime.now().strftime("%Y-%m-%d"),
            "source": "mt5",
        }

    def search_symbol(self, query: str) -> list[dict]:
        """Search available MT5 symbols matching query."""
        query_clean = query.strip().upper()
        if query_clean.endswith("=X"):
            query_clean = query_clean[:-2]

        symbols = self.client.symbols_get() or ()
        candidates = []
        suffixes = {
            f"{query_clean}.US",
            f"{query_clean}.P",
            f"{query_clean}.O",
            f"{query_clean}.N",
        }

        for s in symbols:
            name = getattr(s, "name", "")
            if not name:
                continue
            name_upper = name.upper()
            score = 0
            if name_upper == query_clean:
                score = 100
            elif name_upper in suffixes:
                score = 90
            elif query_clean in name_upper:
                score = 50

            if score > 0:
                candidates.append(
                    {
                        "symbol": name,
                        "description": getattr(s, "description", name) or name,
                        "type": "forex" if "USD" in name_upper and len(name_upper) == 6 else "stock",
                        "exchange": getattr(s, "exchange", ""),
                        "score": score,
                    }
                )

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates

    def _get_native_price(self, broker_symbol: str) -> float:
        """Fetch latest quote from MT5 tick or symbol info."""
        try:
            tick = self.client.symbol_info_tick(broker_symbol)
            if tick is not None:
                price = getattr(tick, "last", 0.0) or getattr(tick, "bid", 0.0) or getattr(tick, "ask", 0.0)
                if price and price > 0:
                    return float(price)
            info = self.client.symbol_info(broker_symbol)
            if info is not None:
                price = getattr(info, "last", 0.0) or getattr(info, "bid", 0.0) or getattr(info, "ask", 0.0)
                if price and price > 0:
                    return float(price)
        except Exception as exc:
            logger.debug(f"Failed to fetch MT5 native price for {broker_symbol}: {exc}")
        return 0.0

    def _summary_header(self) -> str:
        env = "DEMO" if self.is_sandbox else "LIVE"
        return f"{self.name} Account ({env}) - Login {self.account_ref}"

    def _position_rows(self) -> tuple[str, list[str]]:
        raw_positions = self.client.positions_get() or ()
        header = f"  {'Ticket':<10} {'Symbol':<12} {'Type':<6} {'Volume':>10} {'OpenPrice':>12}"
        rows = []
        for p in raw_positions:
            side_str = "BUY" if getattr(p, "type", 0) == ORDER_TYPE_BUY else "SELL"
            rows.append(
                f"  {getattr(p, 'ticket', 0):<10} "
                f"{getattr(p, 'symbol', ''):<12} "
                f"{side_str:<6} "
                f"{float(getattr(p, 'volume', 0.0)):>10.4f} "
                f"{float(getattr(p, 'price_open', 0.0)):>12.4f}"
            )
        return header, rows
