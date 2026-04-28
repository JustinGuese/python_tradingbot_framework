# Live Trading Guide

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. Trading involves significant risk of loss and is not suitable for all investors. Use of "Live Trading" features is strictly at your own risk. The authors and contributors are not liable for any financial losses, damages, or unintended trades incurred. Always test strategies thoroughly in a paper-trading environment before deploying real capital.

The Trading Bot Framework can mirror your paper-trade portfolios (stored in PostgreSQL) to a live brokerage account. This is handled by a separate **Live Trade Copier** layer that runs independently of your bots.

> [!CAUTION]
> **ALPHA STATUS**: The live-trade copier is currently in Alpha. While endpoints have been validated against broker APIs (C2 v4), behavior has not yet been confirmed against a live capital account. Use extreme caution and start with very low weights.

---

## 🚀 Quick Start (5-Minute Dry Run)

You can verify the copier logic immediately without any complex setup.

1.  **Set Environment Variables**:
    ```bash
    export COLLECTIVE2_API_KEY="your_api_v4_key"
    export COLLECTIVE2_SYSTEM_ID="12345678"
    export LIVETRADE_BOT_WEIGHTS='{"adaptivemeanreversionbot": 1.0}'
    export LIVETRADE_DRY_RUN=true
    ```
2.  **Run the Copier**:
    ```bash
    uv run python tradingbot/livetrade_collective2.py
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

# Interactive Brokers — reads IB_GATEWAY_HOST/PORT + IB_ACCOUNT_ID
# Connects read-only with IB_CLIENT_ID=19 by default so it won't collide
# with the cron client (17) or the vscode debug config (18).
uv run python tradingbot/livetrade/interactive_brokers.py
```

Programmatically, both brokers expose `print_account_summary()` on the broker
class, so you can call it from any script after constructing the broker.

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
| `LIVETRADE_BOT_WEIGHTS` | JSON: `{"botname": 0.6, "otherbot": 0.4}`. Weights are normalized to 1.0. |
| `LIVETRADE_MIN_ORDER_USD` | Skip trades smaller than this amount (default: $50). |
| `LIVETRADE_DRY_RUN` | `true`: Logs orders without sending them. **Always start here.** |
| `LIVETRADE_STRICT_MAPPING` | `true`: **Aborts the sync** if any target ticker is unmapped, instead of silently skipping it. |
| `LIVETRADE_PORTFOLIO_FRACTION` | Fraction of broker equity to allocate to copy-trading. Default `1.0` (use the full account); `0.5` would mirror the bot portfolios into half the account and leave the rest as cash. Range: `(0, 1]`. |

### Enabling the Copiers in Helm

Each broker is its own CronJob, gated by a separate flag in [helm/tradingbots/values.yaml](../../helm/tradingbots/values.yaml). Both default to `false` — opt in independently:

```yaml
liveTrade:
  enabled: true     # Collective2 copier
  # ...
liveTradeIB:
  enabled: true     # Interactive Brokers copier
  # ...
```

You can run only Collective2, only IBKR, both, or neither.

---

## 🏦 Interactive Brokers (IBKR)

The framework supports Interactive Brokers via **IB Gateway** (or TWS).

### 1. Requirements
- **IB Gateway** running and configured for API access.
- **Paper Account** strongly recommended for initial testing.
- **Paper Port**: Usually `4002` (standard) or `4004` (often used in local setups).

### 2. Configuration
Set these environment variables:

| Variable | Description | Default |
| --- | --- | --- |
| `IB_GATEWAY_HOST` | Hostname of IB Gateway. | `127.0.0.1` |
| `IB_GATEWAY_PORT` | API Port. | `4004` |
| `IB_CLIENT_ID` | Unique ID for this connection. | `17` |
| `IB_ACCOUNT_ID` | Your IB account ID (e.g., `DU1234567`). | **Required** |
| `LIVETRADE_DRY_RUN` | Paper-safety: defaults to `true`. | `true` |

> [!IMPORTANT]
> **clientId and Order Isolation**: The copier is designed to be idempotent. Before each sync, it calls `cancel_open_orders()` to clear any stale orders submitted by previous runs that haven't filled yet. To ensure this **does not cancel your manual orders**, the copier only targets orders with a matching `IB_CLIENT_ID`. 
> - **Always use a unique `IB_CLIENT_ID` (default: 17)** for the copier.
> - **Avoid using "Master Client ID" (0)** for the copier, as it can see and cancel orders from all other clients.

### 3. Usage
```bash
uv run python tradingbot/livetrade_interactive_brokers.py
```

### 4. Ticker Discovery for IB
```bash
uv run python -m tradingbot.livetrade.discover_symbols --broker ib
```

---

## 🔍 Ticker Mapping (Discovery)

Broker symbols (e.g., `EURUSD`) rarely match yfinance tickers (`EURUSD=X`) exactly.

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
