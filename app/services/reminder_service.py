"""Persistent Reminder & Notification Service with startup recovery, quiet hours, and retry logic."""

from datetime import datetime, time, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from app.core.config import Settings, get_settings
from app.core.logging_config import logger
from app.db.database import Database
from app.services.whatsapp_service import WhatsAppService


MAX_DELIVERY_RETRIES = 3


class ReminderService:
    """Manages SQLite-persisted reminders, quiet hours, startup recovery, and recurring schedules."""

    def __init__(
        self,
        db: Database,
        whatsapp_service: WhatsAppService,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.whatsapp = whatsapp_service
        self.settings = settings or get_settings()
        self.tz = self.settings.tz

    def _normalize_iso(self, iso_str: str) -> datetime:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def is_quiet_hours(self, check_dt: datetime | None = None) -> bool:
        """Return True if check_dt falls inside the user's configured quiet period."""
        if not self.settings.QUIET_HOURS_ENABLED:
            return False
        now = (check_dt or datetime.now(self.tz)).astimezone(self.tz)
        try:
            sh, sm = (int(x) for x in self.settings.QUIET_HOURS_START.split(":"))
            eh, em = (int(x) for x in self.settings.QUIET_HOURS_END.split(":"))
            start_t = time(sh, sm)
            end_t = time(eh, em)
            cur_t = now.time()
            if start_t <= end_t:
                return start_t <= cur_t < end_t
            # Crosses midnight (e.g., 23:00 to 07:00)
            return cur_t >= start_t or cur_t < end_t
        except Exception:
            return False

    def create_reminder(
        self,
        title: str,
        remind_at_iso: str,
        recurrence: str | None = None,
        category: str = "general",
        task_id: int | None = None,
        calendar_event_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a new pending reminder in SQLite."""
        dt = self._normalize_iso(remind_at_iso)
        norm_remind_at = dt.isoformat(timespec="seconds")
        now_iso = self.db.now_iso()

        norm_rec = recurrence.lower().strip() if recurrence else None
        if norm_rec in ("none", "null", ""):
            norm_rec = None

        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reminders (
                    title, remind_at, recurrence, category, task_id,
                    calendar_event_id, status, retry_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)
                """,
                (
                    title.strip(),
                    norm_remind_at,
                    norm_rec,
                    (category or "general").strip().lower(),
                    task_id,
                    calendar_event_id,
                    now_iso,
                ),
            )
            rem_id = int(cursor.lastrowid or 0)
        logger.info("Created reminder #%s '%s' for %s", rem_id, title, norm_remind_at)
        return self.get_reminder(rem_id) or {}

    def get_reminder(self, reminder_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_pending_reminders(self) -> list[dict[str, Any]]:
        """Return all pending reminders ordered chronologically."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE status = 'pending' ORDER BY remind_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def find_pending_reminders(self, query: str) -> list[dict[str, Any]]:
        q = f"%{query.strip().lower()}%"
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE status = 'pending'
                  AND (LOWER(title) LIKE ? OR LOWER(remind_at) LIKE ?)
                ORDER BY remind_at ASC
                """,
                (q, q),
            ).fetchall()
            return [dict(r) for r in rows]

    def cancel_reminder(
        self, reminder_id: int | None = None, search_query: str | None = None
    ) -> tuple[bool, str]:
        """Cancel a pending reminder by ID or keyword."""
        target: dict[str, Any] | None = None
        if reminder_id:
            target = self.get_reminder(reminder_id)
        elif search_query:
            matches = self.find_pending_reminders(search_query)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                items = ", ".join(f"#{m['id']} ({m['title']})" for m in matches[:5])
                return (
                    False,
                    f"Multiple reminders match '{search_query}': {items}. Please specify the ID (e.g., 'Cancel reminder #{matches[0]['id']}').",
                )

        if not target or target.get("status") != "pending":
            return False, "Could not find a matching pending reminder to cancel."

        with self.db.connect() as conn:
            conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE id = ?",
                (target["id"],),
            )
        return True, f"🔕 Cancelled reminder *#{target['id']} ({target['title']})*."

    def postpone_reminder(
        self,
        new_remind_at_iso: str,
        reminder_id: int | None = None,
        search_query: str | None = None,
    ) -> tuple[bool, str]:
        """Postpone/reschedule a pending reminder to a new timestamp."""
        target: dict[str, Any] | None = None
        if reminder_id:
            target = self.get_reminder(reminder_id)
        elif search_query:
            matches = self.find_pending_reminders(search_query)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                items = ", ".join(f"#{m['id']} ({m['title']})" for m in matches[:5])
                return (
                    False,
                    f"Multiple reminders match '{search_query}': {items}. Please specify the ID.",
                )

        if not target:
            return False, "Could not find a matching reminder to postpone."

        new_dt = self._normalize_iso(new_remind_at_iso)
        norm_iso = new_dt.isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE reminders SET remind_at = ?, status = 'pending', retry_count = 0 WHERE id = ?",
                (norm_iso, target["id"]),
            )
        return (
            True,
            f"⏰ Postponed reminder *#{target['id']} ({target['title']})* to *{new_dt.strftime('%d %b %Y, %I:%M %p')}*.",
        )

    def _compute_next_occurrence(
        self, current_dt: datetime, recurrence: str
    ) -> datetime | None:
        rec = recurrence.lower().strip()
        now = datetime.now(self.tz)
        next_dt = current_dt
        # Advance forward until the next occurrence is strictly in the future
        for _ in range(400):
            if rec == "daily":
                next_dt = next_dt + timedelta(days=1)
            elif rec == "weekly":
                next_dt = next_dt + timedelta(weeks=1)
            elif rec == "monthly":
                next_dt = next_dt + relativedelta(months=1)
            elif rec in ("every_2_hours", "2h", "2_hours"):
                next_dt = next_dt + timedelta(hours=2)
                # Respect active daytime window (10 AM - 6 PM) if it's a daytime hydration/study interval
                if next_dt.hour > 18 or next_dt.hour < 10:
                    next_dt = (next_dt + timedelta(days=1)).replace(
                        hour=10, minute=0, second=0
                    )
            else:
                return None
            if next_dt > now:
                return next_dt
        return None

    async def process_due_reminders(self, is_startup_recovery: bool = False) -> int:
        """
        Sweep SQLite for pending reminders with remind_at <= now.
        - Respects quiet hours (unless category is 'exam' or 'urgent').
        - Prevents duplicate delivery by checking status == 'pending'.
        - Automatically retries temporary delivery failures up to MAX_DELIVERY_RETRIES.
        - Spawns the next reminder if `recurrence` is set.
        """
        now = datetime.now(self.tz)
        now_iso = now.isoformat(timespec="seconds")

        with self.db.connect() as conn:
            due_rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE status = 'pending' AND remind_at <= ?
                ORDER BY remind_at ASC
                """,
                (now_iso,),
            ).fetchall()

        delivered_count = 0
        for row in due_rows:
            rem = dict(row)
            rem_dt = self._normalize_iso(rem["remind_at"])
            is_overdue_by_minutes = (now - rem_dt).total_seconds() / 60.0

            # If currently in quiet hours and reminder is not urgent/exam, defer until quiet hours end
            if self.is_quiet_hours(now) and rem.get("category") not in ("exam", "urgent"):
                continue

            prefix = "🔔 *NOVA Reminder*"
            if is_startup_recovery or is_overdue_by_minutes > 5.0:
                prefix = f"⏰ *NOVA Overdue Reminder* _(originally scheduled for {rem_dt.strftime('%d %b, %I:%M %p')})_"

            message_body = f"{prefix}\n\n📌 *{rem['title']}*"

            ok, channel, detail = await self.whatsapp.deliver_notification(
                title=f"NOVA Reminder: {rem['title']}",
                message=message_body,
            )

            sent_iso = self.db.now_iso()
            if ok:
                delivered_count += 1
                with self.db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE reminders
                        SET status = 'sent', sent_at = ?, delivery_channel = ?, last_error = NULL
                        WHERE id = ? AND status = 'pending'
                        """,
                        (sent_iso, channel, rem["id"]),
                    )
                # If delivered via local fallback because WhatsApp 24h window was closed,
                # also record in missed WhatsApp queue so user sees it next time they chat on WhatsApp
                if "whatsapp" not in channel:
                    self._queue_missed_whatsapp_alert(
                        f"• *{rem['title']}* (was due {rem_dt.strftime('%d %b %I:%M %p')}, delivered via {channel})"
                    )

                # Spawn next recurring instance if configured
                if rem.get("recurrence"):
                    next_dt = self._compute_next_occurrence(rem_dt, rem["recurrence"])
                    if next_dt:
                        self.create_reminder(
                            title=rem["title"],
                            remind_at_iso=next_dt.isoformat(timespec="seconds"),
                            recurrence=rem["recurrence"],
                            category=rem.get("category", "general"),
                            task_id=rem.get("task_id"),
                        )
            else:
                new_retries = int(rem.get("retry_count", 0)) + 1
                new_status = "failed" if new_retries >= MAX_DELIVERY_RETRIES else "pending"
                with self.db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE reminders
                        SET status = ?, retry_count = ?, last_error = ?
                        WHERE id = ?
                        """,
                        (new_status, new_retries, detail, rem["id"]),
                    )
                if new_status == "failed":
                    self._queue_missed_whatsapp_alert(
                        f"• *{rem['title']}* (was due {rem_dt.strftime('%d %b %I:%M %p')})"
                    )

        return delivered_count

    def _queue_missed_whatsapp_alert(self, line: str) -> None:
        """Store a missed alert in app_state so JARVIS shows it immediately when the user next texts on WhatsApp."""
        existing = self.db.get_state("missed_whatsapp_alerts", "") or ""
        updated = (existing + "\n" + line).strip()
        self.db.set_state("missed_whatsapp_alerts", updated)

    def pop_missed_whatsapp_alerts(self) -> str:
        """Retrieve and clear any reminders that fired while the 24h WhatsApp window was closed."""
        alerts = self.db.get_state("missed_whatsapp_alerts", "") or ""
        if alerts:
            self.db.set_state("missed_whatsapp_alerts", "")
        return alerts

    async def check_and_send_window_keepalive(self) -> bool:
        """
        If the user's 24-hour free WhatsApp service window has < 1 hour remaining
        and there are pending reminders, send a single polite keepalive ping so they can reply.
        """
        if not self.settings.WHATSAPP_WINDOW_KEEPALIVE_ENABLED:
            return False
        if not self.whatsapp.is_configured:
            return False

        already_sent = self.db.get_state("keepalive_sent_for_window", "false") == "true"
        if already_sent:
            return False

        window_open, hours_left = self.whatsapp.is_within_24h_window()
        if not window_open or hours_left > 1.25:
            return False

        # Only bother the user if they have pending reminders or morning briefing enabled
        if not self.list_pending_reminders() and not self.settings.MORNING_BRIEFING_ENABLED:
            return False

        if self.is_quiet_hours():
            return False

        msg = (
            "👋 *NOVA Keepalive Notice*\n"
            "Meta's 24-hour free WhatsApp messaging window will close in ~1 hour. "
            "Reply with any quick message (like `ok`) whenever convenient so I can continue sending your scheduled reminders via WhatsApp for free!"
        )
        ok, _ = await self.whatsapp.send_whatsapp_text(
            self.settings.ALLOWED_WHATSAPP_NUMBER, msg
        )
        if ok:
            self.db.set_state("keepalive_sent_for_window", "true")
        return ok

    def format_reminders_for_whatsapp(
        self, reminders: list[dict[str, Any]], header: str = "⏰ *Your Pending Reminders*"
    ) -> str:
        if not reminders:
            return f"{header}\nYou have no pending reminders."

        lines = [header]
        for r in reminders:
            try:
                dt = self._normalize_iso(r["remind_at"])
                time_str = dt.strftime("%d %b %Y, %I:%M %p")
            except Exception:
                time_str = r["remind_at"]
            rec_str = f" _(Repeats: {r['recurrence']})_" if r.get("recurrence") else ""
            lines.append(f"• *#{r['id']}* *{r['title']}* — {time_str}{rec_str}")
        return "\n".join(lines)
