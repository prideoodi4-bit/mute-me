from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                sent_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_user
                ON messages(chat_id, user_id, sent_at);

            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                last_seen INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_users_username
                ON users(chat_id, username);

            CREATE TABLE IF NOT EXISTS mutes (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                until_ts INTEGER NOT NULL,
                muted_by INTEGER,
                reason TEXT,
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
        self.conn.commit()

    def track_message(self, chat_id: int, user: dict, message_id: int, sent_at: int) -> None:
        user_id = int(user["id"])
        self.conn.execute(
            "INSERT OR REPLACE INTO messages(chat_id,user_id,message_id,sent_at) VALUES(?,?,?,?)",
            (chat_id, user_id, message_id, sent_at),
        )
        self.conn.execute(
            """
            INSERT INTO users(chat_id,user_id,username,first_name,last_name,last_seen)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              username=excluded.username,
              first_name=excluded.first_name,
              last_name=excluded.last_name,
              last_seen=excluded.last_seen
            """,
            (
                chat_id,
                user_id,
                (user.get("username") or "").lower() or None,
                user.get("first_name"),
                user.get("last_name"),
                int(time.time()),
            ),
        )
        self.conn.commit()

    def get_recent_message_ids(self, chat_id: int, user_id: int, cutoff: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT message_id FROM messages WHERE chat_id=? AND user_id=? AND sent_at>=? ORDER BY message_id",
            (chat_id, user_id, cutoff),
        ).fetchall()
        return [int(r["message_id"]) for r in rows]

    def remove_messages(self, chat_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        self.conn.execute(
            f"DELETE FROM messages WHERE chat_id=? AND message_id IN ({placeholders})",
            [chat_id, *message_ids],
        )
        self.conn.commit()

    def resolve_user(self, chat_id: int, value: str) -> int | None:
        v = value.strip()
        if v.startswith("@"):
            username = v[1:].lower()
            row = self.conn.execute(
                "SELECT user_id FROM users WHERE chat_id=? AND username=? ORDER BY last_seen DESC LIMIT 1",
                (chat_id, username),
            ).fetchone()
            return int(row["user_id"]) if row else None
        if v.lstrip("-").isdigit():
            return int(v)
        return None

    def save_mute(self, chat_id: int, user_id: int, until_ts: int, muted_by: int | None, reason: str) -> None:
        self.conn.execute(
            """
            INSERT INTO mutes(chat_id,user_id,until_ts,muted_by,reason)
            VALUES(?,?,?,?,?)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              until_ts=excluded.until_ts,
              muted_by=excluded.muted_by,
              reason=excluded.reason
            """,
            (chat_id, user_id, until_ts, muted_by, reason),
        )
        self.conn.commit()

    def clear_mute(self, chat_id: int, user_id: int) -> None:
        self.conn.execute("DELETE FROM mutes WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        self.conn.commit()

    def get_mute(self, chat_id: int, user_id: int):
        return self.conn.execute(
            "SELECT * FROM mutes WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()

    def prune(self, older_than: int) -> int:
        cur = self.conn.execute("DELETE FROM messages WHERE sent_at<?", (older_than,))
        self.conn.execute("DELETE FROM mutes WHERE until_ts<?", (int(time.time()) - 86400,))
        self.conn.commit()
        return cur.rowcount
