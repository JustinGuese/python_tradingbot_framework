# Live Trading Guide

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. Trading involves significant risk of loss and is not suitable for all investors. Use of "Live Trading" features is strictly at your own risk. The authors and contributors are not liable for any financial losses, damages, or unintended trades incurred. Always test strategies thoroughly in a paper-trading environment before deploying real capital.

The Trading Bot Framework can mirror your paper-trade portfolios (stored in PostgreSQL) to a live brokerage account. This is handled by a separate **Live Trade Copier** layer that runs independently of your bots.

> [!CAUTION]
> **ALPHA STATUS**: The live-trade copier is currently in Alpha. While endpoints have been validated against broker APIs (C2 v4, IB, eToro, Darwinex), behavior has not yet been confirmed against a live capital account. Use extreme caution and start with very low weights.

---

## 🚀 Quick Start (5-Minute Dry Run)

You can verify the copier logic immediately without any complex setup.

1.  **Set Environment Variables**:
    ```bash
    export DARWINEX_USERNAME="your_username"
    export DARWINEX_PASSWORD="your_password"
    export LIVETRADE_BOT_WEIGHTS='{"adaptivemeanreversionbot": 1.0}'
    export LIVETRADE_DRY_RUN=true
    ```
2.  **Run the Copier**:
    ```bash
    uv run python tradingbot/livetrade_darwinex.py
    ```
3.  **Review the Log**: Look for `[DRY RUN] Would BUY/SELL ...` lines to see what the copier would have done.

---

## 🔎 Inspect Account & Positions

Each broker module is runnable as a script and prints a summary of the configured
account — equity, cash, and current open positions. Use it to sanity-check
credentials and account IDs before running the copier:

```bash
# Collective2 — reads COLLECTIVE2_API_KEY + COLLECTIVE2_SYSTEM_ID
uv run python tradingbot/livetrade/collective2.py

# Interactive Brokers — IBKR Web API via headless OAuth 1.0a.
# Reads IB_ACCOUNT_ID + IBIND_OAUTH1A_* credentials; prints an account summary.
uv run python -m tradingbot.livetrade.interactive_brokers

# eToro — reads ETORO_API_KEY + ETORO_USER_KEY + ETORO_DEMO
uv run python tradingbot/livetrade/etoro.py

# Darwinex — reads DARWINEX_USERNAME + DARWINEX_PASSWORD + DARWINEX_DEMO
uv run python tradingbot/livetrade/darwinex.py
```

All brokers expose `print_account_summary()` on the broker class, so you can
call it from any script after constructing the broker.

---

## 📊 How Orders are Calculated

The copier does not just "blindly" copy signals; it synchronizes **target state**:

1.  **Per-Bot Normalization**: For each bot in `LIVETRADE_BOT_WEIGHTS`, the current paper portfolio is converted to percentage weights (e.g., AAPL is 10% of Bot A).
2.  **Weighted Aggregation**: Individual bot weights are aggregated based on your allocation (e.g., if Bot A is 60% of your capital, AAPL becomes 6% of your live total).
3.  **Broker Equity Sync**: The copier fetches your **Live Total Equity** (Cash + Open Positions) from the broker.
4.  **Target Value**: (Total Equity) × (Aggregate Target Weight) = **Target USD Value** for each symbol.
5.  **The Diff**: The copier compares the Target Value vs. your **Current Live Position** value at the broker.
6.  **Order Generation**: It generates the BUY or SELL orders needed to close the gap.
7.  **Safety Filters**: Orders smaller than `LIVETRADE_MIN_ORDER_USD` are skipped to avoid excessive fees.

**Price Fallback**: If the broker cannot provide a real-time quote for a symbol, the framework falls back to `yfinance` to ensure calculations remain accurate.

### Multi-Bot Weighting Example

If you have $100,000 in your live account and configure:
`LIVETRADE_BOT_WEIGHTS='{"adaptivemeanreversionbot": 0.6, "feargreedbot": 0.4}'`

