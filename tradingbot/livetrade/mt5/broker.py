import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.mt5.client import (
    ORDER_FILLING_FOK,
    ORDER_FILLING_IOC,
    ORDER_FILLING_RETURN,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    SYMBOL_FILLING_FOK,
    SYMBOL_FILLING_IOC,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_REMOVE,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_TIMEOUT,
    AccountInfo,
    AsyncResultTimeout,
    MT5Client,
    SymbolInfo,
    describe_retcode,
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

    supports_limit_orders: bool = True

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
        default_deviation: int = 20,
    ):
        self.client = client
        self.name = name
        self.login = login
        self.server = server
        self._is_sandbox = is_sandbox
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.data_service = data_service or DataService()
        self.rate_provider = rate_provider
        self.default_deviation = default_deviation
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
        SYMBOL_FILLING_IOC = 2 -> ORDER_FILLING_IOC (1)
        SYMBOL_FILLING_FOK = 1 -> ORDER_FILLING_FOK (0)
        Default -> ORDER_FILLING_RETURN (2)
        """
        filling_mode = getattr(symbol_info, "filling_mode", 0)
        if filling_mode & SYMBOL_FILLING_IOC:
            return ORDER_FILLING_IOC
        if filling_mode & SYMBOL_FILLING_FOK:
            return ORDER_FILLING_FOK
        return ORDER_FILLING_RETURN

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, AsyncResultTimeout)):
            return True
        exc_type = type(exc).__name__
        if "timeout" in exc_type.lower() or "expired" in exc_type.lower():
            return True
        msg = str(exc).lower()
        return "result expired" in msg or "timeout" in msg or "timed out" in msg

    @staticmethod
    def _is_timeout_code_or_msg(code: int, msg: str) -> bool:
        if code == TRADE_RETCODE_TIMEOUT:
            return True
        msg_lower = msg.lower()
        return "timeout" in msg_lower or "expired" in msg_lower or "timed out" in msg_lower

    def place_order(
        self,
        broker_symbol: str,
        quantity: float,
        side: Literal["BUY", "SELL"],
        symbol_type: str | None = None,
        limit_price: float | None = None,
    ) -> None:
        """
        Place a market/marketable limit order.
        - Rounds volume to volume_step and clamps to volume_min/max.
        - Skips with a warning if below volume_min.
        - Sets deviation (expanded to cover limit_price if specified) and supported filling mode.
        - BUY confirms execution via position lookup if timeout occurs.
        - SELL closes tickets by position id, confirms via position lookup on timeout, and never oversells.
        - Rejections raise RuntimeError with descriptive retcode mapping.
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
        point = getattr(sym_info, "point", 0.01) or 0.01
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
            market_price = tick.ask if tick and tick.ask else getattr(sym_info, "ask", 0.0)

            price = market_price
            deviation = self.default_deviation

            if limit_price is not None:
                if price <= 0:
                    price = limit_price
                if point > 0 and price > 0:
                    points_diff = round(abs(limit_price - price) / point)
                    deviation = max(deviation, points_diff)

            # Record initial positions before order for confirmation on potential timeout
            raw_positions_before = self.client.positions_get(symbol=broker_symbol)
            initial_vol = sum(
                float(getattr(p, "volume", 0.0))
                for p in (raw_positions_before or ())
                if getattr(p, "type", ORDER_TYPE_BUY) == ORDER_TYPE_BUY
            )

            req = {
                "action": TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": vol,
                "type": ORDER_TYPE_BUY,
                "price": price,
                "deviation": deviation,
                "type_filling": filling_mode,
            }

            res = None
            timed_out = False
            timeout_reason = ""

            try:
                res = self.client.order_send(req)
            except Exception as exc:
                if self._is_timeout(exc):
                    timed_out = True
                    timeout_reason = str(exc)
                else:
                    raise

            if not timed_out:
                if res is None:
                    err_code, err_msg = self.client.last_error()
                    if self._is_timeout_code_or_msg(err_code, err_msg):
                        timed_out = True
                        timeout_reason = f"({err_code}) {err_msg}"
                    else:
                        raise RuntimeError(f"MT5 BUY order failed: order_send returned None ({err_code}: {err_msg})")
                elif (
                    getattr(res, "retcode", None) == TRADE_RETCODE_TIMEOUT
                    or "expired" in str(getattr(res, "comment", "")).lower()
                    or "timeout" in str(getattr(res, "comment", "")).lower()
                ):
                    timed_out = True
                    timeout_reason = f"retcode={getattr(res, 'retcode', None)}, comment='{getattr(res, 'comment', '')}'"

            if timed_out:
                logger.warning(
                    f"MT5 BUY order for {broker_symbol} timed out ({timeout_reason}); "
                    "confirming execution via position lookup..."
                )
                raw_positions_after = self.client.positions_get(symbol=broker_symbol)
                current_vol = sum(
                    float(getattr(p, "volume", 0.0))
                    for p in (raw_positions_after or ())
                    if getattr(p, "type", ORDER_TYPE_BUY) == ORDER_TYPE_BUY
                )
                if current_vol >= initial_vol + vol - 1e-6 or (current_vol > initial_vol + 1e-8):
                    logger.info(
                        f"MT5 BUY order confirmed via position lookup: volume is {current_vol} (was {initial_vol})."
                    )
                    return
                else:
                    logger.error(
                        f"MT5 BUY order timed out and position was not updated (expected >= {initial_vol + vol}, found {current_vol})."
                    )
                    raise RuntimeError(f"MT5 BUY order timed out: {timeout_reason}")

            retcode = getattr(res, "retcode", None)
            if retcode not in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL, TRADE_RETCODE_PLACED):
                comment = getattr(res, "comment", "Unknown")
                desc = describe_retcode(retcode)
                logger.error(f"MT5 BUY order rejected: retcode={retcode} ({desc}), comment='{comment}'")
                raise RuntimeError(f"MT5 BUY order rejected with retcode {retcode} ({desc}): {comment}")
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
            market_price = tick.bid if tick and tick.bid else getattr(sym_info, "bid", 0.0)

            price = market_price
            deviation = self.default_deviation

            if limit_price is not None:
                if price <= 0:
                    price = limit_price
                if point > 0 and price > 0:
                    points_diff = round(abs(limit_price - price) / point)
                    deviation = max(deviation, points_diff)

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
                    "deviation": deviation,
                    "type_filling": filling_mode,
                }

                res = None
                timed_out = False
                timeout_reason = ""

                try:
                    res = self.client.order_send(req)
                except Exception as exc:
                    if self._is_timeout(exc):
                        timed_out = True
                        timeout_reason = str(exc)
                    else:
                        raise

                if not timed_out:
                    if res is None:
                        err_code, err_msg = self.client.last_error()
                        if self._is_timeout_code_or_msg(err_code, err_msg):
                            timed_out = True
                            timeout_reason = f"({err_code}) {err_msg}"
                        else:
                            raise RuntimeError(
                                f"MT5 SELL order failed: order_send returned None ({err_code}: {err_msg})"
                            )
                    elif (
                        getattr(res, "retcode", None) == TRADE_RETCODE_TIMEOUT
                        or "expired" in str(getattr(res, "comment", "")).lower()
                        or "timeout" in str(getattr(res, "comment", "")).lower()
                    ):
                        timed_out = True
                        timeout_reason = (
                            f"retcode={getattr(res, 'retcode', None)}, comment='{getattr(res, 'comment', '')}'"
                        )

                if timed_out:
                    logger.warning(
                        f"MT5 SELL order for {broker_symbol} (ticket {pos_id}) timed out ({timeout_reason}); "
                        "confirming execution via position lookup..."
                    )
                    raw_positions_after = self.client.positions_get(symbol=broker_symbol)
                    matching_pos = [p for p in (raw_positions_after or ()) if getattr(p, "ticket", None) == pos_id]
                    total_after = sum(
                        float(getattr(p, "volume", 0.0))
                        for p in (raw_positions_after or ())
                        if getattr(p, "type", ORDER_TYPE_BUY) == ORDER_TYPE_BUY
                    )
                    confirmed = False
                    if (
                        not matching_pos
                        or float(getattr(matching_pos[0], "volume", 0.0)) <= pos_vol - lot_vol + 1e-6
                        or total_after <= total_open_vol - lot_vol + 1e-6
                    ):
                        confirmed = True

                    if confirmed:
                        logger.info(f"MT5 SELL order confirmed via position lookup for ticket {pos_id}.")
                        vol_to_sell = round(vol_to_sell - lot_vol, 8)
                        continue
                    else:
                        logger.error(
                            f"MT5 SELL order timed out and position was not closed/reduced for ticket {pos_id}."
                        )
                        raise RuntimeError(f"MT5 SELL order timed out: {timeout_reason}")

                retcode = getattr(res, "retcode", None)
                if retcode not in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL, TRADE_RETCODE_PLACED):
                    comment = getattr(res, "comment", "Unknown")
                    desc = describe_retcode(retcode)
                    logger.error(f"MT5 SELL order rejected: retcode={retcode} ({desc}), comment='{comment}'")
                    raise RuntimeError(f"MT5 SELL order rejected with retcode {retcode} ({desc}): {comment}")

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

    def _symbol_index(self) -> dict[str, str] | None:
        """Upper-case name -> broker name for every listed symbol, fetched once per run."""
        cached = getattr(self, "_symbol_index_cache", None)
        if cached is not None:
            return cached
        bulk = getattr(self.client, "symbol_names", None)
        if bulk is not None:
            names = bulk()
        else:
            symbols = self.client.symbols_get()
            names = None if symbols is None else [getattr(s, "name", "") for s in symbols]
        if names is None:
            return None
        self._symbol_index_cache = {n.upper(): n for n in names if n}
        return self._symbol_index_cache

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

        available_upper = self._symbol_index()
        if available_upper is None:
            return None

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

    # SYMBOL_TRADE_MODE_*: 0 disabled, 1 long only, 2 short only, 3 close only, 4 full.
    # The copier is long-only, so only 1 and 4 can take a BUY.
    _BUYABLE_TRADE_MODES = frozenset({1, 4})

    def is_tradeable(self, yf_symbol: str) -> bool:
        """Whether MT5 lists this symbol AND the server lets us open a long in it.

        A listed symbol can still be switched off server-side (MetaQuotes-Demo lists SPY
        and UUP with trade_mode 0). Reporting those as tradeable made the copier send
        the buy every run, get "Trade disabled" (10017), and leave the weight idle.
        Reporting them untradeable routes the weight through the copier's drop-and-park
        path instead.
        """
        resolved = self._resolve_symbol(yf_symbol)
        if resolved is None:
            return False
        try:
            info = self.client.symbol_info(resolved)
        except Exception as exc:
            # Don't guess: an unreadable symbol is neither listed-and-open nor known closed.
            logger.warning(f"symbol_info({resolved}) failed ({exc}); treating {yf_symbol} as untradeable.")
            return False
        if info is None:
            return False
        mode = getattr(info, "trade_mode", 4)
        if mode not in self._BUYABLE_TRADE_MODES:
            logger.warning(f"MT5 symbol {resolved} has trade_mode={mode} (not buyable); treating as untradeable.")
            return False
        return True

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
