"""Refer & earn screen."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..repositories import referrals as ref_repo
from ..repositories.users import get_user
from ..ui import keyboards as kb
from ..ui import texts
from ..ui.editor import render_from_callback

router = Router(name="refer")


def _build_link(bot_username: str, code: str) -> str:  # noqa: D401
    return f"https://t.me/{bot_username}?start=ref_{code}"


async def _show_refer(cb: CallbackQuery, session: AsyncSession) -> None:
    s = get_settings()
    user = await get_user(session, cb.from_user.id)
    if user is None:
        await cb.answer("User not found.", show_alert=True)
        return
    summary = await ref_repo.referral_summary(session, user.id)
    link = _build_link(s.bot_username, user.referral_code)
    await render_from_callback(
        cb, session=session,
        text=texts.refer_screen(
            ref_24h=summary["in_24h"],
            ref_7d=summary["in_7d"],
            ref_total=summary["total"],
            earned_total=summary["earned_total"],
            available=summary["available"],
            transferred=summary["transferred"],
            referral_link=link,
        ),
        keyboard=kb.refer_kb(referral_link=link, has_balance=summary["available"] > 0),
    )


@router.callback_query(F.data == kb.CB_REFER)
async def show_refer(cb: CallbackQuery, session: AsyncSession) -> None:
    await _show_refer(cb, session)
    await cb.answer()


@router.callback_query(F.data == kb.CB_REFER_COPY)
async def show_link_alert(cb: CallbackQuery, session: AsyncSession) -> None:
    s = get_settings()
    user = await get_user(session, cb.from_user.id)
    if user is None:
        await cb.answer("User not found.", show_alert=True)
        return
    link = _build_link(s.bot_username, user.referral_code)
    await cb.answer(link, show_alert=True)


@router.callback_query(F.data == kb.CB_REFER_TRANSFER)
async def transfer_to_wallet(cb: CallbackQuery, session: AsyncSession) -> None:
    moved = await ref_repo.transfer_available_to_wallet(session, cb.from_user.id)
    if moved <= 0:
        await cb.answer("Nothing to transfer.", show_alert=True)
        return
    await cb.answer(f"+{moved:.2f} USDT moved to wallet", show_alert=False)
    await _show_refer(cb, session)
