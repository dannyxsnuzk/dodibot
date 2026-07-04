"""Wallet credit / debit + transaction log."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Transaction, User


async def credit(
    session: AsyncSession,
    *,
    user_id: int,
    amount: Decimal,
    kind: str,
    ref_id: int | None = None,
    note: str = "",
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    user.balance_usdt = (Decimal(str(user.balance_usdt or 0)) + Decimal(str(amount)))
    session.add(Transaction(
        user_id=user_id, kind=kind, amount_usdt=amount, ref_id=ref_id, note=note,
    ))
    await session.commit()
    return user


async def debit(
    session: AsyncSession,
    *,
    user_id: int,
    amount: Decimal,
    kind: str,
    ref_id: int | None = None,
    note: str = "",
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    if Decimal(str(user.balance_usdt or 0)) < Decimal(str(amount)):
        raise ValueError("Insufficient balance")
    user.balance_usdt = (Decimal(str(user.balance_usdt or 0)) - Decimal(str(amount)))
    session.add(Transaction(
        user_id=user_id, kind=kind, amount_usdt=-amount, ref_id=ref_id, note=note,
    ))
    await session.commit()
    return user
