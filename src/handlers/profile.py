"""Profile + sub-screens (stats, notifs, orders, withdraw FSM, API tokens)."""
from __future__ import annotations

import contextlib
import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import ApiToken
from ..repositories import orders as orders_repo
from ..repositories import referrals as ref_repo
from ..repositories import withdrawals as wd_repo
from ..repositories.users import get_user, toggle_notifications
from ..ui import keyboards as kb
from ..ui import texts
from ..ui.editor import render, render_from_callback
from ..utils import gen_api_token
from .states import WithdrawStates

router = Router(name="profile")
log = logging.getLogger(__name__)


@router.callback_query(F.data == kb.CB_PROFILE)
async def show_profile(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_user(session, cb.from_user.id)
    if user is None:
        await cb.answer("User not found.", show_alert=True)
        return
    await render_from_callback(
        cb, session=session,
        text=texts.profile(
            user_id=user.id,
            balance=Decimal(str(user.balance_usdt or 0)),
            joined_at=user.joined_at,
        ),
        keyboard=kb.profile_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_PROFILE_STATS)
async def show_stats(cb: CallbackQuery, session: AsyncSession) -> None:
    summary = await ref_repo.referral_summary(session, cb.from_user.id)
    total_orders = await orders_repo.total_orders(session, cb.from_user.id)
    total_order_value = await orders_repo.total_order_value(session, cb.from_user.id)
    await render_from_callback(
        cb, session=session,
        text=texts.profile_stats(
            total_orders=total_orders,
            total_order_value=total_order_value,
            referrals=summary["total"],
            referral_earned=summary["earned_total"],
        ),
        keyboard=_back_only_kb(),
    )
    await cb.answer()


def _back_only_kb():
    from aiogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[kb.back_button(kb.CB_PROFILE)]])


def _orders_back_kb():
    from aiogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[kb.back_button(kb.CB_MAIN)]])


@router.callback_query(F.data == kb.CB_PROFILE_NOTIFS)
async def show_notifs(cb: CallbackQuery, session: AsyncSession) -> None:
    user = await get_user(session, cb.from_user.id)
    enabled = bool(user.notifications_enabled) if user else True
    await render_from_callback(
        cb, session=session,
        text=texts.profile_notifs(enabled=enabled),
        keyboard=kb.notifs_kb(enabled=enabled),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_PROFILE_NOTIFS_TOGGLE)
async def toggle_notifs(cb: CallbackQuery, session: AsyncSession) -> None:
    enabled = await toggle_notifications(session, cb.from_user.id)
    await render_from_callback(
        cb, session=session,
        text=texts.profile_notifs(enabled=enabled),
        keyboard=kb.notifs_kb(enabled=enabled),
    )
    await cb.answer("Updated")


@router.callback_query(
    (F.data == kb.CB_MY_ORDERS) | (F.data.startswith(f"{kb.CB_PROFILE_ORDERS}:"))
)
async def show_orders(cb: CallbackQuery, session: AsyncSession) -> None:
    page = 1 if cb.data == kb.CB_MY_ORDERS else max(1, int(cb.data.split(":")[2]))
    page_size = 5
    rows, total = await orders_repo.list_orders(session, cb.from_user.id, page=page, page_size=page_size)
    has_next = page * page_size < total
    if total == 0:
        await render_from_callback(
            cb, session=session,
            text=texts.profile_orders_header(total=0, page=1, page_size=page_size),
            keyboard=_orders_back_kb() if cb.data == kb.CB_MY_ORDERS else _back_only_kb(),
        )
        await cb.answer()
        return
    items = [
        (o.id, f"#{o.id} · {p.display_name} {p.duration_label} · {o.price_usdt:.2f} USDT")
        for o, p in rows
    ]
    await render_from_callback(
        cb, session=session,
        text=texts.profile_orders_header(total=total, page=page, page_size=page_size),
        keyboard=kb.orders_kb(items, page=page, has_next=has_next),
    )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{kb.CB_PROFILE_ORDER}:"))
