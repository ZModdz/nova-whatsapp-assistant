"""Configuration management using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application settings loaded from .env or environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 1. Core App Settings
    APP_NAME: str = "NOVA"
    APP_ENV: str = "development"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    TIMEZONE: str = "Asia/Kuala_Lumpur"
    LOG_LEVEL: str = "INFO"

    # 2. Security & Access Control
    ALLOWED_WHATSAPP_NUMBER: str = "60123456789"
    WHATSAPP_VERIFY_TOKEN: str = "replace_with_your_custom_verify_string"
    WHATSAPP_APP_SECRET: str = "replace_with_meta_app_secret"
    VERIFY_WEBHOOK_SIGNATURE: bool = True

    # 3. WhatsApp Cloud API Credentials
    WHATSAPP_ACCESS_TOKEN: str = "replace_with_meta_access_token"
    WHATSAPP_PHONE_NUMBER_ID: str = "replace_with_meta_phone_number_id"
    WHATSAPP_API_VERSION: str = "v22.0"

    # 4. Local AI (Ollama) Configuration
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"
    OLLAMA_TIMEOUT_SECONDS: int = 90

    # 5. Database Configuration
    DATABASE_PATH: str = "data/jarvis.db"

    # 6. Google Calendar OAuth 2.0
    GOOGLE_CLIENT_SECRET_FILE: str = "credentials/client_secret.json"
    GOOGLE_TOKEN_FILE: str = "credentials/token.json"
    GOOGLE_CALENDAR_ID: str = "primary"
    GOOGLE_CALENDAR_DEFAULT_REMINDER_MINUTES: int = 30

    # 7. Reminder, Quiet Hours & Fallback Delivery
    QUIET_HOURS_ENABLED: bool = True
    QUIET_HOURS_START: str = "23:00"
    QUIET_HOURS_END: str = "07:00"
    WHATSAPP_WINDOW_KEEPALIVE_ENABLED: bool = True
    DESKTOP_NOTIFICATIONS_ENABLED: bool = True

    EMAIL_FALLBACK_ENABLED: bool = False
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_APP_PASSWORD: str = ""
    FALLBACK_RECIPIENT_EMAIL: str = ""

    # 8. Daily Briefing Settings
    MORNING_BRIEFING_ENABLED: bool = True
    MORNING_BRIEFING_TIME: str = "08:00"

    @field_validator("ALLOWED_WHATSAPP_NUMBER", mode="before")
    @classmethod
    def normalize_phone_number(cls, v: str) -> str:
        """Strip '+', spaces, and dashes so phone comparison is canonical."""
        if not v:
            return ""
        return "".join(ch for ch in str(v) if ch.isdigit())

    @property
    def tz(self) -> ZoneInfo:
        """Return the configured ZoneInfo timezone object."""
        return ZoneInfo(self.TIMEZONE)

    @property
    def db_file(self) -> Path:
        """Return Path object for the SQLite database, ensuring parent dir exists."""
        path = Path(self.DATABASE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
