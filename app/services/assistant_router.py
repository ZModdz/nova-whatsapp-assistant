"""Command, Confirmation, and Tool Intent Router for JARVIS."""

from datetime import datetime, timedelta
import re
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging_config import logger
from app.db.database import Database
from app.services.briefing_service import BriefingService
from app.services.calendar_service import CalendarService
from app.services.memory_service import MemoryService
from app.services.ollama_service import OllamaService
from app.services.reminder_service import ReminderService
from app.services.task_service import TaskService


HELP_MESSAGE = """🤖 *NOVA — Natural-language Organizer & Virtual Assistant*

You can chat with me naturally in English, or use these quick commands anytime:

*Quick Shortcuts:*
• `HELP` — Show this command guide
• `BRIEFING` — Get your full daily briefing
• `TODAY` — View today's Google Calendar events
• `TOMORROW` — View tomorrow's events
• `TASKS` — List all pending tasks
• `OVERDUE` — Show overdue tasks
• `REMINDERS` — List pending reminders
• `MEMORY` — View stored long-term memories
• `MEMORY ON` / `MEMORY OFF` — Enable or disable memory
• `CANCEL` — Cancel any pending confirmation prompt

*Natural Language Examples:*
• _"Schedule 2 hours of database revision tomorrow at 8 PM"_
• _"Remind me to submit my assignment tomorrow at 9 AM"_
• _"Add a high-priority task to finish my SE report by Friday 5 PM"_
• _"Mark my database assignment as completed"_
• _"Remember that my software engineering lecture is in Room B302"_
"""


