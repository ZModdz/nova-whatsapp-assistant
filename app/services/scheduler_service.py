"""Background APScheduler service for persistent reminders, startup recovery, keepalive pings, and morning briefings."""

from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings, get_settings
from app.core.logging_config import logger
from app.db.database import Database
from app.services.briefing_service import BriefingService
from app.services.reminder_service import ReminderService
from app.services.whatsapp_service import WhatsAppService


class JarvisScheduler:
    """Manages APScheduler background jobs and startup overdue reminder recovery."""

    def __init__(
        self,
        db: Database,
        reminder_service: ReminderService,
        briefing_service: BriefingService,
        whatsapp_service: WhatsAppService,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.reminders = reminder_service
        self.briefing = briefing_service
        self.whatsapp = whatsapp_service
        self.settings = settings or get_settings()
        self.tz = self.settings.tz
        self.scheduler = AsyncIOScheduler(timezone=self.tz)

    async def start(self) -> None:
        """
        Run startup overdue reminder recovery immediately, then start recurring background jobs.
        """
        logger.info("Running startup recovery sweep for overdue reminders...")
        recovered = await self.reminders.process_due_reminders(is_startup_recovery=True)
        if recovered:
            logger.info("Startup recovery delivered %d overdue reminder(s).", recovered)

        # 1. Sweep due reminders every 30 seconds
        self.scheduler.add_job(
            self._tick_reminders,
            trigger=IntervalTrigger(seconds=30, timezone=self.tz),
            id="sweep_due_reminders",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # 2. Check 24-hour WhatsApp service window keepalive every 15 minutes
        self.scheduler.add_job(
            self.reminders.check_and_send_window_keepalive,
            trigger=IntervalTrigger(minutes=15, timezone=self.tz),
            id="whatsapp_window_keepalive",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # 3. Optional Morning Daily Briefing
        if self.settings.MORNING_BRIEFING_ENABLED:
            try:
                bh, bm = (int(x) for x in self.settings.MORNING_BRIEFING_TIME.split(":"))
            except Exception:
                bh, bm = 8, 0
            self.scheduler.add_job(
                self._send_scheduled_morning_briefing,
                trigger=CronTrigger(hour=bh, minute=bm, timezone=self.tz),
                id="morning_daily_briefing",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )

        self.scheduler.start()
        logger.info("APScheduler started (timezone=%s).", self.settings.TIMEZONE)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("APScheduler shut down cleanly.")

    async def _tick_reminders(self) -> None:
        try:
            await self.reminders.process_due_reminders(is_startup_recovery=False)
        except Exception as exc:
            logger.error("Error in reminder sweep job: %s", exc)

    async def _send_scheduled_morning_briefing(self) -> None:
        """Send the daily morning briefing once per day if enabled."""
        if not self.settings.MORNING_BRIEFING_ENABLED:
            return
        today_str = datetime.now(self.tz).strftime("%Y-%m-%d")
        if self.db.get_state("last_morning_briefing_date") == today_str:
            return

        text = self.briefing.generate_briefing()
        ok, channel, _ = await self.whatsapp.deliver_notification(
            title="JARVIS Morning Briefing",
            message=text,
        )
        if ok:
            self.db.set_state("last_morning_briefing_date", today_str)
            logger.info("Delivered morning briefing via %s", channel)
