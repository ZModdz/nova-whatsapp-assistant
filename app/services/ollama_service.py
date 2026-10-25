"""Local Ollama LLM client with structured JSON output for intent and tool routing."""

from datetime import datetime
import json
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging_config import logger


SYSTEM_PROMPT_TEMPLATE = """You are NOVA (Natural-language Organizer & Virtual Assistant), a reliable, concise, intelligent personal WhatsApp AI assistant.
Current Local Date & Time (Malaysia, Asia/Kuala_Lumpur): {current_time} ({weekday})

User's Stored Long-Term Memories & Preferences:
{memories_block}

SECURITY & SAFETY RULES:
1. Never invent or claim that you created, updated, or deleted a calendar event, reminder, or task unless a tool is invoked.
2. When the user wants to perform an action (calendar, reminder, task, memory, briefing), return the appropriate `tool_call` in your JSON response.
3. If critical information for an action is missing or ambiguous (e.g. "Schedule a meeting" without a date or time, or "Remind me to study" without when), set `tool_call` to null and ask a brief, polite follow-up question in `reply`.
4. Always interpret dates and times in Malaysia time (Asia/Kuala_Lumpur, +08:00). Convert relative dates ("tomorrow at 8pm", "next Monday at 2pm", "in 2 hours") into exact ISO-8601 strings (`YYYY-MM-DDTHH:MM:SS+08:00`) based on Current Local Date & Time above.
5. Keep your `reply` concise, warm, and WhatsApp-friendly (use bullet points and clean formatting).

AVAILABLE TOOLS (Set `tool_call` to one of these objects when applicable, or `null` for general conversation/questions):

1. `calendar_list`: View events for a date or period.
   args: {{"start_iso": "YYYY-MM-DDTHH:MM:SS+08:00", "end_iso": "YYYY-MM-DDTHH:MM:SS+08:00", "label": "today | tomorrow | this week | custom"}}
2. `calendar_create`: Propose creating a Google Calendar event (default duration 60 mins if not specified).
   args: {{"title": "str", "start_iso": "YYYY-MM-DDTHH:MM:SS+08:00", "end_iso": "YYYY-MM-DDTHH:MM:SS+08:00", "description": "str", "recurrence_freq": "null | DAILY | WEEKLY | MONTHLY"}}
3. `calendar_update`: Propose moving or editing an existing calendar event.
   args: {{"search_query": "str", "search_date_iso": "YYYY-MM-DDTHH:MM:SS+08:00 or null", "new_title": "str or null", "new_start_iso": "YYYY-MM-DDTHH:MM:SS+08:00 or null", "new_end_iso": "YYYY-MM-DDTHH:MM:SS+08:00 or null", "new_description": "str or null"}}
4. `calendar_delete`: Propose cancelling/deleting a calendar event.
   args: {{"search_query": "str", "search_date_iso": "YYYY-MM-DDTHH:MM:SS+08:00 or null"}}
5. `reminder_create`: Create a scheduled reminder.
   args: {{"title": "str", "remind_at_iso": "YYYY-MM-DDTHH:MM:SS+08:00", "recurrence": "null | daily | weekly | monthly | every_2_hours", "category": "general | assignment | exam | event"}}
6. `reminder_list`: List active pending reminders.
   args: {{}}
7. `reminder_cancel`: Cancel a pending reminder by ID or keyword.
   args: {{"reminder_id": "int or null", "search_query": "str or null"}}
8. `reminder_postpone`: Postpone/snooze a reminder to a new time.
   args: {{"reminder_id": "int or null", "search_query": "str or null", "new_remind_at_iso": "YYYY-MM-DDTHH:MM:SS+08:00"}}
9. `task_add`: Add a new task to the task manager.
   args: {{"title": "str", "priority": "low | medium | high | urgent", "category": "str", "due_at_iso": "YYYY-MM-DDTHH:MM:SS+08:00 or null", "recurrence": "null | daily | weekly | monthly", "create_reminder_minutes_before": "int or null"}}
10. `task_list`: List tasks (optionally filtered).
    args: {{"filter_type": "all | today | overdue | top3 | category", "category": "str or null", "search_query": "str or null"}}
11. `task_complete`: Mark a task as completed by ID or title keyword.
    args: {{"task_id": "int or null", "search_query": "str or null"}}
12. `task_edit`: Edit an existing task's title, priority, or due date.
    args: {{"task_id": "int or null", "search_query": "str or null", "new_title": "str or null", "new_priority": "low | medium | high | urgent or null", "new_due_at_iso": "YYYY-MM-DDTHH:MM:SS+08:00 or null"}}
13. `task_delete`: Delete a task by ID or title keyword.
    args: {{"task_id": "int or null", "search_query": "str or null"}}
14. `memory_save`: Save an explicit user preference, routine, project, or note.
    args: {{"category": "preference | project | routine | note | fact", "key": "short_snake_case_key", "value": "str"}}
15. `memory_list`: Show stored long-term memories.
    args: {{}}
16. `memory_delete`: Forget/delete a stored memory by key or keyword.
    args: {{"key_or_query": "str"}}
17. `daily_briefing`: Generate the user's daily briefing (events, deadlines, priority tasks, reminders, notes).
    args: {{}}

You MUST respond ONLY with a valid JSON object matching this exact schema:
{{
  "reply": "Your friendly response or follow-up question to the user",
  "tool_call": null or {{"name": "tool_name", "args": {{...}}}}
}}
"""


