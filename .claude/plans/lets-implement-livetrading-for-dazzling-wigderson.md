# Darwinex DXtrade Live Trading Integration

## Context

The repo already supports three live brokers (Collective2, Interactive Brokers, eToro) via a shared `LiveBroker` abstraction in [tradingbot/livetrade/broker.py](tradingbot/livetrade/broker.py). The user just provisioned a Darwinex DXtrade CFD demo account (`dxtrade.darwinex.com`, master password auth) and wants a fourth broker added that mirrors the eToro/C2 wiring so existing bot weights can be copied to Darwinex via the same `LiveTradeCopier`.

User decisions (locked in via clarifying Qs):
- **API flavor**: official `/dxsca-web` REST + WebSocket (session-token auth via username + password)
- **Symbol mapping**: catalog search first, fall back to a manual `symbol_mappings.json` entry — Darwinex is CFD-only so equity tickers like `QQQ` may need `QQQ.US`-style codes
- **Default `dry_run`**: `false` (live from first successful run, like Collective2)
- **Credentials**: only DXtrade username + master password — no OAuth consumer key

Out of scope: DARWIN Info API (separate product), FIX API, MT4/MT5 paths.

## DXtrade `/dxsca-web` API surface (confirmed via Devexperts SDK + community refs)

- **Base URL**: `https://dxtrade.darwinex.com/dxsca-web`
- **Login**: `POST /login` with `{"username", "password", "domain": "default"}` → returns session token in JSON; subsequent requests pass `Authorization: DXAPI <token>` header. Token must be refreshed on 401.
- **Account/portfolio**: `GET /accounts/{accountId}/metrics` (cash, equity, margin) and `GET /accounts/{accountId}/portfolio` (positions list with `instrumentCode`, `quantity`, `openPrice`).
- **Orders**: `POST /accounts/{accountId}/orders` with `{instrument, quantity, side: "BUY"|"SELL", type: "MARKET", legs: [...]}`. Close-by-position handled by submitting an opposing market order against `instrument` for the position quantity (DXtrade nets automatically).
- **Cancel**: `DELETE /accounts/{accountId}/orders/{orderId}` (only relevant for non-market orders; we submit MARKET so cancellation is rarely needed).
- **Instruments**: `GET /instruments?symbol=<query>` returns matching instrument descriptors (`symbol`, `description`, `type`, `currency`, etc.). Used for catalog search in `map_symbol`.
- **Live quotes**: native quotes are WebSocket-only (`wss://dxtrade.darwinex.com/dxsca-web/md?format=JSON`). For v1 we **do not** open the WebSocket — `_get_native_price()` returns `0.0` and the base class falls back to yfinance, identical to how Collective2 behaves today. Worth revisiting once equities are confirmed CFD-mapped.

> All endpoint paths are best-effort from public docs / community SDKs and must be verified against the live `/dxsca-web/specs` Swagger on first run. The plan deliberately keeps each endpoint isolated in a `_get`/`_post` helper so any path correction is a one-line fix.

## Files to create