class AssistantRouter:
    """Orchestrates incoming user messages, confirmation state machines, shortcuts, and validated AI tool calls."""

    def __init__(
        self,
        db: Database,
        ollama: OllamaService,
        calendar: CalendarService,
        tasks: TaskService,
        reminders: ReminderService,
        memory: MemoryService,
        briefing: BriefingService,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.ollama = ollama
        self.calendar = calendar
        self.tasks = tasks
        self.reminders = reminders
        self.memory = memory
        self.briefing = briefing
        self.settings = settings or get_settings()
        self.tz = self.settings.tz

    async def handle_message(self, user_text: str) -> str:
        """
        Process an incoming text message from the authorized owner and return the reply string.
        Also prepends any overdue/missed alerts that fired while the 24h WhatsApp window was closed.
        """
        cleaned = user_text.strip()
        if not cleaned:
            return "Hello! How can I help you today?"

        # Record inbound timestamp to keep the 24-hour free WhatsApp service window open
        self.db.record_user_inbound_timestamp()

        # Check if any reminders fired via fallback while the 24h WhatsApp window was closed
        missed_alerts = self.reminders.pop_missed_whatsapp_alerts()
        missed_banner = ""
        if missed_alerts:
            missed_banner = (
                "📬 *Missed Alerts While WhatsApp 24h Window Was Closed:*\n"
                f"{missed_alerts}\n"
                "────────────────────\n\n"
            )

        reply = await self._route_message(cleaned)

        # Save rolling conversation context
        self.db.add_conversation_turn("user", cleaned)
        self.db.add_conversation_turn("assistant", reply)

        return f"{missed_banner}{reply}"

    async def _route_message(self, text: str) -> str:
        upper = text.upper().strip()

        # 1. Check active Pending Confirmation state machine first
        pending = self.db.get_pending_confirmation()
        if pending:
            if upper in ("YES", "Y", "CONFIRM", "SURE", "OK", "PROCEED", "DO IT"):
                self.db.clear_pending_confirmation()
                return self._execute_confirmed_action(pending)
            if upper in ("NO", "N", "CANCEL", "STOP", "ABORT", "NEVERMIND"):
                self.db.clear_pending_confirmation()
                return "🚫 Cancelled the pending action. Nothing was changed."

        # 2. Check deterministic fast shortcuts (work 100% instantly even if Ollama is offline)
        if upper in ("HELP", "/HELP", "MENU", "COMMANDS"):
            return HELP_MESSAGE
        if upper in ("CANCEL", "/CANCEL"):
            self.db.clear_pending_confirmation()
            return "🚫 Nothing pending to cancel right now."
        if upper in ("TASKS", "/TASKS", "MY TASKS"):
            items = self.tasks.list_tasks("all")
            return self.tasks.format_tasks_for_whatsapp(items)
        if upper in ("OVERDUE", "/OVERDUE"):
            items = self.tasks.list_tasks("overdue")
            return self.tasks.format_tasks_for_whatsapp(items, header="⚠️ *Overdue Tasks*")
        if upper in ("REMINDERS", "/REMINDERS"):
            rems = self.reminders.list_pending_reminders()
            return self.reminders.format_reminders_for_whatsapp(rems)
        if upper in ("BRIEFING", "/BRIEFING", "DAILY BRIEFING", "SUMMARY"):
            return self.briefing.generate_briefing()
        if upper in ("TODAY", "/TODAY"):
            return self._handle_calendar_day_shortcut(days_offset=0, label="Today")
        if upper in ("TOMORROW", "/TOMORROW"):
            return self._handle_calendar_day_shortcut(days_offset=1, label="Tomorrow")
        if upper in ("MEMORY", "MEMORIES", "/MEMORY"):
            return self.memory.format_memories_for_whatsapp()
        if upper == "MEMORY ON":
            return self.memory.set_memory_enabled(True)
        if upper == "MEMORY OFF":
            return self.memory.set_memory_enabled(False)
        if upper == "CLEAR MEMORY":
            expires = (datetime.now(self.tz) + timedelta(minutes=10)).isoformat(timespec="seconds")
            self.db.set_pending_confirmation(
                action_type="clear_all_memories",
                payload={},
                summary_text="Delete all stored long-term memories",
                expires_at_iso=expires,
            )
            return "⚠️ Are you sure you want to permanently delete *all* stored long-term memories? Reply `YES` to confirm or `NO` to cancel."

        # 3. Route natural language via Local Ollama LLM
        history = self.db.get_recent_conversation(limit=8)
        memories_prompt = self.memory.format_memories_for_prompt()
        llm_result = await self.ollama.generate_structured_response(
            user_message=text,
            conversation_history=history,
            memories_text=memories_prompt,
        )

        tool_call = llm_result.get("tool_call")
        llm_reply = llm_result.get("reply") or "How can I assist you?"

        if not tool_call or not isinstance(tool_call, dict) or not tool_call.get("name"):
            return llm_reply

        tool_name = str(tool_call.get("name", "")).strip()
        tool_args = tool_call.get("args") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        logger.info("Executing tool '%s' with args=%s", tool_name, tool_args)
        return self._execute_tool_call(tool_name, tool_args, fallback_reply=llm_reply)

    def _handle_calendar_day_shortcut(self, days_offset: int, label: str) -> str:
        if not self.calendar.is_configured():
            return (
                "📅 Google Calendar is not connected yet.\n"
                "Place `credentials/client_secret.json` and run `python scripts/setup_google_oauth.py` (see `docs/SETUP_GOOGLE_OAUTH.md`)."
            )
        target = datetime.now(self.tz) + timedelta(days=days_offset)
        start_iso = target.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_iso = target.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        ok, events, err = self.calendar.list_events(start_iso, end_iso)
        if not ok:
            return f"⚠️ Could not fetch calendar events: {err}"
        return self.calendar.format_events_for_whatsapp(
            events, header=f"📅 *{label}'s Events ({target.strftime('%a, %d %b')})*"
        )

    def _execute_tool_call(
        self, tool_name: str, args: dict[str, Any], fallback_reply: str
    ) -> str:
        now = datetime.now(self.tz)
        expires_iso = (now + timedelta(minutes=15)).isoformat(timespec="seconds")

        # --- GOOGLE CALENDAR TOOLS ---
        if tool_name == "calendar_list":
            if not self.calendar.is_configured():
                return (
                    "📅 Google Calendar is not connected yet. "
                    "Please complete `docs/SETUP_GOOGLE_OAUTH.md` and run `python scripts/setup_google_oauth.py`."
                )
            start_iso = args.get("start_iso") or now.replace(hour=0, minute=0, second=0).isoformat()
            end_iso = args.get("end_iso") or (now.replace(hour=23, minute=59, second=59)).isoformat()
            ok, events, err = self.calendar.list_events(start_iso, end_iso)
            if not ok:
                return f"⚠️ Calendar error: {err}"
            return self.calendar.format_events_for_whatsapp(events)

        if tool_name == "calendar_create":
            title = (args.get("title") or "").strip()
            start_iso = args.get("start_iso")
            end_iso = args.get("end_iso")
            if not title or not start_iso:
                return "Could you please specify both the event title and the start date/time?"

            try:
                s_dt = datetime.fromisoformat(start_iso)
                if s_dt.tzinfo is None:
                    s_dt = s_dt.replace(tzinfo=self.tz)
                else:
                    s_dt = s_dt.astimezone(self.tz)
                if end_iso:
                    e_dt = datetime.fromisoformat(end_iso)
                    if e_dt.tzinfo is None:
                        e_dt = e_dt.replace(tzinfo=self.tz)
                    else:
                        e_dt = e_dt.astimezone(self.tz)
                else:
                    e_dt = s_dt + timedelta(hours=1)
                if e_dt <= s_dt:
                    e_dt = s_dt + timedelta(hours=1)
            except ValueError:
                return "I couldn't understand that date/time format. What date and time should I schedule it for?"

            conflict_note = "I found an available period."
            if self.calendar.is_configured():
                ok, conflicts, _ = self.calendar.check_conflicts(
                    s_dt.isoformat(), e_dt.isoformat()
                )
                if ok and conflicts:
                    c_titles = ", ".join(f"'{c['title']}'" for c in conflicts)
                    conflict_note = f"⚠️ *Conflict detected:* This overlaps with {c_titles}."

            payload = {
                "title": title,
                "start_iso": s_dt.isoformat(),
                "end_iso": e_dt.isoformat(),
                "description": args.get("description") or "",
                "recurrence_freq": args.get("recurrence_freq"),
            }
            summary = f"{title} ({s_dt.strftime('%a %d %b %Y, %I:%M %p')} – {e_dt.strftime('%I:%M %p')})"
            self.db.set_pending_confirmation(
                action_type="calendar_create",
                payload=payload,
                summary_text=summary,
                expires_at_iso=expires_iso,
            )
            return (
                f"{conflict_note}\n"
                f"Should I create *'{title}'* from *{s_dt.strftime('%I:%M %p')} to {e_dt.strftime('%I:%M %p')}* on *{s_dt.strftime('%A, %d %b %Y')}*?\n"
                f"Reply `YES` to confirm or `NO` to cancel."
            )

        if tool_name == "calendar_update":
            if not self.calendar.is_configured():
                return "📅 Google Calendar is not authorized yet."
            query = (args.get("search_query") or "").strip()
            search_date = args.get("search_date_iso")
            ok, matches, err = self.calendar.search_events(query, search_date)
            if not ok:
                return f"⚠️ Calendar search failed: {err}"
            if not matches:
                return f"I couldn't find an upcoming event matching '{query}'."
            if len(matches) > 1:
                items = "\n".join(
                    f"• *{m['title']}* ({m['start']})" for m in matches[:5]
                )
                return f"I found multiple matching events:\n{items}\nWhich specific one would you like to move/edit?"

            target = matches[0]
            new_start = args.get("new_start_iso")
            if not new_start and not args.get("new_title"):
                return f"I found *{target['title']}*. What new date/time or title would you like to set?"

            payload = {
                "event_id": target["id"],
                "old_title": target["title"],
                "new_title": args.get("new_title"),
                "new_start_iso": new_start,
                "new_end_iso": args.get("new_end_iso"),
                "new_description": args.get("new_description"),
            }
            self.db.set_pending_confirmation(
                action_type="calendar_update",
                payload=payload,
                summary_text=f"Update event '{target['title']}'",
                expires_at_iso=expires_iso,
            )
            return (
                f"Should I update calendar event *'{target['title']}'* "
                f"(New start: `{new_start or 'unchanged'}`, New title: `{args.get('new_title') or 'unchanged'}`)?\n"
                "Reply `YES` to confirm or `NO` to cancel."
            )

        if tool_name == "calendar_delete":
            if not self.calendar.is_configured():
                return "📅 Google Calendar is not authorized yet."
            query = (args.get("search_query") or "").strip()
            search_date = args.get("search_date_iso")
            ok, matches, err = self.calendar.search_events(query, search_date)
            if not ok:
                return f"⚠️ Calendar error: {err}"
            if not matches:
                return f"I couldn't find any upcoming event matching '{query}' to cancel."
            if len(matches) > 1:
                items = "\n".join(
                    f"• *{m['title']}* ({m['start']})" for m in matches[:5]
                )
                return f"I found multiple matching events:\n{items}\nPlease specify the exact title and date of the event you want to cancel."

            target = matches[0]
            self.db.set_pending_confirmation(
                action_type="calendar_delete",
                payload={"event_id": target["id"], "title": target["title"], "start": target["start"]},
                summary_text=f"Delete '{target['title']}'",
                expires_at_iso=expires_iso,
            )
            return (
                f"⚠️ Are you sure you want to delete *'{target['title']}'* ({target['start']}) from Google Calendar?\n"
                "Reply `YES` to confirm or `NO` to cancel."
            )

        # --- REMINDER TOOLS ---
        if tool_name == "reminder_create":
            title = (args.get("title") or "").strip()
            remind_at_iso = args.get("remind_at_iso")
            if not title or not remind_at_iso:
                return "When should I remind you, and what is the reminder about?"
            try:
                rem = self.reminders.create_reminder(
                    title=title,
                    remind_at_iso=remind_at_iso,
                    recurrence=args.get("recurrence"),
                    category=args.get("category") or "general",
                )
                dt = datetime.fromisoformat(rem["remind_at"]).astimezone(self.tz)
                rec_note = f" (Repeats: {rem['recurrence']})" if rem.get("recurrence") else ""
                return f"⏰ Reminder *#{rem['id']}* set: *{rem['title']}* on *{dt.strftime('%a, %d %b %Y at %I:%M %p')}*{rec_note}."
            except Exception as exc:
                return f"Could not schedule reminder: {exc}. Please clarify the date and time."

        if tool_name == "reminder_list":
            rems = self.reminders.list_pending_reminders()
            return self.reminders.format_reminders_for_whatsapp(rems)

        if tool_name == "reminder_cancel":
            rem_id = self._safe_int(args.get("reminder_id"))
            ok, msg = self.reminders.cancel_reminder(
                reminder_id=rem_id, search_query=args.get("search_query")
            )
            return msg

        if tool_name == "reminder_postpone":
            new_iso = args.get("new_remind_at_iso")
            if not new_iso:
                return "What new time should I postpone the reminder to?"
            rem_id = self._safe_int(args.get("reminder_id"))
            ok, msg = self.reminders.postpone_reminder(
                new_remind_at_iso=new_iso,
                reminder_id=rem_id,
                search_query=args.get("search_query"),
            )
            return msg

        # --- TASK MANAGER TOOLS ---
        if tool_name == "task_add":
            title = (args.get("title") or "").strip()
            if not title:
                return "What is the title of the task you'd like to add?"
            created = self.tasks.add_task(
                title=title,
                priority=args.get("priority") or "medium",
                category=args.get("category") or "general",
                due_at_iso=args.get("due_at_iso"),
                recurrence=args.get("recurrence"),
            )
            due_msg = ""
            if created.get("due_at"):
                dt = datetime.fromisoformat(created["due_at"]).astimezone(self.tz)
                due_msg = f" (Due: {dt.strftime('%a, %d %b %Y %I:%M %p')})"
                # Automatically create a deadline reminder if requested or if due date is set
                mins_before = self._safe_int(args.get("create_reminder_minutes_before")) or 60
                rem_dt = dt - timedelta(minutes=mins_before)
                if rem_dt > now:
                    self.reminders.create_reminder(
                        title=f"Task Deadline Soon: {created['title']}",
                        remind_at_iso=rem_dt.isoformat(timespec="seconds"),
                        category="assignment",
                        task_id=created["id"],
                    )
                    due_msg += f" + ⏰ Reminder set for {rem_dt.strftime('%I:%M %p')}"
            return f"✅ Added *{created['priority'].upper()}* priority task *#{created['id']}: {created['title']}*{due_msg}."

        if tool_name == "task_list":
            ftype = (args.get("filter_type") or "all").lower()
            items = self.tasks.list_tasks(
                filter_type=ftype,
                category=args.get("category"),
                search_query=args.get("search_query"),
            )
            header_map = {
                "top3": "🎯 *Your Top 3 Most Important Tasks*",
                "overdue": "⚠️ *Overdue Tasks*",
                "today": "📋 *Tasks Due Today*",
            }
            return self.tasks.format_tasks_for_whatsapp(
                items, header=header_map.get(ftype, "📋 *Your Pending Tasks*")
            )

        if tool_name == "task_complete":
            tid = self._safe_int(args.get("task_id"))
            ok, msg, _ = self.tasks.complete_task(
                task_id=tid, search_query=args.get("search_query")
            )
            return msg

        if tool_name == "task_edit":
            tid = self._safe_int(args.get("task_id"))
            ok, msg = self.tasks.edit_task(
                task_id=tid,
                search_query=args.get("search_query"),
                new_title=args.get("new_title"),
                new_priority=args.get("new_priority"),
                new_due_at_iso=args.get("new_due_at_iso"),
            )
            return msg

        if tool_name == "task_delete":
            tid = self._safe_int(args.get("task_id"))
            ok, msg = self.tasks.delete_task(
                task_id=tid, search_query=args.get("search_query")
            )
            return msg

        # --- LONG-TERM MEMORY TOOLS ---
        if tool_name == "memory_save":
            ok, msg = self.memory.save_memory(
                key=str(args.get("key") or "note"),
                value=str(args.get("value") or ""),
                category=str(args.get("category") or "fact"),
            )
            return msg

        if tool_name == "memory_list":
            return self.memory.format_memories_for_whatsapp()

        if tool_name == "memory_delete":
            ok, msg = self.memory.delete_memory(str(args.get("key_or_query") or ""))
            return msg

        # --- DAILY BRIEFING TOOL ---
        if tool_name == "daily_briefing":
            return self.briefing.generate_briefing()

        return fallback_reply

    def _execute_confirmed_action(self, pending: dict[str, Any]) -> str:
        """Execute an action only after the user explicitly replies YES."""
        action = pending["action_type"]
        payload = pending["payload"]

        if action == "clear_all_memories":
            return self.memory.clear_all_memories()

        if action == "calendar_create":
            if not self.calendar.is_configured():
                return (
                    "⚠️ Cannot create event yet because Google Calendar OAuth is not configured. "
                    "Please complete `docs/SETUP_GOOGLE_OAUTH.md`."
                )
            ok, created, err = self.calendar.create_event(
                title=payload["title"],
                start_iso=payload["start_iso"],
                end_iso=payload["end_iso"],
                description=payload.get("description", ""),
                recurrence_freq=payload.get("recurrence_freq"),
            )
            if not ok or not created:
                return f"❌ Google Calendar API failed to create the event: {err}"

            # Also create a local WhatsApp reminder 30 mins before the event if in the future
            s_dt = datetime.fromisoformat(created["start"]).astimezone(self.tz)
            rem_dt = s_dt - timedelta(
                minutes=self.settings.GOOGLE_CALENDAR_DEFAULT_REMINDER_MINUTES
            )
            if rem_dt > datetime.now(self.tz):
                self.reminders.create_reminder(
                    title=f"Upcoming Event in {self.settings.GOOGLE_CALENDAR_DEFAULT_REMINDER_MINUTES}m: {created['title']}",
                    remind_at_iso=rem_dt.isoformat(timespec="seconds"),
                    category="event",
                    calendar_event_id=created["id"],
                )
            return (
                f"✅ Done. Your event *'{created['title']}'* has been added to Google Calendar "
                f"(with a {self.settings.GOOGLE_CALENDAR_DEFAULT_REMINDER_MINUTES}-minute reminder)."
            )

        if action == "calendar_update":
            ok, updated, err = self.calendar.update_event(
                event_id=payload["event_id"],
                new_title=payload.get("new_title"),
                new_start_iso=payload.get("new_start_iso"),
                new_end_iso=payload.get("new_end_iso"),
                new_description=payload.get("new_description"),
            )
            if not ok or not updated:
                return f"❌ Failed to update calendar event: {err}"
            return f"✅ Done. Updated Google Calendar event *'{updated['title']}'*."

        if action == "calendar_delete":
            ok, err = self.calendar.delete_event(payload["event_id"])
            if not ok:
                return f"❌ Failed to delete calendar event: {err}"
            return f"🗑️ Done. Cancelled and removed *'{payload['title']}'* from your Google Calendar."

        return "⚠️ Unknown confirmation action."

    @staticmethod
    def _safe_int(val: Any) -> int | None:
        if val is None:
            return None
        try:
            cleaned = re.sub(r"[^\d]", "", str(val))
            return int(cleaned) if cleaned else None
        except ValueError:
            return None
