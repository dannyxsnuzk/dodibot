"""Support screen."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..ui import keyboards as kb
from ..ui import texts
from ..ui.editor import render_from_callback

router = Router(name="support")


@router.callback_query(F.data == kb.CB_SUPPORT)
async def show_support(cb: CallbackQuery, session: AsyncSession) -> None:
    s = get_settings()
    await render_from_callback(
        cb, session=session,
        text=texts.support_screen(),
        keyboard=kb.support_kb(s.support_username),
    )
    await cb.answer()
