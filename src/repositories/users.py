from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User
from ..utils import gen_referral_code


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_or_create_user(
    session: AsyncSession,
    *,
    user_id: int,
    username: str | None,
    full_name: str | None,
    referred_by: int | None = None,
) -> tuple[User, bool]:
    user = await session.get(User, user_id)
    if user is not None:
        # Keep username/full_name in sync.
        changed = False
        if user.username != username:
            user.username = username
            changed = True
        if user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if changed:
            await session.commit()
        return user, False

    # Generate a unique referral code (collision astronomically unlikely but handle).
    code = gen_referral_code()
    while await session.scalar(select(User).where(User.referral_code == code)):
        code = gen_referral_code()

    user = User(
        id=user_id,
        username=username,
        full_name=full_name,
        referral_code=code,
        referred_by=referred_by,
    )
    session.add(user)
    await session.commit()
    return user, True


async def get_user_by_referral_code(session: AsyncSession, code: str) -> User | None:
    return await session.scalar(select(User).where(User.referral_code == code))


async def set_last_menu_message(
    session: AsyncSession, user_id: int, chat_id: int, message_id: int
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        return
    if user.last_chat_id == chat_id and user.last_menu_message_id == message_id:
        return
    user.last_menu_message_id = message_id
    user.last_chat_id = chat_id
    await session.commit()


async def toggle_notifications(session: AsyncSession, user_id: int) -> bool:
    user = await session.get(User, user_id)
    if user is None:
        return False
    user.notifications_enabled = not user.notifications_enabled
    await session.commit()
    return user.notifications_enabled


async def adjust_balance(session: AsyncSession, user_id: int, delta) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    user.balance_usdt = (user.balance_usdt or 0) + delta
    await session.commit()
    return user
