"""FastAPI application entrypoint and Meta WhatsApp Cloud API Webhook endpoints."""

from contextlib import asynccontextmanager
import json
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import Settings, get_settings
from app.core.logging_config import logger
from app.core.security import (
    RateLimiter,
    is_authorized_sender,
    normalize_phone,
    verify_webhook_signature,
)
from app.db.database import Database
from app.services.assistant_router import AssistantRouter
from app.services.briefing_service import BriefingService
from app.services.calendar_service import CalendarService
from app.services.memory_service import MemoryService
from app.services.ollama_service import OllamaService
from app.services.reminder_service import ReminderService
from app.services.scheduler_service import JarvisScheduler
from app.services.task_service import TaskService
from app.services.whatsapp_service import WhatsAppService


def create_app(custom_settings: Settings | None = None, custom_db: Database | None = None) -> FastAPI:
    """Application factory so tests and production can instantiate JARVIS cleanly."""
    settings = custom_settings or get_settings()
    db = custom_db or Database(settings.db_file)

    whatsapp_service = WhatsAppService(db=db, settings=settings)
    ollama_service = OllamaService(settings=settings)
    calendar_service = CalendarService(settings=settings)
    task_service = TaskService(db=db, settings=settings)
    reminder_service = ReminderService(
        db=db, whatsapp_service=whatsapp_service, settings=settings
    )
    memory_service = MemoryService(db=db)
    briefing_service = BriefingService(
        db=db,
        calendar_service=calendar_service,
        task_service=task_service,
        reminder_service=reminder_service,
        memory_service=memory_service,
        settings=settings,
    )
    router = AssistantRouter(
        db=db,
        ollama=ollama_service,
        calendar=calendar_service,
        tasks=task_service,
        reminders=reminder_service,
        memory=memory_service,
        briefing=briefing_service,
        settings=settings,
    )
    scheduler = JarvisScheduler(
        db=db,
        reminder_service=reminder_service,
        briefing_service=briefing_service,
        whatsapp_service=whatsapp_service,
        settings=settings,
    )
    rate_limiter = RateLimiter(max_requests=25, window_seconds=60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting NOVA Personal Assistant (%s)...", settings.TIMEZONE)
        await scheduler.start()
        yield
        scheduler.shutdown()
        logger.info("NOVA stopped.")

    app = FastAPI(
        title="NOVA — Natural-language Organizer & Virtual Assistant",
        version="1.0.0",
        docs_url=None,  # Do not expose Swagger UI publicly over webhook tunnel
        redoc_url=None,
        lifespan=lifespan,
    )

    # Attach services to app.state for inspection and testing
    app.state.settings = settings
    app.state.db = db
    app.state.whatsapp = whatsapp_service
    app.state.ollama = ollama_service
    app.state.calendar = calendar_service
    app.state.tasks = task_service
    app.state.reminders = reminder_service
    app.state.memory = memory_service
    app.state.briefing = briefing_service
    app.state.router = router
    app.state.scheduler = scheduler

    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        """Minimal health status check (does not expose any personal data or secrets)."""
        ollama_ok, ollama_detail = await ollama_service.check_health()
        window_open, hours_left = whatsapp_service.is_within_24h_window()
        return {
            "status": "online",
            "assistant": settings.APP_NAME,
            "timezone": settings.TIMEZONE,
            "ollama_ready": ollama_ok,
            "ollama_status": ollama_detail,
            "whatsapp_configured": whatsapp_service.is_configured,
            "whatsapp_24h_window_open": window_open,
            "whatsapp_24h_hours_remaining": hours_left,
            "google_calendar_configured": calendar_service.is_configured(),
        }

    @app.get("/webhook")
    async def verify_whatsapp_webhook(
        hub_mode: str | None = Query(default=None, alias="hub.mode"),
        hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> PlainTextResponse:
        """Meta WhatsApp Cloud API GET webhook verification handshake."""
        if (
            hub_mode == "subscribe"
            and hub_verify_token
            and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN
        ):
            logger.info("WhatsApp webhook verified successfully.")
            return PlainTextResponse(content=hub_challenge or "", status_code=200)

        logger.warning("Rejected invalid WhatsApp webhook verification attempt.")
        raise HTTPException(status_code=403, detail="Verification token mismatch")

    async def _process_and_reply(sender_phone: str, text_body: str) -> None:
        """Process the authorized user's message and reply asynchronously via WhatsApp Cloud API."""
        try:
            reply_text = await router.handle_message(text_body)
            await whatsapp_service.send_whatsapp_text(sender_phone, reply_text)
        except Exception as exc:
            logger.error("Error processing incoming WhatsApp message: %s", exc)

    @app.post("/webhook")
    async def receive_whatsapp_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_hub_signature_256: str | None = Header(default=None),
    ) -> JSONResponse:
        """
        Receive incoming Meta WhatsApp Cloud API events.
        1. Validates X-Hub-Signature-256 HMAC-SHA256 signature.
        2. Restricts access strictly to ALLOWED_WHATSAPP_NUMBER.
        3. Deduplicates message_id in SQLite so retries never execute twice.
        4. Dispatches AI & tool processing in BackgroundTasks so Meta receives HTTP 200 immediately.
        """
        raw_body = await request.body()
        if not verify_webhook_signature(raw_body, x_hub_signature_256, settings):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Malformed JSON payload")

        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    msg_id = str(msg.get("id") or "")
                    sender = normalize_phone(str(msg.get("from") or ""))
                    msg_type = msg.get("type", "")

                    if not msg_id or not sender:
                        continue

                    # Strict Single-User Phone Whitelist
                    if not is_authorized_sender(sender, settings):
                        logger.warning(
                            "Blocked unauthorized WhatsApp message from sender: %s",
                            sender,
                        )
                        continue

                    # Rate limit protection
                    if not rate_limiter.is_allowed(sender):
                        logger.warning("Rate limit exceeded for sender %s", sender)
                        continue

                    # Atomic SQLite Deduplication
                    if not db.mark_message_processed(msg_id, sender):
                        logger.info(
                            "Ignored duplicate WhatsApp webhook message_id=%s", msg_id
                        )
                        continue

                    if msg_type != "text":
                        background_tasks.add_task(
                            whatsapp_service.send_whatsapp_text,
                            sender,
                            "🤖 I currently process text messages only. Send me a text or type `HELP`!",
                        )
                        continue

                    text_body = msg.get("text", {}).get("body", "").strip()
                    if text_body:
                        background_tasks.add_task(
                            _process_and_reply, sender, text_body
                        )

        return JSONResponse(content={"status": "ok"}, status_code=200)

    return app


app = create_app()
