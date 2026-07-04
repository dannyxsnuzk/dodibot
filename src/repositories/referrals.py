from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ReferralEarning, User


async def add_earning(
    session: AsyncSession,
    *,
    referrer_id: int,
    source_user_id: int,
    source_event: str,
    amount: Decimal,
    source_ref_id: int | None = None,
) -> ReferralEarning:
    rec = ReferralEarning(
        referrer_id=referrer_id,
        source_user_id=source_user_id,
        source_event=source_event,
        source_ref_id=source_ref_id,
        amount_usdt=amount,
        available=True,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


async def referral_summary(session: AsyncSession, user_id: int) -> dict:
    # Count referees by joined date
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    total = int(await session.scalar(
        select(func.count(User.id)).where(User.referred_by == user_id)
    ) or 0)
    in_24h = int(await session.scalar(
        select(func.count(User.id)).where(User.referred_by == user_id, User.joined_at >= cutoff_24h)
    ) or 0)
    in_7d = int(await session.scalar(
        select(func.count(User.id)).where(User.referred_by == user_id, User.joined_at >= cutoff_7d)
    ) or 0)

    earned_total = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(ReferralEarning.amount_usdt), 0))
        .where(ReferralEarning.referrer_id == user_id)
    ) or 0))
    available = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(ReferralEarning.amount_usdt), 0))
        .where(ReferralEarning.referrer_id == user_id, ReferralEarning.available.is_(True))
    ) or 0))
    transferred = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(ReferralEarning.amount_usdt), 0))
        .where(ReferralEarning.referrer_id == user_id, ReferralEarning.available.is_(False))
    ) or 0))

    return {
        "total": total,
        "in_24h": in_24h,
        "in_7d": in_7d,
        "earned_total": earned_total,
        "available": available,
        "transferred": transferred,
    }


async def transfer_available_to_wallet(session: AsyncSession, user_id: int) -> Decimal:
    """Move all available referral earnings to user's wallet balance."""
    available = Decimal(str(await session.scalar(
        select(func.coalesce(func.sum(ReferralEarning.amount_usdt), 0))
        .where(ReferralEarning.referrer_id == user_id, ReferralEarning.available.is_(True))
    ) or 0))
    if available <= 0:
        return Decimal("0")

    await session.execute(
        update(ReferralEarning)
        .where(ReferralEarning.referrer_id == user_id, ReferralEarning.available.is_(True))
        .values(available=False)
    )
    user = await session.get(User, user_id)
    if user is not None:
        user.balance_usdt = (user.balance_usdt or 0) + available
    await session.commit()
    return available
