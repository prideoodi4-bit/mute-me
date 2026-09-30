from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .database import Database
from .durations import extract_self_mute_duration, normalize, parse_duration
from .telegram import TelegramAPIError, TelegramClient

log = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}
SAFE_DELETE_WINDOW = 48 * 3600


def display_name(user: dict) -> str:
    name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x).strip()
    if not name:
        name = user.get("username") or str(user.get("id"))
    return html.escape(name)


def mention(user: dict) -> str:
    return f'<a href="tg://user?id={int(user["id"])}">{display_name(user)}</a>'


def chunked(items: list[int], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class PrideBot:
    def __init__(self, tg: TelegramClient, db: Database):
        self.tg = tg
        self.db = db
        self.bot_user: dict | None = None
        self.offset = 0
        self.last_prune = 0

    async def setup(self):
        # Ensure long polling works even if this token previously had a webhook configured.
        await self.tg.call("deleteWebhook", {"drop_pending_updates": False})
        self.bot_user = await self.tg.call("getMe")
        commands = [
            {"command": "help", "description": "شرح أوامر البوت"},
            {"command": "clearme", "description": "حذف رسائلك المتاحة"},
            {"command": "mute", "description": "Admin: كتم عضو بالرد"},
            {"command": "unmute", "description": "Admin: إلغاء كتم عضو"},
            {"command": "clearuser", "description": "Admin: حذف رسائل عضو"},
            {"command": "status", "description": "Admin: حالة كتم عضو"},
            {"command": "check", "description": "فحص صلاحيات البوت"},
        ]
        try:
            await self.tg.call("setMyCommands", {"commands": commands})
        except Exception:
            log.exception("Could not set commands")

    async def run_polling(self):
        await self.setup()
        log.info("Started as @%s", self.bot_user.get("username") if self.bot_user else "bot")
        while True:
            try:
                updates = await self.tg.call(
                    "getUpdates",
                    {
                        "offset": self.offset,
                        "timeout": 45,
                        "allowed_updates": ["message", "callback_query", "my_chat_member"],
                    },
                    retries=5,
                )
                for update in updates:
                    self.offset = max(self.offset, int(update["update_id"]) + 1)
                    try:
                        await self.handle_update(update)
                    except Exception:
                        log.exception("Update failed: %s", update.get("update_id"))
                now = int(time.time())
                if now - self.last_prune > 3600:
                    self.db.prune(now - 50 * 3600)
                    self.last_prune = now
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Polling error")
                await asyncio.sleep(3)

    async def handle_update(self, update: dict):
        if "callback_query" in update:
            await self.handle_callback(update["callback_query"])
            return
        msg = update.get("message")
        if not msg:
            return
        chat = msg.get("chat") or {}
        if chat.get("type") not in GROUP_TYPES:
            if msg.get("text", "").startswith("/start"):
                await self.tg.send_message(
                    chat["id"],
                    "🤖 <b>Pride Group Manager</b>\n\nضيفني للكروب وصعّدني Admin بصلاحيات حذف الرسائل وكتم الأعضاء، وبعدها أشتغل تلقائياً.",
                )
            return

        user = msg.get("from")
        if user and not user.get("is_bot"):
            self.db.track_message(
                int(chat["id"]), user, int(msg["message_id"]), int(msg.get("date") or time.time())
            )

        text = (msg.get("text") or "").strip()
        if not text or not user or user.get("is_bot"):
            return

        await self.handle_text(msg, text)

    async def handle_text(self, msg: dict, text: str):
        chat_id = int(msg["chat"]["id"])
        user = msg["from"]
        user_id = int(user["id"])
        ntext = normalize(text)

        # Natural-language member commands.
        if ntext == "برايد امسح رسائلي":
            await self.clear_user_messages(chat_id, user_id, requester=user, reply_to=None)
            return

        if ntext.startswith("برايد اكتمني"):
            parsed = extract_self_mute_duration(text)
            if not parsed:
                await self.tg.send_message(
                    chat_id,
                    "⏱ اكتب المدة هكذا مثلاً:\n<b>برايد اكتمني ساعة</b>\n<b>برايد اكتمني 30 دقيقة</b>\n<b>برايد اكتمني ساعتين</b>",
                    reply_to=msg["message_id"],
                )
                return
            seconds, label = parsed
            await self.mute_user(chat_id, user_id, seconds, label, muted_by=user_id, self_request=True, target_user=user)
            return

        # Slash commands.
        if not text.startswith("/"):
            return
        first, *rest = text.split(maxsplit=1)
        command = first.split("@", 1)[0].lower()
        args = rest[0].strip() if rest else ""

        if command in {"/help", "/start"}:
            await self.send_help(chat_id, msg["message_id"])
        elif command == "/clearme":
            await self.clear_user_messages(chat_id, user_id, requester=user)
        elif command == "/check":
            await self.check_bot_rights(chat_id, msg["message_id"])
        elif command in {"/mute", "/unmute", "/clearuser", "/status"}:
            if not await self.is_admin(chat_id, user_id):
                await self.tg.send_message(chat_id, "⛔ هذا الأمر للإدمنية فقط.", reply_to=msg["message_id"])
                return
            target_id, target_user = await self.resolve_target(msg, args)
            if target_id is None:
                await self.tg.send_message(
                    chat_id,
                    "↩️ استخدم الأمر بالرد على رسالة العضو. ويمكن أيضاً استعمال @username إذا سبق للبوت أن شاهد العضو.",
                    reply_to=msg["message_id"],
                )
                return

            if command == "/mute":
                duration_text = self.remove_target_token(args)
                parsed = parse_duration(duration_text or "ساعة")
                if not parsed:
                    await self.tg.send_message(chat_id, "⏱ مثال: <code>/mute 30m</code> أو <code>/mute ساعتين</code> بالرد على العضو.")
                    return
                seconds, label = parsed
                await self.mute_user(chat_id, target_id, seconds, label, muted_by=user_id, target_user=target_user)
            elif command == "/unmute":
                await self.unmute_user(chat_id, target_id, actor_id=user_id, target_user=target_user)
            elif command == "/clearuser":
                await self.clear_user_messages(chat_id, target_id, requester=target_user or {"id": target_id, "first_name": str(target_id)})
            else:
                await self.show_status(chat_id, target_id, target_user, msg["message_id"])

    def remove_target_token(self, args: str) -> str:
        parts = args.split()
        if parts and (parts[0].startswith("@") or parts[0].lstrip("-").isdigit()):
            return " ".join(parts[1:])
        return args

    async def resolve_target(self, msg: dict, args: str):
        reply = msg.get("reply_to_message") or {}
        if reply.get("from") and not reply["from"].get("is_bot"):
            return int(reply["from"]["id"]), reply["from"]
        if args:
            token = args.split()[0]
            uid = self.db.resolve_user(int(msg["chat"]["id"]), token)
            if uid is not None:
                return uid, await self.get_member_user(int(msg["chat"]["id"]), uid)
        return None, None

    async def get_member_user(self, chat_id: int, user_id: int) -> dict | None:
        try:
            member = await self.tg.call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
            return member.get("user")
        except Exception:
            return None

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            m = await self.tg.call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
            return m.get("status") in {"administrator", "creator"}
        except Exception:
            return False

    async def is_target_admin(self, chat_id: int, user_id: int) -> bool:
        return await self.is_admin(chat_id, user_id)

    async def clear_user_messages(self, chat_id: int, user_id: int, requester: dict, reply_to: int | None = None):
        cutoff = int(time.time()) - SAFE_DELETE_WINDOW
        ids = self.db.get_recent_message_ids(chat_id, user_id, cutoff)
        if not ids:
            await self.tg.send_message(chat_id, f"🧹 ما عندي رسائل قابلة للحذف حالياً لـ {mention(requester)}.", reply_to=reply_to)
            return

        deleted: list[int] = []
        failed = 0
        for batch in chunked(ids, 100):
            try:
                await self.tg.delete_messages(chat_id, batch)
                deleted.extend(batch)
            except TelegramAPIError:
                # Fallback makes one bad/deleted ID unable to stop the whole cleanup.
                for mid in batch:
                    try:
                        await self.tg.delete_message(chat_id, mid)
                        deleted.append(mid)
                    except TelegramAPIError:
                        failed += 1
                    await asyncio.sleep(0.02)
        self.db.remove_messages(chat_id, deleted)
        await self.tg.send_message(
            chat_id,
            f"🧹 تم حذف <b>{len(deleted)}</b> رسالة متاحة لـ {mention(requester)}" + (f". تعذّر حذف {failed} رسالة." if failed else "."),
        )

    async def mute_user(
        self,
        chat_id: int,
        user_id: int,
        seconds: int,
        label: str,
        muted_by: int,
        self_request: bool = False,
        target_user: dict | None = None,
    ):
        if await self.is_target_admin(chat_id, user_id):
            await self.tg.send_message(chat_id, "⚠️ Telegram لا يسمح للبوت بكتم مالك الكروب أو أحد الإدمنية.")
            return

        until_ts = int(time.time()) + seconds
        permissions = {
            "can_send_messages": False,
            "can_send_audios": False,
            "can_send_documents": False,
            "can_send_photos": False,
            "can_send_videos": False,
            "can_send_video_notes": False,
            "can_send_voice_notes": False,
            "can_send_polls": False,
            "can_send_other_messages": False,
            "can_add_web_page_previews": False,
            "can_change_info": False,
            "can_invite_users": False,
            "can_pin_messages": False,
            "can_manage_topics": False,
            "can_edit_tag": False,
        }
        try:
            await self.tg.call(
                "restrictChatMember",
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "permissions": permissions,
                    "until_date": until_ts,
                    "use_independent_chat_permissions": True,
                },
            )
        except TelegramAPIError as e:
            await self.tg.send_message(chat_id, f"❌ ما كدرت أكتم العضو. تأكد أن البوت Admin وعنده <b>Restrict members</b>.\n<code>{html.escape(e.description)}</code>")
            return

        self.db.save_mute(chat_id, user_id, until_ts, muted_by, "self" if self_request else "admin")
        target_user = target_user or {"id": user_id, "first_name": str(user_id)}
        button = {
            "inline_keyboard": [[{"text": "🔓 إلغاء الكتم — Admin", "callback_data": f"unmute:{chat_id}:{user_id}"}]]
        }
        if self_request:
            message_text = (
                f"🔇 {mention(target_user)} تم تقييدك لمدة <b>{html.escape(label)}</b>.\n"
                f"⏰ ينتهي التقييد تلقائياً: <code>{datetime.fromtimestamp(until_ts, ZoneInfo('Asia/Baghdad')).strftime('%Y-%m-%d %H:%M')}</code>"
            )
        else:
            message_text = (
                f"🔇 تم تقييد {mention(target_user)} لمدة <b>{html.escape(label)}</b> بأمر Admin.\n"
                f"⏰ ينتهي التقييد تلقائياً: <code>{datetime.fromtimestamp(until_ts, ZoneInfo('Asia/Baghdad')).strftime('%Y-%m-%d %H:%M')}</code>"
            )
        await self.tg.send_message(
            chat_id,
            message_text,
            reply_markup=button,
        )

    async def unmute_user(self, chat_id: int, user_id: int, actor_id: int, target_user: dict | None = None):
        if not await self.is_admin(chat_id, actor_id):
            return False
        try:
            chat = await self.tg.call("getChat", {"chat_id": chat_id})
            permissions = chat.get("permissions") or {
                "can_send_messages": True,
                "can_send_audios": True,
                "can_send_documents": True,
                "can_send_photos": True,
                "can_send_videos": True,
                "can_send_video_notes": True,
                "can_send_voice_notes": True,
                "can_send_polls": True,
                "can_send_other_messages": True,
                "can_add_web_page_previews": True,
            }
            await self.tg.call(
                "restrictChatMember",
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "permissions": permissions,
                    "use_independent_chat_permissions": True,
                },
            )
        except TelegramAPIError as e:
            await self.tg.send_message(chat_id, f"❌ تعذر فك الكتم: <code>{html.escape(e.description)}</code>")
            return False
        self.db.clear_mute(chat_id, user_id)
        target_user = target_user or await self.get_member_user(chat_id, user_id) or {"id": user_id, "first_name": str(user_id)}
        await self.tg.send_message(chat_id, f"🔊 تم إلغاء كتم {mention(target_user)} بواسطة Admin.")
        return True

    async def handle_callback(self, cq: dict):
        data = cq.get("data") or ""
        if not data.startswith("unmute:"):
            return
        actor = cq.get("from") or {}
        try:
            _, chat_s, user_s = data.split(":", 2)
            chat_id, user_id = int(chat_s), int(user_s)
        except ValueError:
            return
        if not await self.is_admin(chat_id, int(actor["id"])):
            await self.tg.call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "للإدمنية فقط", "show_alert": True})
            return
        await self.tg.call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "جاري إلغاء الكتم"})
        await self.unmute_user(chat_id, user_id, int(actor["id"]))

    async def show_status(self, chat_id: int, user_id: int, target_user: dict | None, reply_to: int):
        target_user = target_user or await self.get_member_user(chat_id, user_id) or {"id": user_id, "first_name": str(user_id)}
        row = self.db.get_mute(chat_id, user_id)
        if not row or int(row["until_ts"]) <= int(time.time()):
            await self.tg.send_message(chat_id, f"🔊 {mention(target_user)} لا يوجد له كتم فعّال مسجل عند البوت.", reply_to=reply_to)
            return
        remaining = int(row["until_ts"]) - int(time.time())
        mins = max(1, remaining // 60)
        await self.tg.send_message(chat_id, f"🔇 {mention(target_user)} مكتوم حالياً. المتبقي تقريباً <b>{mins} دقيقة</b>.", reply_to=reply_to)

    async def check_bot_rights(self, chat_id: int, reply_to: int):
        if not self.bot_user:
            return
        try:
            m = await self.tg.call("getChatMember", {"chat_id": chat_id, "user_id": self.bot_user["id"]})
        except TelegramAPIError as e:
            await self.tg.send_message(chat_id, f"❌ فشل فحص الصلاحيات: <code>{html.escape(e.description)}</code>")
            return
        status = m.get("status")
        delete_ok = bool(m.get("can_delete_messages")) or status == "creator"
        restrict_ok = bool(m.get("can_restrict_members")) or status == "creator"
        text = (
            "🛠 <b>فحص صلاحيات Pride Bot</b>\n\n"
            f"Admin: {'✅' if status in {'administrator','creator'} else '❌'}\n"
            f"Delete messages: {'✅' if delete_ok else '❌'}\n"
            f"Restrict members: {'✅' if restrict_ok else '❌'}\n\n"
            "حتى يشتغل الحذف والكتم لازم الثلاثة تكون ✅."
        )
        await self.tg.send_message(chat_id, text, reply_to=reply_to)

    async def send_help(self, chat_id: int, reply_to: int):
        text = (
            "🤖 <b>Pride Group Manager</b>\n\n"
            "<b>للأعضاء:</b>\n"
            "• <code>برايد امسح رسائلي</code> — يحذف رسائلك التي سجلها البوت وما زال Telegram يسمح بحذفها.\n"
            "• <code>برايد اكتمني ساعة</code>\n"
            "• <code>برايد اكتمني 30 دقيقة</code>\n"
            "• <code>/clearme</code>\n\n"
            "<b>للإدمنية — بالرد على رسالة العضو:</b>\n"
            "• <code>/mute 30m</code> أو <code>/mute ساعتين</code>\n"
            "• <code>/unmute</code>\n"
            "• <code>/clearuser</code>\n"
            "• <code>/status</code>\n"
            "• <code>/check</code> — فحص صلاحيات البوت\n\n"
            "ℹ️ الحذف يشمل الرسائل التي شاهدها البوت، وTelegram يسمح عادةً بحذف الرسائل خلال 48 ساعة فقط."
        )
        await self.tg.send_message(chat_id, text, reply_to=reply_to)
