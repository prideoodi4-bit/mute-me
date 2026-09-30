from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiohttp import web, ClientSession

from app.botlogic import PrideBot
from app.database import Database
from app.telegram import TelegramClient


def configure_logging():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def start_health_server():
    app = web.Application()

    async def health(_request):
        return web.Response(text="Pride Group Manager is running")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner


async def main():
    configure_logging()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is missing. Add it as an environment variable.")

    db_path = os.getenv("DATABASE_PATH", "./pride_bot.db")
    db = Database(db_path)

    async with ClientSession() as session:
        tg = TelegramClient(token, session)
        bot = PrideBot(tg, db)
        runner = await start_health_server()
        try:
            await bot.run_polling()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