### 1. `tradingbot/livetrade/darwinex.py` — broker class
Mirror [tradingbot/livetrade/etoro.py](tradingbot/livetrade/etoro.py) structure. Class `DarwinexBroker(LiveBroker)`:
- `__init__(username, password, account_id=None, demo=True, symbol_mapper=None, data_service=None)` — store creds, build `httpx.Client(base_url=...)`, set `self.name = "darwinex"`.
- `_login()` — POST `/login`, store `self._session_token` and `self._token_expires_at`.
- `_ensure_session()` — called at the top of every `_get/_post`; re-login on 401 or near-expiry.
- `_get(path, params)` / `_post(path, json_data)` / `_delete(path)` — auth header injection + `raise_for_status()`.
- `_resolve_account_id()` — if `account_id` not supplied, GET `/users/{username}/accounts` and pick the first (mirrors how IB picks the configured account).
- `get_cash()` → `/accounts/{id}/metrics` → `balance`.
- `get_total_equity()` → `/accounts/{id}/metrics` → `equity`.
- `get_positions()` → `/accounts/{id}/portfolio` → dict of `instrumentCode → signed quantity`. Cache `instrumentCode → positionId` if needed for closes.
- `_get_native_price()` → return `0.0` (yfinance fallback). Leave a `# TODO: WebSocket md subscription for live quotes` line since this is a known v1 limitation, not dead code.
- `place_order(broker_symbol, quantity, side, symbol_type)` → POST `/accounts/{id}/orders` with `{instrument: broker_symbol, quantity, side, type: "MARKET"}`. Respect `dry_run` upstream (copier handles it; broker doesn't need to know).
- `map_symbol(yf_symbol)` — **two-step** per the user's choice:
  1. Check `self.symbol_mapper.map_symbol(yf_symbol, "darwinex")` — if a manual mapping exists in `symbol_mappings.json`, return it.
  2. Otherwise `GET /instruments?symbol={yf_symbol}` and `{yf_symbol}.US` (covers the common equity-CFD naming). Return the first exact-match `{symbol, description, type}` dict; `None` if no match.
  - Cache results in `self._instrument_cache` to avoid hammering the search endpoint each sync.
- `search_symbol(query)` → thin wrapper over `GET /instruments?symbol={query}`.
- `cancel_open_orders()` → list `/accounts/{id}/orders?status=PENDING`, DELETE each, return count. Matches eToro behavior.
- `print_account_summary()` → log cash/equity/positions (copy from C2 verbatim, swap field names).

### 2. `tradingbot/livetrade_darwinex.py` — runner/CLI entry
Copy [tradingbot/livetrade_etoro.py](tradingbot/livetrade_etoro.py) and swap:
- Env vars: `DARWINEX_USERNAME`, `DARWINEX_PASSWORD`, `DARWINEX_ACCOUNT_ID` (optional), `DARWINEX_DEMO` (default `"true"`).
- Default for `LIVETRADE_DRY_RUN`: `"false"` (per user decision — matches C2 runner).
- Keep the existing DB validation block from the eToro runner unchanged (validates `LIVETRADE_BOT_WEIGHTS` against the Bot table).
- Instantiate `DarwinexBroker(...)`, then `LiveTradeCopier(...).sync()`.

### 3. `tests/test_livetrade_darwinex.py`
Mirror [tests/test_livetrade_etoro.py](tests/test_livetrade_etoro.py). Mock `httpx.Client` at the broker; cover:
- `_login` stores token from response
- `get_cash` / `get_total_equity` parse the metrics response
- `get_positions` aggregates portfolio response into `{symbol: qty}`
- `map_symbol` (a) hits the manual map first, (b) falls back to instrument search, (c) returns None on no match
- `place_order` posts the right JSON body for BUY and SELL

### 4. `helm/tradingbots/templates/cronjob-livetrade-darwinex.yaml`
Copy `cronjob-livetrade-etoro.yaml`, swap broker name, env-var names, and gate by `.Values.liveTrade.darwinex.enabled`.

## Files to modify

- **`helm/tradingbots/values.yaml`** — add a `liveTrade.darwinex` block under the existing `liveTrade.etoro` block (`enabled: false`, `schedule`, `demo: true`, optional `portfolioFraction`, `accountId` optional).
- **`README.md`** — add Darwinex to the supported-brokers list, the env-var quick-start block, and the copier/Helm flag table (alongside the existing eToro additions).
- **`docs/guides/live-trading.md`** — add a Darwinex section parallel to the eToro one; extend the configuration reference table to include `DARWINEX_*` rows; update the alpha-notice line to include Darwinex.
- **`AGENTS.md`** — add a Darwinex API quirks subsection under the existing eToro #10 quirks block (session-token auth, CFD-only catalog, no native price endpoint, two-step symbol resolution).
- **`symbol_mappings.json`** — add a small starter map for the most common bot-universe tickers (e.g. `QQQ → QQQ.US`, `SPY → SPY.US`) so the very first sync isn't entirely catalog-search dependent. Concrete codes to be confirmed against `/instruments` on first connect.

## Verification (end-to-end)

1. **Unit tests**: `pytest tests/test_livetrade_darwinex.py -v` — all green with mocked httpx.
2. **Live demo dry-run sanity**: temporarily export `LIVETRADE_DRY_RUN=true` and run `python -m tradingbot.livetrade_darwinex` against the demo. Expect: login OK → metrics fetched → equity > 0 → positions empty → bot weights resolved → mapping log shows which yf tickers map to which DXtrade instruments → orders printed but not sent.
3. **Live demo wet run**: with `LIVETRADE_DRY_RUN=false`, confirm a single small BUY (e.g. `EUR/USD` with min order size) lands in the DXtrade UI.
4. **Mapping coverage spot-check**: run a one-off helper invoking `broker.map_symbol("QQQ")`, `("AAPL")`, `("BTC-USD")`, `("EURUSD=X")` and log results — surfaces which of your active bot tickers are catalog-resolvable vs need manual `symbol_mappings.json` entries.
5. **Lint/type**: whatever the repo runs in CI for the eToro module (no special needs here).

## Known v1 gaps (worth flagging in commit/PR)

- No WebSocket live quotes — equity calculation uses yfinance for position notional, fine for equities/crypto but inaccurate for FX/CFD-only instruments off-hours.
- DXtrade nets positions automatically; the copier's SELL-then-BUY ordering still works but `get_positions()` returns the *net* position, not per-trade lots. eToro had the inverse problem (per-position lots). Should be a non-issue but worth noting.
- Endpoint paths are best-effort from community SDKs; first live run will likely surface a 404 on one or two of them, easily corrected via `/dxsca-web/specs`.
