"""Settings loaded from environment / .env file."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    admin_ids: list[int] | int | str = Field(default_factory=list, alias="ADMIN_IDS")
    bot_username: str = Field("", alias="BOT_USERNAME")

    shop_name: str = Field("Dodi Store", alias="SHOP_NAME")
    support_username: str = Field("babaswiftbot", alias="SUPPORT_USERNAME")

    binance_uid: str = Field("", alias="BINANCE_UID")
    binance_api_key: str = Field("", alias="BINANCE_API_KEY")
    binance_secret_key: str = Field("", alias="BINANCE_SECRET_KEY")
    binance_api_base_url: str = Field(
        "https://api.binance.com",
        alias="BINANCE_API_BASE_URL",
    )

    payment_require_amount_match: bool = Field(
        True,
        alias="PAYMENT_REQUIRE_AMOUNT_MATCH",
    )
    payment_check_duplicate_txid: bool = Field(
        True,
        alias="PAYMENT_CHECK_DUPLICATE_TXID",
    )
    payment_lookback_hours: int = Field(
        72,
        alias="PAYMENT_LOOKBACK_HOURS",
    )
    payment_verify_wait_seconds: int = Field(
        60,
        alias="PAYMENT_VERIFY_WAIT_SECONDS",
    )
    payment_verify_interval_seconds: int = Field(
        12,
        alias="PAYMENT_VERIFY_INTERVAL_SECONDS",
    )

    database_url: str = Field(
        "sqlite+aiosqlite:///./data/bot.db",
        alias="DATABASE_URL",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _fix_database_url(cls, v):
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if v is None or v == "":
            return []

        if isinstance(v, int):
            return [v]

        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]

        if isinstance(v, list):
            return [int(x) for x in v]

        return [int(v)]

    dashboard_host: str = Field("127.0.0.1", alias="DASHBOARD_HOST")
    dashboard_port: int = Field(8088, alias="DASHBOARD_PORT")
    dashboard_password: str = Field("change_me", alias="DASHBOARD_PASSWORD")
    dashboard_session_secret: str = Field(
        "change_me_session",
        alias="DASHBOARD_SESSION_SECRET",
    )

    withdraw_min: Decimal = Field(Decimal("5"), alias="WITHDRAW_MIN")
    withdraw_max: Decimal = Field(Decimal("1000"), alias="WITHDRAW_MAX")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    premium_button_icons: bool = Field(
        True,
        alias="PREMIUM_BUTTON_ICONS",
    )

    button_styles_enabled: bool = Field(
        True,
        alias="BUTTON_STYLES_ENABLED",
    )

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
