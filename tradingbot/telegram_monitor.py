"""
Telegram channel monitor wrapper: fetches new messages, summarizes with AI, stores in DB.

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

from telethon.sessions import StringSession

from utils.telegram_monitor import monitor_channels

logger = logging.getLogger(__name__)


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

    monitor_channels(
        api_id=api_id,
        api_hash=api_hash,
        session_string=StringSession(session_string),
        channels=channels,
        fetch_limit=fetch_limit,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
