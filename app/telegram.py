from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, payload: dict):
        self.method = method
        self.payload = payload
        self.error_code = int(payload.get("error_code", 0) or 0)
        self.description = payload.get("description", "Telegram API error")
        self.retry_after = int((payload.get("parameters") or {}).get("retry_after", 0) or 0)
        super().__init__(f"{method}: {self.error_code} {self.description}")


class TelegramClient:
    def __init__(self, token: str, session: aiohttp.ClientSession):
        self.token = token
        self.session = session
        self.base = f"https://api.telegram.org/bot{token}"

    async def call(self, method: str, data: dict[str, Any] | None = None, retries: int = 3):
        data = data or {}
        for attempt in range(retries + 1):
            try:
                async with self.session.post(f"{self.base}/{method}", json=data, timeout=aiohttp.ClientTimeout(total=70)) as r:
                    payload = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt >= retries:
                    raise
                await asyncio.sleep(min(2 ** attempt, 8))
                continue

            if payload.get("ok"):
                return payload.get("result")

            err = TelegramAPIError(method, payload)
            if err.retry_after and attempt < retries:
                await asyncio.sleep(err.retry_after + 0.5)
                continue
            if err.error_code >= 500 and attempt < retries:
                await asyncio.sleep(min(2 ** attempt, 8))
                continue
            raise err

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None, reply_to: int | None = None):
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            data["reply_markup"] = reply_markup
        if reply_to:
            data["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return await self.call("sendMessage", data)

    async def delete_messages(self, chat_id: int, ids: list[int]):
        return await self.call("deleteMessages", {"chat_id": chat_id, "message_ids": ids})

    async def delete_message(self, chat_id: int, message_id: int):
        return await self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
