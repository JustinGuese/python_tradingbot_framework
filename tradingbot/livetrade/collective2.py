import logging
from typing import Literal

import httpx

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.symbol_map import SymbolMapper
from tradingbot.utils.data_service import DataService

logger = logging.getLogger(__name__)


class Collective2Broker(LiveBroker):
    # C2 API v4 base URL
    BASE_URL = "https://api4-general.collective2.com"

    def __init__(
        self,
        api_key: str,
        system_id: str,
        symbol_mapper: SymbolMapper | None = None,
        data_service: DataService | None = None,
    ):
        self.name = "collective2"
        self.api_key = api_key
        self.system_id = system_id
        self.symbol_mapper = symbol_mapper or SymbolMapper()
        self.client = httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {self.api_key}"})
        self.data_service = data_service or DataService()

    @property
    def account_ref(self) -> str:
        return str(self.system_id)

    # No is_sandbox override: C2 strategies are signal-only, so there is no
    # demo/live split to reflect — every system is equally "real" to C2.

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, json_data: dict | None = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.client.post(url, json=json_data)
        if response.status_code >= 400:
            logger.error(f"C2 POST {endpoint} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _delete(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        response = self.client.request("DELETE", url, params=params)
        if response.status_code >= 400:
            logger.error(f"C2 DELETE {endpoint} -> {response.status_code}: {response.text}")
        response.raise_for_status()
        return response.json()

    def _strategy_details(self) -> dict:
        """Returns the first strategy object from GetStrategyDetails response."""
        data = self._get("Strategies/GetStrategyDetails", {"StrategyId": self.system_id})
        # v4 typically returns { "Results": [ { ... } ] } but field names vary.
        results = data.get("Results") or data.get("Strategy") or []
        if isinstance(results, list) and results:
            return results[0]
        if isinstance(results, dict):
            return results
        logger.warning(f"Could not extract strategy object from response: {data}")
        return {}

    @staticmethod
    def _money(obj: dict, key: str) -> float:
        try:
            return float(obj.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def get_cash(self) -> float:
        return self._money(self._strategy_details(), "ModelAccountCash")

    def get_total_equity(self) -> float:
        return self._money(self._strategy_details(), "ModelAccountValue")

    def get_positions(self) -> dict[str, float]:
        """Returns broker_symbol -> quantity.

        The ticker is NESTED in the v4 response, exactly as `place_order` writes it:
        `{"C2Symbol": {"FullSymbol": "QQQ", ...}, "ExchangeSymbol": {"Symbol": "QQQ", ...}}`.
        There is no top-level "Symbol" key. Reading `pos["Symbol"]` returned None for
        every row, so all positions collapsed onto a single `None` key and the last row
        won — a strategy holding 113 QQQ and 152 TQQQ parsed as `{None: 152}`.

        That is not a cosmetic parse bug. `_calculate_orders` does full target-state
        reconciliation, so invisible holdings read as "not held": the copier would have
        bought the entire target on top of the existing book (roughly doubling it) while
        emitting a nonsense SELL for symbol None. Caught on 2026-08-24 by a dry run.
        """
        data = self._get("Strategies/GetStrategyOpenPositions", {"StrategyIds": [int(self.system_id)]})
        positions: dict[str, float] = {}
        # v4 returns { "Results": [ { ... } ] }
        for pos in data.get("Results", []):
            symbol = (pos.get("C2Symbol") or {}).get("FullSymbol") or (pos.get("ExchangeSymbol") or {}).get("Symbol")
            if not symbol:
                # Raise rather than skip. Skipping would under-report the book and the
                # copier would re-buy what we already hold; sync() catches this and
                # aborts, which is the correct outcome for an unreadable position.
                raise ValueError(f"Collective2 position has no resolvable symbol: {pos}")
            # Accumulate: C2 may return one row per lot for the same ticker, and
            # assignment would silently keep only the last.
            positions[symbol] = positions.get(symbol, 0.0) + float(pos.get("Quantity", 0.0))
        return positions

    def get_active_orders(self) -> list[dict]:
        """Working (unfilled) orders. Note the plural `StrategyIds` — `StrategyId` 400s."""
        data = self._get("Strategies/GetStrategyActiveOrders", {"StrategyIds": [int(self.system_id)]})
        return data.get("Results") or []

    def cancel_open_orders(self) -> int:
        """Cancel every working order before the copier sizes new ones. Returns count cancelled.

        Without this, C2 fell through to LiveBroker's warn-and-return-0 default, and
        GetStrategyOpenPositions does NOT reflect unfilled orders. So a second sync run
        before the first one's orders filled saw the ORIGINAL book, recomputed the same
        deltas, and submitted them again. On 2026-08-25 two runs 4 minutes apart (the
        second triggered by unsuspending the CronJob, which immediately ran the missed
        21:50 schedule) queued 147 QQQ sells against 113 shares held — the strategy would
        have opened net short 34 QQQ. Orders sit working until the next session open, so
        any run outside market hours leaves a full window for this to happen.
        """
        cancelled = 0
        for order in self.get_active_orders():
            signal_id = order.get("SignalId")
            if not signal_id:
                continue
            # Both ids are mandatory; SignalId alone 400s with "StrategyId: Missing value".
            self._delete(
                "Strategies/CancelStrategyOrder",
                {"SignalId": int(signal_id), "StrategyId": int(self.system_id)},
            )
            logger.info(f"Cancelled working C2 order SignalId {signal_id}")
            cancelled += 1
        return cancelled

    # C2 API v4 has no native quote endpoint — base class falls back to yfinance.

    def place_order(
        self, broker_symbol: str, quantity: float, side: Literal["BUY", "SELL"], symbol_type: str | None = None
    ) -> None:

        # Determine precision based on type
        precision = 0
        if symbol_type == "crypto":
            precision = 4  # Allow 4 decimals for crypto
        elif symbol_type == "forex":
            precision = 0  # C2 forex is usually integer units

        rounded_qty = round(quantity, precision)
        if rounded_qty == 0:
            logger.warning(f"Quantity {quantity} for {broker_symbol} rounded to 0. Skipping order.")
            return

        # C2 v4 uses "forex", "crypto", "stock", "future", "option"
        if not symbol_type:
            symbol_type = "stock"  # default

        if symbol_type == "index":
            symbol_type = "stock"

        # Side: 'Buy' or 'Sell'
        # OrderType: 'Market'
        # TIF: 'Day'
        payload = {
            "Order": {
                "StrategyId": int(self.system_id),
                "OrderType": 1,
                "Side": 1 if side == "BUY" else 2,
                "OrderQuantity": abs(rounded_qty),
                "TIF": 1,
                "C2Symbol": {"FullSymbol": broker_symbol, "SymbolType": symbol_type},
            }
        }

        logger.info(f"Submitting C2 order: {payload}")
        try:
            result = self._post("Strategies/NewStrategyOrder", payload)
            # v4 acknowledges an accepted order with {"Results": [{"SignalId": 157376139}],
            # "ResponseStatus": {"ErrorCode": "200"}}. There is no "Success" key and no
            # "OrderID" — checking `result.get("Success")` was false for EVERY accepted
            # order, so every successful submission logged "C2 Order Failed: Unknown error".
            # That is worse than cosmetic: it made a real rejection indistinguishable from
            # a fill, and sent us hunting a submission bug that did not exist (2026-08-25).
            signal_id = next((r.get("SignalId") for r in result.get("Results") or [] if r.get("SignalId")), None)
            if signal_id:
                logger.info(f"C2 Order Success: SignalId {signal_id}")
            else:
                # Genuine logical rejections arrive as ResponseStatus.Errors; anything with a
                # 4xx/5xx status already raised in _post before reaching here.
                status = result.get("ResponseStatus") or {}
                errors = "; ".join(
                    f"{e.get('FieldName')}: {e.get('Message')}" for e in status.get("Errors") or []
                ) or status.get("Message")
                logger.error(f"C2 Order Failed: {errors or f'no SignalId in response: {result}'} (Payload: {payload})")
        except Exception as e:
            logger.error(f"Exception submitting C2 order: {e}")
            raise

    def is_tradeable(self, yf_symbol: str) -> bool:
        """C2 trades US stocks/ETFs only here.

        Judged on the *mapped* symbol so an explicit override (^XAU -> GDX)
        still counts. Any remaining exchange suffix (RENW.DE, IWDA.AS,
        BTEC.L), index caret, or a crypto pair (BTC-USD, mapped to type
        "crypto" — C2 lists only stock/forex/futures) is a venue C2 rejects, and
        the default mapper passes those through, so nothing else would catch them.
        """
        meta = self.map_symbol(yf_symbol)
        if not meta or not meta.get("symbol") or meta.get("type") == "crypto":
            return False
        mapped = meta["symbol"]
        return not any(ch in mapped for ch in ".-^")

    def map_symbol(self, yf_symbol: str) -> dict | None:
        return self.symbol_mapper.map_symbol(yf_symbol, broker_name=self.name)

    def search_symbol(self, query: str) -> list[dict]:
        """
        Search for matching symbols on C2.

        Two v4 behaviours this has to work around, both verified against the live
        endpoint on 2026-08-25 (188 rows: 120 future, 42 forex, 26 stock):

        1. `SearchText` is IGNORED — the endpoint returns the entire supported
           universe for any query, so the filtering has to happen here.
        2. Rows carry no top-level `Symbol`, `SymbolType` or `Exchange`. The ticker
           is `C2Symbol.Underlying` (note: `FullSymbol`, which the positions and
           order endpoints use, is absent here) with `ExchangeSymbol.Symbol` as a
           fallback. Reading the flat keys returned `symbol: None` for all 188 rows
           and typed every one of them "stock", futures and forex included.

        Do not treat an empty result as "C2 cannot trade this". The endpoint is not
        a symbol catalogue: its 26 stocks are irregularly-formatted tickers
        (BF.A, BRK.B, RDS.B, C-N) that need special notation, alongside every
        future and forex pair. Ordinary US equities are absent — QQQ and TQQQ both
        return nothing here while the strategy holds and trades them fine. So
        discover_symbols.py cannot map equities through C2 search; they come from
        the static symbol map instead.
        """
        logger.info(f"Searching C2 for '{query}'")
        try:
            # v4 search endpoint: /Strategies/GetSupportedSymbols
            data = self._get("Strategies/GetSupportedSymbols", {"SearchText": query})
            needle = query.strip().upper()
            candidates = []
            # v4 returns { "Results": [ ... ] }
            for item in data.get("Results", []):
                c2 = item.get("C2Symbol") or {}
                exch = item.get("ExchangeSymbol") or {}
                symbol = c2.get("Underlying") or exch.get("Symbol")
                if not symbol:
                    continue
                haystack = f"{symbol} {item.get('Description') or c2.get('Description') or ''}".upper()
                if needle and needle not in haystack:
                    continue
                candidates.append(
                    {
                        "symbol": symbol,
                        "description": item.get("Description") or c2.get("Description"),
                        "type": c2.get("SymbolType") or "stock",
                        "exchange": exch.get("SecurityExchange"),
                        # Exact ticker beats a description substring, so callers
                        # picking max(score) get QQQ rather than the first row that
                        # merely mentions it.
                        "score": 100 if symbol.upper() == needle else 50,
                    }
                )
            return sorted(candidates, key=lambda c: -c["score"])
        except Exception as e:
            logger.error(f"C2 symbol search failed: {e}")
            return []

    # print_account_summary() itself lives on LiveBroker. Identity line, the
    # IsAlive flag ahead of cash/equity, and the positions table (C2 exposes
    # SymbolType and OpenPnL that get_positions()'s plain symbol->qty dict
    # discards) all differ here.
    def _summary_header(self) -> str:
        return f"Collective2 Strategy {self.system_id}"

    def _account_lines(self) -> list[str]:
        obj = self._strategy_details()
        return [
            self._summary_line("IsAlive:", obj.get("IsAlive", "?"), fmt=""),
            self._summary_line("Cash:", self.get_cash()),
            self._summary_line("Equity:", self.get_total_equity()),
        ]

    def _position_rows(self) -> tuple[str, list[str]]:
        # Same nesting trap as get_positions: there is no top-level Symbol,
        # SymbolType or OpenPrice on a v4 position. This printed a blank ticker,
        # a blank type and 0.0000 for the price on every row. The average price
        # field is AvgPx. OpenPnL genuinely is not returned, so it is dropped
        # rather than displayed as a hardcoded 0.00.
        positions_data = self._get("Strategies/GetStrategyOpenPositions", {"StrategyIds": [int(self.system_id)]})
        results = positions_data.get("Results", [])
        header = f"  {'Symbol':<14} {'Type':<8} {'Qty':>12} {'AvgPrice':>12}"
        rows = []
        for p in results:
            c2 = p.get("C2Symbol") or {}
            symbol = c2.get("FullSymbol") or (p.get("ExchangeSymbol") or {}).get("Symbol") or ""
            rows.append(
                f"  {symbol!s:<14} {c2.get('SymbolType', '')!s:<8} "
                f"{float(p.get('Quantity', 0) or 0):>12.4f} "
                f"{float(p.get('AvgPx', 0) or 0):>12.4f}"
            )
        return header, rows


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    api_key = os.getenv("COLLECTIVE2_API_KEY")
    system_id = os.getenv("COLLECTIVE2_SYSTEM_ID")
    if not api_key or not system_id:
        raise SystemExit("COLLECTIVE2_API_KEY and COLLECTIVE2_SYSTEM_ID must be set in .env")

    broker = Collective2Broker(api_key=api_key, system_id=system_id)
    broker.print_account_summary()
