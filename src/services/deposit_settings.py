"""Database-backed deposit configuration with environment defaults."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Setting

PREFIX = "deposit."


@dataclass(frozen=True)
class DepositSettings:
    binance_uid: str
    binance_api_key: str
    binance_secret: str
    binance_api_base_url: str
    binance_pay_api_key: str
    binance_pay_secret: str
    binance_pay_api_base_url: str
    binance_wallet_address: str
    bep20_wallet_address: str
    bsc_rpc_url: str
    bep20_usdt_contract: str
    minimum: Decimal
    maximum: Decimal
    required_confirmations: int
    allowed_window_minutes: int
    uid_enabled: bool
    order_id_enabled: bool
    bep20_enabled: bool


def _bool(value: str, default: bool) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


async def get_deposit_settings(session: AsyncSession) -> DepositSettings:
    env = get_settings()
    rows = (await session.scalars(
        select(Setting).where(Setting.key.like(f"{PREFIX}%"))
    )).all()
    values = {row.key.removeprefix(PREFIX): row.value for row in rows}

    def value(key: str, default: object) -> str:
        return values.get(key, str(default))

    return DepositSettings(
        binance_uid=value("binance_uid", env.binance_uid).strip(),
        binance_api_key=value(
            "binance_api_key", env.binance_api_key
        ).strip(),
        binance_secret=value(
            "binance_secret", env.binance_secret_key
        ).strip(),
        binance_api_base_url=value("binance_api_base_url", env.binance_api_base_url).strip(),
        binance_pay_api_key=value("binance_pay_api_key", env.binance_pay_api_key).strip(),
        binance_pay_secret=value("binance_pay_secret", env.binance_pay_secret_key).strip(),
        binance_pay_api_base_url=value(
            "binance_pay_api_base_url", env.binance_pay_api_base_url
        ).strip(),
        binance_wallet_address=value(
            "binance_wallet_address", env.binance_wallet_address
        ).strip(),
        bep20_wallet_address=value("bep20_wallet_address", env.bep20_wallet_address).strip(),
        bsc_rpc_url=value("bsc_rpc_url", env.bsc_rpc_url).strip(),
        bep20_usdt_contract=value("bep20_usdt_contract", env.bep20_usdt_contract).strip(),
        minimum=Decimal(value("minimum", env.deposit_min)),
        maximum=Decimal(value("maximum", env.deposit_max)),
        required_confirmations=max(
            1, int(value("required_confirmations", env.deposit_required_confirmations))
        ),
        allowed_window_minutes=max(
            1, int(value("allowed_window_minutes", env.deposit_allowed_window_minutes))
        ),
        uid_enabled=_bool(value("uid_enabled", env.uid_deposit_enabled), env.uid_deposit_enabled),
        order_id_enabled=_bool(
            value("order_id_enabled", env.order_id_deposit_enabled),
            env.order_id_deposit_enabled,
        ),
        bep20_enabled=_bool(
            value("bep20_enabled", env.bep20_deposit_enabled), env.bep20_deposit_enabled
        ),
    )


async def update_deposit_settings(
    session: AsyncSession,
    values: dict[str, str],
) -> None:
    allowed = {
        "binance_uid",
        "binance_api_key",
        "binance_secret",
        "binance_api_base_url",
        "binance_pay_api_key",
        "binance_pay_secret",
        "binance_pay_api_base_url",
        "binance_wallet_address",
        "bep20_wallet_address",
        "bsc_rpc_url",
        "bep20_usdt_contract",
        "minimum",
        "maximum",
        "required_confirmations",
        "allowed_window_minutes",
        "uid_enabled",
        "order_id_enabled",
        "bep20_enabled",
    }
    for key, raw_value in values.items():
        if key not in allowed:
            continue
        full_key = f"{PREFIX}{key}"
        record = await session.get(Setting, full_key)
        if record is None:
            session.add(Setting(key=full_key, value=str(raw_value)))
        else:
            record.value = str(raw_value)
    await session.commit()
