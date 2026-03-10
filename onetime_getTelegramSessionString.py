from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from os import environ
api_id = int(environ.get('TELEGRAM_API_ID'))
api_hash = environ.get('TELEGRAM_API_HASH')
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print('SESSION STRING:', c.session.save())