1.  **Bot A (Adaptive)**: Paper portfolio is 100% QQQ.
2.  **Bot B (FearGreed)**: Paper portfolio is 50% QQQ, 50% CASH.
3.  **Aggregation**:
    *   QQQ Target = (1.0 × 0.6) + (0.5 × 0.4) = **0.8 (80%)**
    *   CASH Target = (0.0 × 0.6) + (0.5 × 0.4) = **0.2 (20%)**
4.  **Final Target**: The copier will try to make your live account hold **$80,000 of QQQ**.

---

## 🛠 Configuration Reference

| Variable | Description |
| --- | --- |
| `COLLECTIVE2_API_KEY` | Your C2 API v4 key. Get it from the [C2 API Dashboard](https://collective2.com/account-management/apiv4/dashboard/0). |
| `COLLECTIVE2_SYSTEM_ID` | The ID of your C2 strategy. |
| `IB_ACCOUNT_ID` | Your Interactive Brokers account ID (e.g., `DU1234567` for paper, `U1234567` for live). |
| `IBIND_USE_OAUTH` | Enable headless OAuth 1.0a for the IBKR Web API. Set to `True`. |
| `IBIND_OAUTH1A_CONSUMER_KEY` | 9-char consumer key registered in the IBKR self-service OAuth portal. |
| `IBIND_OAUTH1A_ACCESS_TOKEN` | OAuth access token generated in the IBKR portal. |
| `IBIND_OAUTH1A_ACCESS_TOKEN_SECRET` | OAuth access token secret generated in the IBKR portal. |
| `IBIND_OAUTH1A_DH_PRIME` | Diffie-Hellman prime (hex string from `dhparam.pem`). |
| `IBIND_OAUTH1A_ENCRYPTION_KEY_FP` | Path to the private encryption key file (`private_encryption.pem`). |
| `IBIND_OAUTH1A_SIGNATURE_KEY_FP` | Path to the private signature key file (`private_signature.pem`). |
| `ETORO_API_KEY` | Your eToro Public API Key from the [eToro API Portal](https://api-portal.etoro.com/). |
| `ETORO_USER_KEY` | Your eToro User Key (generated in API Portal settings). |
| `ETORO_DEMO` | `true` for demo/paper account, `false` for live account (default: `true`). |
| `DARWINEX_USERNAME` | Your Darwinex DXtrade username. |
| `DARWINEX_PASSWORD` | Your Darwinex DXtrade master password. |
| `DARWINEX_ACCOUNT_ID` | Optional: Specific Darwinex account ID to use. |
| `DARWINEX_DEMO` | `true` for demo/paper account, `false` for live account (default: `true`). |
| `LIVETRADE_BOT_WEIGHTS` | JSON: `{"botname": 0.6, "otherbot": 0.4}`. Weights are normalized to 1.0. |
| `LIVETRADE_MIN_ORDER_USD` | Skip trades smaller than this amount (default: $50). |
| `LIVETRADE_DRY_RUN` | `true`: Logs orders without sending them. **Always start here.** |
| `LIVETRADE_STRICT_MAPPING` | `true`: **Aborts the sync** if any target ticker is unmapped, instead of silently skipping it. |
| `LIVETRADE_PORTFOLIO_FRACTION` | Fraction of broker equity to allocate to copy-trading. Default `1.0` (use the full account); `0.5` would mirror the bot portfolios into half the account and leave the rest as cash. Range: `(0, 1]`. |

### Enabling the Copiers in Helm

Each broker is its own CronJob, gated by a per-broker `enabled` flag nested under
`liveTrade` in [helm/tradingbots/values.yaml](../../helm/tradingbots/values.yaml).
Settings directly under `liveTrade` are shared by every broker; each broker block
carries only its own connection details and schedule:

```yaml
liveTrade:
  # shared across all brokers
  botWeights: '{"SomeBot": 0.5, "OtherBot": 0.5}'
  dryRun: "false"
  strictMapping: "true"
  portfolioFraction: "1.0"

  collective2:
    enabled: true
    schedule: "10 21 * * 1-5"
    systemId: "155809898"
  interactiveBrokers:
    enabled: true
    # ...
  etoro:
    enabled: false
  darwinex:
    enabled: false
```

You can run any combination of brokers (Collective2, IBKR, eToro, Darwinex), or none.

### Running Multiple Collective2 Strategies

A C2 API key is **account-scoped** and every request addresses a strategy by its
numeric `StrategyId`, so one `COLLECTIVE2_API_KEY` can drive several strategies.
To publish a different set of bots as a separate, independently-tracked C2
strategy, add another CronJob rather than another credential.

The `XAUZerifine` strategy is the worked example — see
[cronjob-livetrade-collective2-xauzerifine.yaml](../../helm/tradingbots/templates/cronjob-livetrade-collective2-xauzerifine.yaml).
It is a copy of the base Collective2 template that overrides only the strategy id
and the per-instance settings:

```yaml
liveTrade:
  collective2XauZerifine:
    enabled: false                            # flip on once systemId is filled in
    schedule: "50 21 * * 1-5"
    systemId: ""                              # the XAUZerifine StrategyId
    botWeights: '{"XAUZenbotTreeBot": 1.0}'   # overrides the shared botWeights
    dryRun: "true"                            # overrides the shared dryRun
    # apiKeySecretKey: "COLLECTIVE2_API_KEY_XAUZERIFINE"  # only for a different C2 account
```

`botWeights`, `dryRun` and `portfolioFraction` fall back to the shared
`liveTrade.*` values when omitted. The template refuses to render if `systemId`
is empty, or if it duplicates `liveTrade.collective2.systemId` **while both
instances are enabled** — two copiers pointed at one strategy would each
liquidate the other's positions, since `sync()` is a full target-state
reconciliation.

Sharing a `systemId` is legitimate when you are **handing a strategy over**:
disable the old instance in the same commit that enables the new one, so only one
copier ever runs against it. Be aware that the C2 strategy keeps its existing
track record and equity curve — the first run of the new instance liquidates
whatever the old bots held and rebuilds the portfolio from the new ones. If you
want a clean track record, create a fresh C2 strategy instead.

Each strategy needs its bots' tickers to be tradeable on C2. `XAUZenbotTreeBot`
trades `^XAU` (the PHLX Gold/Silver **index**), which is mapped to the `GDX`
miners ETF in [symbol_map.json](../../tradingbot/livetrade/symbol_map.json) —
without that override it could not trade at all. See the warning about index
tickers under [Ticker Mapping (Discovery)](#-ticker-mapping-discovery).

---

## 🏦 Interactive Brokers (IBKR)

The framework connects to Interactive Brokers through the **IBKR Web API** (Client Portal REST) using the [`ibind`](https://github.com/Voyz/ibind) library with **fully headless OAuth 1.0a** authentication. There is **no IB Gateway / TWS container** and no daily browser login — the live-session token is obtained programmatically and self-renews on a 24h cycle.

### 1. One-time OAuth setup (in the IBKR self-service portal)
1. Generate keys locally with OpenSSL: a private encryption key (`private_encryption.pem`), a private signature key (`private_signature.pem`), and DH params (`dhparam.pem`).
2. In the IBKR **self-service OAuth portal**: register a 9-char consumer key, upload the public encryption + signature keys and the DH params, then generate an **access token** and **access token secret**.
3. Extract the DH prime as a hex string from `dhparam.pem`.

> IBKR approval/activation of OAuth access can take from a day up to a couple of weeks. A **Paper Account** is strongly recommended for initial testing.

### 2. Configuration
Set these environment variables (see the reference table above for full descriptions):

| Variable | Description | Default |
| --- | --- | --- |
| `IB_ACCOUNT_ID` | Your IB account ID (e.g., `DU1234567`). | **Required** |
| `IBIND_USE_OAUTH` | Enable headless OAuth 1.0a. | `True` |
| `IBIND_OAUTH1A_CONSUMER_KEY` | Consumer key from the portal. | **Required** |
| `IBIND_OAUTH1A_ACCESS_TOKEN` | Access token from the portal. | **Required** |
| `IBIND_OAUTH1A_ACCESS_TOKEN_SECRET` | Access token secret from the portal. | **Required** |
| `IBIND_OAUTH1A_DH_PRIME` | DH prime (hex). | **Required** |
| `IBIND_OAUTH1A_ENCRYPTION_KEY_FP` | Path to `private_encryption.pem`. | **Required** |
| `IBIND_OAUTH1A_SIGNATURE_KEY_FP` | Path to `private_signature.pem`. | **Required** |
| `LIVETRADE_DRY_RUN` | Paper-safety: defaults to `true`. | `true` |

> [!IMPORTANT]
> **Order Isolation**: The copier is idempotent. Before each sync it calls `cancel_open_orders()` to clear stale working orders from previous runs. Over the Web API this cancels the **account's** live orders — run the copier against a dedicated account (or accept that it manages that account's open orders) so it does not clear unrelated manual orders.

> [!NOTE]
> **Scope**: order routing currently supports **US equities/ETFs** (conid resolved via `stock_conid_by_symbol`, which the live bots use). Forex/crypto/futures over the Web API is not yet implemented and raises `NotImplementedError`.

### 3. Usage
```bash
uv run python tradingbot/livetrade_interactive_brokers.py
```

### 4. Ticker Discovery for IB
```bash
uv run python -m tradingbot.livetrade.discover_symbols --broker ib
```

---

## 🐂 eToro

The framework supports eToro via their Public REST API.

### 1. Requirements
- **eToro Public API Key & User Key**. Obtain them from the [eToro API Portal](https://api-portal.etoro.com/).
- **Paper Account** (Virtual Portfolio) is strongly recommended. Set `ETORO_DEMO=true`.

### 2. Configuration
Set these environment variables:

| Variable | Description | Default |
| --- | --- | --- |
| `ETORO_API_KEY` | Your eToro API Key. | **Required** |
| `ETORO_USER_KEY` | Your eToro User Key. | **Required** |
| `ETORO_DEMO` | `true` for paper trading, `false` for live. | `true` |
| `LIVETRADE_DRY_RUN` | Safety: defaults to `true`. | `true` |

### 3. Order Model (USD Amount)
eToro orders are placed using **USD Amount** (notional) for BUY orders, which allows for fractional shares automatically. For SELL orders, the framework closes existing positions by their `positionId`.

### 4. Ticker Mapping
eToro uses numeric **Instrument IDs**. The framework automatically resolves these via the eToro search API during the mapping phase.

### 5. Usage
```bash
uv run python tradingbot/livetrade_etoro.py
```

---

## 📈 Darwinex (DXtrade)

The framework supports Darwinex via the **DXtrade** REST API (`/dxsca-web`).

### 1. Requirements
- **Darwinex DXtrade Account** (Username + Master Password).
- **Demo Account** strongly recommended for initial testing.

### 2. Configuration
Set these environment variables:

| Variable | Description | Default |
| --- | --- | --- |
| `DARWINEX_USERNAME` | Your DXtrade username. | **Required** |
| `DARWINEX_PASSWORD` | Your DXtrade master password. | **Required** |
| `DARWINEX_DEMO` | `true` for demo/paper trading, `false` for live. | `true` |
| `DARWINEX_ACCOUNT_ID` | Optional: defaults to the first account found. | `None` |

### 3. Symbol Mapping (CFDs)
Darwinex is a CFD broker. Common equity tickers like `QQQ` are often mapped to `QQQ.US`. The framework uses a two-step resolution:
1.  Check `symbol_mappings.json` for manual overrides.
2.  Search the Darwinex catalog for the ticker (and ticker + `.US`).

### 4. Native Quotes
DXtrade REST API does not provide a simple "last price" endpoint (it is WebSocket only). For v1, the framework falls back to **yfinance** for real-time price lookups to calculate equity and order sizing.

### 5. Usage
```bash
uv run python tradingbot/livetrade_darwinex.py
```

---

## 🔍 Ticker Mapping (Discovery)

Broker symbols (e.g., `EURUSD`) rarely match yfinance tickers (`EURUSD=X`) exactly.

> ⚠️ **Index tickers need an explicit override.** A bot whose ticker is an index
> (`^XAU`, `^VIX`, …) has nothing tradeable to buy. `SymbolMapper` only translates
> `^GSPC`/`^NDX`/`^IXIC` by default; any other `^` ticker is returned **unchanged**,
> so it is not `None` and does not look "unmapped". The copier therefore rejects any
> mapped symbol that still starts with `^`, and `LIVETRADE_STRICT_MAPPING=true` will
> abort the sync — which is what you want, since the alternative is silently
> submitting orders the broker rejects on every run. Fix it by mapping the index to a
> tradeable proxy ETF in `symbol_map.json`, as `^XAU` → `GDX` does for Collective2.

### 1. Run Discovery
Find unmapped tickers in your bot portfolios:
```bash
uv run python -m tradingbot.livetrade.discover_symbols
```

### 2. Review and Approve
Open `symbol_map.review.json`. For each ticker, **manually add** the `selected_symbol` and `selected_type` keys:

```json
"BTC-USD": {
  "candidates": [...],
  "selected_symbol": "BTCUSD",  // YOU ADD THIS
  "selected_type": "crypto"     // YOU ADD THIS
}
```

### 3. Apply
```bash
uv run python -m tradingbot.livetrade.discover_symbols --apply
```

---

## 🛡 Going Live Checklist

1.  **Dry Run**: Run for at least 3-5 days with `LIVETRADE_DRY_RUN=true`.
2.  **Verify Equity**: Confirm the "Broker total equity" logged matches your broker dashboard.
3.  **Strict Mode**: Set `LIVETRADE_STRICT_MAPPING=true`.
4.  **Start Small**: Use one bot with a low weight (e.g., `0.1`) first.
5.  **Manual Check**: After the first live sync, verify the orders appear in your broker's "Open Orders" or "Positions" tab.

---

## 🏗 Deployment (Kubernetes)

Deploy the copier as a `CronJob` that runs after your trading bots finish.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: livetrade-copier
spec:
  schedule: "5 21 * * 1-5" # 9:05 PM UTC (Post-market)
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: copier
            image: your-repo/tradingbot:latest
            command: ["python", "tradingbot/livetrade_collective2.py"]
            envFrom:
            - secretRef:
                name: tradingbot-secrets
```

---

## 🔌 Adding a New Broker

To support a new broker, implement the `LiveBroker` interface in `tradingbot/livetrade/broker.py`:

1.  `get_positions()`: Return `Dict[symbol, qty]`.
2.  `get_total_equity()`: Return float (Cash + Market Value).
3.  `place_order(symbol, qty, side, type)`: Execute the trade.
4.  `get_latest_price(symbol)`: Return current price for a broker symbol.
5.  `map_symbol(yf_symbol)`: Convert yf ticker to broker ticker.
6.  `search_symbol(query)`: Helper for ticker discovery.

Reference `tradingbot/livetrade/collective2.py` for a complete example.

---

## ❓ Troubleshooting

| Issue | Root Cause | Solution |
| --- | --- | --- |
| **401/403 Error** | Invalid API Key | Ensure you are using a **v4** key from the C2 dashboard. |
| **Empty "Results"** | Wrong System ID | Verify `COLLECTIVE2_SYSTEM_ID` matches your strategy URL. |
| **"Order Failed" Log** | Broker Rejection | Check the log line for `ErrorCode`; usually due to buying power or invalid symbol. |
| **All targets unmapped** | Typo or Empty Bot | Verify bot names in `LIVETRADE_BOT_WEIGHTS` match the DB exactly. |
| **Quantity 0 warning** | Min Order Filter | Increase a specific bot's weight or decrease `LIVETRADE_MIN_ORDER_USD`. |

---

## 🚫 When NOT to Use

- Before you have at least 1 month of paper trading history.
- With a `System ID` that you do not own/manage.
- If you require sub-second execution (this is a scheduled copier, not a HFT engine).
