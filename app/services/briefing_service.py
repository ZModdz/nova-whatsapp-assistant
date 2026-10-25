"""Daily Briefing generator combining Calendar Events, Tasks, Reminders, and Notes."""

from datetime import datetime, timedelta

from app.core.config import Settings, get_settings
from app.db.database import Database
from app.services.calendar_service import CalendarService
from app.services.memory_service import MemoryService
from app.services.reminder_service import ReminderService
from app.services.task_service import PRIORITY_EMOJI, TaskService


class BriefingService:
    """Builds daily and morning briefings across all JARVIS subsystems."""

    def __init__(
        self,
        db: Database,
        calendar_service: CalendarService,
        task_service: TaskService,
        reminder_service: ReminderService,
        memory_service: MemoryService,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.calendar = calendar_service
        self.tasks = task_service
        self.reminders = reminder_service
        self.memory = memory_service
        self.settings = settings or get_settings()
        self.tz = self.settings.tz

    def generate_briefing(self) -> str:
        """Compile a comprehensive, concise daily briefing in Asia/Kuala_Lumpur time."""
        now = datetime.now(self.tz)
        greeting = (
            "Good morning"
            if now.hour < 12
            else ("Good afternoon" if now.hour < 18 else "Good evening")
        )
        date_header = now.strftime("%A, %d %B %Y")
        sections: list[str] = [
            f"☀️ *{greeting}! Here is your NOVA Briefing for {date_header}:*"
        ]

        # 1. Today's Calendar Events
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        if self.calendar.is_configured():
            ok, events, _ = self.calendar.list_events(start_of_day, end_of_day)
            if ok and events:
                ev_lines = ["\n📅 *Today's Schedule:*"]
                for idx, ev in enumerate(events, start=1):
                    raw_s = ev["start"]
                    try:
                        if "T" in raw_s:
                            s_dt = datetime.fromisoformat(raw_s).astimezone(self.tz)
                            t_label = s_dt.strftime("%I:%M %p")
                        else:
                            t_label = "All Day"
                    except Exception:
                        t_label = raw_s
                    ev_lines.append(f"{idx}. {t_label} — *{ev['title']}*")
                sections.append("\n".join(ev_lines))
            else:
                sections.append("\n📅 *Today's Schedule:*\n• No calendar events today.")
        else:
            sections.append(
                "\n📅 *Today's Schedule:*\n• _(Google Calendar not connected yet)_"
            )

        # 2. High-Priority & Overdue Tasks + Upcoming Deadlines
        all_pending = self.tasks.list_tasks("all")
        overdue = self.tasks.list_tasks("overdue")
        top_tasks = all_pending[:4]

        if top_tasks:
            t_lines = [f"\n📋 *Priority Tasks ({len(all_pending)} remaining):*"]
            for t in top_tasks:
                badge = PRIORITY_EMOJI.get(t.get("priority", "medium"), "🟡")
                due_tag = ""
                if t.get("due_at"):
                    try:
                        d_dt = datetime.fromisoformat(t["due_at"]).astimezone(self.tz)
                        is_ov = " ⚠️ *OVERDUE*" if d_dt < now else ""
                        due_tag = f" (Due {d_dt.strftime('%a %d %b')}{is_ov})"
                    except ValueError:
                        pass
                t_lines.append(f"• #{t['id']} {badge} *{t['title']}*{due_tag}")
            if overdue:
                t_lines.append(f"⚠️ _You have {len(overdue)} overdue task(s)._")
            sections.append("\n".join(t_lines))
        else:
            sections.append("\n📋 *Tasks:*\n• All caught up! Zero pending tasks.")

        # 3. Reminders Due Soon (Next 24 Hours)
        pending_rems = self.reminders.list_pending_reminders()
        cutoff = (now + timedelta(hours=24)).isoformat(timespec="seconds")
        soon_rems = [r for r in pending_rems if r["remind_at"] <= cutoff]
        if soon_rems:
            r_lines = ["\n⏰ *Reminders Due in Next 24h:*"]
            for r in soon_rems[:5]:
                try:
                    r_dt = datetime.fromisoformat(r["remind_at"]).astimezone(self.tz)
                    r_time = r_dt.strftime("%I:%M %p")
                except ValueError:
                    r_time = r["remind_at"]
                r_lines.append(f"• {r_time} — *{r['title']}*")
            sections.append("\n".join(r_lines))

        # 4. Important Saved Notes
        notes = self.memory.list_memories(category="note")
        if notes:
            n_lines = ["\n📝 *Saved Notes:*"]
            for n in notes[:3]:
                n_lines.append(f"• {n['value']}")
            sections.append("\n".join(n_lines))

        return "\n".join(sections)
