"""Google Calendar OAuth 2.0 integration with conflict detection and confirmation gates."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import Settings, get_settings
from app.core.logging_config import logger


# Least privilege scope required to read, create, update, and delete calendar events
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class CalendarService:
    """Manages Google Calendar API authentication, event retrieval, conflict checks, and mutations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.tz = self.settings.tz

    @property
    def client_secret_path(self) -> Path:
        return Path(self.settings.GOOGLE_CLIENT_SECRET_FILE)

    @property
    def token_path(self) -> Path:
        return Path(self.settings.GOOGLE_TOKEN_FILE)

    def get_credentials(self, allow_interactive: bool = False) -> Credentials | None:
        """
        Load valid Google OAuth 2.0 credentials, refreshing expired tokens automatically.
        If allow_interactive is True (used by scripts/setup_google_oauth.py), launches browser login.
        """
        creds: Credentials | None = None
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_path), SCOPES
                )
            except Exception as exc:
                logger.error("Failed to read token.json: %s", exc)
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                self.token_path.write_text(creds.to_json(), encoding="utf-8")
                logger.info("Refreshed Google OAuth 2.0 token automatically.")
            except Exception as exc:
                logger.error("Failed to refresh Google OAuth token: %s", exc)
                creds = None

        if (not creds or not creds.valid) and allow_interactive:
            if not self.client_secret_path.exists():
                raise FileNotFoundError(
                    f"Missing {self.client_secret_path}. See docs/SETUP_GOOGLE_OAUTH.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.client_secret_path), SCOPES
            )
            creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Saved new Google OAuth token to %s", self.token_path)

        return creds if (creds and creds.valid) else None

    def is_configured(self) -> bool:
        """Return True if valid OAuth credentials are ready for API calls."""
        return self.get_credentials(allow_interactive=False) is not None

    def _get_service(self) -> Any:
        creds = self.get_credentials(allow_interactive=False)
        if not creds:
            raise RuntimeError(
                "Google Calendar is not authorized yet. Run `python scripts/setup_google_oauth.py` first."
            )
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def _parse_iso(self, iso_str: str) -> datetime:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def list_events(
        self, start_iso: str, end_iso: str, max_results: int = 25
    ) -> tuple[bool, list[dict[str, Any]], str]:
        """
        Retrieve events between start_iso and end_iso.
        Returns (success, events_list, error_message).
        """
        try:
            service = self._get_service()
            start_dt = self._parse_iso(start_iso)
            end_dt = self._parse_iso(end_iso)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(days=1)

            events_result = (
                service.events()
                .list(
                    calendarId=self.settings.GOOGLE_CALENDAR_ID,
                    timeMin=start_dt.isoformat(),
                    timeMax=end_dt.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=max_results,
                )
                .execute()
            )
            items = events_result.get("items", [])
            parsed_events = []
            for item in items:
                start_raw = item.get("start", {}).get(
                    "dateTime", item.get("start", {}).get("date", "")
                )
                end_raw = item.get("end", {}).get(
                    "dateTime", item.get("end", {}).get("date", "")
                )
                parsed_events.append(
                    {
                        "id": item.get("id"),
                        "title": item.get("summary", "(Untitled Event)"),
                        "start": start_raw,
                        "end": end_raw,
                        "description": item.get("description", ""),
                        "htmlLink": item.get("htmlLink", ""),
                    }
                )
            return True, parsed_events, ""
        except RuntimeError as exc:
            return False, [], str(exc)
        except HttpError as exc:
            logger.error("Google Calendar API HttpError: %s", exc)
            return False, [], f"Google Calendar API error: {exc.reason}"
        except Exception as exc:
            logger.error("Unexpected error listing calendar events: %s", exc)
            return False, [], str(exc)

    def format_events_for_whatsapp(
        self, events: list[dict[str, Any]], header: str = "📅 *Upcoming Events*"
    ) -> str:
        """Format a list of calendar events cleanly for WhatsApp reading."""
        if not events:
            return f"{header}\nNo events scheduled for this period."

        lines = [header]
        for idx, ev in enumerate(events, start=1):
            start_raw = ev["start"]
            end_raw = ev["end"]
            try:
                if "T" in start_raw:
                    s_dt = datetime.fromisoformat(start_raw).astimezone(self.tz)
                    e_dt = datetime.fromisoformat(end_raw).astimezone(self.tz)
                    time_str = f"{s_dt.strftime('%d %b, %I:%M %p')} – {e_dt.strftime('%I:%M %p')}"
                else:
                    time_str = f"{start_raw} (All day)"
            except Exception:
                time_str = start_raw

            lines.append(f"{idx}. *{ev['title']}* — {time_str}")
        return "\n".join(lines)

    def check_conflicts(
        self,
        start_iso: str,
        end_iso: str,
        exclude_event_id: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]], str]:
        """Check if any existing event overlaps with [start_iso, end_iso]."""
        ok, events, err = self.list_events(start_iso, end_iso)
        if not ok:
            return False, [], err
        conflicts = [
            e for e in events if not exclude_event_id or e.get("id") != exclude_event_id
        ]
        return True, conflicts, ""

    def search_events(
        self, query: str, search_date_iso: str | None = None
    ) -> tuple[bool, list[dict[str, Any]], str]:
        """Search upcoming (or date-specific) events matching a title keyword."""
        now = datetime.now(self.tz)
        if search_date_iso:
            target = self._parse_iso(search_date_iso)
            start_dt = target.replace(hour=0, minute=0, second=0)
            end_dt = target.replace(hour=23, minute=59, second=59)
        else:
            start_dt = now - timedelta(hours=2)
            end_dt = now + timedelta(days=30)

        ok, events, err = self.list_events(start_dt.isoformat(), end_dt.isoformat(), max_results=50)
        if not ok:
            return False, [], err

        q_lower = query.strip().lower()
        if not q_lower:
            return True, events, ""
        matched = [
            e
            for e in events
            if q_lower in e["title"].lower() or q_lower in e.get("description", "").lower()
        ]
        return True, matched, ""

    def create_event(
        self,
        title: str,
        start_iso: str,
        end_iso: str,
        description: str = "",
        recurrence_freq: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Execute Google Calendar event creation after user confirmation.
        Includes native Google Calendar popup reminder so user gets phone alerts even if laptop is off.
        """
        try:
            service = self._get_service()
            start_dt = self._parse_iso(start_iso)
            end_dt = self._parse_iso(end_iso)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=1)

            body: dict[str, Any] = {
                "summary": title,
                "description": description or "Created by JARVIS WhatsApp Assistant",
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": self.settings.TIMEZONE,
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": self.settings.TIMEZONE,
                },
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {
                            "method": "popup",
                            "minutes": self.settings.GOOGLE_CALENDAR_DEFAULT_REMINDER_MINUTES,
                        }
                    ],
                },
            }

            if recurrence_freq and recurrence_freq.upper() in ("DAILY", "WEEKLY", "MONTHLY"):
                body["recurrence"] = [f"RRULE:FREQ={recurrence_freq.upper()}"]

            created = (
                service.events()
                .insert(calendarId=self.settings.GOOGLE_CALENDAR_ID, body=body)
                .execute()
            )
            logger.info("Created Google Calendar event id=%s title='%s'", created.get("id"), title)
            return (
                True,
                {
                    "id": created.get("id"),
                    "title": created.get("summary", title),
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "htmlLink": created.get("htmlLink", ""),
                },
                "",
            )
        except Exception as exc:
            logger.error("Failed to create Google Calendar event: %s", exc)
            return False, None, str(exc)

    def update_event(
        self,
        event_id: str,
        new_title: str | None = None,
        new_start_iso: str | None = None,
        new_end_iso: str | None = None,
        new_description: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """Update an existing Google Calendar event after confirmation."""
        try:
            service = self._get_service()
            existing = (
                service.events()
                .get(calendarId=self.settings.GOOGLE_CALENDAR_ID, eventId=event_id)
                .execute()
            )

            if new_title:
                existing["summary"] = new_title
            if new_description is not None:
                existing["description"] = new_description

            if new_start_iso:
                s_dt = self._parse_iso(new_start_iso)
                if new_end_iso:
                    e_dt = self._parse_iso(new_end_iso)
                else:
                    # Preserve original duration if possible
                    old_start = existing.get("start", {}).get("dateTime")
                    old_end = existing.get("end", {}).get("dateTime")
                    if old_start and old_end:
                        dur = self._parse_iso(old_end) - self._parse_iso(old_start)
                        e_dt = s_dt + (dur if dur.total_seconds() > 0 else timedelta(hours=1))
                    else:
                        e_dt = s_dt + timedelta(hours=1)
                existing["start"] = {
                    "dateTime": s_dt.isoformat(),
                    "timeZone": self.settings.TIMEZONE,
                }
                existing["end"] = {
                    "dateTime": e_dt.isoformat(),
                    "timeZone": self.settings.TIMEZONE,
                }

            updated = (
                service.events()
                .update(
                    calendarId=self.settings.GOOGLE_CALENDAR_ID,
                    eventId=event_id,
                    body=existing,
                )
                .execute()
            )
            logger.info("Updated Google Calendar event id=%s", event_id)
            return (
                True,
                {
                    "id": updated.get("id"),
                    "title": updated.get("summary"),
                    "start": updated.get("start", {}).get("dateTime"),
                    "end": updated.get("end", {}).get("dateTime"),
                },
                "",
            )
        except Exception as exc:
            logger.error("Failed to update Google Calendar event %s: %s", event_id, exc)
            return False, None, str(exc)

    def delete_event(self, event_id: str) -> tuple[bool, str]:
        """Delete a Google Calendar event by ID after user confirmation."""
        try:
            service = self._get_service()
            service.events().delete(
                calendarId=self.settings.GOOGLE_CALENDAR_ID, eventId=event_id
            ).execute()
            logger.info("Deleted Google Calendar event id=%s", event_id)
            return True, ""
        except Exception as exc:
            logger.error("Failed to delete Google Calendar event %s: %s", event_id, exc)
            return False, str(exc)
