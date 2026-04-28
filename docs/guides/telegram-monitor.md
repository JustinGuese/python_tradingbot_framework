# Telegram Channel Monitor Guide

> [!WARNING]
> **DISCLAIMER:** This software is for educational and research purposes only. AI-generated analysis of news and signals is not financial advice. Trading based on these signals involves significant risk of loss. Use of these features is strictly at your own risk.

Monitor Telegram channels for news and trading signals. The monitor polls channels for new messages, summarizes them with AI, extracts trading symbols, and stores everything in the database.

## Overview

**What it does**:
- Fetches new messages from monitored Telegram channels
- Summarizes each message with AI (key facts, trading implications)
- Extracts the primary trading symbol (e.g., AAPL, BTC, EURUSD)
- Stores results in the `telegram_messages` database table
- Runs as a **stateless Kubernetes CronJob** (no persistent process)

**Key benefits**:
- Automated news ingestion from Telegram channels
- AI-powered extraction of trading signals and symbols
- Database integration for downstream analysis
- Cost-optimized with cheap LLM (OpenRouter)
- No session files — credentials stored as K8s secrets

For detailed API documentation, see [Telegram Monitor API](../api/telegram-monitor.md).

---

## Quick Setup

### 1. Get Telegram API Credentials

Go to **https://my.telegram.org/apps**, log in with your phone number, and create an application. You'll receive:
- `TELEGRAM_API_ID` (integer)
- `TELEGRAM_API_HASH` (hex string)

### 2. Generate a StringSession

**One-time setup (do this locally on your machine)**:

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

Follow the prompts (phone number → SMS/Telegram code → 2FA if enabled).

Copy the printed `Session string:` value — this is your `TELEGRAM_SESSION_STRING`.

⚠️ **Security**: The session string grants full access to your Telegram account. Treat it like a password — never commit to git.

### 3. Find Channel IDs

Use this one-time helper script to list all your channels with their IDs:

```python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from os import environ

with TelegramClient(StringSession(environ["TELEGRAM_SESSION_STRING"]),
                    int(environ["TELEGRAM_API_ID"]),
                    environ["TELEGRAM_API_HASH"]) as c:
    for dialog in c.get_dialogs():
        print(f"{dialog.id:>20}  {dialog.name}")
```

Output example:
```
      -1001234567890  Trading News
              246810  financial_data_channel
```

Use:
- **Public channel username** (no @ prefix) for public channels: `financial_data_channel`
- **Numeric ID** (starts with `-100`) for private channels: `-1001234567890`

### 4. Store Credentials in Kubernetes

Choose one approach:

**Option A: Patch existing secret**
```bash
kubectl patch secret tradingbot-secrets -n tradingbots-2025 \
  --type=json -p='[
    {"op":"add","path":"/data/TELEGRAM_API_ID","value":"'$(echo -n 'YOUR_API_ID' | base64 -w 0)'"},
    {"op":"add","path":"/data/TELEGRAM_API_HASH","value":"'$(echo -n 'YOUR_API_HASH' | base64 -w 0)'"},
    {"op":"add","path":"/data/TELEGRAM_SESSION_STRING","value":"'$(echo -n 'YOUR_SESSION_STRING' | base64 -w 0)'"}
  ]'
```

**Option B: Add to `.env` and recreate**
```
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abc123...
TELEGRAM_SESSION_STRING=1ApWapz...
```

Then create the secret in Kubernetes.

### 5. Enable in Helm Values

Edit `kubernetes/helm/tradingbots/values.yaml`:

```yaml
telegramMonitor:
  enabled: true
  schedule: "*/30 * * * *"           # Every 30 minutes
  channels: "financial_data_channel,-1001234567890"
  fetchLimit: "50"
```

### 6. Deploy

```bash
helm upgrade --install tradingbots ./helm/tradingbots --namespace tradingbots-2025
```

---

## Configuration

### Environment Variables

| Variable | Required | Description | Example |
|---|---|---|---|
| `TELEGRAM_API_ID` | Yes | From my.telegram.org | `12345678` |
| `TELEGRAM_API_HASH` | Yes | From my.telegram.org | `abc123def456...` |
| `TELEGRAM_SESSION_STRING` | Yes | Generated StringSession | `1ApWapz...` |
| `TELEGRAM_CHANNELS` | Yes | Comma-separated channels | `news_channel,-1001234567890` |
| `TELEGRAM_FETCH_LIMIT` | No | Messages per channel per run | `50` (default) |
| `OPENROUTER_API_KEY` | Yes | For AI summarization | (inherited from secrets) |

### Helm Configuration

In `values.yaml`:

```yaml
telegramMonitor:
  # Enable the monitor
  enabled: true

  # CronJob schedule (standard cron format)
  # Run every 30 minutes
  schedule: "*/30 * * * *"

  # Channels to monitor (comma-separated)
  # Mix of public usernames and private numeric IDs
  channels: "some_channel,financial_news,-1001234567890"

  # Messages to check per channel per run
  # Default: 50 (safe for small channels)
  # Increase for high-volume channels
  fetchLimit: "100"
```

