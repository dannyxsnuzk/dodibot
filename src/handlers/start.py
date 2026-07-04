"""/start command + main-menu navigation. This is where the single-message UX begins."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.users import get_or_create_user, get_user_by_referral_code
from ..ui import keyboards as kb
from ..ui import texts
from ..ui.editor import render, render_from_callback

router = Router(name="start")
log = logging.getLogger(__name__)


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep(message: Message, command, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    arg = (command.args or "").strip()
    referrer_id: int | None = None
    if arg.startswith("ref_"):
        code = arg[len("ref_"):]
        referrer = await get_user_by_referral_code(session, code)
        if referrer is not None and referrer.id != message.from_user.id:
            referrer_id = referrer.id
    await _start_common(message, session, referrer_id)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _start_common(message, session, None)


async def _start_common(message: Message, session: AsyncSession, referrer_id: int | None) -> None:
    assert message.from_user is not None
    user, _created = await get_or_create_user(
        session,
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referred_by=referrer_id,
    )
    if user.is_banned:
        await message.answer(texts.banned(), parse_mode="HTML")
        return
    await render(
        bot=message.bot,
        session=session,
        user_id=user.id,
        chat_id=message.chat.id,
        text=texts.main_menu(message.from_user.first_name or ""),
        keyboard=kb.main_menu_kb(),
        message_id=None,  # always send a fresh message on /start
    )


@router.callback_query(F.data == kb.CB_MAIN)
async def show_main_menu(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await render_from_callback(
        cb,
        session=session,
        text=texts.main_menu(cb.from_user.first_name or ""),
        keyboard=kb.main_menu_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_NOOP)
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.message(Command("help", "menu"))
async def cmd_help(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user, _ = await get_or_create_user(
        session,
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referred_by=None,
    )
    if user.is_banned:
        return
    await render(
        bot=message.bot,
        session=session,
        user_id=user.id,
        chat_id=message.chat.id,
        text=texts.help_text() + "\n\n" + texts.main_menu(message.from_user.first_name or ""),
        keyboard=kb.main_menu_kb(),
        message_id=None,
    )
