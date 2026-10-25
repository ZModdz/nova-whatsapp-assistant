"""WhatsApp Cloud API client and 24-hour customer service window + fallback delivery manager."""

from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
import subprocess
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging_config import logger
from app.db.database import Database


class WhatsAppService:
    """Handles WhatsApp Cloud API messaging, 24-hour window checks, and free fallback channels."""

    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        """Check whether real Meta WhatsApp Cloud API credentials are set."""
        token = self.settings.WHATSAPP_ACCESS_TOKEN
        phone_id = self.settings.WHATSAPP_PHONE_NUMBER_ID
        return bool(
            token
            and phone_id
            and token != "replace_with_meta_access_token"
            and phone_id != "replace_with_meta_phone_number_id"
        )

    def is_within_24h_window(self) -> tuple[bool, float]:
        """
        Check if the user has sent an inbound WhatsApp message within the last 24 hours.
        Returns (is_open, hours_remaining).
        """
        last_inbound = self.db.get_last_inbound_timestamp()
        if not last_inbound:
            return False, 0.0
        now = datetime.now(self.settings.tz)
        elapsed = now - last_inbound
        remaining_hours = 24.0 - (elapsed.total_seconds() / 3600.0)
        return remaining_hours > 0.05, max(0.0, round(remaining_hours, 2))

    async def send_whatsapp_text(self, recipient_phone: str, text: str) -> tuple[bool, str]:
        """
        Send a free-form text message via Meta WhatsApp Cloud API.
        Returns (success, detail_or_message_id).
        """
        if not self.is_configured:
            logger.warning(
                "WhatsApp Cloud API credentials are placeholders. Message not sent over network."
            )
            return False, "WhatsApp Cloud API credentials not configured in .env"

        url = (
            f"https://graph.facebook.com/{self.settings.WHATSAPP_API_VERSION}/"
            f"{self.settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        headers = {
            "Authorization": f"Bearer {self.settings.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        # Meta limits single text bodies to 4096 chars
        safe_body = text[:4000] if len(text) > 4000 else text
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "text",
            "text": {"preview_url": False, "body": safe_body},
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code in (200, 201):
                    data = response.json()
                    msg_id = (
                        data.get("messages", [{}])[0].get("id", "unknown_id")
                    )
                    logger.info("WhatsApp message delivered to %s (id=%s)", recipient_phone, msg_id)
                    return True, msg_id

                err_text = response.text
                logger.error(
                    "WhatsApp API error (%s): %s", response.status_code, err_text
                )
                return False, f"HTTP {response.status_code}: {err_text}"
        except Exception as exc:
            logger.error("Network error sending WhatsApp message: %s", exc)
            return False, str(exc)

    def send_windows_toast(self, title: str, message: str) -> bool:
        """Trigger a native Windows local notification popup (100% free, zero dependencies)."""
        if not self.settings.DESKTOP_NOTIFICATIONS_ENABLED:
            return False
        try:
            safe_title = title.replace("'", "").replace('"', "")[:60]
            safe_msg = message.replace("'", "").replace('"', "").replace("\n", " ")[:200]
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$notify = New-Object System.Windows.Forms.NotifyIcon; "
                "$notify.Icon = [System.Drawing.SystemIcons]::Information; "
                "$notify.Visible = $true; "
                f"$notify.ShowBalloonTip(8000, '{safe_title}', '{safe_msg}', [System.Windows.Forms.ToolTipIcon]::Info);"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Sent Windows Desktop Toast notification: %s", safe_title)
            return True
        except Exception as exc:
            logger.warning("Failed to display Windows Toast notification: %s", exc)
            return False

    def send_email_fallback(self, subject: str, body: str) -> bool:
        """Send an email via free SMTP (e.g., Gmail App Password) when WhatsApp window is closed."""
        if not self.settings.EMAIL_FALLBACK_ENABLED:
            return False
        if not (
            self.settings.SMTP_USERNAME
            and self.settings.SMTP_APP_PASSWORD
            and self.settings.FALLBACK_RECIPIENT_EMAIL
        ):
            logger.warning("EMAIL_FALLBACK_ENABLED is true, but SMTP credentials are missing.")
            return False

        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.settings.SMTP_USERNAME
            msg["To"] = self.settings.FALLBACK_RECIPIENT_EMAIL
            msg.set_content(body)

            with smtplib.SMTP(self.settings.SMTP_HOST, self.settings.SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(self.settings.SMTP_USERNAME, self.settings.SMTP_APP_PASSWORD)
                server.send_message(msg)
            logger.info("Fallback email sent to %s", self.settings.FALLBACK_RECIPIENT_EMAIL)
            return True
        except Exception as exc:
            logger.error("Failed to send fallback email: %s", exc)
            return False

    async def deliver_notification(
        self, title: str, message: str
    ) -> tuple[bool, str, str]:
        """
        Deliver a scheduled reminder or briefing to the authorized user.
        1. Checks if the 24-hour free WhatsApp service window is open.
        2. If open, delivers via WhatsApp Cloud API.
        3. If closed or network/API fails, delivers via configured free fallbacks (Windows Desktop + Email)
           and queues the missed notification for the user's next WhatsApp interaction.
        Returns (delivered, channel_used, detail).
        """
        window_open, hours_left = self.is_within_24h_window()
        recipient = self.settings.ALLOWED_WHATSAPP_NUMBER

        if window_open and self.is_configured:
            ok, detail = await self.send_whatsapp_text(recipient, message)
            if ok:
                # Also pop a subtle local toast if on laptop
                self.send_windows_toast(title, message)
                return True, "whatsapp", detail
        else:
            detail = (
                "24-hour WhatsApp free service window is closed"
                if not window_open
                else "WhatsApp API not configured"
            )

        # Fallback path when 24h WhatsApp window is closed or WhatsApp delivery failed
        toast_ok = self.send_windows_toast(title, message)
        email_ok = self.send_email_fallback(f"[JARVIS] {title}", message)

        channels = []
        if toast_ok:
            channels.append("windows_toast")
        if email_ok:
            channels.append("email")

        if channels:
            return True, "+".join(channels), f"Fallback used ({detail})"
        return False, "none", detail
