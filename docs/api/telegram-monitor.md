# Telegram Monitor API

Core functions for monitoring Telegram channels, summarizing messages with AI, and storing results.

## Module: `tradingbot.utils.telegram_monitor`

Implementation logic. The wrapper (`tradingbot/telegram_monitor.py`) handles env var parsing and calls into this module.

### `monitor_channels(api_id, api_hash, session_string, channels, fetch_limit=50)`

Connect to Telegram and monitor all configured channels. Called by the wrapper's `main()`.

### `process_channel(client, channel, fetch_limit) -> int`

Fetch up to `fetch_limit` recent messages, skip already-stored IDs, summarize new ones, write to `telegram_messages`. Returns number of new messages stored.

### `summarize_message(text) -> tuple[str | None, str | None]`

Calls `run_ai_simple()` with a financial summarization prompt. Returns `(summary, symbol)`. Falls back to raw response if JSON parsing fails.

### `get_existing_message_ids(session, channel) -> set`

Returns the set of already-stored `message_id` values for a channel (deduplication).

---

## Database Model: `TelegramMessage`

Located in `tradingbot.utils.db`:

```python
class TelegramMessage(Base):
    id: int                  # Auto-increment primary key
    channel: str             # Channel username or numeric ID (indexed)
    message_id: int          # Telegram message ID (unique per channel)
    text: str                # Original message text (nullable, max 4000 chars)
    summary: str             # AI-generated summary (nullable)
    symbol: str              # Primary ticker extracted by AI (nullable, indexed)
    acted_on: bool           # Set True by signals bot before evaluation (default False)
    published_at: datetime   # UTC posting time of the message
    created_at: datetime     # When stored in database
```

**Constraints**:
- Unique on `(channel, message_id)` — prevents duplicate storage
- `acted_on` set atomically before AI classification in `telegramsignalsbankbot` — crash-safe

**Indexes**: `(channel, published_at)`, `symbol`

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_API_ID` | Yes | From my.telegram.org (integer) |
| `TELEGRAM_API_HASH` | Yes | From my.telegram.org (hex string) |
| `TELEGRAM_SESSION_STRING` | Yes | Telethon StringSession (generated once) |
| `TELEGRAM_CHANNELS` | Yes | Comma-separated channel usernames or IDs |
| `TELEGRAM_FETCH_LIMIT` | No | Messages per channel per run (default: 50) |
| `OPENROUTER_API_KEY` | Yes | For AI summarization (inherited from secrets) |

---

## See Also

- [Telegram Monitor Guide](../guides/telegram-monitor.md) — Setup and configuration
- [Telegram Signals Bot Guide](../guides/telegram-signals-bot.md) — Acting on stored signals
