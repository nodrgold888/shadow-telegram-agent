from __future__ import annotations

import getpass
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(os.getenv("TELEGRAM_API_ID") or input("TELEGRAM_API_ID: ").strip())
    api_hash = os.getenv("TELEGRAM_API_HASH") or getpass.getpass("TELEGRAM_API_HASH: ").strip()
    phone = input("Telegram phone number (international format): ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    await client.send_code_request(phone)
    code = getpass.getpass("Telegram login code: ").strip()
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        password = getpass.getpass("Telegram two-step password: ")
        await client.sign_in(password=password)

    print("\nTELEGRAM_SESSION (store this only as a private Render secret):\n")
    print(client.session.save())
    await client.disconnect()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
