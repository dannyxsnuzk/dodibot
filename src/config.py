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

    shop_name: str = Field("Batman Store", alias="SHOP_NAME")
    support_username: str = Field("pvtbatman", alias="SUPPORT_USERNAME")

    binance_uid: str = Field("", alias="BINANCE_UID")
    binance_api_key: str = Field("", alias="BINANCE_API_KEY")
    binance_secret_key: str = Field("", alias="BINANCE_SECRET_KEY")
    binance_api_base_url: str = Field(
        "https://api.binance.com",
        alias="BINANCE_API_BASE_URL",
    )
    binance_pay_api_key: str = Field("", alias="BINANCE_PAY_API_KEY")
    binance_pay_secret_key: str = Field("", alias="BINANCE_PAY_SECRET_KEY")
    binance_pay_api_base_url: str = Field(
        "https://bpay.binanceapi.com",
        alias="BINANCE_PAY_API_BASE_URL",
    )
    binance_wallet_address: str = Field("", alias="BINANCE_WALLET_ADDRESS")
    bep20_wallet_address: str = Field("", alias="BEP20_WALLET_ADDRESS")
    bsc_rpc_url: str = Field("https://bsc-dataseed.binance.org", alias="BSC_RPC_URL")
    bep20_usdt_contract: str = Field(
        "0x55d398326f99059fF775485246999027B3197955",
        alias="BEP20_USDT_CONTRACT",
    )
    deposit_min: Decimal = Field(Decimal("1"), alias="DEPOSIT_MIN")
    deposit_max: Decimal = Field(Decimal("10000"), alias="DEPOSIT_MAX")
    deposit_required_confirmations: int = Field(12, alias="DEPOSIT_REQUIRED_CONFIRMATIONS")
    deposit_allowed_window_minutes: int = Field(60, alias="DEPOSIT_ALLOWED_WINDOW_MINUTES")
    uid_deposit_enabled: bool = Field(True, alias="UID_DEPOSIT_ENABLED")
    order_id_deposit_enabled: bool = Field(True, alias="ORDER_ID_DEPOSIT_ENABLED")
    bep20_deposit_enabled: bool = Field(True, alias="BEP20_DEPOSIT_ENABLED")

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