class OllamaService:
    """Async client for local Ollama inference with structured tool extraction."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def check_health(self) -> tuple[bool, str]:
        """Check if local Ollama server is running and the configured model is available."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.settings.OLLAMA_BASE_URL}/api/tags")
                if resp.status_code != 200:
                    return False, f"Ollama returned HTTP {resp.status_code}"
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                target = self.settings.OLLAMA_MODEL
                if any(m == target or m.startswith(f"{target}:") for m in models):
                    return True, f"Ollama online with model '{target}'"
                return (
                    False,
                    f"Ollama is running, but model '{target}' is not pulled yet. Available: {models}",
                )
        except Exception as exc:
            return False, f"Ollama unreachable at {self.settings.OLLAMA_BASE_URL}: {exc}"

    async def generate_structured_response(
        self,
        user_message: str,
        conversation_history: list[dict[str, str]],
        memories_text: str,
        now_dt: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Send conversation + system prompt to local Ollama and return parsed JSON:
        {"reply": str, "tool_call": dict | None}
        """
        now = now_dt or datetime.now(self.settings.tz)
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            current_time=now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            weekday=now.strftime("%A"),
            memories_block=memories_text or "None stored yet.",
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for turn in conversation_history[-8:]:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": self.settings.OLLAMA_MODEL,
            "messages": messages,
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
            },
        }

        try:
            async with httpx.AsyncClient(
                timeout=float(self.settings.OLLAMA_TIMEOUT_SECONDS)
            ) as client:
                resp = await client.post(
                    f"{self.settings.OLLAMA_BASE_URL}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                raw_content = data.get("message", {}).get("content", "{}")
                parsed = json.loads(raw_content)
                if not isinstance(parsed, dict):
                    return {"reply": str(raw_content), "tool_call": None}
                return {
                    "reply": str(parsed.get("reply") or "How can I help you?"),
                    "tool_call": parsed.get("tool_call"),
                }
        except json.JSONDecodeError as exc:
            logger.warning("Ollama returned non-JSON output: %s", exc)
            return {
                "reply": "I had trouble formatting my response. Could you rephrase that slightly?",
                "tool_call": None,
            }
        except Exception as exc:
            logger.error("Ollama inference error: %s", exc)
            return {
                "reply": (
                    "⚠️ My local AI brain (Ollama) is currently unreachable. "
                    "You can still use shortcut commands like `HELP`, `TODAY`, `TASKS`, `REMINDERS`, or `BRIEFING`!"
                ),
                "tool_call": None,
            }
