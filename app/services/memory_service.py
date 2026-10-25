"""Controlled Long-Term Memory Service backed by SQLite."""

from typing import Any

from app.db.database import Database


VALID_MEMORY_CATEGORIES = ("preference", "project", "routine", "note", "fact")


class MemoryService:
    """Manages persistent user preferences, routines, projects, and notes with privacy controls."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def is_memory_enabled(self) -> bool:
        """Check whether long-term memory is enabled (default True)."""
        return self.db.get_state("memory_enabled", "true") == "true"

    def set_memory_enabled(self, enabled: bool) -> str:
        self.db.set_state("memory_enabled", "true" if enabled else "false")
        state_str = "enabled" if enabled else "disabled"
        return f"🧠 Long-term memory is now *{state_str}*."

    def save_memory(
        self, key: str, value: str, category: str = "fact"
    ) -> tuple[bool, str]:
        """Save or update a specific long-term memory entry."""
        if not self.is_memory_enabled():
            return (
                False,
                "⚠️ Long-term memory is currently disabled. Send `MEMORY ON` to enable it first.",
            )

        norm_cat = (category or "fact").strip().lower()
        if norm_cat not in VALID_MEMORY_CATEGORIES:
            norm_cat = "fact"

        norm_key = (
            key.strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")[:64]
        )
        if not norm_key or not value.strip():
            return False, "Both memory key and value are required."

        now = self.db.now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (category, key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    category = excluded.category,
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (norm_cat, norm_key, value.strip(), now, now),
            )
        return (
            True,
            f"🧠 Remembered under *{norm_cat}* (`{norm_key}`): {value.strip()}",
        )

    def list_memories(self, category: str | None = None) -> list[dict[str, Any]]:
        """List all stored memories, optionally filtered by category."""
        with self.db.connect() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM memories WHERE category = ? ORDER BY category, key",
                    (category.lower().strip(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memories ORDER BY category, key"
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_memory(self, key_or_query: str) -> tuple[bool, str]:
        """Delete a stored memory by key or matching value substring."""
        q = key_or_query.strip().lower()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE LOWER(key) = ? OR LOWER(value) LIKE ?",
                (q.replace(" ", "_"), f"%{q}%"),
            ).fetchone()
            if not row:
                return False, f"Could not find any stored memory matching '{key_or_query}'."
            conn.execute("DELETE FROM memories WHERE id = ?", (row["id"],))
        return True, f"🗑️ Forgot memory `{row['key']}` ({row['value']})."

    def clear_all_memories(self) -> str:
        """Delete all stored long-term memories after confirmation."""
        with self.db.connect() as conn:
            conn.execute("DELETE FROM memories")
        return "🗑️ All stored long-term memories have been permanently deleted."

    def format_memories_for_prompt(self) -> str:
        """Format memories concisely for injection into the LLM system prompt."""
        if not self.is_memory_enabled():
            return "(Long-term memory is disabled by user.)"
        memories = self.list_memories()
        if not memories:
            return "None saved yet."
        lines = []
        for m in memories:
            lines.append(f"- [{m['category']}] {m['key']}: {m['value']}")
        return "\n".join(lines)

    def format_memories_for_whatsapp(self) -> str:
        """Render all stored memories for user inspection via WhatsApp."""
        status = "ENABLED ✅" if self.is_memory_enabled() else "DISABLED 🚫"
        memories = self.list_memories()
        if not memories:
            return f"🧠 *NOVA Long-Term Memory ({status})*\nNo memories stored yet."

        lines = [f"🧠 *NOVA Long-Term Memory ({status})*"]
        for m in memories:
            lines.append(f"• *[{m['category']}]* `{m['key']}`: {m['value']}")
        lines.append(
            "\n_Tip: Say 'Forget [key]' to delete an item, or 'MEMORY OFF' to disable memory._"
        )
        return "\n".join(lines)
