"""MT5 client contract and RPyC connection factory."""

from typing import Any, NamedTuple, Protocol, runtime_checkable

# MT5 return codes
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_CONNECTION = 10006
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_INVALID = 10013
TRADE_RETCODE_INVALID_VOLUME = 10014
TRADE_RETCODE_INVALID_PRICE = 10015
TRADE_RETCODE_TRADE_DISABLED = 10018
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_NO_MONEY = 10028

# Account margin modes
ACCOUNT_MARGIN_MODE_RETAIL_NETTING = 0
ACCOUNT_MARGIN_MODE_EXCHANGE = 1
ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2

# Trade request actions
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8
TRADE_ACTION_CLOSE_BY = 10

# Order types
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5


class AccountInfo(NamedTuple):
    login: int = 0
    trade_mode: int = 0
    balance: float = 0.0
    equity: float = 0.0
    profit: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float = 0.0
    margin_mode: int = ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    currency: str = "USD"
    server: str = ""
    company: str = ""


class SymbolInfo(NamedTuple):
    name: str = ""
    visible: bool = True
    select: bool = True
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 1000.0
    volume_limit: float = 0.0
    digits: int = 2
    point: float = 0.01
    spread: int = 1
    trade_mode: int = 4
    filling_mode: int = 1
    currency_base: str = "USD"
    currency_profit: str = "USD"
    currency_margin: str = "USD"
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0


class TradePosition(NamedTuple):
    ticket: int = 0
    time: int = 0
    time_msc: int = 0
    time_update: int = 0
    time_update_msc: int = 0
    type: int = ORDER_TYPE_BUY
    magic: int = 0
    identifier: int = 0
    reason: int = 0
    volume: float = 0.0
    price_open: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    price_current: float = 0.0
    swap: float = 0.0
    profit: float = 0.0
    symbol: str = ""
    comment: str = ""
    external_id: str = ""


class TradeOrder(NamedTuple):
    ticket: int = 0
    time_setup: int = 0
    time_setup_msc: int = 0
    time_done: int = 0
    time_done_msc: int = 0
    time_expiration: int = 0
    type: int = ORDER_TYPE_BUY
    type_time: int = 0
    type_filling: int = 0
    state: int = 0
    magic: int = 0
    position_id: int = 0
    position_by_id: int = 0
    reason: int = 0
    volume_initial: float = 0.0
    volume_current: float = 0.0
    price_open: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    price_current: float = 0.0
    symbol: str = ""
    comment: str = ""
    external_id: str = ""


class OrderSendResult(NamedTuple):
    retcode: int = TRADE_RETCODE_DONE
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    comment: str = ""
    request_id: int = 0
    retcode_external: int = 0
    request: Any = None


class Tick(NamedTuple):
    time: int = 0
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: float = 0.0
    time_msc: int = 0
    flags: int = 0
    volume_real: float = 0.0


@runtime_checkable
class MT5Client(Protocol):
    """Protocol covering the subset of MetaTrader5 API used by the framework."""

    def initialize(
        self,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout: int | None = None,
        portable: bool = False,
        **kwargs: Any,
    ) -> bool: ...

    def login(
        self,
        login: int,
        password: str = "",
        server: str = "",
        timeout: int | None = None,
        **kwargs: Any,
    ) -> bool: ...

    def shutdown(self) -> None: ...

    def account_info(self) -> Any | None: ...

    def positions_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[Any, ...] | None: ...

    def orders_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[Any, ...] | None: ...

    def symbol_info(self, symbol: str) -> Any | None: ...

    def symbol_info_tick(self, symbol: str) -> Any | None: ...

    def symbols_get(self, group: str | None = None) -> tuple[Any, ...] | None: ...

    def symbol_select(self, symbol: str, enable: bool = True) -> bool: ...

    def order_send(self, request: dict[str, Any] | Any) -> Any: ...

    def last_error(self) -> tuple[int, str]: ...


def connect_rpyc(host: str = "localhost", port: int = 18812) -> MT5Client:
    """Connect to an MT5 bridge over RPyC (classic) and return its MetaTrader5 module.

    The bridge runs mt5linux's classic RPyC server inside Wine Python, so the
    `MetaTrader5` package is reachable as a remote module. We use rpyc directly
    instead of the mt5linux client: its 1.x releases try to manage Docker
    containers and 0.1.x pins an ancient dependency set.
    """
    try:
        import rpyc
    except ImportError as exc:
        raise ImportError("rpyc is required to connect to MT5 over RPyC. Please install rpyc.") from exc

    conn = rpyc.classic.connect(host, port)
    return conn.modules["MetaTrader5"]