async def show_order(cb: CallbackQuery, session: AsyncSession) -> None:
    oid = int(cb.data.split(":")[2])
    pair = await orders_repo.get_order(session, oid, cb.from_user.id)
    if pair is None:
        await cb.answer("Order not found.", show_alert=True)
        return
    order, product = pair
    await render_from_callback(
        cb, session=session,
        text=texts.order_detail(
            order_id=order.id,
            name=product.display_name,
            price=Decimal(str(order.price_usdt)),
            payload=order.payload_snapshot,
            created_at=order.created_at,
        ),
        keyboard=kb.order_detail_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_PROFILE_WITHDRAW)
async def withdraw_intro(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    s = get_settings()
    user = await get_user(session, cb.from_user.id)
    bal = Decimal(str(user.balance_usdt)) if user else Decimal("0")
    await state.clear()
    await render_from_callback(
        cb, session=session,
        text=texts.withdraw_intro(balance=bal, min_amt=s.withdraw_min, max_amt=s.withdraw_max),
        keyboard=kb.withdraw_method_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{kb.CB_PROFILE_WITHDRAW_METHOD}:"))
async def withdraw_method_chosen(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    method = cb.data.split(":")[3]
    user = await get_user(session, cb.from_user.id)
    bal = Decimal(str(user.balance_usdt)) if user else Decimal("0")
    await state.set_state(WithdrawStates.waiting_amount)
    await state.update_data(method=method)
    await render_from_callback(
        cb, session=session,
        text=texts.withdraw_ask_amount(method=method, balance=bal),
        keyboard=kb.withdraw_cancel_kb(),
    )
    await cb.answer()


@router.message(WithdrawStates.waiting_amount)
async def withdraw_amount_input(message: Message, session: AsyncSession, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    s = get_settings()
    user = await get_user(session, message.from_user.id)
    if text == "cancel" or user is None:
        await state.clear()
        await _back_to_profile(message, session, user)
        with contextlib.suppress(Exception):
            await message.delete()
        return
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer(f"{texts.pe('warning')} Invalid amount. Try again or send <code>cancel</code>.",
                             parse_mode="HTML")
        return
    if amount < s.withdraw_min or amount > s.withdraw_max:
        await message.answer(
            f"{texts.pe('warning')} Amount must be between {s.withdraw_min:.2f} and {s.withdraw_max:.2f} USDT.",
            parse_mode="HTML",
        )
        return
    if amount > Decimal(str(user.balance_usdt)):
        await message.answer(f"{texts.pe('warning')} Insufficient balance.", parse_mode="HTML")
        return
    data = await state.get_data()
    method = data.get("method", "binance")
    await state.update_data(amount_cents=int(amount * 100))
    await state.set_state(WithdrawStates.waiting_address)
    if user.last_chat_id and user.last_menu_message_id:
        await render(
            bot=message.bot, session=session,
            user_id=user.id, chat_id=user.last_chat_id,
            text=texts.withdraw_ask_address(method=method, amount=amount),
            keyboard=kb.withdraw_cancel_kb(),
            message_id=user.last_menu_message_id,
        )
    with contextlib.suppress(Exception):
        await message.delete()


@router.message(WithdrawStates.waiting_address)
async def withdraw_address_input(message: Message, session: AsyncSession, state: FSMContext) -> None:
    text = (message.text or "").strip()
    user = await get_user(session, message.from_user.id)
    if text.lower() == "cancel" or user is None:
        await state.clear()
        await _back_to_profile(message, session, user)
        with contextlib.suppress(Exception):
            await message.delete()
        return
    if len(text) < 3:
        await message.answer(f"{texts.pe('warning')} That doesn't look like a valid address.",
                             parse_mode="HTML")
        return
    data = await state.get_data()
    method = data.get("method", "binance")
    amount = (Decimal(data.get("amount_cents", 0)) / Decimal(100)).quantize(Decimal("0.01"))

    if amount > Decimal(str(user.balance_usdt)):
        await message.answer(f"{texts.pe('warning')} Insufficient balance.", parse_mode="HTML")
        await state.clear()
        return
    try:
        w = await wd_repo.create_withdrawal_with_hold(
            session, user_id=user.id, amount_usdt=amount, method=method, address=text
        )
    except wd_repo.InsufficientWithdrawalBalance:
        await message.answer(f"{texts.pe('warning')} Insufficient balance.", parse_mode="HTML")
        await state.clear()
        return
    await state.clear()
    if user.last_chat_id and user.last_menu_message_id:
        await render(
            bot=message.bot, session=session,
            user_id=user.id, chat_id=user.last_chat_id,
            text=texts.withdraw_submitted(wid=w.id),
            keyboard=kb.main_menu_kb(),
            message_id=user.last_menu_message_id,
        )
    with contextlib.suppress(Exception):
        await message.delete()
    await _notify_admins_new_withdrawal(message.bot, w, message.from_user.username)


async def _back_to_profile(message: Message, session: AsyncSession, user) -> None:
    if user is None or user.last_chat_id is None or user.last_menu_message_id is None:
        return
    await render(
        bot=message.bot, session=session,
        user_id=user.id, chat_id=user.last_chat_id,
        text=texts.profile(
            user_id=user.id, balance=Decimal(str(user.balance_usdt)), joined_at=user.joined_at,
        ),
        keyboard=kb.profile_kb(),
        message_id=user.last_menu_message_id,
    )


async def _notify_admins_new_withdrawal(bot, w, username: str | None) -> None:
    s = get_settings()
    text = (
        f"{texts.pe('withdraw')} <b>New withdrawal request</b>\n\n"
        f"User: <code>{w.user_id}</code> @{username or '-'}\n"
        f"Method: <b>{w.method.upper()}</b>\n"
        f"Amount: <b>{w.amount_usdt:.2f} USDT</b>\n"
        f"Address: <code>{w.address}</code>\n"
        f"Ticket #: <code>{w.id}</code>\n\n"
        f"Approve: <code>/approve_wd {w.id}</code>\n"
        f"Reject:  <code>/reject_wd {w.id} reason</code>"
    )
    for admin_id in s.admin_ids:
        with contextlib.suppress(Exception):
            await bot.send_message(admin_id, text, parse_mode="HTML")


# ─── Developer API ────────────────────────────────────────────────────────────

@router.callback_query(F.data == kb.CB_PROFILE_API)
async def show_api(cb: CallbackQuery, session: AsyncSession) -> None:
    token = await session.scalar(
        select(ApiToken).where(ApiToken.user_id == cb.from_user.id, ApiToken.revoked.is_(False))
    )
    await render_from_callback(
        cb, session=session,
        text=texts.api_screen(token.token if token else None),
        keyboard=kb.api_kb(has_token=token is not None, token_id=token.id if token else None),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_PROFILE_API_NEW)
async def new_api_token(cb: CallbackQuery, session: AsyncSession) -> None:
    # Revoke any existing token first
    existing = (await session.scalars(
        select(ApiToken).where(ApiToken.user_id == cb.from_user.id, ApiToken.revoked.is_(False))
    )).all()
    for t in existing:
        t.revoked = True
    new_token = gen_api_token()
    rec = ApiToken(user_id=cb.from_user.id, token=new_token, label="default")
    session.add(rec)
    await session.commit()
    await render_from_callback(
        cb, session=session,
        text=texts.api_token_created(new_token),
        keyboard=kb.api_kb(has_token=True, token_id=rec.id),
    )
    await cb.answer("Token generated")


@router.callback_query(F.data.startswith(f"{kb.CB_PROFILE_API_REVOKE}:"))
async def revoke_api(cb: CallbackQuery, session: AsyncSession) -> None:
    tid = int(cb.data.split(":")[3])
    rec = await session.get(ApiToken, tid)
    if rec is None or rec.user_id != cb.from_user.id:
        await cb.answer("Not found.", show_alert=True)
        return
    rec.revoked = True
    await session.commit()
    await render_from_callback(
        cb, session=session,
        text=texts.api_screen(None),
        keyboard=kb.api_kb(has_token=False, token_id=None),
    )
    await cb.answer("Revoked")
