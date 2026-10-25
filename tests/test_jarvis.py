"""Comprehensive automated unit & integration test suite for JARVIS."""

from datetime import datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.db.database import Database
from app.main import create_app


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Create isolated test settings with temporary SQLite DB and known secrets."""
    db_file = tmp_path / "test_jarvis.db"
    return Settings(
        APP_NAME="JARVIS_TEST",
        TIMEZONE="Asia/Kuala_Lumpur",
        ALLOWED_WHATSAPP_NUMBER="60123456789",
        WHATSAPP_VERIFY_TOKEN="test_verify_token_123",
        WHATSAPP_APP_SECRET="test_app_secret_key",
        VERIFY_WEBHOOK_SIGNATURE=True,
        WHATSAPP_ACCESS_TOKEN="test_access_token",
        WHATSAPP_PHONE_NUMBER_ID="1234567890",
        DATABASE_PATH=str(db_file),
        QUIET_HOURS_ENABLED=False,
        DESKTOP_NOTIFICATIONS_ENABLED=False,
        EMAIL_FALLBACK_ENABLED=False,
        MORNING_BRIEFING_ENABLED=False,
    )


@pytest.fixture
def test_db(test_settings: Settings) -> Database:
    return Database(test_settings.db_file)


@pytest.fixture
def app_instance(test_settings: Settings, test_db: Database):
    app = create_app(custom_settings=test_settings, custom_db=test_db)
    # Mock external network calls by default
    app.state.whatsapp.send_whatsapp_text = AsyncMock(return_value=(True, "wamid.mock123"))
    return app


def _sign_payload(payload_dict: dict[str, Any], secret: str) -> tuple[bytes, str]:
    raw = json.dumps(payload_dict).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, f"sha256={sig}"


# ==============================================================================
# 1. Webhook Security, Signature Verification, Whitelist & Deduplication Tests
# ==============================================================================

def test_webhook_verification_handshake(app_instance, test_settings: Settings) -> None:
    with TestClient(app_instance) as client:
        # Valid token
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": test_settings.WHATSAPP_VERIFY_TOKEN,
                "hub.challenge": "987654321",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "987654321"

        # Invalid token
        bad_resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "987654321",
            },
        )
        assert bad_resp.status_code == 403


def test_webhook_rejects_invalid_hmac_signature(app_instance) -> None:
    payload = {"object": "whatsapp_business_account", "entry": []}
    raw = json.dumps(payload).encode("utf-8")
    with TestClient(app_instance) as client:
        resp = client.post(
            "/webhook",
            content=raw,
            headers={"X-Hub-Signature-256": "sha256=invalid_signature"},
        )
        assert resp.status_code == 401


def test_webhook_blocks_unauthorized_sender_and_deduplicates(
    app_instance, test_settings: Settings
) -> None:
    # 1. Unauthorized phone number (e.g., 60199999999)
    unauth_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.unauth_1",
                                    "from": "60199999999",
                                    "type": "text",
                                    "text": {"body": "TASKS"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    raw_unauth, sig_unauth = _sign_payload(unauth_payload, test_settings.WHATSAPP_APP_SECRET)

    with TestClient(app_instance) as client:
        resp = client.post(
            "/webhook",
            content=raw_unauth,
            headers={"X-Hub-Signature-256": sig_unauth},
        )
        assert resp.status_code == 200
        # Ensure JARVIS never replied to unauthorized number
        app_instance.state.whatsapp.send_whatsapp_text.assert_not_called()

        # 2. Authorized phone number sending "HELP" twice with the same message_id
        auth_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.auth_dup_test",
                                        "from": "60123456789",
                                        "type": "text",
                                        "text": {"body": "HELP"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        raw_auth, sig_auth = _sign_payload(auth_payload, test_settings.WHATSAPP_APP_SECRET)

        resp1 = client.post(
            "/webhook",
            content=raw_auth,
            headers={"X-Hub-Signature-256": sig_auth},
        )
        assert resp1.status_code == 200
        assert app_instance.state.whatsapp.send_whatsapp_text.call_count == 1

        # Second identical webhook delivery must be ignored by deduplication
        resp2 = client.post(
            "/webhook",
            content=raw_auth,
            headers={"X-Hub-Signature-256": sig_auth},
        )
        assert resp2.status_code == 200
        assert app_instance.state.whatsapp.send_whatsapp_text.call_count == 1


# ==============================================================================
# 2. Task Manager CRUD, Priorities, Overdue Detection & Recurrence Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_task_manager_lifecycle_and_recurrence(app_instance) -> None:
    router = app_instance.state.router
    tasks = app_instance.state.tasks

    # Add urgent and low priority tasks, plus an overdue task
    past_due = (datetime.now(tasks.tz) - timedelta(days=2)).isoformat(timespec="seconds")
    future_due = (datetime.now(tasks.tz) + timedelta(days=1)).isoformat(timespec="seconds")

    t1 = tasks.add_task(
        title="Finish Software Engineering Assignment",
        priority="high",
        category="study",
        due_at_iso=future_due,
    )
    t2 = tasks.add_task(
        title="Submit overdue lab report",
        priority="urgent",
        category="study",
        due_at_iso=past_due,
        recurrence="weekly",
    )

    assert t1["id"] == 1
    assert t2["id"] == 2

    # Test OVERDUE shortcut via router
    overdue_reply = await router.handle_message("OVERDUE")
    assert "Submit overdue lab report" in overdue_reply
    assert "Finish Software Engineering Assignment" not in overdue_reply

    # Complete recurring task #2 -> should mark #2 completed and spawn #3 next week
    ok, msg, _ = tasks.complete_task(task_id=t2["id"])
    assert ok is True
    assert "Recurring weekly task #3" in msg

    pending = tasks.list_tasks("all")
    pending_ids = [t["id"] for t in pending]
    assert 2 not in pending_ids
    assert 3 in pending_ids


# ==============================================================================
# 3. Persistent Reminders, Restart Survival, Startup Recovery & Duplicate Guard
# ==============================================================================

@pytest.mark.asyncio
async def test_reminders_persist_across_restart_and_recover_overdue(
    test_settings: Settings,
) -> None:
    # Step 1: First application instance creates a reminder due in the past (simulating laptop off)
    db1 = Database(test_settings.db_file)
    app1 = create_app(custom_settings=test_settings, custom_db=db1)
    past_time = (datetime.now(test_settings.tz) - timedelta(minutes=30)).isoformat(
        timespec="seconds"
    )
    rem = app1.state.reminders.create_reminder(
        title="Submit Database Project",
        remind_at_iso=past_time,
        category="assignment",
    )
    rem_id = rem["id"]

    # Step 2: Simulate application restart with a fresh Database & App instance pointing to same SQLite file
    db2 = Database(test_settings.db_file)
    app2 = create_app(custom_settings=test_settings, custom_db=db2)
    app2.state.whatsapp.send_whatsapp_text = AsyncMock(return_value=(True, "wamid.rec1"))
    # Open the 24h window so WhatsApp delivery succeeds
    db2.record_user_inbound_timestamp()

    # Verify reminder survived restart as 'pending'
    pending_before = app2.state.reminders.list_pending_reminders()
    assert len(pending_before) == 1
    assert pending_before[0]["id"] == rem_id

    # Step 3: Run startup recovery sweep
    delivered = await app2.state.reminders.process_due_reminders(is_startup_recovery=True)
    assert delivered == 1
    app2.state.whatsapp.send_whatsapp_text.assert_called_once()
    sent_text = app2.state.whatsapp.send_whatsapp_text.call_args[0][1]
    assert "Overdue Reminder" in sent_text
    assert "Submit Database Project" in sent_text

    # Step 4: Verify duplicate prevention — running sweep again must NOT re-send
    delivered_again = await app2.state.reminders.process_due_reminders(
        is_startup_recovery=False
    )
    assert delivered_again == 0
    assert app2.state.whatsapp.send_whatsapp_text.call_count == 1


# ==============================================================================
# 4. Google Calendar Confirmation Gate & Conflict Detection Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_calendar_confirmation_flow_never_creates_without_yes(app_instance) -> None:
    router = app_instance.state.router
    calendar = app_instance.state.calendar

    # Mock Google Calendar as configured with conflict check & create_event
    calendar.is_configured = MagicMock(return_value=True)
    calendar.check_conflicts = MagicMock(return_value=(True, [], ""))
    calendar.create_event = MagicMock(
        return_value=(
            True,
            {
                "id": "gcal_evt_999",
                "title": "Database Revision",
                "start": "2026-10-22T20:00:00+08:00",
                "end": "2026-10-22T22:00:00+08:00",
                "htmlLink": "https://calendar.google.com/event?eid=999",
            },
            "",
        )
    )

    # Mock Ollama proposing calendar_create tool call
    app_instance.state.ollama.generate_structured_response = AsyncMock(
        return_value={
            "reply": "Checking your calendar...",
            "tool_call": {
                "name": "calendar_create",
                "args": {
                    "title": "Database Revision",
                    "start_iso": "2026-10-22T20:00:00+08:00",
                    "end_iso": "2026-10-22T22:00:00+08:00",
                    "description": "Study SQL indexing",
                },
            },
        }
    )

    # User asks to schedule event -> JARVIS must ask confirmation first, NOT call create_event yet
    proposal_reply = await router.handle_message(
        "JARVIS, schedule two hours of database revision tomorrow at 8 PM."
    )
    assert "Should I create *'Database Revision'*" in proposal_reply
    calendar.create_event.assert_not_called()

    # User replies "Yes" -> Now JARVIS calls Google Calendar API and confirms
    confirm_reply = await router.handle_message("Yes")
    calendar.create_event.assert_called_once()
    assert "Done. Your event *'Database Revision'* has been added to Google Calendar" in confirm_reply


# ==============================================================================
# 5. Long-Term Memory Persistence & User Control Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_long_term_memory_persistence_and_toggle(
    test_settings: Settings,
) -> None:
    db1 = Database(test_settings.db_file)
    app1 = create_app(custom_settings=test_settings, custom_db=db1)

    ok, _ = app1.state.memory.save_memory(
        key="assistant_nickname",
        value="Call me Sir",
        category="preference",
    )
    assert ok is True

    # Restart simulation
    db2 = Database(test_settings.db_file)
    app2 = create_app(custom_settings=test_settings, custom_db=db2)
    router2 = app2.state.router

    mem_list_reply = await router2.handle_message("MEMORY")
    assert "assistant_nickname" in mem_list_reply
    assert "Call me Sir" in mem_list_reply

    # Disable memory
    off_reply = await router2.handle_message("MEMORY OFF")
    assert "disabled" in off_reply
    ok_when_off, _ = app2.state.memory.save_memory("test_key", "test_val")
    assert ok_when_off is False
