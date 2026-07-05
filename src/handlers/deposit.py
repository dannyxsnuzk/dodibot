"""Wallet deposit menus and verification FSM."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import deposits as deposits_repo
from ..repositories.users import get_user
from ..services.deposit_settings import DepositSettings, get_deposit_settings
from ..services.deposit_verification import (
    DepositVerificationError,
    query_binance_pay_order,
    query_binance_pay_transaction,
    verify_bep20_tx,
)
from ..services.payment_flow import (
    is_plausible_reference,
    payment_instructions,
    retry_operation,
    run_with_progress,
)
from ..ui import keyboards as kb
from ..ui.editor import render, render_from_callback
from .states import DepositStates

router = Router(name="deposit")


@router.callback_query(F.data == kb.CB_DEPOSIT)
async def deposit_menu(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    settings = await get_deposit_settings(session)
    enabled = settings.uid_enabled or settings.order_id_enabled or settings.bep20_enabled
    text = (
        "━━━━━━━━━━━━━━\n"
        "<b>Choose Deposit Method</b>\n\n"
        "Select how you sent or want to send USDT.\n"
        "━━━━━━━━━━━━━━"
        if enabled
        else "💰 <b>Deposits are temporarily disabled.</b>\n\nPlease try again later."
    )
    await render_from_callback(
        cb,
        session=session,
        text=text,
        keyboard=kb.deposit_methods_kb(
            binance_enabled=settings.uid_enabled or settings.order_id_enabled,
            bep20_enabled=settings.bep20_enabled,
        ),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_DEPOSIT_BINANCE)
async def binance_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    settings = await get_deposit_settings(session)
    if not (settings.uid_enabled or settings.order_id_enabled):
        await cb.answer("Binance Pay deposits are disabled.", show_alert=True)
        return
    if not settings.binance_uid:
        await cb.answer("Binance UID is not configured.", show_alert=True)
        return
    await state.clear()
    await state.update_data(verification_method="auto_binance")
    await state.set_state(DepositStates.waiting_uid_order_id)
    await render_from_callback(
        cb,
        session=session,
        text=payment_instructions("binance_pay", settings.binance_uid),
        keyboard=kb.deposit_cancel_kb(),
    )
    await cb.answer()
    return
    await render_from_callback(
        cb,
        session=session,
        text=(
            "🟡 <b>Binance Pay</b>\n\n"
            "💡 You can send <b>any amount</b> — it will be added to your balance.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🏦 <b>Binance Pay / Internal Transfer</b>\n\n"
            "Binance ID:\n"
            f"<code>{_html(settings.binance_uid)}</code>\n"
            "👆 <i>Tap to copy</i>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "After sending, paste your Transaction Hash <b>(TxID)</b> or Order ID here and we'll verify it <b>automatically</b>."
        ),
        keyboard=kb.deposit_cancel_kb(),
    )
    await cb.answer()


@router.message(DepositStates.waiting_binance_amount)
async def binance_amount(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    settings = await get_deposit_settings(session)
    amount = await _parse_amount(message, settings)
    if amount is None:
        return
    await state.update_data(expected_amount=str(amount))
    await state.set_state(DepositStates.choosing_binance_verification)
    await _render_for_message(
        message,
        session,
        "<b>Choose Verification Method</b>\n\n"
        f"Expected: <b>{amount} USDT</b>\n\n"
        "Select how the payment should be verified:",
        keyboard=kb.binance_verification_kb(
            order_id_enabled=settings.order_id_enabled,
            uid_enabled=settings.uid_enabled,
        ),
    )
    await _delete_user_message(message)


@router.callback_query(F.data == kb.CB_DEPOSIT_VERIFY)
async def show_binance_verification(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    amount = data.get("expected_amount")
    if not amount:
        await binance_start(cb, session, state)
        return
    settings = await get_deposit_settings(session)
    await state.set_state(DepositStates.choosing_binance_verification)
    await render_from_callback(
        cb,
        session=session,
        text=(
            "<b>Choose Verification Method</b>\n\n"
            f"Expected: <b>{_html(amount)} USDT</b>\n\n"
            "Select how the payment should be verified:"
        ),
        keyboard=kb.binance_verification_kb(
            order_id_enabled=settings.order_id_enabled,
            uid_enabled=settings.uid_enabled,
        ),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_DEPOSIT_VERIFY_ORDER)
async def choose_order_id(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    settings = await get_deposit_settings(session)
    if not settings.order_id_enabled:
        await cb.answer("Order ID verification is disabled.", show_alert=True)
        return
    if not (await state.get_data()).get("expected_amount"):
        await cb.answer("Deposit session expired. Start again.", show_alert=True)
        return
    await state.update_data(verification_method="binance_order_id")
    await state.set_state(DepositStates.waiting_direct_order_id)
    await render_from_callback(
        cb,
        session=session,
        text=(
            "🟡 <b>Order ID Verification</b>\n\n"
            "Paste the Binance Pay Order ID:"
        ),
        keyboard=kb.deposit_cancel_kb(kb.CB_DEPOSIT_VERIFY),
    )
    await cb.answer()


@router.callback_query(F.data == kb.CB_DEPOSIT_VERIFY_UID)
async def choose_uid_payment(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    settings = await get_deposit_settings(session)
    if not settings.uid_enabled:
        await cb.answer("UID verification is disabled.", show_alert=True)
        return
    if not (await state.get_data()).get("expected_amount"):
        await cb.answer("Deposit session expired. Start again.", show_alert=True)
        return
    await state.update_data(verification_method="binance_uid")
    await state.set_state(DepositStates.waiting_uid_order_id)
    await render_from_callback(
        cb,
        session=session,
        text=(
            "🟢 <b>UID Payment Verification</b>\n\n"
            "After completing the payment to the shown Binance UID, "
            "send the Binance Pay Order ID:"
        ),
        keyboard=kb.deposit_cancel_kb(kb.CB_DEPOSIT_VERIFY),
    )
    await cb.answer()


@router.message(DepositStates.waiting_uid_order_id)
@router.message(DepositStates.waiting_direct_order_id)
async def binance_order_input(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    reference = (message.text or "").strip()
    if not is_plausible_reference("binance_pay", reference):
        await message.answer("Please send a valid Binance Pay Order ID / transaction ID.")
        return
    data = await state.get_data()
    method = str(data.get("verification_method") or "")
    expected_amount: Decimal | None = None
    if data.get("expected_amount") is not None:
        try:
            expected_amount = Decimal(str(data.get("expected_amount")))
        except (InvalidOperation, ValueError):
            await state.clear()
            await message.answer("Deposit session expired. Please start again.")
            return
    if method not in {"auto_binance", "binance_uid", "binance_order_id"}:
        await state.clear()
        await message.answer("Deposit session expired. Please start again.")
        return
    order = await deposits_repo.create_deposit(
        session,
        user_id=message.from_user.id,
        method="binance_uid" if method == "auto_binance" else method,
        expected_amount=expected_amount,
    )
    await _render_for_message(message, session, "⏳ <b>Verifying Payment...</b>")
    await _delete_user_message(message)

    if await deposits_repo.reference_is_used(
        session, method=order.method, reference=reference
    ):
        await deposits_repo.reject_deposit(
            session,
            order,
            reference=reference,
            code="already_used",
            detail="Order ID was already credited.",
            provider="binance_pay",
        )
        await _finish_error(message, session, state, "❌ <b>Already Used</b>")
        return
    settings = await get_deposit_settings(session)
    try:
        match = await run_with_progress(
            reference,
            retry_operation(
                lambda: _query_binance_reference(reference, method, settings)
            ),
            lambda text: _render_for_message(message, session, text),
        )
        _validate_binance_match(order, match.amount, match.paid_at, settings)
        balance = await deposits_repo.finalize_binance(
            session, order, submitted_order_id=reference, match=match
        )
    except deposits_repo.DepositAlreadyUsed:
        await _finish_error(message, session, state, "❌ <b>Already Used</b>")
        return
    except DepositVerificationError as exc:
        await deposits_repo.reject_deposit(
            session,
            order,
            reference=reference,
            code=exc.code,
            detail=exc.detail,
            provider="binance_pay",
        )
        await _finish_error(message, session, state, _error_text(exc))
        return
    await state.clear()
    await _render_for_message(
        message,
        session,
        "✅ <b>Payment Verified</b>\n\n"
        f"💰 Wallet Credited: <b>{match.amount} USDT</b>\n"
        f"Wallet Balance: <b>{balance} USDT</b>",
        keyboard=kb.main_menu_kb(),
    )


@router.callback_query(F.data == kb.CB_DEPOSIT_BEP20)
async def bep20_start(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    settings = await get_deposit_settings(session)
    if not settings.bep20_enabled:
        await cb.answer("BEP20 deposits are disabled.", show_alert=True)
        return
    if not settings.bep20_wallet_address:
        await cb.answer("BEP20 wallet is not configured.", show_alert=True)
        return
    await state.clear()
    await state.set_state(DepositStates.waiting_bep20_txid)
    await render_from_callback(
        cb,
        session=session,
        text=payment_instructions("bep20", settings.bep20_wallet_address),
        keyboard=kb.deposit_cancel_kb(),
    )
    await cb.answer()
    return
    await render_from_callback(
        cb,
        session=session,
        text=(
            "🟢 <b>USDT (BEP20 - BSC)</b>\n\n"
            "💡 You can send <b>any amount</b> — it will be added to your balance.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🪙 <b>USDT (BEP20 - BSC)</b>\n\n"
            "Wallet Address:\n"
            f"<code>{_html(settings.bep20_wallet_address)}</code>\n"
            "👆 <i>Tap to copy</i>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "After sending, paste your Transaction Hash <b>(TxID)</b> here and we'll verify it <b>automatically</b>."
        ),
        keyboard=kb.deposit_cancel_kb(),
    )
    await cb.answer()


@router.message(DepositStates.waiting_bep20_amount)
async def bep20_amount(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    settings = await get_deposit_settings(session)
    amount = await _parse_amount(message, settings)
    if amount is None:
        return
    order = await deposits_repo.create_deposit(
        session, user_id=message.from_user.id, method="bep20", expected_amount=amount
    )
    await state.update_data(deposit_id=order.id)
    await state.set_state(DepositStates.waiting_bep20_txid)
    await _render_for_message(
        message,
        session,
        "🟠 <b>BEP20 transaction</b>\n\n"
        f"Expected: <b>{amount} USDT</b>\n\n"
        "<b>Paste Transaction Hash (TXID)</b>",
    )
    await _delete_user_message(message)


@router.message(DepositStates.waiting_bep20_txid)
async def bep20_txid(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    reference = (message.text or "").strip().lower()
    if not is_plausible_reference("bep20", reference):
        await message.answer("Please send a valid BEP20 transaction hash.")
        return
    data = await state.get_data()
    deposit_id = data.get("deposit_id")
    if deposit_id:
        order = await deposits_repo.get_deposit(
            session, int(deposit_id), message.from_user.id
        )
        if order is None:
            await state.clear()
            await message.answer("Deposit session expired. Please start again.")
            return
    else:
        order = await deposits_repo.create_deposit(
            session,
            user_id=message.from_user.id,
            method="bep20",
            expected_amount=None,
        )
    await _render_for_message(message, session, "⏳ <b>Verifying Payment...</b>")
    await _delete_user_message(message)
    if await deposits_repo.reference_is_used(
        session, method="bep20", reference=reference
    ):
        await deposits_repo.reject_deposit(
            session,
            order,
            reference=reference,
            code="already_used",
            detail="TXID was already credited.",
            provider="bsc",
        )
        await _finish_error(message, session, state, "❌ <b>Already Used</b>")
        return
    settings = await get_deposit_settings(session)
    try:
        expected_amount = (
            Decimal(str(order.expected_amount))
            if order.expected_amount is not None
            else None
        )
        match = await run_with_progress(
            reference,
            retry_operation(
                lambda: verify_bep20_tx(reference, expected_amount, settings)
            ),
            lambda text: _render_for_message(message, session, text),
        )
        _validate_deposit_amount(match.amount, settings)
        balance = await deposits_repo.finalize_bep20(session, order, match=match)
    except deposits_repo.DepositAlreadyUsed:
        await _finish_error(message, session, state, "❌ <b>Already Used</b>")
        return
    except DepositVerificationError as exc:
        await deposits_repo.reject_deposit(
            session,
            order,
            reference=reference,
            code=exc.code,
            detail=exc.detail,
            provider="bsc",
        )
        await _finish_error(message, session, state, _error_text(exc))
        return
    await state.clear()
    await _render_for_message(
        message,
        session,
        "✅ <b>Payment Verified</b>\n\n"
        f"💰 Wallet Credited: <b>{match.amount} USDT</b>\n"
        f"Wallet Balance: <b>{balance} USDT</b>",
        keyboard=kb.main_menu_kb(),
    )


async def _parse_amount(
    message: Message, settings: DepositSettings
) -> Decimal | None:
    try:
        amount = Decimal((message.text or "").strip())
        if not amount.is_finite():
            raise InvalidOperation
        amount = amount.quantize(Decimal("0.000001")).normalize()
    except (InvalidOperation, ValueError):
        await message.answer("❌ Invalid amount. Enter a number in USDT.")
        return None
    if amount < settings.minimum or amount > settings.maximum:
        await message.answer(
            f"❌ Amount must be between {settings.minimum} and {settings.maximum} USDT."
        )
        return None
    return amount


def _validate_binance_match(
    order, amount: Decimal, paid_at: datetime, settings: DepositSettings
) -> None:
    _validate_deposit_amount(amount, settings)
    if order.expected_amount is not None and amount != Decimal(str(order.expected_amount)):
        raise DepositVerificationError("wrong_amount", "The deposited amount does not match.")
    now = datetime.now(timezone.utc)
    if (
        paid_at < now - timedelta(minutes=settings.allowed_window_minutes)
        or paid_at > now + timedelta(minutes=2)
    ):
        raise DepositVerificationError(
            "expired", "The deposit is outside the allowed verification window."
        )


def _validate_deposit_amount(amount: Decimal, settings: DepositSettings) -> None:
    if amount < settings.minimum or amount > settings.maximum:
        raise DepositVerificationError(
            "wrong_amount", "Deposit amount is outside the configured limits."
        )


async def _query_binance_reference(
    reference: str,
    method: str,
    settings: DepositSettings,
):
    if method == "binance_uid":
        return await query_binance_pay_transaction(reference, settings)
    if method == "binance_order_id":
        return await query_binance_pay_order(reference, settings)

    first_error: DepositVerificationError | None = None
    if settings.uid_enabled:
        try:
            return await query_binance_pay_transaction(reference, settings)
        except DepositVerificationError as exc:
            first_error = exc
    if settings.order_id_enabled:
        try:
            return await query_binance_pay_order(reference, settings)
        except DepositVerificationError as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
    raise DepositVerificationError("not_configured", "Binance verification is disabled.")


async def _render_for_message(
    message: Message,
    session: AsyncSession,
    text: str,
    *,
    keyboard=None,
) -> None:
    user = await get_user(session, message.from_user.id)
    if user and user.last_chat_id and user.last_menu_message_id:
        await render(
            bot=message.bot,
            session=session,
            user_id=user.id,
            chat_id=user.last_chat_id,
            message_id=user.last_menu_message_id,
            text=text,
            keyboard=keyboard or kb.deposit_cancel_kb(),
        )
    else:
        await message.answer(text, reply_markup=keyboard or kb.deposit_cancel_kb())


async def _finish_error(
    message: Message, session: AsyncSession, state: FSMContext, text: str
) -> None:
    await state.clear()
    await _render_for_message(
        message, session, text, keyboard=kb.deposit_cancel_kb()
    )


def _error_text(exc: DepositVerificationError) -> str:
    labels = {
        "already_used": "❌ <b>Already Used</b>",
        "wrong_amount": "❌ <b>Wrong Amount</b>",
        "wrong_currency": "❌ <b>Wrong Currency</b>",
        "invalid_txid": "❌ <b>Invalid TXID</b>",
        "invalid_order_id": "❌ <b>Invalid Order ID</b>",
        "not_found": "❌ <b>Invalid Order ID</b>",
    }
    if exc.code == "not_found" and "transaction" in exc.detail.lower():
        return "❌ <b>Invalid TXID</b>"
    return f"{labels.get(exc.code, '❌ <b>Verification Failed</b>')}\n\n{_html(exc.detail)}"


async def _delete_user_message(message: Message) -> None:
    with contextlib.suppress(Exception):
        await message.delete()


def _html(value: object) -> str:
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
