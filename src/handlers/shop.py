"""Shop list, product detail, buy flow."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Transaction
from ..db.session import SessionLocal
from ..repositories import payments as payments_repo
from ..repositories import products as products_repo
from ..repositories import users as users_repo
from ..services.payment_flow import verify_reference
from ..services.payment_verification import detect_id_type
from ..services.shop import BuyError, OutOfStock, buy_product_quantity
from .states import ShopStates
from ..ui import keyboards as kb
from ..ui import texts
from ..ui.editor import render_from_callback
from ..ui.emoji import find_emoji_id

router = Router(name="shop")
log = logging.getLogger(__name__)
_ACTIVE_PAYMENT_BY_MESSAGE: dict[tuple[int, int], int] = {}
MAX_TX_ATTEMPTS = 2
PAYMENT_FINAL_STATUSES = {
    "auto_verified",
    "manual_approved",
    "delivered",
}
PAYMENT_APPEALABLE_STATUSES = {"auto_rejected"}
PAYMENT_CLOSED_STATUSES = {
    "auto_failed",
    "delivery_failed",
    "duplicate_reference",
    "invalid_reference",
    "manual_rejected",
}
PAYMENT_STOP_STATUSES = PAYMENT_FINAL_STATUSES | PAYMENT_APPEALABLE_STATUSES | PAYMENT_CLOSED_STATUSES | {
    "ignored",
    "manual_review",
}


def _payment_rejection_reason(payment) -> str:
    note = str(payment.verification_note or "").lower()
    status = str(payment.status or "")
    if status == "duplicate_reference" or "duplicate txid" in note:
        return "This TxID has already been used."
    if status == "invalid_reference":
        return "Invalid TxID format."
    if "amount_rejected" in note or "amount" in note and "expected" in note:
        return "Deposit amount is below the order total."
    if "asset_rejected" in note:
        return "Only USDT deposits are accepted."
    if "no matching binance transaction" in note:
        return "TxID was not found in Binance deposit history."
    if "api" in note or "configured" in note:
        return "Automatic verification is temporarily unavailable."
    return "No matching USDT deposit was found."


def _payment_timing() -> tuple[int, int]:
    settings = get_settings()
    wait_seconds = max(10, int(settings.payment_verify_wait_seconds))
    configured_interval = int(settings.payment_verify_interval_seconds)
    if wait_seconds <= 15:
        interval_seconds = wait_seconds
    else:
        interval_seconds = min(max(15, configured_interval), 20, wait_seconds)
    return wait_seconds, interval_seconds


async def _order_amounts(
    session: AsyncSession,
    *,
    user_id: int,
    product,
    qty: int,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    total = (Decimal(str(product.price_usdt)) * Decimal(qty)).quantize(Decimal("0.01"))
    user = await users_repo.get_user(session, user_id)
    balance = Decimal(str(user.balance_usdt or 0)) if user is not None else Decimal("0")
    balance = balance.quantize(Decimal("0.01"))
    deduction = min(max(balance, Decimal("0.00")), total)
    amount_due = (total - deduction).quantize(Decimal("0.01"))
    return total, balance, deduction, amount_due


async def _release_payment_reservation(session: AsyncSession, payment) -> None:
    await products_repo.release_user_reservations(
        session,
        product_id=payment.product_id,
        user_id=payment.user_id,
    )


@router.callback_query(F.data.in_({kb.CB_SHOP, kb.CB_REFRESH_SHOP}))
async def show_shop(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    items = await products_repo.list_active_products_with_stock(session, in_stock_only=True)
    if not items:
        await render_from_callback(cb, session=session, text=texts.shop_empty(),
                                   keyboard=kb.main_menu_kb())
        await cb.answer()
        return
    reserved_product_name = None
    data = await state.get_data()
    reserved_product_id = data.get("reserved_product_id")
    if reserved_product_id:
        reserved_product = await products_repo.get_product(session, int(reserved_product_id))
        if reserved_product is not None:
            reserved_product_name = reserved_product.display_name
    rows = [
        (
            p.id,
            p.display_name,
            p.emoji,
            p.emoji_id,
            p.duration_label,
            stock,
        )
        for p, stock in items
    ]
    await render_from_callback(
        cb, session=session,
        text=texts.shop_header(total=len(items), reserved_product=reserved_product_name),
        keyboard=kb.shop_list_kb(rows),
    )
    if cb.data == kb.CB_REFRESH_SHOP:
        await cb.answer("Refreshed")
    else:
        await cb.answer()


@router.callback_query(F.data.startswith(f"{kb.CB_PRODUCT}:"))
async def show_product(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    pid = int(cb.data.split(":")[2])
    product = await products_repo.get_product(session, pid)
    if product is None or not product.is_active:
        await cb.answer("Product not available.", show_alert=True)
        return
    stock = await products_repo.count_available_stock(session, pid)
    await render_from_callback(
        cb, session=session,
        text=texts.product_detail(
            name=product.display_name,
            emoji=product.emoji,
            emoji_id=product.emoji_id,
            duration=product.duration_label,
            price=Decimal(str(product.price_usdt)),
            description=product.description,
            stock=stock,
        ),
        keyboard=kb.product_detail_kb(product.id, can_buy=stock > 0),
    )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{kb.CB_BUY}:"))
async def buy_confirm(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _release_state_reservation(session, state, cb.from_user.id)
    await state.clear()
    pid = int(cb.data.split(":")[2])
    product = await products_repo.get_product(session, pid)
    if product is None:
        await cb.answer("Unavailable.", show_alert=True)
        return
    price = Decimal(str(product.price_usdt))
    await render_from_callback(
        cb, session=session,
        text=texts.buy_quantity(
            name=product.display_name,
            emoji=product.emoji,
            emoji_id=product.emoji_id,
            duration=product.duration_label,
            price=price,
        ),
        keyboard=kb.quantity_kb(product.id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{kb.CB_QTY}:"))
async def show_order_summary(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _release_state_reservation(session, state, cb.from_user.id)
    await state.clear()
    _, _, pid_s, qty_s = cb.data.split(":")
    await _render_order_summary(cb, session, product_id=int(pid_s), qty=int(qty_s))


@router.callback_query(F.data.startswith(f"{kb.CB_ORDER_SUMMARY}:"))
async def back_to_order_summary(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _release_state_reservation(session, state, cb.from_user.id)
    await state.clear()
    _, _, pid_s, qty_s = cb.data.split(":")
    await _render_order_summary(cb, session, product_id=int(pid_s), qty=int(qty_s))


@router.callback_query(F.data.startswith(f"{kb.CB_QTY_CUSTOM}:"))
async def ask_custom_quantity(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    pid = int(cb.data.split(":")[2])
    product = await products_repo.get_product(session, pid)
    if product is None or not product.is_active:
        await cb.answer("Unavailable.", show_alert=True)
        return
    await state.set_state(ShopStates.waiting_custom_qty)
    await state.update_data(product_id=pid)
    await render_from_callback(
        cb,
        session=session,
        text=texts.custom_qty_prompt(
            name=f"{product.display_name} {product.duration_label}",
            price=Decimal(str(product.price_usdt)),
        ),
        keyboard=kb.custom_quantity_kb(product.id),
    )
    await cb.answer()


@router.message(ShopStates.waiting_custom_qty)
async def receive_custom_quantity(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    active_payment_id = data.get("active_payment_id")
    if active_payment_id:
        active_payment = await payments_repo.get_payment_verification(session, int(active_payment_id))
        if active_payment is not None and active_payment.status == "delivered":
            await state.clear()
            await message.answer("This order is already delivered.", reply_markup=kb.main_menu_kb())
            return
        if active_payment is not None and active_payment.status in {
            "auto_rejected",
            "auto_failed",
            "manual_rejected",
            "delivery_failed",
        }:
            await state.clear()
            await message.answer(
                "This payment attempt has expired/rejected. Please start a new order or submit manual review.",
                reply_markup=kb.main_menu_kb(),
            )
            return
    pid = int(data.get("product_id") or 0)
    if raw.lower() == "cancel":
        await state.clear()
        await message.answer("Order cancelled.", reply_markup=kb.main_menu_kb())
        return
    try:
        qty = int(raw)
    except ValueError:
        await message.answer("Please send a whole number, for example: 5.")
        return
    if qty < 1:
        await message.answer("Quantity must be at least 1.")
        return
    await state.clear()
    await _send_order_summary(message, session, product_id=pid, qty=qty)


@router.callback_query(F.data.startswith(f"{kb.CB_PAY_METHODS}:"))
async def show_payment_methods(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await _release_state_reservation(session, state, cb.from_user.id)
    await state.clear()
    _, _, pid_s, qty_s = cb.data.split(":")
    pid = int(pid_s)
    qty = int(qty_s)
    product = await products_repo.get_product(session, pid)
    if product is None or not product.is_active:
        await cb.answer("Unavailable.", show_alert=True)
        return
    _total, balance, _deduction, amount_due = await _order_amounts(
        session, user_id=cb.from_user.id, product=product, qty=qty
    )
    if amount_due <= 0:
        await cb.answer("Your balance is enough. Use Pay with Balance.", show_alert=True)
        await _render_order_summary(cb, session, product_id=pid, qty=qty)
        return
    await render_from_callback(
        cb,
        session=session,
        text=texts.payment_method(
            name=product.display_name,
            emoji=product.emoji,
            emoji_id=product.emoji_id,
            duration=product.duration_label,
            qty=qty,
            price_each=Decimal(str(product.price_usdt)),
            balance=balance,
        ),
        keyboard=kb.payment_methods_kb(pid, qty, can_pay_balance=False),
    )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{kb.CB_PAY_BALANCE}:"))
async def pay_with_balance(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await _release_state_reservation(session, state, cb.from_user.id)
    await state.clear()
    _, _, pid_s, qty_s = cb.data.split(":")
    pid = int(pid_s)
    qty = int(qty_s)
    product = await products_repo.get_product(session, pid)
    if product is None or not product.is_active:
        await cb.answer("Unavailable.", show_alert=True)
        return
    total = (Decimal(str(product.price_usdt)) * Decimal(qty)).quantize(Decimal("0.01"))
    user = await users_repo.get_user(session, cb.from_user.id)
    balance = Decimal(str(user.balance_usdt or 0)) if user is not None else Decimal("0")
    if user is None or balance < total:
        await cb.answer("Insufficient balance.", show_alert=True)
        await render_from_callback(
            cb,
            session=session,
            text=texts.payment_method(
                name=product.display_name,
                emoji=product.emoji,
                emoji_id=product.emoji_id,
                duration=product.duration_label,
                qty=qty,
                price_each=Decimal(str(product.price_usdt)),
            ),
            keyboard=kb.payment_methods_kb(pid, qty, can_pay_balance=False),
        )
        return
    reserved = await products_repo.reserve_stock_items(
        session,
        product_id=pid,
        user_id=cb.from_user.id,
        qty=qty,
        ttl_minutes=10,
    )
    if reserved < qty:
        await products_repo.release_user_reservations(
            session,
            product_id=pid,
            user_id=cb.from_user.id,
        )
        await cb.answer(f"Only {reserved} code(s) could be reserved. Please choose a smaller quantity.", show_alert=True)
        return

    user.balance_usdt = balance - total
    session.add(Transaction(
        user_id=cb.from_user.id,
        kind="balance_purchase",
        amount_usdt=-total,
        note=f"shop purchase product_id={pid} qty={qty}",
    ))
    try:
        result = await buy_product_quantity(
            session,
            user_id=cb.from_user.id,
            product_id=pid,
            qty=qty,
        )
    except OutOfStock:
        await session.rollback()
        await products_repo.release_user_reservations(
            session,
            product_id=pid,
            user_id=cb.from_user.id,
        )
        await cb.answer("Stock is no longer available.", show_alert=True)
        return
    except BuyError as e:
        await session.rollback()
        await products_repo.release_user_reservations(
            session,
            product_id=pid,
            user_id=cb.from_user.id,
        )
        await cb.answer(str(e), show_alert=True)
        return

    with contextlib.suppress(Exception):
        await cb.message.delete()
    await cb.message.answer(
        texts.order_success(
            name=result.product.display_name,
            duration=result.product.duration_label,
            qty=qty,
            total=total,
            reference="Wallet Balance",
            payloads=result.payloads,
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await cb.answer("Paid with balance")


@router.callback_query(F.data.startswith(f"{kb.CB_PAY_BINANCE}:"))
async def show_binance_payment(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    _, _, pid_s, qty_s = cb.data.split(":")
    pid = int(pid_s)
    qty = int(qty_s)
    product = await products_repo.get_product(session, pid)
    if product is None or not product.is_active:
        await cb.answer("Unavailable.", show_alert=True)
        return
    _total, _balance, deduction, amount_due = await _order_amounts(
        session, user_id=cb.from_user.id, product=product, qty=qty
    )
    if amount_due <= 0:
        await cb.answer("Your balance is enough. Use Pay with Balance.", show_alert=True)
        await _render_order_summary(cb, session, product_id=pid, qty=qty)
        return
    reserved = await products_repo.reserve_stock_items(
        session,
        product_id=pid,
        user_id=cb.from_user.id,
        qty=qty,
        ttl_minutes=10,
    )
    if reserved < qty:
        await products_repo.release_user_reservations(
            session,
            product_id=pid,
            user_id=cb.from_user.id,
        )
        await cb.answer(f"Only {reserved} code(s) could be reserved. Please choose a smaller quantity.", show_alert=True)
        return
    settings = get_settings()
    binance_id = settings.binance_uid.strip()
    if not binance_id:
        await products_repo.release_user_reservations(
            session,
            product_id=pid,
            user_id=cb.from_user.id,
        )
        await cb.answer("Binance receiving UID is not configured.", show_alert=True)
        return
    await state.set_state(ShopStates.waiting_binance_reference)
    await state.update_data(
        product_id=pid,
        qty=qty,
        reserved_product_id=pid,
        payment_provider="binance_pay",
        balance_deduction=str(deduction),
        amount_due=str(amount_due),
        payment_chat_id=cb.message.chat.id if cb.message else None,
        payment_message_id=cb.message.message_id if cb.message else None,
        tx_attempts=0,
    )
    await render_from_callback(
        cb,
        session=session,
        text=texts.binance_pay_instructions(
            name=product.display_name,
            duration=product.duration_label,
            qty=qty,
            price_each=Decimal(str(product.price_usdt)),
            binance_id=binance_id,
            amount_due=amount_due,
        ),
        keyboard=kb.binance_payment_kb(pid, qty),
    )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{kb.CB_PAY_BEP20}:"))
async def show_bep20_payment(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    _, _, pid_s, qty_s = cb.data.split(":")
    pid = int(pid_s)
    qty = int(qty_s)
    product = await products_repo.get_product(session, pid)
    if product is None or not product.is_active:
        await cb.answer("Unavailable.", show_alert=True)
        return
    _total, _balance, deduction, amount_due = await _order_amounts(
        session, user_id=cb.from_user.id, product=product, qty=qty
    )
    if amount_due <= 0:
        await cb.answer("Your balance is enough. Use Pay with Balance.", show_alert=True)
        await _render_order_summary(cb, session, product_id=pid, qty=qty)
        return
    reserved = await products_repo.reserve_stock_items(
        session,
        product_id=pid,
        user_id=cb.from_user.id,
        qty=qty,
        ttl_minutes=10,
    )
    if reserved < qty:
        await products_repo.release_user_reservations(
            session,
            product_id=pid,
            user_id=cb.from_user.id,
        )
        await cb.answer(f"Only {reserved} code(s) could be reserved. Please choose a smaller quantity.", show_alert=True)
        return
    settings = get_settings()
    wallet_address = settings.receiving_wallet_address or settings.bep20_wallet_address
    if not wallet_address:
        await products_repo.release_user_reservations(
            session,
            product_id=pid,
            user_id=cb.from_user.id,
        )
        await cb.answer("BEP20 wallet is not configured.", show_alert=True)
        return
    await state.set_state(ShopStates.waiting_bep20_reference)
    await state.update_data(
        product_id=pid,
        qty=qty,
        reserved_product_id=pid,
        payment_provider="bep20",
        balance_deduction=str(deduction),
        amount_due=str(amount_due),
        payment_chat_id=cb.message.chat.id if cb.message else None,
        payment_message_id=cb.message.message_id if cb.message else None,
        tx_attempts=0,
    )
    await render_from_callback(
        cb,
        session=session,
        text=texts.bep20_payment_instructions(
            name=product.display_name,
            duration=product.duration_label,
            qty=qty,
            price_each=Decimal(str(product.price_usdt)),
            wallet_address=wallet_address,
            amount_due=amount_due,
        ),
        keyboard=kb.bep20_payment_kb(pid, qty),
    )
    await cb.answer()


@router.message(ShopStates.waiting_binance_reference)
@router.message(ShopStates.waiting_bep20_reference)
async def receive_binance_reference(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    reference = (message.text or "").strip()
    if not reference:
        await message.answer("Please send the payment Order ID / TxID / transaction hash.")
        return
    data = await state.get_data()
    provider_hint = str(data.get("payment_provider") or "binance_pay")
    detected_id_type = detect_id_type(reference)
    if detected_id_type == "unknown":
        await message.answer(
            "❌ That doesn't look like a valid Transaction Hash or Order ID. "
            "Please double-check and paste the exact ID/hash you received after payment."
        )
        return
    provider = "bep20" if detected_id_type == "bep20_hash" else "binance_pay"
    if provider != provider_hint:
        log.warning(
            "payment_verify provider_mismatch user_id=%s hinted_provider=%s detected_provider=%s "
            "detected_id_type=%s reference=%r",
            message.from_user.id,
            provider_hint,
            provider,
            detected_id_type,
            reference,
        )
    duplicate_payment = await payments_repo.get_reference(session, reference=reference)
    if duplicate_payment is not None:
        await message.answer("⚠️ This transaction has already been submitted.")
        return
    tx_attempts = int(data.get("tx_attempts") or 0)
    active_payment_id = data.get("active_payment_id")
    if active_payment_id:
        active_payment = await payments_repo.get_payment_verification(session, int(active_payment_id))
        if active_payment is not None and active_payment.status in PAYMENT_FINAL_STATUSES:
            await state.clear()
            await message.answer(
                "This payment is already verified. Please start a new order if needed.",
                reply_markup=kb.main_menu_kb(),
            )
            return
        if active_payment is not None and active_payment.status in {"auto_rejected", "manual_review"}:
            await message.answer(
                "This TxID attempt has finished. Use the manual review button shown on the payment screen, or start a new order.",
            )
            return
        if active_payment is not None and active_payment.status == "pending" and tx_attempts >= MAX_TX_ATTEMPTS:
            await message.answer(
                "You have used both TxID attempts for this order. Please wait for verification or manual review.",
            )
            return
        if (
            active_payment is not None
            and active_payment.status == "invalid_reference"
            and tx_attempts < MAX_TX_ATTEMPTS
        ):
            pass
        elif active_payment is not None and active_payment.status in PAYMENT_CLOSED_STATUSES:
            await state.clear()
            await message.answer(
                "This payment attempt is closed. Please start a new order.",
                reply_markup=kb.main_menu_kb(),
            )
            return
    pid = int(data.get("product_id") or 0)
    qty = int(data.get("qty") or 1)
    payment_chat_id = int(data.get("payment_chat_id") or message.chat.id)
    payment_message_id = data.get("payment_message_id")
    previous_tx_message_id = data.get("tx_message_id")
    if tx_attempts >= MAX_TX_ATTEMPTS:
        await state.clear()
        await message.answer(
            "You have used both TxID attempts for this order. Please start a new order or contact support.",
            reply_markup=kb.main_menu_kb(),
        )
        return
    product = await products_repo.get_product(session, pid)
    if product is None:
        await state.clear()
        await message.answer("Product is no longer available.", reply_markup=kb.main_menu_kb())
        return
    if active_payment_id:
        previous_payment = await payments_repo.get_payment_verification(session, int(active_payment_id))
        if previous_payment is not None and previous_payment.status == "pending":
            await payments_repo.mark_rejected(
                session,
                previous_payment,
                status="ignored",
                note="superseded by newer TxID attempt",
            )
        if previous_tx_message_id:
            with contextlib.suppress(Exception):
                await message.bot.delete_message(
                    chat_id=message.chat.id,
                    message_id=int(previous_tx_message_id),
                )
        if payment_message_id:
            with contextlib.suppress(Exception):
                await message.bot.delete_message(
                    chat_id=payment_chat_id,
                    message_id=int(payment_message_id),
                )
    expected = Decimal(str(data.get("amount_due") or "0")).quantize(Decimal("0.01"))
    if expected <= 0:
        _total, _balance, _deduction, expected = await _order_amounts(
            session, user_id=message.from_user.id, product=product, qty=qty
        )
    payment = await payments_repo.create_payment_verification(
        session,
        user_id=message.from_user.id,
        product_id=pid,
        provider=provider,
        reference=reference,
        qty=qty,
        expected_amount_usdt=expected,
    )
    tx_attempts += 1
    await state.update_data(
        tx_attempts=tx_attempts,
        active_payment_id=payment.id,
        tx_message_id=message.message_id,
    )
    log.info(
        "payment_verify submitted payment_id=%s user_id=%s product_id=%s qty=%s expected=%s "
        "provider=%s hinted_provider=%s detected_id_type=%s reference=%s",
        payment.id,
        message.from_user.id,
        pid,
        qty,
        expected,
        provider,
        provider_hint,
        detected_id_type,
        reference,
    )
    wait_seconds, _interval_seconds = _payment_timing()
    verifying_text = texts.payment_verifying(
        reference=reference,
        progress_pct=0,
        remaining_text=_format_remaining(wait_seconds),
    )
    if tx_attempts < MAX_TX_ATTEMPTS:
        verifying_text += (
            f"\n\nAttempt <b>{tx_attempts}/{MAX_TX_ATTEMPTS}</b>. "
            "If this TxID is wrong, send one more."
        )
    else:
        verifying_text += f"\n\nAttempt <b>{MAX_TX_ATTEMPTS}/{MAX_TX_ATTEMPTS}</b>."
    progress = await message.answer(verifying_text)
    progress_message_id = progress.message_id
    await state.update_data(
        payment_chat_id=payment_chat_id,
        payment_message_id=progress_message_id,
    )
    _ACTIVE_PAYMENT_BY_MESSAGE[(payment_chat_id, progress_message_id)] = payment.id
    asyncio.create_task(
        _verify_payment_background(message.bot, payment.id, payment_chat_id, progress_message_id)
    )


@router.callback_query(F.data.startswith(f"{kb.CB_CANCEL_ORDER}:"))
async def cancel_order(cb: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _release_state_reservation(session, state, cb.from_user.id)
    await state.clear()
    await render_from_callback(
        cb,
        session=session,
        text="Order cancelled.",
        keyboard=kb.main_menu_kb(),
    )
    await cb.answer("Cancelled")


@router.callback_query(F.data.startswith(f"{kb.CB_MANUAL_REVIEW}:"))
async def submit_manual_review(cb: CallbackQuery, session: AsyncSession) -> None:
    payment_id = int(cb.data.split(":")[2])
    payment = await payments_repo.get_payment_verification(session, payment_id)
    if payment is None or payment.user_id != cb.from_user.id:
        await cb.answer("Payment ticket not found.", show_alert=True)
        return
    if payment.status == "delivered":
        await cb.answer("Already delivered.", show_alert=True)
        return

    payment.status = "manual_review"
    await session.commit()

    product = await products_repo.get_product(session, payment.product_id)
    product_name = "Unknown product"
    if product is not None:
        product_name = f"{product.display_name} {product.duration_label}"
    settings = get_settings()
    for admin_id in settings.admin_ids:
        with contextlib.suppress(Exception):
            await cb.bot.send_message(
                admin_id,
                texts.admin_payment_review(
                    payment_id=payment.id,
                    user_id=payment.user_id,
                    product=product_name,
                    qty=payment.qty,
                    expected=Decimal(str(payment.expected_amount_usdt)),
                    reference=payment.reference,
                ),
                reply_markup=kb.admin_payment_review_kb(payment.id),
                parse_mode="HTML",
            )
    await render_from_callback(
        cb,
        session=session,
        text=texts.manual_review_submitted(payment_id=payment.id, reference=payment.reference),
        keyboard=kb.main_menu_kb(),
    )
    await cb.answer("Submitted")


async def _render_order_summary(
    cb: CallbackQuery,
    session: AsyncSession,
    *,
    product_id: int,
    qty: int,
) -> None:
    product = await products_repo.get_product(session, product_id)
    if product is None or not product.is_active:
        await cb.answer("Unavailable.", show_alert=True)
        return
    if qty < 1:
        await cb.answer("Quantity must be at least 1.", show_alert=True)
        return
    stock = await products_repo.count_available_stock(session, product_id)
    if qty > stock:
        await cb.answer(f"Only {stock} code(s) available right now.", show_alert=True)
        return
    total, balance, _deduction, _amount_due = await _order_amounts(
        session, user_id=cb.from_user.id, product=product, qty=qty
    )
    await render_from_callback(
        cb,
        session=session,
        text=texts.order_summary(
            name=product.display_name,
            emoji=product.emoji,
            emoji_id=product.emoji_id,
            duration=product.duration_label,
            qty=qty,
            price_each=Decimal(str(product.price_usdt)),
            balance=balance,
        ),
        keyboard=kb.order_summary_kb(product_id, qty, can_pay_balance=balance >= total),
    )
    await cb.answer()


async def _send_order_summary(
    message: Message,
    session: AsyncSession,
    *,
    product_id: int,
    qty: int,
) -> None:
    product = await products_repo.get_product(session, product_id)
    if product is None or not product.is_active:
        await message.answer("Product is no longer available.", reply_markup=kb.main_menu_kb())
        return
    stock = await products_repo.count_available_stock(session, product_id)
    if qty > stock:
        await message.answer(
            f"Only {stock} code(s) available right now. Please choose a smaller quantity.",
            reply_markup=kb.quantity_kb(product_id),
        )
        return
    total, balance, _deduction, _amount_due = await _order_amounts(
        session, user_id=message.from_user.id, product=product, qty=qty
    )
    await message.answer(
        texts.order_summary(
            name=product.display_name,
            emoji=product.emoji,
            emoji_id=product.emoji_id,
            duration=product.duration_label,
            qty=qty,
            price_each=Decimal(str(product.price_usdt)),
            balance=balance,
        ),
        reply_markup=kb.order_summary_kb(product_id, qty, can_pay_balance=balance >= total),
    )


async def _verify_payment_background(
    bot: Bot,
    payment_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    try:
        await _verify_payment_background_inner(bot, payment_id, chat_id, message_id)
    except Exception:
        log.exception("payment_verify unexpected_error payment_id=%s", payment_id)
        async with SessionLocal() as session:
            payment = await payments_repo.get_payment_verification(session, payment_id)
            if payment is None or payment.status in PAYMENT_STOP_STATUSES:
                return
            await payments_repo.mark_rejected(
                session,
                payment,
                status="manual_review",
                note="automatic verification error; needs admin review",
            )
            await _send_payment_manual_review(bot, session, payment, chat_id, message_id)


async def _verify_payment_background_inner(
    bot: Bot,
    payment_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    async with SessionLocal() as session:
        payment = await payments_repo.get_payment_verification(session, payment_id)
        if payment is None:
            log.info("payment_verify missing payment_id=%s", payment_id)
            return
        log.info(
            "payment_verify started payment_id=%s user_id=%s expected=%s reference=%s",
            payment.id,
            payment.user_id,
            payment.expected_amount_usdt,
            payment.reference,
        )
        wait_seconds, interval_seconds = _payment_timing()

        checks = [
            (
                min(100, int(elapsed / wait_seconds * 100)),
                _format_remaining(max(0, wait_seconds - elapsed)),
                0 if elapsed == 0 else interval_seconds,
            )
            for elapsed in range(0, wait_seconds + 1, interval_seconds)
        ]
        for pct, label, delay in checks:
            if delay:
                await asyncio.sleep(delay)
            with contextlib.suppress(Exception):
                await _edit_payment_progress(bot, payment, chat_id, message_id, pct, label)
            payment = await payments_repo.get_payment_verification(session, payment_id)
            if payment is None or payment.status in PAYMENT_STOP_STATUSES:
                return
            verified = await _verify_payment_reference(session, payment)
            payment = await payments_repo.get_payment_verification(session, payment_id)
            if payment is None or payment.status in PAYMENT_STOP_STATUSES:
                return
            if verified:
                log.info(
                    "payment_verify matched payment_id=%s provider=%s amount=%s reference=%s",
                    payment.id,
                    payment.provider,
                    payment.received_amount_usdt,
                    payment.reference,
                )
                await payments_repo.mark_manual_approved(
                    session,
                    payment,
                    admin_id=0,
                    note=payment.verification_note or "automatic payment verification approved",
                    received_amount=payment.received_amount_usdt or payment.expected_amount_usdt,
                )
                await _deliver_verified_payment(
                    bot,
                    session,
                    payment,
                    chat_id,
                    message_id,
                    payment.received_amount_usdt or payment.expected_amount_usdt,
                )
                return

        await asyncio.sleep(interval_seconds)
        payment = await payments_repo.get_payment_verification(session, payment_id)
        if payment is None or payment.status in PAYMENT_STOP_STATUSES:
            return
        verified = await _verify_payment_reference(session, payment)
        payment = await payments_repo.get_payment_verification(session, payment_id)
        if payment is None or payment.status in PAYMENT_STOP_STATUSES:
            return
        if verified:
            log.info(
                "payment_verify matched payment_id=%s provider=%s amount=%s reference=%s",
                payment.id,
                payment.provider,
                payment.received_amount_usdt,
                payment.reference,
            )
            await payments_repo.mark_manual_approved(
                session,
                payment,
                admin_id=0,
                note=payment.verification_note or "automatic payment verification approved",
                received_amount=payment.received_amount_usdt or payment.expected_amount_usdt,
            )
            await _deliver_verified_payment(
                bot,
                session,
                payment,
                chat_id,
                message_id,
                payment.received_amount_usdt or payment.expected_amount_usdt,
            )
            return

        log.info(
            "payment_verify manual_review payment_id=%s provider=%s wait_seconds=%s reference=%s note=%s",
            payment.id,
            payment.provider,
            wait_seconds,
            payment.reference,
            payment.verification_note,
        )
        await payments_repo.mark_rejected(
            session,
            payment,
            status="manual_review",
            note=payment.verification_note or f"no matching {payment.provider} transaction after {wait_seconds}s",
        )
        await _send_payment_manual_review(bot, session, payment, chat_id, message_id)


async def _verify_payment_reference(session: AsyncSession, payment) -> bool:
    settings = get_settings()
    if settings.payment_check_duplicate_txid:
        duplicate_payment = await payments_repo.get_completed_reference(
            session,
            provider=payment.provider,
            reference=payment.reference,
            exclude_id=payment.id,
        )
        if duplicate_payment is not None:
            payment.verification_note = f"duplicate TxID already used by payment #{duplicate_payment.id}"
            return False
    log.info(
        "payment_verify lookup payment_id=%s provider=%s reference=%s expected=%s check_duplicate_txid=%s",
        payment.id,
        payment.provider,
        payment.reference,
        payment.expected_amount_usdt,
        settings.payment_check_duplicate_txid,
    )
    return await verify_reference(payment.provider, payment.reference, payment)


async def _release_state_reservation(
    session: AsyncSession,
    state: FSMContext,
    user_id: int,
) -> None:
    data = await state.get_data()
    product_id = data.get("reserved_product_id")
    if product_id:
        await products_repo.release_user_reservations(
            session,
            product_id=int(product_id),
            user_id=user_id,
        )


def _format_remaining(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"~{minutes}m {secs}s"


async def _edit_payment_progress(
    bot: Bot,
    payment,
    chat_id: int,
    message_id: int,
    pct: int,
    remaining_text: str,
) -> None:
    if _ACTIVE_PAYMENT_BY_MESSAGE.get((chat_id, message_id)) != payment.id:
        return
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=texts.payment_verifying(
            reference=payment.reference,
            progress_pct=pct,
            remaining_text=remaining_text,
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _send_payment_rejected(bot: Bot, payment, chat_id: int, message_id: int) -> None:
    if _ACTIVE_PAYMENT_BY_MESSAGE.get((chat_id, message_id)) != payment.id:
        return
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=texts.payment_rejected(payment.reference, _payment_rejection_reason(payment)),
            reply_markup=kb.manual_review_kb(payment.id),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def _send_payment_manual_review(
    bot: Bot,
    session: AsyncSession,
    payment,
    chat_id: int,
    message_id: int,
) -> None:
    if _ACTIVE_PAYMENT_BY_MESSAGE.get((chat_id, message_id)) != payment.id:
        return

    product = await products_repo.get_product(session, payment.product_id)
    product_name = "Unknown product"
    if product is not None:
        product_name = f"{product.display_name} {product.duration_label}"

    settings = get_settings()
    for admin_id in settings.admin_ids:
        with contextlib.suppress(Exception):
            await bot.send_message(
                admin_id,
                texts.admin_payment_review(
                    payment_id=payment.id,
                    user_id=payment.user_id,
                    product=product_name,
                    qty=payment.qty,
                    expected=Decimal(str(payment.expected_amount_usdt)),
                    reference=payment.reference,
                ),
                reply_markup=kb.admin_payment_review_kb(payment.id),
                parse_mode="HTML",
            )

    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=texts.manual_review_submitted(payment_id=payment.id, reference=payment.reference),
            reply_markup=kb.main_menu_kb(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def _deliver_verified_payment(
    bot: Bot,
    session: AsyncSession,
    payment,
    chat_id: int,
    message_id: int,
    amount: Decimal,
) -> None:
    _ACTIVE_PAYMENT_BY_MESSAGE[(chat_id, message_id)] = payment.id
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=texts.payment_verified_detail(
                amount=amount,
                reference=payment.reference,
                network="USDT BEP20" if payment.provider == "bep20" else "USDT Binance Pay",
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    product = await products_repo.get_product(session, payment.product_id)
    if product is None:
        await payments_repo.mark_rejected(
            session,
            payment,
            status="delivery_failed",
            note="product_missing_after_payment_verification",
        )
        await bot.send_message(
            chat_id,
            "Payment verified, but delivery failed because the product is no longer available. Support will review it.",
            parse_mode="HTML",
        )
        return
    order_total = (Decimal(str(product.price_usdt)) * Decimal(payment.qty)).quantize(Decimal("0.01"))
    external_paid = Decimal(str(payment.expected_amount_usdt)).quantize(Decimal("0.01"))
    balance_deduction = max(Decimal("0.00"), (order_total - external_paid).quantize(Decimal("0.01")))
    if balance_deduction > 0:
        user = await users_repo.get_user(session, payment.user_id)
        current_balance = Decimal(str(user.balance_usdt or 0)) if user is not None else Decimal("0")
        if user is None or current_balance < balance_deduction:
            await payments_repo.mark_rejected(
                session,
                payment,
                status="delivery_failed",
                note="wallet_balance_changed_after_payment_verification",
            )
            await products_repo.release_user_reservations(
                session,
                product_id=payment.product_id,
                user_id=payment.user_id,
            )
            await bot.send_message(
                chat_id,
                "Payment verified, but your wallet balance is no longer enough for the balance deduction. Support will review it.",
                parse_mode="HTML",
            )
            return
        user.balance_usdt = current_balance - balance_deduction
        session.add(Transaction(
            user_id=payment.user_id,
            kind="balance_purchase",
            amount_usdt=-balance_deduction,
            ref_id=payment.id,
            note=f"partial balance purchase product_id={payment.product_id} qty={payment.qty}",
        ))

    try:
        result = await buy_product_quantity(
            session,
            user_id=payment.user_id,
            product_id=payment.product_id,
            qty=payment.qty,
        )
    except OutOfStock:
        if balance_deduction > 0:
            user.balance_usdt = current_balance
            session.add(Transaction(
                user_id=payment.user_id,
                kind="balance_refund",
                amount_usdt=balance_deduction,
                ref_id=payment.id,
                note=f"partial balance refund product_id={payment.product_id} qty={payment.qty}",
            ))
        await payments_repo.mark_rejected(
            session,
            payment,
            status="delivery_failed",
            note="out_of_stock_after_payment_verification",
        )
        await products_repo.release_user_reservations(
            session,
            product_id=payment.product_id,
            user_id=payment.user_id,
        )
        await bot.send_message(
            chat_id,
            "Payment verified, but delivery failed because stock is not available. Support will review it.",
            parse_mode="HTML",
        )
        return
    except BuyError as e:
        log.warning("payment_verify deliver_buy_error payment_id=%s error=%s", payment.id, e)
        if balance_deduction > 0:
            user.balance_usdt = current_balance
            session.add(Transaction(
                user_id=payment.user_id,
                kind="balance_refund",
                amount_usdt=balance_deduction,
                ref_id=payment.id,
                note=f"partial balance refund product_id={payment.product_id} qty={payment.qty}",
            ))
        await payments_repo.mark_rejected(
            session,
            payment,
            status="delivery_failed",
            note=str(e),
        )
        await products_repo.release_user_reservations(
            session,
            product_id=payment.product_id,
            user_id=payment.user_id,
        )
        await bot.send_message(chat_id, f"Payment verified, but delivery failed: {e}")
        return

    await payments_repo.mark_delivered(session, payment, order_ids=[o.id for o in result.orders])
    total = order_total
    with contextlib.suppress(Exception):
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    for (active_chat_id, active_message_id), active_payment_id in list(_ACTIVE_PAYMENT_BY_MESSAGE.items()):
        if active_payment_id == payment.id or active_chat_id == chat_id:
            with contextlib.suppress(Exception):
                await bot.delete_message(chat_id=active_chat_id, message_id=active_message_id)
            _ACTIVE_PAYMENT_BY_MESSAGE.pop((active_chat_id, active_message_id), None)
    _ACTIVE_PAYMENT_BY_MESSAGE.pop((chat_id, message_id), None)
    await bot.send_message(
        chat_id,
        texts.order_success(
            name=result.product.display_name,
            duration=result.product.duration_label,
            qty=payment.qty,
            total=total,
            reference=payment.reference,
            payloads=result.payloads,
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
