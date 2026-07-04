from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Withdrawal


async def create_withdrawal(
    session: AsyncSession,
    *,
    user_id: int,
    amount_usdt: Decimal,
    method: str,
    address: str,
) -> Withdrawal:
    w = Withdrawal(
        user_id=user_id,
        amount_usdt=amount_usdt,
        method=method,
        address=address,
        status="pending",
    )
    session.add(w)
    await session.commit()
    await session.refresh(w)
    return w


async def get_pending_withdrawals(session: AsyncSession, limit: int = 50) -> list[Withdrawal]:
    return list((await session.scalars(
        select(Withdrawal).where(Withdrawal.status == "pending").order_by(Withdrawal.id).limit(limit)
    )).all())


async def get_withdrawal(session: AsyncSession, wid: int) -> Withdrawal | None:
    return await session.get(Withdrawal, wid)


async def decide_withdrawal(
    session: AsyncSession,
    *,
    withdrawal: Withdrawal,
    approved: bool,
    admin_id: int,
    admin_note: str = "",
) -> Withdrawal:
    withdrawal.status = "approved" if approved else "rejected"
    withdrawal.admin_note = admin_note
    withdrawal.decided_by = admin_id
    withdrawal.decided_at = datetime.now(timezone.utc)
    await session.commit()
    return withdrawal


async def recent_withdrawals(session: AsyncSession, user_id: int, limit: int = 10) -> list[Withdrawal]:
    return list((await session.scalars(
        select(Withdrawal).where(Withdrawal.user_id == user_id)
        .order_by(desc(Withdrawal.id)).limit(limit)
    )).all())
