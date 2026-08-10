from os import environ

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(environ["TELEGRAM_API_ID"])
api_hash = environ.get("TELEGRAM_API_HASH")
session = environ.get("TELEGRAM_SESSION_STRING")

with TelegramClient(StringSession(session), api_id, api_hash) as c:
    for dialog in c.get_dialogs():
        print(f"{dialog.id:>20}  {dialog.name}")
