"""Telegram channel monitoring implementation: fetch, summarize, and store messages."""

import logging
from datetime import datetime

from telethon.sync import TelegramClient
from telethon.tl.types import PeerChannel
from tqdm import tqdm

from .db import TelegramMessage, get_db_session

logger = logging.getLogger(__name__)


def get_existing_message_ids(session, channel: str) -> set:
    """Get the set of already-stored message IDs for a channel."""
    rows = (
        session.query(TelegramMessage.message_id)
        .filter(TelegramMessage.channel == channel)
        .all()
    )
    return {r[0] for r in rows}


def summarize_message(text: str) -> tuple[str | None, str | None]:
    """Summarize a message and extract the primary symbol.

    Returns:
        (summary, symbol): summary is 1-3 sentences, symbol is the primary ticker or None.
    """
    import json

    from .aitools import run_ai_simple

    system = (
        "You are a concise financial news summarizer. "
        "Given a Telegram message, return a JSON object with two fields:\n"
        '  "summary": 1-3 sentence summary highlighting key facts, numbers, and trading implications.\n'
        '  "symbol": the single most relevant stock/crypto/forex ticker (e.g. "AAPL", "BTC", "EURUSD"), '
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


def process_channel(client: TelegramClient, channel: str, fetch_limit: int) -> int:
    """Process a single channel: fetch, summarize, and store new messages.

    Args:
        client: Connected TelegramClient
        channel: Channel identifier (username or numeric ID)
        fetch_limit: Number of recent messages to fetch

    Returns:
        Number of new messages stored
    """
    # Resolve entity without loading all dialogs.
    # Bot API IDs (-100XXXXXXXXXX) → PeerChannel(XXXXXXXXXX) for direct lookup.
    logger.info(f"  >> Resolving entity for {channel}...")
    if channel.lstrip("-").isdigit():
        cid = int(channel)
        real_id = int(str(abs(cid))[3:])  # strip leading '100' from -100XXXXXXXXXX
        entity = client.get_entity(PeerChannel(real_id))
    else:
        entity = channel  # public username, resolved by Telethon directly
    logger.info(f"  >> Entity resolved: {entity}")

    logger.info(f"  >> Fetching messages (limit={fetch_limit})...")
    messages = client.get_messages(entity, limit=fetch_limit)
    logger.info(f"  >> Got {len(messages)} messages.")


    with get_db_session() as session:
        existing_ids = get_existing_message_ids(session, channel)
        new_messages = [
            msg
            for msg in messages
            if msg.id not in existing_ids
            and (getattr(msg, "text", None) or getattr(msg, "message", None) or "").strip()
        ]
        new_count = 0

        for msg in tqdm(new_messages, desc=f"Summarizing {channel}", unit="msg"):
            text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""

            summary, symbol = None, None
            try:
                summary, symbol = summarize_message(text)
            except Exception as e:
                logger.warning(
                    "AI summarization failed for msg %s in %s: %s",
                    msg.id,
                    channel,
                    e,
                )

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

    return new_count


def monitor_channels(
    api_id: int,
    api_hash: str,
    session_string,  # StringSession object
    channels: list[str],
    fetch_limit: int = 50,
) -> None:
    """Connect to Telegram and monitor all channels.

    Args:
        api_id: Telegram API ID
        api_hash: Telegram API hash
        session_string: Telethon StringSession object
        channels: List of channel identifiers
        fetch_limit: Recent messages to check per channel per run
    """
    if not channels:
        logger.warning("No channels configured for monitoring.")
        return

    logger.info("Starting Telegram monitor for channels: %s", channels)
    logger.info(">> Connecting to Telegram...")
    with TelegramClient(session_string, api_id, api_hash) as client:
        logger.info(">> Connected.")
        for channel in tqdm(channels, desc="Channels", unit="ch"):
            logger.info(f">> Processing channel: {channel}")
            logger.info("Processing channel: %s", channel)
            try:
                process_channel(client, channel, fetch_limit)
            except Exception as e:
                logger.error("Error processing channel %s: %s", channel, e)

    logger.info(">> Done.")
    logger.info("Telegram monitor run complete.")
