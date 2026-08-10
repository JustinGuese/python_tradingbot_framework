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
# Needs tradingbot/ on PYTHONPATH: the livetrade modules use the container-style
# `from livetrade.X import ...` imports, so `-m tradingbot.livetrade...` fails.
PYTHONPATH=tradingbot uv run python tradingbot/livetrade/interactive_brokers.py

# eToro — reads ETORO_API_KEY + ETORO_USER_KEY + ETORO_DEMO
uv run python tradingbot/livetrade/etoro.py

# Darwinex — reads DARWINEX_USERNAME + DARWINEX_PASSWORD + DARWINEX_DEMO
uv run python tradingbot/livetrade/darwinex.py

# Hyperliquid — reads HYPERLIQUID_PRIVATE_KEY + _ACCOUNT_ADDRESS + _VAULT_ADDRESS
# + _TESTNET. Also prints effective leverage, which must stay <= 1.0x.
PYTHONPATH=tradingbot uv run python tradingbot/livetrade/hyperliquid.py
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

This is **first-party self-service** OAuth: you register against your own account and issue
your own tokens. It replaces the **Client Portal Gateway** — the small Java program IBKR's
"Getting Started → Retail" section points at. With OAuth 1.0a there is no gateway process
to run, which is the whole reason for this integration: a CronJob cannot babysit a
long-lived local Java daemon.

**Eligibility.** The live account must be fully open, **funded**, and of the **IBKR Pro**
type (not Lite). Note that IBKR's own docs file OAuth 1.0a under "Web API Access for
Organizations" and steer individuals toward the CP Gateway; in practice individual Pro
accountholders do use the self-service portal, and IBKR API support has stated there is no
technical limitation preventing it. If the portal refuses your login, that assumption is
what broke — fall back to asking API support to enable OAuth on the username.

**Activation lag.** There is no formal approval process here (unlike *third-party* OAuth,
which carries an 8–14 week compliance review). But consumer keys are not always live
immediately: reported activation delays run from 24 hours to about two weeks, possibly
tied to IBKR's weekend server restarts. Register the key before you need it.

**Paper accounts are supported.** Log into the portal with your **paper** credentials and
follow the identical flow. Generate a *separate* keypair for paper — do not reuse the live
account's keys.

1. Generate the keys and DH params:

   ```bash
   mkdir -p ~/ibkr-oauth-paper && cd ~/ibkr-oauth-paper
   openssl genrsa -out private_signature.pem 2048
   openssl rsa -in private_signature.pem -outform PEM -pubout -out public_signature.pem
   openssl genrsa -out private_encryption.pem 2048
   openssl rsa -in private_encryption.pem -outform PEM -pubout -out public_encryption.pem
   openssl dhparam -out dhparam.pem 2048   # slow, a minute or two
   ```

