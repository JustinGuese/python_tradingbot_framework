# Telegram Channel Monitor

The Telegram monitor polls one or more Telegram channels for new messages, summarizes each message with AI, extracts the primary asset ticker, and writes results to the `telegram_messages` database table.

It runs as an **optional Kubernetes CronJob** — stateless, no persistent process required.

## How It Works

1. Connects to Telegram using a **StringSession** (stored as a K8s secret — no session file needed)
2. Fetches the last N messages per channel (`TELEGRAM_FETCH_LIMIT`, default 50)
3. Skips messages already stored in the DB (deduplication by `channel + message_id`)
4. For each new message, calls the AI (cheap LLM via OpenRouter) to produce:
   - **`summary`**: 1–3 sentence summary of the message
   - **`symbol`**: primary ticker extracted (e.g. `AAPL`, `BTC`), or `null`
5. Persists to `telegram_messages` table

## Setup

### 1. Get Telegram API Credentials

Go to **https://my.telegram.org/apps**, log in with your phone number, and create an application. You'll receive:
- `TELEGRAM_API_ID` (integer)
- `TELEGRAM_API_HASH` (hex string)

### 2. Generate a StringSession (one-time, local)

```bash
python -c "
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
api_id = int(input('api_id: '))
api_hash = input('api_hash: ')
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print('Session string:', c.session.save())
"
```

Follow the prompts (phone number → SMS/Telegram code → 2FA if enabled). Copy the printed string — this is your `TELEGRAM_SESSION_STRING`.

> **Security**: The session string grants full access to your Telegram account. Treat it like a password.

### 3. Find Channel IDs

Use the helper script to list all your channels with their IDs:

```python
# onetime_getTelegramChats.py
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from os import environ

with TelegramClient(StringSession(environ["TELEGRAM_SESSION_STRING"]),
                    int(environ["TELEGRAM_API_ID"]),
                    environ["TELEGRAM_API_HASH"]) as c:
    for dialog in c.get_dialogs():
        print(f"{dialog.id:>20}  {dialog.name}")
```

Use the username (public channels, no `@`) or numeric ID (private channels, starts with `-100`).

### 4. Add Secrets to K8s

```bash
kubectl patch secret tradingbot-secrets -n tradingbots-2025 \
  --type=json -p="[
    {\"op\":\"add\",\"path\":\"/data/TELEGRAM_API_ID\",\"value\":\"$(echo -n 'YOUR_API_ID' | base64 -w 0)\"},
    {\"op\":\"add\",\"path\":\"/data/TELEGRAM_API_HASH\",\"value\":\"$(echo -n 'YOUR_API_HASH' | base64 -w 0)\"},
    {\"op\":\"add\",\"path\":\"/data/TELEGRAM_SESSION_STRING\",\"value\":\"$(echo -n 'YOUR_SESSION_STRING' | base64 -w 0)\"}
  ]"
```

Or add to `.env` and recreate the secret:
```
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abc123...
TELEGRAM_SESSION_STRING=1ApWapz...
```

### 5. Enable in values.yaml

```yaml
telegramMonitor:
  enabled: true
  schedule: "*/30 * * * *"   # Every 30 minutes
  channels: "some_news_channel,-1001234567890"
  fetchLimit: "50"
```

### 6. Run the DB migration

The `symbol` column was added after initial table creation. Run once on the existing DB:

```sql
ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS symbol VARCHAR;
CREATE INDEX IF NOT EXISTS ix_telegram_messages_symbol ON telegram_messages (symbol);
```

### 7. Deploy

```bash
helm upgrade --install tradingbots ./helm/tradingbots --namespace tradingbots-2025
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_API_ID` | Yes | From my.telegram.org (integer) |
| `TELEGRAM_API_HASH` | Yes | From my.telegram.org (hex string) |
| `TELEGRAM_SESSION_STRING` | Yes | Telethon StringSession (generated once) |
| `TELEGRAM_CHANNELS` | Yes | Comma-separated usernames or IDs |
| `TELEGRAM_FETCH_LIMIT` | No | Messages to fetch per channel per run (default: `50`) |
| `OPENROUTER_API_KEY` | Yes | For AI summarization |

## Database Table

```python
class TelegramMessage(Base):
    id: int                  # Auto-increment primary key
    channel: str             # Channel username or ID (indexed)
    message_id: int          # Telegram message ID (unique per channel)
    text: str                # Original message text (nullable, truncated to 4000 chars)
    summary: str             # AI-generated summary (nullable)
    symbol: str              # Primary ticker extracted by AI e.g. "AAPL" (nullable, indexed)
    published_at: datetime   # When the message was posted (UTC)
    created_at: datetime
```

**Unique constraint**: `(channel, message_id)` — same message is never stored twice.
**Indexes**: `(channel, published_at)`, `symbol`

## Querying Messages

```python
from tradingbot.utils.db import TelegramMessage, get_db_session

# All messages for a symbol
with get_db_session() as session:
    msgs = (session.query(TelegramMessage)
            .filter(TelegramMessage.symbol == "AAPL")
            .order_by(TelegramMessage.published_at.desc())
            .limit(10)
            .all())

# Recent messages from a channel
with get_db_session() as session:
    msgs = (session.query(TelegramMessage)
            .filter(TelegramMessage.channel == "some_news_channel")
            .order_by(TelegramMessage.published_at.desc())
            .limit(20)
            .all())
```

## Key Files

| File | Purpose |
|---|---|
| `tradingbot/telegram_monitor.py` | `TelegramChannelMonitor` class |
| `tradingbot/telegrammonitorbot.py` | Entry point script |
| `helm/tradingbots/templates/cronjob-telegram-monitor.yaml` | Optional Helm CronJob |
| `helm/tradingbots/values.yaml` → `telegramMonitor` | Enable/configure the monitor |
