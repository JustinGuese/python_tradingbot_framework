"""In-memory FakeMT5 client implementing MT5Client protocol for testing."""

import fnmatch
import time
from typing import Any

from tradingbot.livetrade.mt5.client import (
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
    ACCOUNT_MARGIN_MODE_RETAIL_NETTING,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_MARKET_CLOSED,
    AccountInfo,
    OrderSendResult,
    SymbolInfo,
    Tick,
    TradeOrder,
    TradePosition,
)


class FakeMT5:
    """Fake MetaTrader5 client for unit testing MT5 adapters and copiers."""

    def __init__(
        self,
        account_currency: str = "USD",
        margin_mode: int = ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
        balance: float = 100000.0,
        equity: float | None = None,
        market_closed: bool = False,
        login: int = 12345678,
        server: str = "MetaQuotes-Demo",
        symbols: dict[str, SymbolInfo] | None = None,
    ) -> None:
        self.account_currency = account_currency
        self.margin_mode = margin_mode
        self.balance = balance
        self.equity = balance if equity is None else equity
        self.market_closed = market_closed
        self.login_id = login
        self.server = server
        self.connected = True
        self.profit = 0.0
        self.margin = 0.0

        self._ticket_counter = 1000
        self._last_error = (0, "Success")
        self._positions: dict[int, TradePosition] = {}
        self._orders: dict[int, TradeOrder] = {}
        self._symbols: dict[str, SymbolInfo] = {}

        if symbols is not None:
            self._symbols.update(symbols)
        else:
            self._init_default_symbols()

    def _init_default_symbols(self) -> None:
        defaults = [
            ("SPY", 0.01, 0.01, 1000.0, 500.0),
            ("QQQ", 0.01, 0.01, 1000.0, 450.0),
            ("TLT", 0.01, 0.01, 1000.0, 90.0),
            ("SHV", 0.01, 0.01, 1000.0, 110.0),
            ("EURUSD", 0.01, 0.01, 500.0, 1.08),
        ]
        for name, vmin, vstep, vmax, price in defaults:
            self.add_symbol(
                name=name,
                volume_min=vmin,
                volume_step=vstep,
                volume_max=vmax,
                bid=price,
                ask=price,
                last=price,
            )

    def _next_ticket(self) -> int:
        self._ticket_counter += 1
        return self._ticket_counter

    def add_symbol(
        self,
        name: str,
        volume_min: float = 0.01,
        volume_step: float = 0.01,
        volume_max: float = 1000.0,
        volume_limit: float = 0.0,
        digits: int = 2,
        point: float = 0.01,
        spread: int = 1,
        trade_mode: int = 4,
        filling_mode: int = 1,
        currency_base: str = "USD",
        currency_profit: str = "USD",
        currency_margin: str = "USD",
        bid: float = 100.0,
        ask: float = 100.0,
        last: float = 100.0,
        visible: bool = True,
        select: bool = True,
    ) -> SymbolInfo:
        info = SymbolInfo(
            name=name,
            visible=visible,
            select=select,
            volume_min=volume_min,
            volume_step=volume_step,
            volume_max=volume_max,
            volume_limit=volume_limit,
            digits=digits,
            point=point,
            spread=spread,
            trade_mode=trade_mode,
            filling_mode=filling_mode,
            currency_base=currency_base,
            currency_profit=currency_profit,
            currency_margin=currency_margin,
            bid=bid,
            ask=ask,
            last=last,
        )
        self._symbols[name] = info
        return info

    def add_position(
        self,
        symbol: str,
        volume: float,
        pos_type: int = ORDER_TYPE_BUY,
        price_open: float = 100.0,
        ticket: int | None = None,
    ) -> TradePosition:
        t = ticket if ticket is not None else self._next_ticket()
        pos = TradePosition(
            ticket=t,
            time=int(time.time()),
            time_msc=int(time.time() * 1000),
            time_update=int(time.time()),
            time_update_msc=int(time.time() * 1000),
            type=pos_type,
            volume=volume,
            price_open=price_open,
            price_current=price_open,
            symbol=symbol,
        )
        self._positions[t] = pos
        return pos

    def set_market_closed(self, closed: bool) -> None:
        self.market_closed = closed

    # ------------------------------------------------------------------
    # MT5Client Protocol Implementation
    # ------------------------------------------------------------------

    def initialize(
        self,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout: int | None = None,
        portable: bool = False,
        **kwargs: Any,
    ) -> bool:
        self.connected = True
        if login is not None:
            self.login_id = login
        if server is not None:
            self.server = server
        return True

    def login(
        self,
        login: int,
        password: str = "",
        server: str = "",
        timeout: int | None = None,
        **kwargs: Any,
    ) -> bool:
        self.connected = True
        self.login_id = login
        if server:
            self.server = server
        return True

    def shutdown(self) -> None:
        self.connected = False

    def account_info(self) -> AccountInfo | None:
        if not self.connected:
            return None
        return AccountInfo(
            login=self.login_id,
            trade_mode=0,
            balance=self.balance,
            equity=self.equity,
            profit=self.profit,
            margin=self.margin,
            margin_free=self.balance - self.margin,
            margin_level=100.0,
            margin_mode=self.margin_mode,
            currency=self.account_currency,
            server=self.server,
            company="FakeMetaQuotes",
        )

    def _match_group(self, name: str, group: str) -> bool:
        patterns = [p.strip() for p in group.split(",") if p.strip()]
        if not patterns:
            return True
        matched = False
        for pat in patterns:
            if pat.startswith("!"):
                if fnmatch.fnmatch(name, pat[1:]):
                    return False
            elif fnmatch.fnmatch(name, pat):
                matched = True
        return matched

    def positions_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[TradePosition, ...] | None:
        if not self.connected:
            return None
        positions = list(self._positions.values())
        if symbol is not None:
            positions = [p for p in positions if p.symbol == symbol]
        if ticket is not None:
            positions = [p for p in positions if p.ticket == ticket]
        if group is not None and group != "" and group != "*":
            positions = [p for p in positions if self._match_group(p.symbol, group)]
        return tuple(positions)

    def orders_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[TradeOrder, ...] | None:
        if not self.connected:
            return None
        orders = list(self._orders.values())
        if symbol is not None:
            orders = [o for o in orders if o.symbol == symbol]
        if ticket is not None:
            orders = [o for o in orders if o.ticket == ticket]
        if group is not None and group != "" and group != "*":
            orders = [o for o in orders if self._match_group(o.symbol, group)]
        return tuple(orders)

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        return self._symbols.get(symbol)

    def symbol_info_tick(self, symbol: str) -> Tick | None:
        sym = self._symbols.get(symbol)
        if sym is None:
            return None
        now = time.time()
        return Tick(
            time=int(now),
            bid=sym.bid,
            ask=sym.ask,
            last=sym.last,
            volume=1.0,
            time_msc=int(now * 1000),
            flags=0,
            volume_real=1.0,
        )

    def symbols_get(self, group: str | None = None) -> tuple[SymbolInfo, ...] | None:
        symbols = list(self._symbols.values())
        if group is not None and group != "" and group != "*":
            symbols = [s for s in symbols if self._match_group(s.name, group)]
        return tuple(symbols)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        if symbol not in self._symbols:
            return False
        self._symbols[symbol] = self._symbols[symbol]._replace(select=enable, visible=enable)
        return True

    def last_error(self) -> tuple[int, str]:
        return self._last_error

    def order_send(self, request: dict[str, Any] | Any) -> OrderSendResult:
        if isinstance(request, dict):
            req = request
        elif hasattr(request, "_asdict"):
            req = request._asdict()
        else:
            req = vars(request)

        if self.market_closed:
            self._last_error = (TRADE_RETCODE_MARKET_CLOSED, "Market is closed")
            return OrderSendResult(
                retcode=TRADE_RETCODE_MARKET_CLOSED,
                comment="Market is closed",
                request=request,
            )

        action = req.get("action", TRADE_ACTION_DEAL)

        if action == TRADE_ACTION_REMOVE:
            order_ticket = req.get("order")
            if order_ticket in self._orders:
                del self._orders[order_ticket]
                return OrderSendResult(
                    retcode=TRADE_RETCODE_DONE,
                    order=order_ticket,
                    comment="Order removed",
                    request=request,
                )
            self._last_error = (TRADE_RETCODE_INVALID, "Order not found")
            return OrderSendResult(
                retcode=TRADE_RETCODE_INVALID,
                comment="Order not found",
                request=request,
            )

        if action == TRADE_ACTION_PENDING:
            ticket = self._next_ticket()
            order = TradeOrder(
                ticket=ticket,
                symbol=req.get("symbol", ""),
                volume_initial=float(req.get("volume", 0.0)),
                volume_current=float(req.get("volume", 0.0)),
                price_open=float(req.get("price", 0.0)),
                type=req.get("type", ORDER_TYPE_BUY),
                comment=req.get("comment", ""),
            )
            self._orders[ticket] = order
            return OrderSendResult(
                retcode=TRADE_RETCODE_DONE,
                order=ticket,
                volume=order.volume_initial,
                price=order.price_open,
                comment="Pending order placed",
                request=request,
            )

        if action == TRADE_ACTION_DEAL:
            symbol = req.get("symbol", "")
            volume = float(req.get("volume", 0.0))
            order_type = req.get("type", ORDER_TYPE_BUY)
            pos_id = req.get("position")

            sym_info = self._symbols.get(symbol)
            if sym_info and (volume < sym_info.volume_min or volume > sym_info.volume_max):
                self._last_error = (TRADE_RETCODE_INVALID_VOLUME, "Invalid volume")
                return OrderSendResult(
                    retcode=TRADE_RETCODE_INVALID_VOLUME,
                    comment="Invalid volume",
                    request=request,
                )

            # Processing SELL
            if order_type == ORDER_TYPE_SELL:
                if pos_id is not None:
                    if pos_id not in self._positions:
                        self._last_error = (TRADE_RETCODE_INVALID, f"Position {pos_id} not found")
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_INVALID,
                            comment=f"Position {pos_id} not found",
                            request=request,
                        )
                    existing = self._positions[pos_id]
                    if round(volume, 8) > round(existing.volume, 8):
                        self._last_error = (TRADE_RETCODE_INVALID_VOLUME, "Volume exceeds position volume")
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_INVALID_VOLUME,
                            comment="Volume exceeds position volume",
                            request=request,
                        )
                    deal_ticket = self._next_ticket()
                    order_ticket = self._next_ticket()
                    rem = round(existing.volume - volume, 8)
                    if rem <= 1e-8:
                        del self._positions[pos_id]
                    else:
                        self._positions[pos_id] = existing._replace(volume=rem)
                    return OrderSendResult(
                        retcode=TRADE_RETCODE_DONE,
                        deal=deal_ticket,
                        order=order_ticket,
                        volume=volume,
                        comment="Position closed/reduced",
                        request=request,
                    )

                if self.margin_mode == ACCOUNT_MARGIN_MODE_RETAIL_NETTING:
                    net_pos = next((p for p in self._positions.values() if p.symbol == symbol), None)
                    if net_pos is None:
                        ticket = self._next_ticket()
                        deal_ticket = self._next_ticket()
                        self._positions[ticket] = TradePosition(
                            ticket=ticket,
                            symbol=symbol,
                            volume=volume,
                            type=ORDER_TYPE_SELL,
                            price_open=float(req.get("price", sym_info.bid if sym_info else 0.0)),
                        )
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_DONE,
                            deal=deal_ticket,
                            order=ticket,
                            volume=volume,
                            comment="Net short position opened",
                            request=request,
                        )
                    if net_pos.type == ORDER_TYPE_BUY:
                        if round(volume, 8) > round(net_pos.volume, 8):
                            self._last_error = (TRADE_RETCODE_INVALID_VOLUME, "Volume exceeds net position")
                            return OrderSendResult(
                                retcode=TRADE_RETCODE_INVALID_VOLUME,
                                comment="Volume exceeds net position",
                                request=request,
                            )
                        deal_ticket = self._next_ticket()
                        order_ticket = self._next_ticket()
                        rem = round(net_pos.volume - volume, 8)
                        if rem <= 1e-8:
                            del self._positions[net_pos.ticket]
                        else:
                            self._positions[net_pos.ticket] = net_pos._replace(volume=rem)
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_DONE,
                            deal=deal_ticket,
                            order=order_ticket,
                            volume=volume,
                            comment="Net long position reduced",
                            request=request,
                        )
                    else:
                        deal_ticket = self._next_ticket()
                        order_ticket = self._next_ticket()
                        self._positions[net_pos.ticket] = net_pos._replace(volume=round(net_pos.volume + volume, 8))
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_DONE,
                            deal=deal_ticket,
                            order=order_ticket,
                            volume=volume,
                            comment="Net short position increased",
                            request=request,
                        )
                else:
                    ticket = self._next_ticket()
                    deal_ticket = self._next_ticket()
                    self._positions[ticket] = TradePosition(
                        ticket=ticket,
                        symbol=symbol,
                        volume=volume,
                        type=ORDER_TYPE_SELL,
                        price_open=float(req.get("price", sym_info.bid if sym_info else 0.0)),
                    )
                    return OrderSendResult(
                        retcode=TRADE_RETCODE_DONE,
                        deal=deal_ticket,
                        order=ticket,
                        volume=volume,
                        comment="Hedge short position opened",
                        request=request,
                    )

            # Processing BUY
            if order_type == ORDER_TYPE_BUY:
                if self.margin_mode == ACCOUNT_MARGIN_MODE_RETAIL_NETTING:
                    net_pos = next((p for p in self._positions.values() if p.symbol == symbol), None)
                    if net_pos is None:
                        ticket = self._next_ticket()
                        deal_ticket = self._next_ticket()
                        self._positions[ticket] = TradePosition(
                            ticket=ticket,
                            symbol=symbol,
                            volume=volume,
                            type=ORDER_TYPE_BUY,
                            price_open=float(req.get("price", sym_info.ask if sym_info else 0.0)),
                        )
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_DONE,
                            deal=deal_ticket,
                            order=ticket,
                            volume=volume,
                            comment="Net long position opened",
                            request=request,
                        )
                    if net_pos.type == ORDER_TYPE_BUY:
                        deal_ticket = self._next_ticket()
                        order_ticket = self._next_ticket()
                        self._positions[net_pos.ticket] = net_pos._replace(volume=round(net_pos.volume + volume, 8))
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_DONE,
                            deal=deal_ticket,
                            order=order_ticket,
                            volume=volume,
                            comment="Net long position increased",
                            request=request,
                        )
                    else:
                        if round(volume, 8) > round(net_pos.volume, 8):
                            self._last_error = (TRADE_RETCODE_INVALID_VOLUME, "Volume exceeds net short position")
                            return OrderSendResult(
                                retcode=TRADE_RETCODE_INVALID_VOLUME,
                                comment="Volume exceeds net short position",
                                request=request,
                            )
                        deal_ticket = self._next_ticket()
                        order_ticket = self._next_ticket()
                        rem = round(net_pos.volume - volume, 8)
                        if rem <= 1e-8:
                            del self._positions[net_pos.ticket]
                        else:
                            self._positions[net_pos.ticket] = net_pos._replace(volume=rem)
                        return OrderSendResult(
                            retcode=TRADE_RETCODE_DONE,
                            deal=deal_ticket,
                            order=order_ticket,
                            volume=volume,
                            comment="Net short position reduced",
                            request=request,
                        )
                else:
                    ticket = self._next_ticket()
                    deal_ticket = self._next_ticket()
                    self._positions[ticket] = TradePosition(
                        ticket=ticket,
                        symbol=symbol,
                        volume=volume,
                        type=ORDER_TYPE_BUY,
                        price_open=float(req.get("price", sym_info.ask if sym_info else 0.0)),
                    )
                    return OrderSendResult(
                        retcode=TRADE_RETCODE_DONE,
                        deal=deal_ticket,
                        order=ticket,
                        volume=volume,
                        comment="Hedge long position opened",
                        request=request,
                    )

        return OrderSendResult(
            retcode=TRADE_RETCODE_INVALID,
            comment="Unsupported trade action",
            request=request,
        )
