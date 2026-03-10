# Telegram Signals Bot Guide

`telegramsignalsbankbot` is a trading bot that reads AI-summarized Telegram messages from the database, classifies them as BUY/SELL entry signals, and executes trades automatically.

It is the **downstream consumer** of the `telegram-monitor` CronJob — the monitor fills the `telegram_messages` table, the signals bot acts on it.

---

## How It Works

1. Queries `telegram_messages` where `channel = '-1001998690333'`, `symbol IS NOT NULL`, `acted_on = FALSE`, `published_at` within last 3 days
2. For each message, sets `acted_on = TRUE` immediately (crash-safe — won't re-process even if bot dies mid-run)
3. Calls AI (`run_ai_simple`) to classify: is this a new entry signal or noise (TP hit / analysis / recap)?
4. AI also returns the correct **Yahoo Finance ticker** — no hardcoded symbol map needed
5. Executes `buy(yf_ticker, quantityUSD=cash * 0.20)` or `sell(yf_ticker)` via the Bot class

---

## Position Sizing

`POSITION_SIZE_PCT = 0.2` — spends **20% of available cash** per signal.

With $10,000 starting capital and multiple signals:
- Signal 1 BUY BTC-USD → $2,000
- Signal 2 BUY ^GSPC → $1,600 (20% of remaining $8,000)
- Up to ~5 positions before cash runs low

Sells use all of the holding (`sell(symbol)` with no `quantityUSD`).

---

## Signal Classification

The AI classifier receives the raw message text + the pre-computed summary from `telegram_monitor`. It returns:

```json
{"is_signal": true, "direction": "BUY", "yf_ticker": "BTC-USD"}
```

**Classified as signals**: messages with an explicit entry price and BUY/SELL direction.

**Not classified as signals** (correctly skipped):
- Take-profit hit notifications ("TP1 HIT ✅")
- Performance recaps ("Performance 09.03.26 🏆")
- Market analysis without a concrete entry
- General commentary

The AI resolves broker-specific CFD names (US500, DJ30.c, XAUUSD) to Yahoo Finance tickers. `yf.Search()` was evaluated but doesn't understand these names — AI resolution is more reliable.

---

## Deduplication

`acted_on = TRUE` is written **per message in its own DB transaction**, before the AI call and before any trade. This means:

- If the bot crashes mid-run, already-evaluated messages stay `acted_on = TRUE`
- No message is ever processed twice, regardless of crash timing
- No in-memory ID lists or portfolio-dict tracking needed

---

## Configuration

```python
# telegramsignalsbankbot.py
CHANNEL_ID = "-1001998690333"   # The Signals Bank - FREE
LOOKBACK_DAYS = 3               # Only look at messages from last N days
POSITION_SIZE_PCT = 0.2         # 20% of cash per signal
```

To monitor a different channel, change `CHANNEL_ID` to the numeric Telegram channel ID (find with `onetime_getTelegramChats.py`).

---

## Helm Deployment

In `helm/tradingbots/values.yaml`:

```yaml
- name: telegramsignalsbankbot
  schedule: "*/5 * * * *"  # Every 5 minutes — matches telegram-monitor cadence
```

The bot runs every 5 minutes. If `telegram-monitor` just fetched new messages, this bot will pick them up within 5 minutes.

No special env vars beyond the standard set — it reads from the same `postgres` database as all other bots.

---

## Key Files

| File | Purpose |
|---|---|
| `tradingbot/telegramsignalsbankbot.py` | Bot implementation |
| `tradingbot/utils/db.py` | `TelegramMessage` model (incl. `acted_on`) |
| `helm/tradingbots/values.yaml` | Schedule configuration |

---

## See Also

- [Telegram Monitor Guide](telegram-monitor.md) — How messages are fetched and stored
- [Database Models](../architecture/database-models.md) — `TelegramMessage` schema
- [Creating a Bot](../getting-started/creating-a-bot.md) — Bot framework overview
