"""MT5 client contract and RPyC connection factory."""

from typing import Any, NamedTuple, Protocol, runtime_checkable

# MT5 return codes
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_CANCEL = 10007
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_ERROR = 10011
TRADE_RETCODE_TIMEOUT = 10012
TRADE_RETCODE_INVALID = 10013
TRADE_RETCODE_INVALID_VOLUME = 10014
TRADE_RETCODE_INVALID_PRICE = 10015
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_TRADE_DISABLED = 10017
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_PRICE_CHANGED = 10020
TRADE_RETCODE_PRICE_OFF = 10021
TRADE_RETCODE_INVALID_EXPIRATION = 10022
TRADE_RETCODE_ORDER_CHANGED = 10023
TRADE_RETCODE_TOO_MANY_REQUESTS = 10024
TRADE_RETCODE_NO_CHANGES = 10025
TRADE_RETCODE_SERVER_DISABLES_AT = 10026
TRADE_RETCODE_CLIENT_DISABLES_AT = 10027
TRADE_RETCODE_LOCKED = 10028
TRADE_RETCODE_FROZEN = 10029
TRADE_RETCODE_UNSUPPORTED_FILLING_MODE = 10030
TRADE_RETCODE_CONNECTION = 10031
TRADE_RETCODE_ONLY_REAL = 10032
TRADE_RETCODE_LIMIT_ORDERS = 10033
TRADE_RETCODE_LIMIT_VOLUME = 10034
TRADE_RETCODE_POSITION_CLOSED = 10035
TRADE_RETCODE_INVALID_CLOSE_VOLUME = 10036
TRADE_RETCODE_CLOSE_ORDER_EXIST = 10038
TRADE_RETCODE_LIMIT_POSITIONS = 10039

# Order filling modes
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
ORDER_FILLING_BOC = 3

# Symbol filling flags (bitmask)
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
SYMBOL_FILLING_BOC = 4

RETCODE_DESCRIPTIONS: dict[int, str] = {
    TRADE_RETCODE_REQUOTE: "Requote",
    TRADE_RETCODE_REJECT: "Request rejected",
    TRADE_RETCODE_CANCEL: "Request canceled by trader",
    TRADE_RETCODE_PLACED: "Order placed",
    TRADE_RETCODE_DONE: "Request completed",
    TRADE_RETCODE_DONE_PARTIAL: "Only part of request completed",
    TRADE_RETCODE_ERROR: "Request processing error",
    TRADE_RETCODE_TIMEOUT: "Request canceled by timeout",
    TRADE_RETCODE_INVALID: "Invalid request",
    TRADE_RETCODE_INVALID_VOLUME: "Invalid volume",
    TRADE_RETCODE_INVALID_PRICE: "Invalid price",
    TRADE_RETCODE_INVALID_STOPS: "Invalid stops",
    TRADE_RETCODE_TRADE_DISABLED: "Trade is disabled",
    TRADE_RETCODE_MARKET_CLOSED: "Market is closed",
    TRADE_RETCODE_NO_MONEY: "Not enough money",
    TRADE_RETCODE_PRICE_CHANGED: "Prices changed",
    TRADE_RETCODE_PRICE_OFF: "Off quotes",
    TRADE_RETCODE_INVALID_EXPIRATION: "Invalid expiration",
    TRADE_RETCODE_ORDER_CHANGED: "Order state changed",
    TRADE_RETCODE_TOO_MANY_REQUESTS: "Too many requests",
    TRADE_RETCODE_NO_CHANGES: "No changes",
    TRADE_RETCODE_SERVER_DISABLES_AT: "Autotrading disabled by server",
    TRADE_RETCODE_CLIENT_DISABLES_AT: "Autotrading disabled by client terminal",
    TRADE_RETCODE_LOCKED: "Request locked for processing",
    TRADE_RETCODE_FROZEN: "Order or position frozen",
    TRADE_RETCODE_UNSUPPORTED_FILLING_MODE: "Invalid order filling type",
    TRADE_RETCODE_CONNECTION: "No connection with trade server",
    TRADE_RETCODE_ONLY_REAL: "Operation allowed only for live accounts",
    TRADE_RETCODE_LIMIT_ORDERS: "Limit orders reached",
    TRADE_RETCODE_LIMIT_VOLUME: "Limit volume reached",
    TRADE_RETCODE_POSITION_CLOSED: "Position closed",
    TRADE_RETCODE_INVALID_CLOSE_VOLUME: "Invalid close volume",
    TRADE_RETCODE_CLOSE_ORDER_EXIST: "Close order already exists",
    TRADE_RETCODE_LIMIT_POSITIONS: "Limit positions reached",
}


def describe_retcode(retcode: int | None) -> str:
    """Return human-readable explanation for an MT5 retcode."""
    if retcode is None:
        return "Unknown"
    return RETCODE_DESCRIPTIONS.get(retcode, f"Unknown MT5 retcode {retcode}")


try:
    from rpyc.core.async_ import AsyncResultTimeout
except ImportError:
    class AsyncResultTimeout(Exception):  # type: ignore[no-redef]
        pass

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


def connect_rpyc(host: str = "localhost", port: int = 18812, timeout: int = 60) -> MT5Client:
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
    conn._config["sync_request_timeout"] = timeout
    return _RemoteMT5(conn)


class _RemoteMT5:
    """The bridge's MetaTrader5 module, plus bulk helpers that avoid per-item RPC.

    Iterating a remote symbols_get() result costs one round trip per symbol
    (thousands of them, minutes in total), so names are collected server-side
    and shipped in one pickle.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._mt5 = conn.modules["MetaTrader5"]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._mt5, name)

    # MetaTrader5 5.0.36 rejects a positional request dict arriving over RPyC
    # ("Unnamed arguments not allowed", last_error -2), so send it as kwargs.
    def order_send(self, request: dict[str, Any]) -> Any:
        return self._mt5.order_send(**request)

    def order_check(self, request: dict[str, Any]) -> Any:
        return self._mt5.order_check(**request)

    def symbol_names(self) -> list[str] | None:
        from rpyc.utils.classic import obtain

        self._conn.execute("import MetaTrader5 as _mt5")
        names = self._conn.eval("[s.name for s in (_mt5.symbols_get() or [])]")
        return list(obtain(names))