---

## Database Schema

The monitor stores results in the `telegram_messages` table:

| Column | Type | Purpose |
|---|---|---|
| `id` | INT | Auto-increment primary key |
| `channel` | VARCHAR | Channel identifier (indexed) |
| `message_id` | INT | Telegram message ID (unique per channel) |
| `text` | VARCHAR | Original message (max 4000 chars) |
| `summary` | VARCHAR | AI-generated summary |
| `symbol` | VARCHAR | Primary ticker extracted (indexed) |
| `acted_on` | BOOL | Set True by signals bot before evaluation (default False) |
| `published_at` | DATETIME | When the message was posted (UTC) |
| `created_at` | DATETIME | When stored in database |

**Unique constraint**: `(channel, message_id)` — same message never stored twice.

**Indexes**:
- `(channel, published_at)` — fast date range queries
- `symbol` — fast ticker lookups

---

## Querying Results

### Find Messages for a Symbol

```python
from tradingbot.utils.db import TelegramMessage, get_db_session

with get_db_session() as session:
    messages = (session.query(TelegramMessage)
                .filter(TelegramMessage.symbol == "AAPL")
                .order_by(TelegramMessage.published_at.desc())
                .limit(10)
                .all())

    for msg in messages:
        print(f"{msg.published_at} | {msg.channel} | {msg.summary}")
```

### Find Recent Messages from a Channel

```python
with get_db_session() as session:
    messages = (session.query(TelegramMessage)
                .filter(TelegramMessage.channel == "financial_news")
                .order_by(TelegramMessage.published_at.desc())
                .limit(20)
                .all())
```

### Find Messages in a Time Range

```python
from datetime import datetime, timedelta, timezone

cutoff = datetime.now(timezone.utc) - timedelta(days=1)

with get_db_session() as session:
    messages = (session.query(TelegramMessage)
                .filter(TelegramMessage.published_at >= cutoff)
                .order_by(TelegramMessage.published_at.desc())
                .all())
```

---

## Testing Locally

### Run Once Manually

```bash
export TELEGRAM_API_ID=your_id
export TELEGRAM_API_HASH=your_hash
export TELEGRAM_SESSION_STRING=your_session
export TELEGRAM_CHANNELS=channel1,channel2
export OPENROUTER_API_KEY=your_key
export POSTGRES_URI=user:password@localhost:5432/postgres

python tradingbot/telegram_monitor.py
```

### Debug Mode

Add logging to see what's happening:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

from tradingbot.telegram_monitor import main
main()
```

---

## Troubleshooting

### "No messages found"

- Channels might not have new messages since last run
- Check `TELEGRAM_FETCH_LIMIT` — increase to fetch more messages
- Verify channel configuration in Helm values

### "Summary is empty"

- Message might be non-text (image, video, etc.)
- AI might have failed silently — check logs
- Check `OPENROUTER_API_KEY` is valid

### "Invalid session"

- `TELEGRAM_SESSION_STRING` might have expired
- Generate a new StringSession locally
- Update K8s secret

### "Unique constraint violation"

- Usually means data was already stored (safe)
- Check database logs for details

---

## Architecture

```
tradingbot/telegram_monitor.py          (wrapper: env vars, logging)
└── calls utils/telegram_monitor.py

tradingbot/utils/telegram_monitor.py   (implementation)
├── monitor_channels()              → Main orchestrator
├── process_channel()               → Process single channel
├── summarize_message()             → AI summarization
└── get_existing_message_ids()      → Deduplication
```

Runs as a stateless CronJob — no persistent connection. Downstream, `telegramsignalsbankbot` reads the stored messages and acts on signals. See [Telegram Signals Bot Guide](telegram-signals-bot.md).

---

## Advanced Topics

### High-Volume Channels

For channels with 1000+ messages per day:
- Increase `TELEGRAM_FETCH_LIMIT` conservatively
- Run CronJob more frequently (e.g., every 5 minutes)
- Monitor database disk usage

### Custom Summarization

To modify the AI prompt, edit `summarize_message()` in `tradingbot/utils/telegram_monitor.py`:

```python
def summarize_message(text: str) -> tuple[str | None, str | None]:
    system = (
        "Your custom prompt here..."
    )
    raw = run_ai_simple(system_prompt=system, user_message=text)
    # ... rest of function
```

---

## Key Files

| File | Purpose |
|---|---|
| `tradingbot/telegram_monitor.py` | Wrapper/entry point |
| `tradingbot/utils/telegram_monitor.py` | Core implementation |
| `tradingbot/utils/db.py` | Database models (TelegramMessage) |
| `helm/tradingbots/values.yaml` | Helm configuration |
| `helm/tradingbots/templates/cronjob-telegram-monitor.yaml` | CronJob template |

---

## See Also

- [Telegram Monitor API Reference](../api/telegram-monitor.md) — Function signatures and database schema
- [AI Tools Guide](ai-tools.md) — How AI summarization works
- [Local Development](local-development.md) — Testing bots locally