2. Open the [OAuth self-service portal](https://ndcdyn.interactivebrokers.com/sso/Login?action=OAUTH&RL=1&ip2loc=US)
   and log in with the username you want the API to trade as (your paper username).
   Register a **9-character consumer key** (you choose the string), then upload
   `public_signature.pem`, `public_encryption.pem`, and `dhparam.pem`. Finally generate the
   **access token** and **access token secret**.

3. Extract the DH prime as a hex string:

   ```bash
   python3 -c "
   import subprocess, re
   out = subprocess.run(['openssl','dhparam','-in','dhparam.pem','-text'],
                        capture_output=True, text=True).stdout
   m = re.search(r'(?:prime|P):\s*((?:\s*[0-9a-fA-F:]+\s*)+)', out)
   print(re.sub(r'[\s:]', '', m.group(1)) if m else 'No prime found')
   "
   ```

4. Load them into the cluster — the two PEMs as a file-mounted secret, the rest as env keys:

   ```bash
   kubectl -n tradingbots-2025 create secret generic ib-oauth-keys \
     --from-file=private_encryption.pem --from-file=private_signature.pem

   kubectl -n tradingbots-2025 patch secret tradingbot-secrets --type=merge -p "$(cat <<JSON
   {"stringData": {
     "IBIND_OAUTH1A_CONSUMER_KEY": "<9-char key>",
     "IBIND_OAUTH1A_ACCESS_TOKEN": "<token>",
     "IBIND_OAUTH1A_ACCESS_TOKEN_SECRET": "<token secret>",
     "IBIND_OAUTH1A_DH_PRIME": "<hex from step 3>",
     "IB_ACCOUNT_ID": "<DU… paper account id>"
   }}
   JSON
   )"
   ```

5. Smoke-test locally before enabling the CronJob (see the account-summary command above),
   then set `liveTrade.interactiveBrokers.enabled: true` and do one run with
   `liveTrade.dryRun: "true"`.

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

> [!WARNING]
> **One brokerage session per username, across all IBKR platforms.** If you are logged into
> TWS, IBKR Mobile, or Client Portal with the same username when the CronJob fires, the two
> sessions compete and one gets displaced. Give the copier a **dedicated username** rather
> than the one you log in with manually, or stay logged out around 21:20 UTC.

> [!NOTE]
> **No market data subscription is needed.** `_get_native_price()` returns `0.0` on purpose,
> so the base class falls back to yfinance for pricing. The copier therefore never touches
> `/iserver/marketdata/*` and consumes none of the account's 100 market data lines.
> Rate limits worth knowing if this grows: 10 req/s globally, but `/iserver/orders` and
> `/portfolio/accounts` are 1 req/5s each, and `/tickle` is 1 req/s. Exceeding them returns
> 429 and can put the source IP in a 10-minute penalty box.

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

## ⚡ Hyperliquid (Perpetuals / On-Chain Vault)

Runs one strategy on Hyperliquid perpetuals, optionally inside a **user vault** so
outside depositors can follow it. This is the only **perps** broker here; every
other one is spot. Three things behave differently and none of them are cosmetic.

### 1. Requirements
- An **API (agent) wallet** — generate at <https://app.hyperliquid.xyz/API>.
  Never use your master wallet key. Use **separate agent wallets for testnet and
  mainnet**: nonces are tracked per signer.
- For a vault: 100 USDC minimum seed, a **one-time 10,000 USDC creation fee**,
  and the leader must hold **≥5% of vault equity** at all times. Depositors are
  subject to a lock-up. Read Hyperliquid's docs — these are protocol rules.

### 2. Configuration

| Variable | Description | Default |
| --- | --- | --- |
| `HYPERLIQUID_PRIVATE_KEY` | API/agent wallet private key (the signer). | **Required** |
| `HYPERLIQUID_ACCOUNT_ADDRESS` | Master account the agent signs for. | wallet address |
| `HYPERLIQUID_VAULT_ADDRESS` | Routes all trades into the vault. Empty = your own account. | `""` |
| `HYPERLIQUID_TESTNET` | `true` for testnet, `false` for mainnet. | `true` |

Helm: `liveTrade.hyperliquid` in `helm/tradingbots/values.yaml`. The template
**hard-fails at render time** if `testnet: "false"` and `vaultAddress` is empty,
unless you also set `allowMainnetWithoutVault: "true"` — that combination trades
real personal funds, so it has to be deliberate.

### 3. Why the settings are what they are

- **`portfolioFraction: "0.95"`, not `1.0`.** The copier clamps buys to
  `get_cash() * 0.98`. At 1.0 the target notional exceeds that budget on every
  run, so it scales every order down and sits on the margin boundary. At 0.95 the
  scaling branch never fires.
- **`minOrderUsd: "25"`.** Hyperliquid rejects orders below **$10** notional
  (exact reduce-only closes are exempt); 25 leaves headroom for that 0.98
  scale-down. **Raise it as the vault grows** — target roughly 1–2% of equity.
  Past ~$50k TVL a fixed $25 floor churns taker fees on noise every single run.
- **1x cross leverage**, set by the broker before the first opening order on each
  coin. This is what makes "notional ≤ equity" true even if the copier's weight
  maths is wrong: at 1x the exchange itself rejects an oversized order instead of
  silently levering up. It also makes liquidation of a single cross position
  effectively impossible.
- **`get_cash()` returns `withdrawable`, not `accountValue`.** Perps have no
  settled cash; free collateral is the real constraint on new notional.
  `accountValue` would let the copier submit orders the exchange rejects, and
  `place_order` can only log that — a silent no-op is harder to debug than a clamp.
- **`get_positions()` returns SIGNED sizes** (like Darwinex, unlike C2/IB). With
  `abs()`, a stray short would take the copier's full-liquidation branch and emit
  a SELL, doubling the short — every run. Signed falls through to the general diff
  and buys it back. `test_copier_flattens_a_stray_short` locks this in.

### 4. Staged rollout — do not skip stages

| Stage | Where | Config | Advance when |
| --- | --- | --- | --- |
| 0 | laptop | `TESTNET=true`, no vault, `DRY_RUN=true` | Summary prints; sync logs `[DRY RUN] Would BUY`; nothing unmapped; **no websocket hang** |
| 1 | laptop | `DRY_RUN=false`, ~$1000 faucet USDC | Order fills; effective leverage ≈0.95x; immediate re-run emits no orders; flipping the paper bot to cash closes to exactly 0 |
| 2 | laptop | testnet vault created, `VAULT_ADDRESS` set | **Vault page shows the position and your personal account shows nothing.** Confirms an agent wallet can sign for a vault |
| 3 | cluster | `enabled: true`, `dryRun: true` → then `false` | 7 full days including a weekend; `live_equity` has 7 rows |
| 4 | cluster | mainnet key, `testnet: false`, no vault, `portfolioFraction: "0.20"`, ~$200 real | Loud no-vault warning fires; one clean fill; leverage ≈0.2x; 3–5 days clean |
| 5 | cluster | vault created, `vaultAddress` set, `portfolioFraction: "0.95"` | First run liquidates nothing and opens the target long |

Trigger a run manually:
```bash
kubectl create job --from=cronjob/livetrade-hyperliquid hl-manual-1 -n tradingbots-2025
```

**Open question, answered at stage 2:** Hyperliquid's "Nonces and API wallets"
docs only describe master → sub-account delegation, not agent → vault. `vaultAddress`
is part of the signed preimage so it is not a security hole, and third-party docs
say it works — but verify with a $10 testnet order. If it is rejected, fall back
to the leader's master key and note that in the runbook.

### 5. ⚠️ Rollback leaves the position open

Setting `enabled: false` and running `helm upgrade` stops **new** orders. It does
**not** flatten what is already open — there is no job left to close it. To go flat:

- point `LIVETRADE_BOT_WEIGHTS` at a bot that is currently in cash and run once
  with `dryRun: "false"`, or
- close the position manually in the Hyperliquid UI.

Disabling the copier and walking away leaves a live, unmanaged, funding-accruing
perp position.

### 6. Vault equity tracking

Real equity is recorded to the **`live_equity`** table — deliberately *not*
`portfolio_worth`, which is the paper leaderboard ($10k starts, no fees, no
funding); mixing real money into it makes both curves uninterpretable.

Two writers, one row per (broker, account, UTC day), idempotent:
- `livetrade_hyperliquid.py` records in its `finally` block (free — the sync
  already made those API calls).
- `record_live_equity.py` runs as its own CronJob at 23:05 UTC so the curve stays
  gapless on days the copier errors, days `dryRun` is on, and after it is disabled.

`is_testnet` keeps validation runs out of the published curve. The website
generator reads this table via `generator/vault.py`.

### 7. Usage
```bash
uv run python tradingbot/livetrade_hyperliquid.py
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
