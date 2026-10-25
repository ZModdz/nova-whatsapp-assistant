"""Security utilities: webhook HMAC-SHA256 verification, phone authorization, and rate limiting."""

import hashlib
import hmac
import time
from collections import defaultdict

from app.core.config import Settings
from app.core.logging_config import logger


def normalize_phone(phone: str) -> str:
    """Normalize a phone number string to digits only."""
    if not phone:
        return ""
    return "".join(ch for ch in str(phone) if ch.isdigit())


def is_authorized_sender(sender_phone: str, settings: Settings) -> bool:
    """Check if the incoming WhatsApp sender matches the single authorized owner number."""
    normalized_sender = normalize_phone(sender_phone)
    normalized_allowed = normalize_phone(settings.ALLOWED_WHATSAPP_NUMBER)
    if not normalized_sender or not normalized_allowed:
        return False
    return hmac.compare_digest(normalized_sender, normalized_allowed)


def verify_webhook_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    settings: Settings,
) -> bool:
    """
    Verify Meta's X-Hub-Signature-256 HMAC-SHA256 header.
    If VERIFY_WEBHOOK_SIGNATURE is False (e.g., local test mode without secret), returns True.
    """
    if not settings.VERIFY_WEBHOOK_SIGNATURE:
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Missing or malformed X-Hub-Signature-256 header.")
        return False

    if (
        not settings.WHATSAPP_APP_SECRET
        or settings.WHATSAPP_APP_SECRET == "replace_with_meta_app_secret"
    ):
        logger.error(
            "VERIFY_WEBHOOK_SIGNATURE is enabled, but WHATSAPP_APP_SECRET is not configured!"
        )
        return False

    expected_sig = hmac.new(
        key=settings.WHATSAPP_APP_SECRET.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()

    received_sig = signature_header.removeprefix("sha256=").strip()
    is_valid = hmac.compare_digest(expected_sig, received_sig)
    if not is_valid:
        logger.warning("Webhook HMAC signature mismatch!")
    return is_valid


class RateLimiter:
    """Simple in-memory sliding-window rate limiter per sender."""

    def __init__(self, max_requests: int = 20, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        valid_hits = [ts for ts in self._hits[key] if ts > cutoff]
        if len(valid_hits) >= self.max_requests:
            self._hits[key] = valid_hits
            return False
        valid_hits.append(now)
        self._hits[key] = valid_hits
        return True
