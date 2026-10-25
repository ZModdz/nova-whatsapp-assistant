"""Personal Task Manager service backed by SQLite."""

from datetime import datetime, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from app.core.config import Settings, get_settings
from app.db.database import Database


PRIORITY_RANK = {"urgent": 1, "high": 2, "medium": 3, "low": 4}
PRIORITY_EMOJI = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}


class TaskService:
    """Manages tasks, priorities, deadlines, categories, recurring tasks, and overdue detection."""

    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.tz = self.settings.tz

    def add_task(
        self,
        title: str,
        priority: str = "medium",
        category: str = "general",
        due_at_iso: str | None = None,
        description: str = "",
        recurrence: str | None = None,
        calendar_event_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new task in SQLite and return the created record."""
        norm_priority = priority.lower().strip()
        if norm_priority not in PRIORITY_RANK:
            norm_priority = "medium"

        norm_due: str | None = None
        if due_at_iso:
            try:
                dt = datetime.fromisoformat(due_at_iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=self.tz)
                else:
                    dt = dt.astimezone(self.tz)
                norm_due = dt.isoformat(timespec="seconds")
            except ValueError:
                norm_due = None

        now = self.db.now_iso()
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks (title, description, priority, category, status, due_at, recurrence, calendar_event_id, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    title.strip(),
                    description.strip(),
                    norm_priority,
                    (category or "general").strip().lower(),
                    norm_due,
                    recurrence,
                    calendar_event_id,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid or 0)
        return self.get_task(task_id) or {}

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def find_pending_tasks_by_query(self, query: str) -> list[dict[str, Any]]:
        q = f"%{query.strip().lower()}%"
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'pending'
                  AND (LOWER(title) LIKE ? OR LOWER(category) LIKE ? OR LOWER(description) LIKE ?)
                ORDER BY id ASC
                """,
                (q, q, q),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_tasks(
        self,
        filter_type: str = "all",
        category: str | None = None,
        search_query: str | None = None,
    ) -> list[dict[str, Any]]:
        """List pending tasks sorted by priority (urgent->low) and due date."""
        now = datetime.now(self.tz)
        now_iso = now.isoformat(timespec="seconds")
        end_of_today = now.replace(hour=23, minute=59, second=59).isoformat(timespec="seconds")

        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'pending'"
            ).fetchall()

        tasks = [dict(r) for r in rows]

        if category:
            cat_lower = category.strip().lower()
            tasks = [t for t in tasks if t.get("category", "").lower() == cat_lower]

        if search_query:
            q_lower = search_query.strip().lower()
            tasks = [
                t
                for t in tasks
                if q_lower in t["title"].lower()
                or q_lower in (t.get("description") or "").lower()
                or q_lower in (t.get("category") or "").lower()
            ]

        if filter_type == "overdue":
            tasks = [t for t in tasks if t.get("due_at") and t["due_at"] < now_iso]
        elif filter_type == "today":
            tasks = [
                t
                for t in tasks
                if not t.get("due_at") or t["due_at"] <= end_of_today
            ]

        tasks.sort(
            key=lambda t: (
                PRIORITY_RANK.get(t.get("priority", "medium"), 3),
                t.get("due_at") or "9999-12-31",
                t.get("id", 0),
            )
        )

        if filter_type == "top3":
            return tasks[:3]
        return tasks

    def complete_task(
        self, task_id: int | None = None, search_query: str | None = None
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """
        Mark a task as completed. If it has a recurrence rule, automatically spawn the next occurrence.
        """
        target: dict[str, Any] | None = None
        if task_id:
            target = self.get_task(task_id)
        elif search_query:
            matches = self.find_pending_tasks_by_query(search_query)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                titles = ", ".join(f"#{m['id']} ({m['title']})" for m in matches[:5])
                return (
                    False,
                    f"Multiple tasks match '{search_query}': {titles}. Please specify the task number (e.g., 'Complete task #{matches[0]['id']}').",
                    None,
                )

        if not target:
            return False, "Could not find a matching pending task.", None

        now_iso = self.db.now_iso()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ?",
                (now_iso, target["id"]),
            )

        next_msg = ""
        recurrence = (target.get("recurrence") or "").lower().strip()
        if recurrence in ("daily", "weekly", "monthly") and target.get("due_at"):
            try:
                old_due = datetime.fromisoformat(target["due_at"])
                if recurrence == "daily":
                    new_due = old_due + timedelta(days=1)
                elif recurrence == "weekly":
                    new_due = old_due + timedelta(weeks=1)
                else:
                    new_due = old_due + relativedelta(months=1)
                spawned = self.add_task(
                    title=target["title"],
                    priority=target["priority"],
                    category=target["category"],
                    due_at_iso=new_due.isoformat(timespec="seconds"),
                    description=target.get("description", ""),
                    recurrence=recurrence,
                )
                next_msg = f" (Recurring {recurrence} task #{spawned['id']} scheduled for {new_due.strftime('%d %b %Y %I:%M %p')})."
            except Exception:
                pass

        return (
            True,
            f"✅ Marked task *#{target['id']} {target['title']}* as completed!{next_msg}",
            target,
        )

    def edit_task(
        self,
        task_id: int | None = None,
        search_query: str | None = None,
        new_title: str | None = None,
        new_priority: str | None = None,
        new_due_at_iso: str | None = None,
    ) -> tuple[bool, str]:
        """Update an existing task's title, priority, or deadline."""
        target: dict[str, Any] | None = None
        if task_id:
            target = self.get_task(task_id)
        elif search_query:
            matches = self.find_pending_tasks_by_query(search_query)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                titles = ", ".join(f"#{m['id']} ({m['title']})" for m in matches[:5])
                return (
                    False,
                    f"Multiple tasks match '{search_query}': {titles}. Please specify the task number.",
                )

        if not target:
            return False, "Could not find a matching task to edit."

        title = new_title.strip() if new_title else target["title"]
        priority = (
            new_priority.lower().strip()
            if (new_priority and new_priority.lower().strip() in PRIORITY_RANK)
            else target["priority"]
        )
        due_at = target.get("due_at")
        if new_due_at_iso:
            try:
                dt = datetime.fromisoformat(new_due_at_iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=self.tz)
                else:
                    dt = dt.astimezone(self.tz)
                due_at = dt.isoformat(timespec="seconds")
            except ValueError:
                pass

        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tasks SET title = ?, priority = ?, due_at = ? WHERE id = ?",
                (title, priority, due_at, target["id"]),
            )
        return True, f"✏️ Updated task *#{target['id']}* -> *{title}* (Priority: {priority})."

    def delete_task(
        self, task_id: int | None = None, search_query: str | None = None
    ) -> tuple[bool, str]:
        """Delete a task by ID or search query."""
        target: dict[str, Any] | None = None
        if task_id:
            target = self.get_task(task_id)
        elif search_query:
            matches = self.find_pending_tasks_by_query(search_query)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                titles = ", ".join(f"#{m['id']} ({m['title']})" for m in matches[:5])
                return (
                    False,
                    f"Multiple tasks match '{search_query}': {titles}. Please specify the task number.",
                )

        if not target:
            return False, "Could not find a matching task to delete."

        with self.db.connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (target["id"],))
        return True, f"🗑️ Deleted task *#{target['id']} {target['title']}*."

    def format_tasks_for_whatsapp(
        self, tasks: list[dict[str, Any]], header: str = "📋 *Your Pending Tasks*"
    ) -> str:
        """Render a clean WhatsApp summary of tasks with priority badges and overdue warnings."""
        if not tasks:
            return f"{header}\n🎉 You have no matching pending tasks!"

        now = datetime.now(self.tz)
        lines = [header]
        for t in tasks:
            badge = PRIORITY_EMOJI.get(t.get("priority", "medium"), "🟡")
            due_str = ""
            if t.get("due_at"):
                try:
                    due_dt = datetime.fromisoformat(t["due_at"]).astimezone(self.tz)
                    overdue_flag = " ⚠️ *OVERDUE*" if due_dt < now else ""
                    due_str = f" — Due: {due_dt.strftime('%d %b, %I:%M %p')}{overdue_flag}"
                except ValueError:
                    due_str = f" — Due: {t['due_at']}"
            cat_str = f" [{t['category']}]" if t.get("category") and t["category"] != "general" else ""
            lines.append(f"• *#{t['id']}* {badge} *{t['title']}*{cat_str}{due_str}")
        return "\n".join(lines)
