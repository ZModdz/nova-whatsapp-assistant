"""SQLite database management with WAL mode, schema initialization, and deduplication."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from app.core.config import get_settings
from app.core.logging_config import logger


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1. Processed Webhook Messages (Deduplication to prevent duplicate execution)
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    sender_phone TEXT NOT NULL,
    received_at TEXT NOT NULL
);

-- 2. Application State (24h WhatsApp Window Tracker, Keepalive Flag, Memory Toggle, etc.)
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 3. Short-Term Conversation Context (Rolling chat history for natural follow-ups)
CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 4. Pending Confirmations (Confirmation gate for event creation/deletion/updates)
CREATE TABLE IF NOT EXISTS pending_confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- 5. Tasks (Personal Task Manager)
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low', 'medium', 'high', 'urgent')),
    category TEXT DEFAULT 'general',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed', 'cancelled')),
    due_at TEXT,
    recurrence TEXT,
    calendar_event_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- 6. Persistent Reminders
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    recurrence TEXT,
    category TEXT DEFAULT 'general',
    task_id INTEGER,
    calendar_event_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed', 'cancelled')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    delivery_channel TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

-- 7. Long-Term Memory (Controlled preferences, projects, notes, routines)
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'fact' CHECK(category IN ('preference', 'project', 'routine', 'note', 'fact')),
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    """Thread-safe SQLite database wrapper using WAL mode."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        settings = get_settings()
        self.db_path = Path(db_path) if db_path else settings.db_file
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.tz = settings.tz
        self.init_db()

    def now_iso(self) -> str:
        """Return current timestamp in Asia/Kuala_Lumpur ISO-8601 format."""
        return datetime.now(self.tz).isoformat(timespec="seconds")

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a SQLite connection with Row factory and automatic commit/rollback."""
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Initialize all tables and default settings."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.debug("SQLite database initialized at %s", self.db_path)

    # --- Webhook Deduplication ---
    def is_message_processed(self, message_id: str) -> bool:
        """Check if a WhatsApp message_id has already been handled."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return row is not None

    def mark_message_processed(self, message_id: str, sender_phone: str) -> bool:
        """
        Atomically record a message_id as processed.
        Returns True if newly inserted, False if it was already present.
        """
        now = self.now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO processed_messages (message_id, sender_phone, received_at)
                VALUES (?, ?, ?)
                """,
                (message_id, sender_phone, now),
            )
            return cursor.rowcount == 1

    # --- App State Key-Value Helpers ---
    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (key,),
            ).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        now = self.now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, str(value), now),
            )

    # --- 24-Hour WhatsApp Customer Service Window Tracking ---
    def record_user_inbound_timestamp(self) -> None:
        """Record the timestamp of the user's latest incoming WhatsApp message."""
        self.set_state("last_inbound_whatsapp_at", self.now_iso())
        self.set_state("keepalive_sent_for_window", "false")

    def get_last_inbound_timestamp(self) -> datetime | None:
        val = self.get_state("last_inbound_whatsapp_at")
        if not val:
            return None
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None

    # --- Short-Term Conversation Context ---
    def add_conversation_turn(self, role: str, content: str, max_history: int = 16) -> None:
        """Store a chat message and prune old history so the DB does not grow indefinitely."""
        now = self.now_iso()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversation_messages (role, content, created_at) VALUES (?, ?, ?)",
                (role, content, now),
            )
            conn.execute(
                """
                DELETE FROM conversation_messages
                WHERE id NOT IN (
                    SELECT id FROM conversation_messages ORDER BY id DESC LIMIT ?
                )
                """,
                (max_history,),
            )

    def get_recent_conversation(self, limit: int = 10) -> list[dict[str, str]]:
        """Retrieve the most recent conversation turns in chronological order."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM conversation_messages
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (limit,),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    def clear_conversation(self) -> None:
        """Clear rolling conversation history."""
        with self.connect() as conn:
            conn.execute("DELETE FROM conversation_messages")

    # --- Pending Confirmations (Safety Gate for Consequential Actions) ---
    def set_pending_confirmation(
        self,
        action_type: str,
        payload: dict[str, Any],
        summary_text: str,
        expires_at_iso: str,
    ) -> int:
        """Save a pending confirmation (replaces any prior pending confirmation)."""
        now = self.now_iso()
        with self.connect() as conn:
            conn.execute("DELETE FROM pending_confirmations")
            cursor = conn.execute(
                """
                INSERT INTO pending_confirmations (action_type, payload_json, summary_text, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (action_type, json.dumps(payload), summary_text, now, expires_at_iso),
            )
            return int(cursor.lastrowid or 0)

    def get_pending_confirmation(self) -> dict[str, Any] | None:
        """Return active, non-expired pending confirmation if one exists."""
        now = datetime.now(self.tz)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_confirmations ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if now > expires_at:
                conn.execute("DELETE FROM pending_confirmations WHERE id = ?", (row["id"],))
                return None
            return {
                "id": row["id"],
                "action_type": row["action_type"],
                "payload": json.loads(row["payload_json"]),
                "summary_text": row["summary_text"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }

    def clear_pending_confirmation(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM pending_confirmations")
