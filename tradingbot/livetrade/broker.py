import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tradingbot.livetrade.symbol_map import SymbolMapper
    from tradingbot.utils.data_service import DataService

logger = logging.getLogger(__name__)


class LiveBroker(ABC):
    # Subclasses must set these before any get_latest_price call.
    name: str
    symbol_mapper: "SymbolMapper"
    data_service: "DataService"

    # ------------------------------------------------------------------
    # Session lifecycle
    #
    # Default no-ops so callers can drive any broker uniformly. Only the IBKR
    # adapter holds a real session (an OAuth client plus a background tickler);
    # the REST brokers authenticate per request and have nothing to open or
    # close. Declaring these here is what lets a caller write `with broker:` or
    # an unconditional `broker.disconnect()` instead of branching on the broker
    # name — which is what discover_symbols.py and the IB entry script used to
    # do, and is why lifecycle handling kept leaking into callers.
    # ------------------------------------------------------------------
    # B027 is suppressed below: an empty non-abstract method in an ABC is exactly
    # the intent here — "most brokers have no session to manage". Making these
    # abstract would force four adapters to write empty overrides just to satisfy
    # the one broker that does hold a session.
    def connect(self, readonly: bool = False) -> None:  # noqa: B027
        """Open a session if this broker needs one. No-op by default."""

    def disconnect(self) -> None:  # noqa: B027
        """Close any session opened by connect(). No-op by default."""

    def __enter__(self) -> "LiveBroker":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Account identity
    #
    # Used by equity_recorder to key rows in live_equity as
    # (broker, account_ref, date) and to flag paper money. These were
    # previously a getattr() chain in the recorder — `query_address or
    # account_id or system_id` and `getattr(broker, "testnet", False)` — which
    # only ever matched Hyperliquid. That was harmless while Hyperliquid was
    # the sole broker recording equity, but eToro and Darwinex spell their
    # paper flag `demo`, not `testnet`, so extending recording to every broker
    # under the old chain would have silently stamped demo-account equity as
    # real money in the published track record.
    # ------------------------------------------------------------------
    @property
    def account_ref(self) -> str:
        """Stable identifier for the account this session trades.

        Empty string when the venue exposes no account identifier (eToro).
        Rows stay distinguishable because live_equity keys on broker name too.
        """
        return ""

    @property
    def is_sandbox(self) -> bool:
        """True when this session trades paper/demo/testnet money.

        Equity recorded with this set must never be presented as a live track
        record. Defaults to False, so an adapter that forgets to override it
        is treated as real money — the safe direction to be wrong in.
        """
        return False

    @abstractmethod
    def get_cash(self) -> float:
        """Return the current cash balance in USD."""

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Return current open positions as a dict: broker_symbol -> quantity."""

    @abstractmethod
    def get_total_equity(self) -> float:
        """Return total equity (cash + mark-to-market of positions)."""

    def _get_native_price(self, broker_symbol: str) -> float:
        """Broker-native price fetch. Return 0.0 if unavailable so the base
        class can fall back to yfinance via DataService."""
        return 0.0

    def get_latest_price(self, broker_symbol: str) -> float:
        """Fetch the latest price. Tries the broker first, then falls back to
        yfinance via DataService. Subclasses override `_get_native_price`."""
        try:
            price = self._get_native_price(broker_symbol)
            if price and price > 0:
                return price
        except Exception as e:
            logger.debug(f"{self.name} native price fetch failed for {broker_symbol}: {e}")

        yf_symbol = self.symbol_mapper.unmap_symbol(broker_symbol, broker_name=self.name)
        try:
            return self.data_service.get_latest_price(yf_symbol)
        except Exception as e:
            logger.warning(f"yfinance fallback failed for {broker_symbol} (yf: {yf_symbol}): {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Account summary (diagnostic CLI helper)
    #
    # Every adapter's __main__ block calls print_account_summary() as a manual
    # sanity check — never from a trading path. All five prior implementations
    # shared the same skeleton (header, cash/equity lines, a positions count
    # line, "(none)" when flat, else a column header + one row per position)
    # and only differed in: the identity line, which figures belong on the
    # account-lines block, and which raw fields the positions table shows (each
    # venue's API returns different columns — lot ids, open P&L, market value,
    # ...). That skeleton now lives here once; adapters override only the hooks
    # below where their output genuinely differs.
    # ------------------------------------------------------------------
    @staticmethod
    def _summary_line(label: str, value: object, fmt: str = ">15,.2f") -> str:
        """Render one label/value row, e.g. '  Cash:             1,234.56'.

        Every adapter pads its label to the same 18-char column so figures line
        up regardless of label length ('Cash:' vs 'Net Liquidation:' vs
        'Effective lev:') — this was already true independently across all five
        original implementations. Pass fmt="" for a plain string value (e.g. a
        wallet address) instead of a right-aligned number.
        """
        return f"  {label:<18}{value:{fmt}}"

    def _summary_header(self) -> str:
        """First line of print_account_summary, identifying the account.

        Default assumes a plain "<Name> Account (<DEMO|LIVE>)" shape; override
        when the identity line needs different wording (Collective2 is
        identified by strategy id, not an env flag; Hyperliquid needs
        vault-vs-account and testnet-vs-mainnet instead of demo/live).
        """
        env = "DEMO" if self.is_sandbox else "LIVE"
        return f"{self.name} Account ({env})"

    def _pre_summary_lines(self) -> list[str]:
        """Lines printed before the header. Empty by default; overridden by
        adapters that probe extra broker state as a sanity check before
        showing the configured account (IBKR lists every portfolio account
        visible to the session, to catch a misconfigured account_id)."""
        return []

    def _account_lines(self) -> list[str]:
        """Lines printed between the header and the positions block.

        Defaults to plain Cash/Equity via the abstract get_cash/get_total_equity,
        which covers most adapters unchanged. Overridden by Hyperliquid (no
        settled cash on perps — shows withdrawable collateral, total notional
        and effective leverage instead) and IBKR (same two figures, different
        labels: 'Cash (USD)' / 'Net Liquidation').
        """
        return [
            self._summary_line("Cash:", self.get_cash()),
            self._summary_line("Equity:", self.get_total_equity()),
        ]

    def _position_rows(self) -> tuple[str, list[str]]:
        """Return (column header line, formatted row lines) for the positions
        table.

        Default renders get_positions()'s plain symbol->qty dict. Overridden by
        every current adapter because each venue's raw position payload has
        richer fields worth showing (lot ids, side, open P&L, market value, ...)
        that the plain symbol->qty dict discards — kept here as the fallback
        shape for any future adapter that doesn't need those extras.
        """
        positions = self.get_positions()
        header = f"  {'Symbol':<14} {'Qty':>12}"
        rows = [f"  {symbol:<14} {qty:>12.4f}" for symbol, qty in positions.items()]
        return header, rows

    def print_account_summary(self) -> None:
        """Print cash, equity, and open positions for a manual/CLI sanity
        check. Called only from each adapter's __main__ block — never part of
        a trading path. See the hooks above for what each adapter overrides.
        """
        for line in self._pre_summary_lines():
            print(line)
        print(f"\n{self._summary_header()}")
        for line in self._account_lines():
            print(line)

        col_header, rows = self._position_rows()
        print(f"\nPositions ({len(rows)}):")
        if not rows:
            print("  (none)")
            return
        print(col_header)
        for row in rows:
            print(row)

    @abstractmethod
    def place_order(
        self, broker_symbol: str, quantity: float, side: Literal["BUY", "SELL"], symbol_type: str | None = None
    ) -> None:
        """Place a market order."""

    @abstractmethod
    def map_symbol(self, yf_symbol: str) -> dict | None:
        """Translate yfinance symbol (e.g. EURUSD=X) to broker-specific metadata."""

    @abstractmethod
    def search_symbol(self, query: str) -> list[dict]:
        """Return candidates: [{symbol, description, type, exchange, score}, ...]"""

    #: Set True by adapters that deliberately have nothing to cancel (e.g. a
    #: venue whose orders always fill or expire same-session), to silence the
    #: not-implemented warning below.
    cancels_orders_implicitly: bool = False

    def cancel_open_orders(self) -> int:
        """
        Cancel any orders this broker session has previously submitted that
        are still open. Override per-broker. Returns count cancelled.

        copier.sync() calls this before every reconciliation, so an adapter that
        does not override it leaves stale working orders in place while the
        copier sizes new ones against a book it believes is settled. That used
        to be silent — this warning exists so a missing implementation shows up
        in the job log instead of looking like "0 orders to cancel".
        """
        if not self.cancels_orders_implicitly:
            logger.warning(
                "%s does not implement cancel_open_orders(); stale working orders (if any) will survive this sync.",
                getattr(self, "name", type(self).__name__),
            )
        return 0
