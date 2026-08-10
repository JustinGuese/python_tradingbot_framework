from os import environ

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(environ["TELEGRAM_API_ID"])
api_hash = environ.get("TELEGRAM_API_HASH")
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print("SESSION STRING:", c.session.save())
