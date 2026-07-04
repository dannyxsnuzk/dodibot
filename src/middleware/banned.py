"""Block any interaction from banned users with a brief notice."""
from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.users import get_user


class BannedUserMiddleware(BaseMiddleware):
    """Stop processing if the acting user has `is_banned=True`.

    Falls through for /start so the user still gets a banned notice on first
    contact instead of complete silence.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user is not None:
            user_id = event.from_user.id

        if user_id is not None:
            session: AsyncSession | None = data.get("session")
            if session is not None:
                u = await get_user(session, user_id)
                if u is not None and u.is_banned:
                    if isinstance(event, CallbackQuery):
                        with contextlib.suppress(Exception):
                            await event.answer("You are banned.", show_alert=True)
                    else:
                        with contextlib.suppress(Exception):
                            await event.answer("You are banned from using this bot.")
                    return None
        return await handler(event, data)
