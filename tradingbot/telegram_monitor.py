"""
Telegram channel monitor: fetches new messages, summarizes with AI, stores in DB.

Required env vars:
    TELEGRAM_API_ID          From my.telegram.org (integer)
    TELEGRAM_API_HASH        From my.telegram.org (string)
    TELEGRAM_SESSION_STRING  Telethon StringSession generated once (see docstring below)
    TELEGRAM_CHANNELS        Comma-separated channel usernames or IDs
                             e.g. "durov,some_news_channel,-1001234567890"

Optional env vars:
    TELEGRAM_FETCH_LIMIT     Recent messages to check per channel per run (default: 50)

Generating a session string (run once locally, then store as K8s secret):
    python -c "
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    import os
    api_id = int(input('api_id: '))
    api_hash = input('api_hash: ')
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        print('Session string:', client.session.save())
    "
"""

import logging
import os
from datetime import datetime
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)


def _get_existing_message_ids(session, channel: str) -> set:
    from utils.db import TelegramMessage
    rows = (
        session.query(TelegramMessage.message_id)
        .filter(TelegramMessage.channel == channel)
        .all()
    )
    return {r[0] for r in rows}


def _summarize(text: str) -> tuple[str | None, str | None]:
    """Returns (summary, symbol). Symbol is the primary ticker or None if not applicable."""
    import json
    from utils.ai import run_ai_simple
    system = (
        "You are a concise financial news summarizer. "
        "Given a Telegram message, return a JSON object with two fields:\n"
        "  \"summary\": 1-3 sentence summary highlighting key facts, numbers, and trading implications.\n"
        "  \"symbol\": the single most relevant stock/crypto/forex ticker (e.g. \"AAPL\", \"BTC\", \"EURUSD\"), "
        "or null if no specific asset is mentioned.\n"
        "Respond with valid JSON only, no markdown."
    )
    raw = run_ai_simple(system_prompt=system, user_message=text)
    try:
        parsed = json.loads(raw)
        return parsed.get("summary"), parsed.get("symbol")
    except Exception:
        # If JSON parsing fails, treat the whole response as the summary
        return raw, None


def _process_channel(client, channel: str, fetch_limit: int):
    from utils.db import TelegramMessage, get_db_session

    # Resolve entity without loading all dialogs.
    # Bot API IDs (-100XXXXXXXXXX) → PeerChannel(XXXXXXXXXX) for direct lookup.
    print(f"  >> Resolving entity for {channel}...", flush=True)
    if channel.lstrip("-").isdigit():
        from telethon.tl.types import PeerChannel
        cid = int(channel)
        real_id = int(str(abs(cid))[3:])  # strip leading '100' from -100XXXXXXXXXX
        entity = client.get_entity(PeerChannel(real_id))
    else:
        entity = channel  # public username, resolved by Telethon directly
    print(f"  >> Entity resolved: {entity}", flush=True)

    print(f"  >> Fetching messages (limit={fetch_limit})...", flush=True)
    messages = client.get_messages(entity, limit=fetch_limit)
    print(f"  >> Got {len(messages)} messages.", flush=True)

    with get_db_session() as session:
        existing_ids = _get_existing_message_ids(session, channel)
        new_messages = [
            msg for msg in messages
            if msg.id not in existing_ids
            and (getattr(msg, "text", None) or getattr(msg, "message", None) or "").strip()
        ]
        new_count = 0

        from tqdm import tqdm
        for msg in tqdm(new_messages, desc=f"Summarizing {channel}", unit="msg"):
            text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""

            summary, symbol = None, None
            try:
                summary, symbol = _summarize(text)
            except Exception as e:
                logger.warning("AI summarization failed for msg %s in %s: %s", msg.id, channel, e)

            published_at = getattr(msg, "date", None)
            if published_at is not None and published_at.tzinfo is not None:
                # Store as naive UTC (consistent with rest of project)
                published_at = published_at.replace(tzinfo=None)
            if published_at is None:
                published_at = datetime.utcnow()

            record = TelegramMessage(
                channel=channel,
                message_id=msg.id,
                text=text[:4000],  # guard against very long messages
                summary=summary,
                symbol=symbol,
                published_at=published_at,
            )
            session.add(record)
            new_count += 1

        logger.info("Channel %s: %d new messages stored", channel, new_count)


def main():
    """Fetch, summarize, and store new messages from all configured channels."""
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session_string = os.environ["TELEGRAM_SESSION_STRING"]
    channels_raw = os.environ.get("TELEGRAM_CHANNELS", "")
    # Strip inline comments (e.g. "-100123,chanB # some note" → ["-100123", "chanB"])
    channels = [
        c.split("#")[0].strip()
        for c in channels_raw.split(",")
        if c.split("#")[0].strip()
    ]
    fetch_limit = int(os.environ.get("TELEGRAM_FETCH_LIMIT", "50"))

    if not channels:
        logger.warning("TELEGRAM_CHANNELS is empty — nothing to monitor.")
        return

    logger.info("Starting Telegram monitor for channels: %s", channels)
    print(">> Connecting to Telegram...", flush=True)
    with TelegramClient(StringSession(session_string), api_id, api_hash) as client:
        print(">> Connected.", flush=True)
        from tqdm import tqdm
        for channel in tqdm(channels, desc="Channels", unit="ch"):
            print(f">> Processing channel: {channel}", flush=True)
            logger.info("Processing channel: %s", channel)
            try:
                _process_channel(client, channel, fetch_limit)
            except Exception as e:
                logger.error("Error processing channel %s: %s", channel, e)

    print(">> Done.", flush=True)
    logger.info("Telegram monitor run complete.")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
